"""HotShare core (no GUI). Protocol HSH3 over plain TCP - shared with the Android app.

  sender  -> "HSH3\\n" {name,pin,dest,files:[{id,t,path,size[,text]}]}\\n        ("HSH?\\n" -> {name,port,os} is the discovery hello)
  receiver-> "NO\\n" | "ERR msg\\n" | "OK\\n" {"off":{id:bytes_already_have}}\\n   (resume point per file)
  sender  -> for each file: bytes[off:size] then sha256(whole file) hex line
  receiver-> "DONE\\n" | "ERR msg\\n"       ("ERR paused" = the receiving user paused; the sender waits)
Reverse connection (works even if the receiver's firewall blocks incoming connections): the RECEIVER opens the connection with
  "HSHR\n" {name,port,os,pin}\n  and the other side answers either "HSHN\n" (nothing to send) or starts a normal "HSH3" send on that same connection.
Partial files stay in <folder>/.hotshare/<id>.part so a dropped link, a pause or a power cut resumes where it stopped.
Interrupted sends are saved as jobs (jobs.json) so they can be resumed after a restart.
"""
import hashlib, hmac, json, os, platform, queue, re, shutil, socket, threading, time

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".hotshare", "config.json")
DEF = {"name": socket.gethostname(), "folder": os.path.join(HOME, "Downloads", "HotShare"), "port": 45455, "pin": "", "auto": False, "theme": "Midnight",
       "visible": True, "open_after": False, "retries": 3, "reconnect_s": 900, "trusted": [], "history": []}
DISC, BUF, MAXH = 45454, 1 << 20, 16 << 20
_lock = threading.Lock()
APPROVED, PAUSED, CANCELLED = {}, set(), {}
SKIP, BAD, LEGACY = {}, {}, set()   # per transfer: file ids the user cancelled / that could not be received / per-file strike count / peers whose sender ignores the "done" list
FINISHED, FINALIZING = {}, set()   # per transfer: file ids already saved (a reconnect never resends them) / ids being moved into place right now     # approval memory per transfer (so reconnects never re-prompt) / transfers the receiving user paused


class Paused(Exception): """Paused by the user; partial data is kept."""
class PeerPaused(Exception): """The receiver paused this transfer."""
class ConnectionLost(Exception): """Could not reconnect within the reconnect window; resumable."""
class Rerun(Exception): """Drop this connection on purpose so the sender reconnects (a file was cancelled)."""
class Desync(Exception):
    """The other side's byte stream no longer matches the sizes it announced (older app version, or a file that changed size)."""
    def __init__(s, msg, fid=None): super().__init__(msg); s.fid = fid
class Unreachable(ConnectionRefusedError): """Nobody answered on the first attempt (closed app, wrong network, or a firewall)."""


HINT = "Could not reach {host}. Check that HotShare is open there and both devices are on the same network. If it is a Windows PC, allow HotShare in Windows Firewall (HotShare on the PC > Settings > Fix Windows Firewall)."


def load_cfg():
    c = dict(DEF)
    try: c.update(json.load(open(CFG_PATH, encoding="utf-8")))
    except Exception: pass
    return c


def save_cfg(c):
    with _lock:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        json.dump(c, open(CFG_PATH, "w", encoding="utf-8"), indent=1)


# ---------- file names that are safe on Windows (phones allow characters Windows refuses) ----------
_HEX = re.compile(rb"[0-9a-f]{64}")
_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def clean_seg(s):
    s = _BAD.sub("_", s).rstrip(" .")
    if s.split(".")[0].upper() in _RESERVED: s = "_" + s
    return s or "_"


