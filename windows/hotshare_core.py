"""HotShare core (no GUI). Protocol HSH3 over plain TCP - shared with the Android app.

  sender  -> "HSH3\\n" {name,pin,dest,files:[{id,t,path,size[,text]}]}\\n        ("HSH?\\n" -> {name,port,os} is the discovery hello)
  receiver-> "NO\\n" | "ERR msg\\n" | "OK\\n" {"off":{id:bytes_already_have}}\\n   (resume point per file)
  sender  -> for each file: bytes[off:size] then sha256(whole file) hex line
  receiver-> "DONE\\n" | "ERR msg\\n"       ("ERR paused" = the receiving user paused; the sender waits)
Partial files stay in <folder>/.hotshare/<id>.part so a dropped link, a pause or a power cut resumes where it stopped.
Interrupted sends are saved as jobs (jobs.json) so they can be resumed after a restart.
"""
import hashlib, hmac, json, os, platform, queue, re, shutil, socket, threading, time

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".hotshare", "config.json")
DEF = {"name": socket.gethostname(), "folder": os.path.join(HOME, "Downloads", "HotShare"), "port": 45455, "pin": "", "auto": False, "theme": "Midnight",
       "visible": True, "open_after": False, "retries": 3, "reconnect_s": 300, "trusted": [], "history": []}
DISC, BUF, MAXH = 45454, 1 << 20, 16 << 20
_lock = threading.Lock()
APPROVED, PAUSED, CANCELLED = {}, set(), {}     # approval memory per transfer (so reconnects never re-prompt) / transfers the receiving user paused


class Paused(Exception): """Paused by the user; partial data is kept."""
class PeerPaused(Exception): """The receiver paused this transfer."""
class ConnectionLost(Exception): """Could not reconnect within the reconnect window; resumable."""


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

    def set_state(s, st, msg=""): s.state = st; s.on_state(st, msg)
    def set_files(s, names, sizes): s.names, s.sizes, s.fdone = list(names), list(sizes), [0] * len(names); s.on_files()
    def start(s, base=0): s._t0, s._last, s._base = time.monotonic(), 0, base

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
    def __init__(s, ctl, dest):
        super().__init__(daemon=True); s.q, s.err, s.ctl, s.dest, s.f, s.md = queue.Queue(maxsize=8), None, ctl, dest, None, None

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
                    _, expected, part, rel, size, idx = m; s.f.close(); s.f = None
                    if expected != s.md.hexdigest(): _rm(part); raise IOError(f"checksum mismatch: {rel}")
                    final = unique(safe_path(s.dest, rel)); os.makedirs(lp(os.path.dirname(final)), exist_ok=True); move_retry(part, final)
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


def handle(sock, ctl):
    r = sock.makefile("rb", BUF)
    try: _handle(sock, ctl, r)
    finally:   # always tear the connection down NOW: a lingering half-open socket makes the other side wait and keeps partial files locked
        try: sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: r.close()
        except OSError: pass


def _handle(sock, ctl, r):
    cfg = ctl.cfg; sock.settimeout(45); tune(sock)
    first = readline(r)
    if first == b"HSH?":   # discovery over the transfer port (works where UDP broadcast is blocked)
        if cfg.get("visible", True): sock.sendall(json.dumps({"name": cfg["name"], "port": cfg["port"], "os": platform.system()}).encode() + b"\n")
        return
    if first != b"HSH3": return
    h = json.loads(readline(r, MAXH)); peer = str(h.get("name", "?")); files = h["files"]; ctl.label = ctl._peer = peer
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
    offs, parts = {}, {}
    for f in files:
        if f.get("t") != "file": continue
        p = os.path.join(pdir, re.sub(r"[^0-9A-Za-z]", "", str(f["id"]))[:40] + ".part"); parts[f["id"]] = p
        n = os.path.getsize(lp(p)) if os.path.exists(lp(p)) else 0
        if n > f["size"]: _rm(p); n = 0
        offs[f["id"]] = n
    ctl.parts = list(parts.values())
    if shutil.disk_usage(root).free < total - sum(offs.values()):
        sock.sendall(b"ERR not enough free space\n"); raise IOError("not enough free space")
    sock.sendall(b"OK\n" + json.dumps({"off": offs}).encode() + b"\n")
    done = sum(offs.values()); ctl.start(done)
    ctl.set_files([f["path"] if f["t"] == "file" else "Text message" for f in files], [f.get("size", 0) for f in files])
    for ix, f in enumerate(files):
        if f["t"] == "file": ctl.fdone[ix] = offs[f["id"]]
    w = _Writer(ctl, dest); w.start()
    try:
        for idx, f in enumerate(files):
            if f.get("t") == "text":
                txt = str(f.get("text", ""))[:1_000_000]; ctl.text(peer, txt); ctl.hist("in", "text", len(txt.encode()), peer); ctl.fdone[idx] = ctl.sizes[idx]; continue
            rel, size, off, part = f["path"], f["size"], offs[f["id"]], parts[f["id"]]
            if off: ctl.log(f"Resuming {rel} from {hsize(off)}")
            w.put(("start", part, off)); left = size - off
            while left:
                b = r.read(min(BUF, left))
                if not b: raise ConnectionError("connection lost")
                left -= len(b); done += len(b); ctl.fdone[idx] = size - left; w.put(("data", b)); ctl.tick(done, total)
            w.put(("fin", readline(r).decode(), part, rel, size, idx))
        w.finish()
        if w.err: raise w.err
        sock.sendall(b"DONE\n"); ctl.progress(total, total, 0, 0)
        try: os.rmdir(pdir)      # nothing left in the cache: remove the folder too
        except OSError: pass
    except BaseException as e:
        w.finish()
        if isinstance(e, InterruptedError): APPROVED.pop(key, None); CANCELLED[key] = time.time()
        try: sock.sendall(b"ERR paused\n" if isinstance(e, Paused) else f"ERR {e}\n".encode())
        except OSError: pass
        if ctl.discard:
            for p in parts.values(): _rm(p)
        raise


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