def safe_dir(root, rel):
    parts = [clean_seg(p) for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return os.path.join(root, *parts) if parts else root


def safe_path(root, rel):
    p = safe_dir(root, rel)
    return p if p != root else os.path.join(root, "file")


def lp(p):
    """Windows long-path prefix so deep folders (>260 chars) still work."""
    if os.name == "nt" and len(p) > 240 and not p.startswith("\\\\?\\"): return "\\\\?\\" + os.path.abspath(p)
    return p


def unique(p):
    b, e = os.path.splitext(p); i = 1
    while os.path.exists(lp(p)):
        p = f"{b} ({i}){e}"; i += 1
    return p


def move_retry(src, dst, tries=12):
    """Antivirus/indexers briefly lock freshly written files on Windows; retry instead of failing at 100%."""
    for i in range(tries):
        try: return shutil.move(lp(src), lp(dst))
        except OSError:
            if i == tries - 1: raise
            time.sleep(.2 * (i + 1))


def _rm(p):
    try: os.remove(lp(p))
    except OSError: pass


def hsize(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB": return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fid(rel, size, mtime): return hashlib.sha1(f"{rel}|{size}|{mtime}".encode()).hexdigest()[:20]
def manifest_key(peer, files): return hashlib.sha1((peer + "|" + "|".join(sorted(f"{f.get('id')}:{f.get('size')}" for f in files))).encode()).hexdigest()[:16]


def tune(s):
    """Big socket buffers (default Windows buffers throttle Wi-Fi), no Nagle delay, keep-alive so a dead link is noticed in ~30 s."""
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 << 20); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if hasattr(socket, "SIO_KEEPALIVE_VALS"): s.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 10000, 3000))
        elif hasattr(socket, "TCP_KEEPIDLE"):
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4)
    except (OSError, AttributeError, ValueError): pass


# ---------- saved jobs (resume after restart / power cut) and cache ----------
def jobs_path(): return os.path.join(os.path.dirname(CFG_PATH), "jobs.json")


def load_jobs():
    try: return json.load(open(jobs_path(), encoding="utf-8"))
    except Exception: return []


def _write_jobs(jobs):
    with _lock:
        os.makedirs(os.path.dirname(jobs_path()), exist_ok=True); tmp = jobs_path() + ".tmp"
        json.dump(jobs, open(tmp, "w", encoding="utf-8")); os.replace(tmp, jobs_path())


def save_job(job): _write_jobs([j for j in load_jobs() if j["id"] != job["id"]] + [job])
def drop_job(jid): _write_jobs([j for j in load_jobs() if j["id"] != jid])
def new_job(host, port, name, items, dest): return {"id": hashlib.sha1(f"{host}{port}{time.time()}".encode()).hexdigest()[:12], "host": host, "port": port, "name": name, "dest": dest, "created": time.time(), "items": items}


def cache_size(root):
    n = 0
    try:
        for f in os.listdir(os.path.join(root, ".hotshare")): n += os.path.getsize(os.path.join(root, ".hotshare", f))
    except OSError: pass
    return n


def clear_cache(root, jobs=True):
    """Delete partial files of unfinished transfers (and, optionally, the saved interrupted-send list). Returns bytes freed."""
    n = cache_size(root); d = os.path.join(root, ".hotshare")
    try:
        for f in os.listdir(d): _rm(os.path.join(d, f))
        os.rmdir(d)
    except OSError: pass
    if jobs: _write_jobs([])
    FINISHED.clear(); SKIP.clear(); BAD.clear()
    return n


def clean_partials(root, days=7):
    d = os.path.join(root, ".hotshare")
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if time.time() - os.path.getmtime(p) > days * 86400: os.remove(p)
        os.rmdir(d)
    except OSError: pass


class Ctl:
    """One per transfer: callbacks, cancel/pause flags, speed meter and per-file state."""
    def __init__(s, cfg, ask=None, progress=None, log=print, text=None, hist=None):
        s.cfg, s.log, s.label, s.got = cfg, log, "", 0
        s.ask = ask or (lambda h, n, t: ("once", None)); s.progress = progress or (lambda *a: None)
        s.text = text or (lambda p, t: None); s.hist = hist or (lambda *a: None)
        s.declined = s.silent = s.discard = s.connected = False
        s.cancel, s.pause = threading.Event(), threading.Event()
        s._t0 = s._last = s._base = 0; s.moved = s._seen = 0; s.key = None; s.parts = []; s.job = None; s.state = ""
        s.names, s.sizes, s.fdone, s.last, s.on_files = [], [], [], (0, 0, 0, 0), (lambda: None); s.on_state = lambda st, msg: None
        s.fids, s.fstate, s.skip_req, s.gone, s.fsent = [], {}, set(), set(), set()   # per-file: ids / "skipped" rows / cancel requests / rows removed from the manifest / rows already fully sent

    def set_state(s, st, msg=""): s.state = st; s.on_state(st, msg)
    def set_files(s, names, sizes): s.names, s.sizes, s.fdone = list(names), list(sizes), [0] * len(names); s.on_files()
    def start(s, base=0): s._t0, s._last, s._base = time.monotonic(), 0, base

    def cancel_file(s, ix):
        """Skip ONE file of this transfer; the other files carry on. Returns False if that file is already finished."""
        if not 0 <= ix < len(s.sizes) or ix in s.fstate or (s.sizes[ix] > 0 and s.fdone[ix] >= s.sizes[ix]) or ix in s.fsent: return False
        s.skip_req.add(ix); s.fstate[ix] = "skipped"; s.progress(*s.last); return True

    def tick(s, done, total):
        if s.cancel.is_set(): raise InterruptedError("cancelled")
        if s.pause.is_set(): raise Paused("paused")
        s.moved += 1; now = time.monotonic()
        if now - s._last < .12 and done != total: return
        s._last = now; sp = (done - s._base) / max(now - s._t0, 1e-6)
        s.last = (done, total, sp, (total - done) / sp if sp > 0 else 0); s.progress(*s.last)


def readline(r, limit=65536):
    b = r.readline(limit + 1)
    if not b: raise EOFError("connection closed")
    if len(b) > limit: raise IOError("line too long")
    return b.rstrip(b"\n")


# ---------- receive (network reader + disk/hash writer run in parallel) ----------
class _Writer(threading.Thread):
    def __init__(s, ctl, dest, key):
        super().__init__(daemon=True); s.q, s.err, s.ctl, s.dest, s.f, s.md, s.key = queue.Queue(maxsize=8), None, ctl, dest, None, None, key

    def put(s, m):
        while True:
            if s.err: raise s.err
            try: s.q.put(m, timeout=.5); return
            except queue.Full: pass

    def run(s):
        try:
            while True:
                m = s.q.get(); k = m[0]
                if k == "stop": break
                if k == "start":
                    _, part, off = m; s.md = hashlib.sha256(); os.makedirs(lp(os.path.dirname(part)), exist_ok=True)    # another finished transfer may have removed the empty cache folder
                    if off:
                        with open(lp(part), "rb") as pf:
                            while b := pf.read(BUF): s.md.update(b)
                    s.f = open(lp(part), "ab", buffering=0)
                elif k == "data": s.f.write(m[1]); s.md.update(m[1])
                elif k == "fin":
                    _, expected, part, rel, size, idx, fid_ = m; s.f.close(); s.f = None
                    if expected != s.md.hexdigest(): _rm(part); raise IOError(f"checksum mismatch: {rel}")
                    FINALIZING.add(fid_)
                    try:
                        final = unique(safe_path(s.dest, rel)); os.makedirs(lp(os.path.dirname(final)), exist_ok=True); move_retry(part, final)
                        FINISHED.setdefault(s.key, set()).add(fid_)          # from now on a reconnecting sender skips this file entirely
                    finally: FINALIZING.discard(fid_)
                    c = s.ctl; c.fdone[idx] = size; c.got += 1; c.log(f"Received {rel} ({hsize(size)})"); c.hist("in", rel, size, c._peer)
        except BaseException as e: s.err = e
        finally:
            if s.f:
                try: s.f.close()
                except OSError: pass

    def finish(s):
        try: s.q.put(("stop",), timeout=10)
        except queue.Full: pass
        s.join(15)


def handle(sock, ctl, r=None, first=None):
    r = r or sock.makefile("rb", BUF)
    try: _handle(sock, ctl, r, first)
    finally:   # always tear the connection down NOW: a lingering half-open socket makes the other side wait and keeps partial files locked
        try: sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: r.close()
        except OSError: pass