def _producer(items, offs, q, stop):
    def put(m):
        while not stop.is_set():
            try: q.put(m, timeout=.5); return
            except queue.Full: pass
    try:
        for ix, it in enumerate(items):
            if stop.is_set(): return
            if it["t"] == "text": put(("skip", ix)); continue
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
    cfg, man, total, seen, uniq = ctl.cfg, [], 0, set(), []
    for it in items:   # the same file picked twice would corrupt resume bookkeeping
        k = fid(it["rel"], it["size"], it["mtime"]) if it["t"] == "file" else None
        if k is None or k not in seen: uniq.append(it); seen.add(k)
    items = uniq
    for n, it in enumerate(items):
        if it["t"] == "text": man.append({"id": f"t{n}", "t": "text", "path": "text", "size": it["size"], "text": it["text"]})
        else:
            it["id"] = fid(it["rel"], it["size"], it["mtime"]); total += it["size"]
            man.append({"id": it["id"], "t": "file", "path": it["rel"].replace("\\", "/"), "size": it["size"]})
    ctl.set_state("connecting", "Connecting...")
    s = socket.create_connection((host, port), 10); tune(s); s.settimeout(180); r = s.makefile("rb", BUF)   # long: the receiver may be waiting for a tap on Accept
    stop = threading.Event(); th = None
    try:
        s.sendall(b"HSH3\n" + json.dumps({"name": cfg["name"], "pin": cfg["pin"], "dest": dest, "files": man}).encode() + b"\n")
        resp = readline(r)
        if resp.startswith(b"ERR"):
            msg = resp[4:].decode("utf-8", "replace")
            raise PeerPaused(msg) if msg == "paused" else PermissionError(msg)
        if resp != b"OK": raise PermissionError("Receiver declined (or PIN mismatch)")
        ctl.connected = True; s.settimeout(60); ctl.set_state("running", "")
        offs = json.loads(readline(r, MAXH))["off"]; done = sum(offs.values()); ctl.start(done)
        ctl.set_files(["Text message" if it["t"] == "text" else it["rel"] for it in items], [it["size"] for it in items])
        for ix, it in enumerate(items):
            if it["t"] == "file": ctl.fdone[ix] = min(max(int(offs.get(it["id"], 0)), 0), it["size"])
        q = queue.Queue(maxsize=8); th = threading.Thread(target=_producer, args=(items, offs, q, stop), daemon=True); th.start()
        while True:
            m = q.get(); k = m[0]
            if k == "end": break
            if k == "err": raise m[1]
            ix = m[1]
            if k == "skip": ctl.fdone[ix] = items[ix]["size"]
            elif k == "start": ctl.fdone[ix] = m[2]
            elif k == "data": s.sendall(m[2]); done += len(m[2]); ctl.fdone[ix] += len(m[2]); ctl.tick(done, total)
            elif k == "hash": s.sendall(m[2].encode() + b"\n"); ctl.fdone[ix] = items[ix]["size"]
        res = readline(r).decode()
        if res == "ERR paused": raise PeerPaused("paused")
        if res != "DONE": raise IOError(res or "no reply")
        ctl.progress(total, total, 0, 0); ctl.log(f"Sent {len(items)} item(s), {hsize(total)} to {ctl.label or host}")
        for i in items: ctl.hist("out", i["rel"], i["size"], ctl.label or host)
    finally:
        stop.set()
        if th: th.join(3)
        try: s.shutdown(socket.SHUT_RDWR)
        except OSError: pass
        try: r.close()
        except OSError: pass
        s.close()


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


def send(host, port, items, ctl, dest=""):
    """Sends everything; reconnects and resumes on its own for up to cfg['reconnect_s'] seconds. Raises Paused / ConnectionLost (resumable)."""
    attempt, deadline = 0, None
    while True:
        try: return send_once(host, port, items, ctl, dest)
        except (PermissionError, InterruptedError, Paused): raise
        except PeerPaused:
            ctl.set_state("waiting", "Paused by the receiver - waiting for it to resume"); _wait(ctl, 2)
        except (ConnectionRefusedError, socket.gaierror) as e:
            if attempt == 0 and not ctl.moved: raise
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