def _handle(sock, ctl, r, first=None):
    cfg = ctl.cfg; sock.settimeout(120); tune(sock)
    if first is None: first = readline(r)
    if first == b"HSHN": return                       # reverse poll: the other side has nothing for us
    if first == b"HSHR":                              # someone asks us to send: we are not a sender in this app
        readline(r); sock.sendall(b"HSHN\n"); return
    if first == b"HSH?":   # discovery over the transfer port (works where UDP broadcast is blocked)
        if cfg.get("visible", True): sock.sendall(json.dumps({"name": cfg["name"], "port": cfg["port"], "os": platform.system()}).encode() + b"\n")
        return
    if first != b"HSH3": return
    h = json.loads(readline(r, MAXH).decode("utf-8", "replace")); peer = str(h.get("name", "?")); files = h["files"]; ctl.label = ctl._peer = peer
    if len(files) > 200000: raise ValueError("too many files")
    for f in files:
        if f.get("t") not in ("file", "text") or (f["t"] == "file" and (not isinstance(f.get("size"), int) or f["size"] < 0)): raise ValueError("bad manifest")
    total = sum(f["size"] for f in files if f.get("t") == "file"); key = ctl.key = manifest_key(peer, files)
    ok, root = (not cfg["pin"] or hmac.compare_digest(str(h.get("pin", "")), cfg["pin"])), cfg["folder"]
    if ok and key in PAUSED:
        ctl.silent = True; sock.sendall(b"ERR paused\n"); return
    if ok and time.time() - CANCELLED.get(key, 0) < 30:      # the receiving user cancelled this one: a reconnecting sender must not restart it
        ctl.silent = True; sock.sendall(b"ERR cancelled by the receiver\n"); return
    if ok and key in APPROVED and time.time() - APPROVED[key][0] < 3600: root = APPROVED[key][1]       # already approved: reconnects don't ask again
    elif ok and not cfg["auto"] and peer not in cfg["trusted"]:
        dec, folder = ctl.ask(h, len(files), total); ok = bool(dec)
        if ok and folder: root = folder
        if dec == "always" and peer not in cfg["trusted"]: cfg["trusted"].append(peer)
    if not ok:
        ctl.declined = True; sock.sendall(b"NO\n"); ctl.log(f"Declined transfer from {peer}"); return
    APPROVED[key] = (time.time(), root)
    dest = safe_dir(root, h.get("dest", "")); pdir = os.path.join(root, ".hotshare"); os.makedirs(lp(pdir), exist_ok=True)
    t0 = time.time()
    while any(f.get("id") in FINALIZING for f in files) and time.time() - t0 < 900: time.sleep(.2)    # an earlier connection is still saving a big file: let it finish
    ids = {f.get("id") for f in files}; fin_ok = FINISHED.get(key, set()) & ids
    skipped = (SKIP.get(key, set()) & ids) - fin_ok           # files the user cancelled (or that kept failing): the sender is told they are done
    fin = fin_ok | skipped; legacy = peer in LEGACY            # legacy: this sender ignores the "done" list, so it must be told "I already hold every byte" instead
    offs, parts = {}, {}
    for f in files:
        if f.get("t") != "file": continue
        if f["id"] in fin:
            if legacy: offs[f["id"]] = f["size"]
            continue
        p = os.path.join(pdir, re.sub(r"[^0-9A-Za-z]", "", str(f["id"]))[:40] + ".part"); parts[f["id"]] = p
        n = os.path.getsize(lp(p)) if os.path.exists(lp(p)) else 0
        if n > f["size"]: _rm(p); n = 0
        offs[f["id"]] = n
    ctl.parts = list(parts.values())
    total_left = sum(f["size"] for f in files if f.get("t") == "file" and f["id"] not in fin)
    if shutil.disk_usage(root).free < total_left - sum(offs.values()):
        sock.sendall(b"ERR not enough free space\n"); raise IOError("not enough free space")
    sock.sendall(b"OK\n" + json.dumps({"off": offs, "done": [] if legacy else sorted(fin)}).encode() + b"\n")
    total -= sum(f["size"] for f in files if f.get("t") == "file" and f["id"] in skipped)
    done = sum(n for i, n in offs.items() if i in parts) + sum(f["size"] for f in files if f.get("t") == "file" and f["id"] in fin_ok); ctl.start(done)
    ctl.set_files([f["path"] if f["t"] == "file" else "Text message" for f in files], [f.get("size", 0) for f in files]); ctl.fids = [f["id"] for f in files]
    for ix, f in enumerate(files):
        if f["id"] in skipped: ctl.fstate[ix] = "skipped"
        elif f["t"] == "file": ctl.fdone[ix] = f["size"] if f["id"] in fin else offs[f["id"]]
        elif f["id"] in fin: ctl.fdone[ix] = ctl.sizes[ix]

    def hashline(f):
        """The checksum line after a file's bytes. Anything else means the stream is out of step - say so instead of crashing on binary data."""
        b = r.readline(65)
        if len(b) == 65 and b[64:] == b"\n" and _HEX.fullmatch(b[:64]): return b[:64].decode()
        if not b: raise ConnectionError("connection lost")
        raise Desync(f"out of sync at '{f['path']}': the sender's data does not match the size it announced", f["id"])
    w = _Writer(ctl, dest, key); w.start()
    try:
        for idx, f in enumerate(files):
            if f["id"] in fin:
                if legacy and f.get("t") == "file": hashline(f)      # old sender: it still sends the checksum line of a file we already hold
                continue
            if f.get("t") == "text":
                txt = str(f.get("text", ""))[:1_000_000]; ctl.text(peer, txt); ctl.hist("in", "text", len(txt.encode()), peer); ctl.fdone[idx] = ctl.sizes[idx]; FINISHED.setdefault(key, set()).add(f["id"]); continue
            rel, size, off, part = f["path"], f["size"], offs[f["id"]], parts[f["id"]]
            if idx in ctl.skip_req: raise Rerun(f"skipped {rel}")
            if off: ctl.log(f"Resuming {rel} from {hsize(off)}")
            w.put(("start", part, off)); left = size - off
            while left:
                if idx in ctl.skip_req: raise Rerun(f"skipped {rel}")
                b = r.read(min(BUF, left))
                if not b: raise ConnectionError("connection lost")
                left -= len(b); done += len(b); ctl.fdone[idx] = size - left; w.put(("data", b)); ctl.tick(done, total)
            w.put(("fin", hashline(f), part, rel, size, idx, f["id"]))
        w.finish()
        if w.err: raise w.err
        sock.sendall(b"DONE\n"); ctl.progress(total, total, 0, 0); FINISHED.pop(key, None); SKIP.pop(key, None)
        for k_ in [k_ for k_ in BAD if k_[0] == key]: BAD.pop(k_, None)
        try: os.rmdir(pdir)      # nothing left in the cache: remove the folder too
        except OSError: pass
    except BaseException as e:
        w.finish()
        if isinstance(e, InterruptedError): APPROVED.pop(key, None); CANCELLED[key] = time.time()
        if isinstance(e, Rerun):      # the user cancelled file(s): remember them, drop their partial data, and let the sender reconnect without them
            for ix in ctl.skip_req: SKIP.setdefault(key, set()).add(ctl.fids[ix])
            for i in SKIP.get(key, ()): _rm(parts.get(i, ""))
        elif isinstance(e, Desync):
            if not legacy and any(f.get("t") == "file" and f["id"] in fin for f in files):    # first suspect: a sender that ignores the "done" list
                LEGACY.add(peer); e = Desync(f"{e} - retrying in compatibility mode", e.fid)
            else:                                                                           # same file went wrong twice: give up on that one file, keep the rest
                BAD[(key, e.fid)] = BAD.get((key, e.fid), 0) + 1
                if BAD[(key, e.fid)] >= 2: SKIP.setdefault(key, set()).add(e.fid); _rm(parts.get(e.fid, "")); e = Desync(f"{e} - that file was skipped, the rest continues", e.fid)
        try: sock.sendall(b"ERR paused\n" if isinstance(e, Paused) else f"ERR {e}\n".encode())
        except OSError: pass
        if ctl.discard:
            for p in parts.values(): _rm(p)
        raise e


# ---------- send (disk reader + hasher and the network sender run in parallel) ----------
def _readable(p):
    try:
        with open(lp(p), "rb"): return True
    except OSError: return False


def collect(paths):
    items = []
    for p in filter(None, paths):
        if os.path.isdir(p):
            base = os.path.dirname(p.rstrip("/\\"))
            for rt, _, fs in os.walk(p):
                for n in fs:
                    a = os.path.join(rt, n)
                    try: st = os.stat(lp(a))
                    except OSError: continue
                    if _readable(a): items.append({"t": "file", "abs": a, "rel": os.path.relpath(a, base), "size": st.st_size, "mtime": int(st.st_mtime)})
        elif os.path.isfile(p) and _readable(p):
            st = os.stat(lp(p)); items.append({"t": "file", "abs": p, "rel": os.path.basename(p), "size": st.st_size, "mtime": int(st.st_mtime)})
    return items


def text_item(txt): return {"t": "text", "text": txt, "rel": "text", "size": len(txt.encode())}


def _producer(items, offs, dones, q, stop):
    def put(m):
        while not stop.is_set():
            try: q.put(m, timeout=.5); return
            except queue.Full: pass
    try:
        for ix, it in enumerate(items):
            if stop.is_set(): return
            if it["t"] == "text" or it["id"] in dones: put(("skip", ix)); continue
            off, md = min(max(int(offs.get(it["id"], 0)), 0), it["size"]), hashlib.sha256()
            with open(lp(it["abs"]), "rb", buffering=0) as f:
                left = off
                while left:
                    b = f.read(min(BUF, left))
                    if not b: raise IOError("file changed while sending: " + it["rel"])
                    md.update(b); left -= len(b)
                put(("start", ix, off)); left = it["size"] - off
                while left:
                    if stop.is_set(): return
                    b = f.read(min(BUF, left))
                    if not b: raise IOError("file shrank while sending: " + it["rel"])
                    md.update(b); left -= len(b); put(("data", ix, b))
            put(("hash", ix, md.hexdigest()))
        put(("end",))
    except BaseException as e: put(("err", e))


def send_once(host, port, items, ctl, dest=""):
    ctl.set_state("connecting", "Connecting...")
    s = socket.create_connection((host, port), 10); tune(s); s.settimeout(180); r = s.makefile("rb", BUF)   # long: the receiver may be waiting for a tap on Accept
    try: _send_over(s, r, items, ctl, dest, host)
    finally:
        try: s.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: r.close()
        except OSError: pass
        s.close()


def _send_over(s, r, items, ctl, dest, host):
    """The sender side of HSH3 on an already-connected socket (used for normal sends and for reverse connections)."""
    cfg, man, total = ctl.cfg, [], 0
    all_items = items; live = [(ox, it) for ox, it in enumerate(all_items) if ox not in ctl.gone]     # rows removed by a per-file cancel are not announced any more
    items, omap = [it for _, it in live], [ox for ox, _ in live]
    for n, it in enumerate(items):
        if it["t"] == "text": man.append({"id": f"t{n}", "t": "text", "path": "text", "size": it["size"], "text": it["text"]})
        else:
            it["id"] = fid(it["rel"], it["size"], it["mtime"]); total += it["size"]
            man.append({"id": it["id"], "t": "file", "path": it["rel"].replace("\\", "/"), "size": it["size"]})
    stop = threading.Event(); th = None
    try:
        s.sendall(b"HSH3\n" + json.dumps({"name": cfg["name"], "pin": cfg["pin"], "dest": dest, "files": man}).encode() + b"\n")
        resp = readline(r)
        if resp.startswith(b"ERR"):
            msg = resp[4:].decode("utf-8", "replace")
            raise PeerPaused(msg) if msg == "paused" else PermissionError(msg)
        if resp != b"OK": raise PermissionError("Receiver declined (or PIN mismatch)")
        ctl.connected = True; s.settimeout(300); ctl.set_state("running", "")
        reply = json.loads(readline(r, MAXH)); offs, dones = reply["off"], set(reply.get("done", []))
        done = sum(offs.values()) + sum(it["size"] for it in items if it["t"] == "file" and it["id"] in dones); ctl.start(done)
        if len(ctl.names) != len(all_items): ctl.set_files(["Text message" if it["t"] == "text" else it["rel"] for it in all_items], [it["size"] for it in all_items])   # first connection only: later ones keep the rows
        for ix, it in enumerate(items):
            if it["t"] == "file": ctl.fdone[omap[ix]] = it["size"] if it["id"] in dones else min(max(int(offs.get(it["id"], 0)), 0), it["size"])
        q = queue.Queue(maxsize=8); th = threading.Thread(target=_producer, args=(items, offs, dones, q, stop), daemon=True); th.start()
        while True:
            m = q.get(); k = m[0]
            if k == "end": break
            if k == "err": raise m[1]
            ix = m[1]; ox = omap[ix]
            if k == "skip": ctl.fdone[ox] = items[ix]["size"]; ctl.fsent.add(ox)
            elif k == "start":
                if ox in ctl.skip_req: raise Rerun("file cancelled")
                ctl.fdone[ox] = m[2]
            elif k == "data":
                if ox in ctl.skip_req: raise Rerun("file cancelled")
                s.sendall(m[2]); done += len(m[2]); ctl.fdone[ox] += len(m[2]); ctl.tick(done, total)
            elif k == "hash": s.sendall(m[2].encode() + b"\n"); ctl.fdone[ox] = items[ix]["size"]; ctl.fsent.add(ox)
        s.settimeout(1800)       # the receiver may spend minutes moving / virus-scanning a big file before it says DONE: that is NOT a dead connection
        res = readline(r).decode()
        if res == "ERR paused": raise PeerPaused("paused")
        if res != "DONE": raise IOError(res or "no reply")
        ctl.progress(total, total, 0, 0); ctl.log(f"Sent {len(items)} item(s), {hsize(total)} to {ctl.label or host}")
        for i in items: ctl.hist("out", i["rel"], i["size"], ctl.label or host)
    finally:
        stop.set()
        if th: th.join(3)


def poll(host, port, cfg, timeout=.8):
    """Reverse connection: ask a device whether it has something for us (this also tells it who we are, so it can list us even when our
    firewall blocks its scans). Returns (sock, reader, first_line) when it starts sending, else None."""
    s = None
    try:
        s = socket.create_connection((host, port), timeout); tune(s); s.settimeout(timeout + 5); r = s.makefile("rb", BUF)
        s.sendall(b"HSHR\n" + json.dumps({"name": cfg["name"], "port": cfg["port"], "os": platform.system(), "pin": cfg["pin"]}).encode() + b"\n")
        first = readline(r)
        if first == b"HSHN": raise EOFError("nothing pending")
        return s, r, first
    except Exception:
        try: s.close()
        except Exception: pass
        return None


def _wait(ctl, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if ctl.cancel.is_set(): raise InterruptedError("cancelled")
        if ctl.pause.is_set(): raise Paused("paused")
        time.sleep(.15)


def _retry(ctl, attempt, deadline, e):
    now = time.monotonic()
    if ctl.moved != ctl._seen: ctl._seen, attempt, deadline = ctl.moved, 0, None    # progress since the last failure -> fresh window
    if deadline is None: deadline = now + ctl.cfg.get("reconnect_s", 300)
    if now > deadline: raise ConnectionLost(f"connection lost ({e})")
    attempt += 1; delay = min(10, 1.5 ** attempt)
    ctl.set_state("reconnecting", f"Connection lost - reconnecting in {int(delay) + 1}s"); _wait(ctl, delay)
    return attempt, deadline


def _dedupe(items):
    """The same file picked twice would corrupt resume bookkeeping."""
    seen, uniq = set(), []
    for it in items:
        k = fid(it["rel"], it["size"], it["mtime"]) if it["t"] == "file" else None
        if k is None or k not in seen: uniq.append(it); seen.add(k)
    return uniq


def send(host, port, items, ctl, dest=""):
    """Sends everything; reconnects and resumes on its own for up to cfg['reconnect_s'] seconds. Raises Paused / ConnectionLost (resumable)."""
    attempt, deadline = 0, None; items = _dedupe(items)
    while True:
        try: return send_once(host, port, items, ctl, dest)
        except Rerun:         # a file was cancelled: leave it (and everything already delivered) out and send the rest as a fresh, smaller transfer
            for ox in ctl.skip_req: ctl.gone.add(ox)
            ctl.skip_req.clear(); ctl.gone.update(ox for ox in ctl.fsent if ox < len(items))
            if ctl.job: ctl.job["items"] = [it for ox, it in enumerate(items) if ox not in ctl.gone]; save_job(ctl.job)
            if len(ctl.gone) >= len(items): ctl.progress(1, 1, 0, 0); return
            attempt, deadline = 0, None
        except (PermissionError, InterruptedError, Paused): raise
        except PeerPaused:
            ctl.set_state("waiting", "Paused by the receiver - waiting for it to resume"); _wait(ctl, 2)
        except (ConnectionRefusedError, socket.gaierror, socket.timeout) as e:
            if attempt == 0 and not ctl.moved and not ctl.connected: raise Unreachable(HINT.format(host=ctl.label or host))
            attempt, deadline = _retry(ctl, attempt, deadline, e)
        except (OSError, EOFError) as e:
            attempt, deadline = _retry(ctl, attempt, deadline, e)


# ---------- discovery ----------
def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("10.255.255.255", 1)); ips.add(s.getsockname()[0]); s.close()
    except OSError: pass
    try: ips.update(i for i in socket.gethostbyname_ex(socket.gethostname())[2] if not i.startswith("127."))
    except OSError: pass
    return sorted(ips)


def _udp_noreset(s):
    try: s.ioctl(socket.SIO_UDP_CONNRESET, False)   # Windows only
    except (AttributeError, OSError, ValueError): pass


def discovery_server(cfg):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); _udp_noreset(s); s.bind(("", DISC))
    while True:
        try:
            d, a = s.recvfrom(256)
            if d.startswith(b"HSH?") and cfg.get("visible", True): s.sendto(json.dumps({"name": cfg["name"], "port": cfg["port"], "os": platform.system()}).encode(), a)
        except OSError: time.sleep(.05)


def probe(ip, port, timeout=.6):
    try:
        c = socket.create_connection((ip, port), timeout); c.settimeout(timeout); c.sendall(b"HSH?\n")
        j = json.loads(c.makefile("rb").readline(2048)); c.close(); return (j["name"], int(j["port"]), j.get("os", ""))
    except Exception: return None


def sweep(port, workers=64):
    from concurrent.futures import ThreadPoolExecutor
    mine = set(local_ips()); hosts = [f"{i.rsplit('.', 1)[0]}.{n}" for i in mine for n in range(1, 255)]
    hosts = [h for h in dict.fromkeys(hosts) if h not in mine]; found = {}
    with ThreadPoolExecutor(workers) as ex:
        for h, r in zip(hosts, ex.map(lambda h: probe(h, port), hosts)):
            if r: found[h] = r
    return found


def scan(timeout=1.5, port=45455):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1); _udp_noreset(s)
    for t in {"255.255.255.255"} | {i.rsplit(".", 1)[0] + ".255" for i in local_ips()}:
        try: s.sendto(b"HSH?", (t, DISC))
        except OSError: pass
    found, mine, end = {}, set(local_ips()), time.monotonic() + timeout
    while (left := end - time.monotonic()) > 0:
        s.settimeout(left)
        try: d, a = s.recvfrom(512)
        except socket.timeout: break
        except OSError: continue
        if a[0] not in mine:
            try: j = json.loads(d); found[a[0]] = (j["name"], int(j["port"]), j.get("os", ""))
            except (ValueError, KeyError): pass
    s.close()
    return found or sweep(port)
