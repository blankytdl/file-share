"""HotShare core (no GUI). Protocol HSH3 over plain TCP — shared with the Android app.

  sender  -> "HSH3\\n" {name,pin,dest,files:[{id,t,path,size[,text]}]}\\n
  receiver-> "NO\\n"  |  "OK\\n" {"off":{id:bytes_already_have}}\\n        (resume point per file)
  sender  -> for each file: bytes[off:size] then sha256(whole file) hex line
  receiver-> "DONE\\n" | "ERR msg\\n"
Partial files stay on disk (<folder>/.hotshare/<id>.part) so a dropped link resumes where it stopped.
"""
import hashlib, hmac, json, os, platform, re, shutil, socket, threading, time

HOME = os.path.expanduser("~")
CFG_PATH = os.path.join(HOME, ".hotshare", "config.json")
DEF = {"name": socket.gethostname(), "folder": os.path.join(HOME, "Downloads", "HotShare"), "port": 45455, "pin": "",
       "auto": False, "theme": "dark", "visible": True, "open_after": False, "retries": 3, "trusted": [], "history": []}
DISC, BUF, MAXH = 45454, 1 << 20, 16 << 20
_lock = threading.Lock()


def load_cfg():
    c = dict(DEF)
    try:
        c.update(json.load(open(CFG_PATH, encoding="utf-8")))
    except Exception:
        pass
    return c


def save_cfg(c):
    with _lock:
        os.makedirs(os.path.dirname(CFG_PATH), exist_ok=True)
        json.dump(c, open(CFG_PATH, "w", encoding="utf-8"), indent=1)


def safe_dir(root, rel):
    parts = [p for p in str(rel).replace("\\", "/").split("/") if p not in ("", ".", "..") and ":" not in p]
    return os.path.join(root, *parts) if parts else root


def safe_path(root, rel):
    p = safe_dir(root, rel)
    return p if p != root else os.path.join(root, "file")


def unique(p):
    b, e = os.path.splitext(p); i = 1
    while os.path.exists(p):
        p = f"{b} ({i}){e}"; i += 1
    return p


def hsize(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def fid(rel, size, mtime):
    return hashlib.sha1(f"{rel}|{size}|{mtime}".encode()).hexdigest()[:20]


def clean_partials(root, days=7):
    d = os.path.join(root, ".hotshare")
    try:
        for n in os.listdir(d):
            p = os.path.join(d, n)
            if time.time() - os.path.getmtime(p) > days * 86400: os.remove(p)
    except OSError:
        pass


class Ctl:
    """One per transfer: callbacks + cancel flag + speed meter."""
    def __init__(s, cfg, ask=None, progress=None, log=print, text=None, hist=None):
        s.cfg, s.log, s.label, s.got = cfg, log, "", 0
        s.ask = ask or (lambda h, n, t: ("once", None)); s.progress = progress or (lambda *a: None)
        s.text = text or (lambda p, t: None); s.hist = hist or (lambda *a: None)
        s.declined = False; s.cancel = threading.Event(); s._t0 = s._last = s._base = 0

    def start(s, base=0): s._t0, s._last, s._base = time.monotonic(), 0, base

    def tick(s, done, total):
        if s.cancel.is_set(): raise InterruptedError("cancelled")
        now = time.monotonic()
        if now - s._last < .12 and done != total: return
        s._last = now; sp = (done - s._base) / max(now - s._t0, 1e-6)
        s.progress(done, total, sp, (total - done) / sp if sp > 0 else 0)


def readline(r, limit=65536):
    b = r.readline(limit + 1)
    if not b: raise EOFError("connection closed")
    if len(b) > limit: raise IOError("line too long")
    return b.rstrip(b"\n")


# ---------- receive ----------
def handle(sock, ctl):
    cfg = ctl.cfg; sock.settimeout(120); sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    r = sock.makefile("rb", BUF)
    if readline(r) != b"HSH3": return
    h = json.loads(readline(r, MAXH)); peer = str(h.get("name", "?")); files = h["files"]; ctl.label = peer
    if len(files) > 200000: raise ValueError("too many files")
    for f in files:
        if f.get("t") not in ("file", "text") or (f["t"] == "file" and (not isinstance(f.get("size"), int) or f["size"] < 0)): raise ValueError("bad manifest")
    total = sum(f["size"] for f in files if f.get("t") == "file")
    ok, root = (not cfg["pin"] or hmac.compare_digest(str(h.get("pin", "")), cfg["pin"])), cfg["folder"]
    if ok and not cfg["auto"] and peer not in cfg["trusted"]:
        dec, folder = ctl.ask(h, len(files), total); ok = bool(dec)
        if ok and folder: root = folder
        if dec == "always" and peer not in cfg["trusted"]: cfg["trusted"].append(peer)
    if not ok:
        ctl.declined = True; sock.sendall(b"NO\n"); ctl.log(f"Declined transfer from {peer}"); return
    dest = safe_dir(root, h.get("dest", "")); pdir = os.path.join(root, ".hotshare"); os.makedirs(pdir, exist_ok=True)
    offs, parts = {}, {}
    for f in files:
        if f.get("t") != "file": continue
        p = os.path.join(pdir, re.sub(r"[^0-9A-Za-z]", "", str(f["id"]))[:40] + ".part"); parts[f["id"]] = p
        n = os.path.getsize(p) if os.path.exists(p) else 0
        if n > f["size"]: os.remove(p); n = 0
        offs[f["id"]] = n
    if shutil.disk_usage(root).free < total - sum(offs.values()):
        sock.sendall(b"ERR not enough free space\n"); raise IOError("not enough free space")
    sock.sendall(b"OK\n" + json.dumps({"off": offs}).encode() + b"\n")
    done = sum(offs.values()); ctl.start(done); mv = memoryview(bytearray(BUF))
    try:
        for f in files:
            if f.get("t") == "text":
                txt = str(f.get("text", ""))[:1_000_000]; ctl.text(peer, txt); ctl.hist("in", "text", len(txt.encode()), peer); continue
            rel, size, off, part, md = f["path"], f["size"], offs[f["id"]], parts[f["id"]], hashlib.sha256()
            if off:
                ctl.log(f"Resuming {rel} from {hsize(off)}")
                with open(part, "rb") as pf:
                    while b := pf.read(BUF): md.update(b)
            left = size - off
            with open(part, "ab", buffering=0) as o:
                while left:
                    n = r.readinto(mv[:min(BUF, left)])
                    if not n: raise ConnectionError("connection lost")
                    o.write(mv[:n]); md.update(mv[:n]); left -= n; done += n; ctl.tick(done, total)
            if readline(r).decode() != md.hexdigest():
                os.remove(part); raise IOError(f"checksum mismatch: {rel}")
            final = unique(safe_path(dest, rel)); os.makedirs(os.path.dirname(final), exist_ok=True); shutil.move(part, final)
            ctl.got += 1; ctl.log(f"Received {rel} ({hsize(size)}) \u2713"); ctl.hist("in", rel, size, peer)
        sock.sendall(b"DONE\n"); ctl.progress(total, total, 0, 0)
    except Exception as e:
        try: sock.sendall(f"ERR {e}\n".encode())
        except OSError: pass
        raise


# ---------- send ----------
def collect(paths):
    items = []
    for p in filter(None, paths):
        if os.path.isdir(p):
            base = os.path.dirname(p.rstrip("/\\"))
            for rt, _, fs in os.walk(p):
                for n in fs:
                    a = os.path.join(rt, n)
                    try: st = os.stat(a)
                    except OSError: continue
                    items.append({"t": "file", "abs": a, "rel": os.path.relpath(a, base), "size": st.st_size, "mtime": int(st.st_mtime)})
        elif os.path.isfile(p):
            st = os.stat(p); items.append({"t": "file", "abs": p, "rel": os.path.basename(p), "size": st.st_size, "mtime": int(st.st_mtime)})
    return items


def text_item(txt):
    return {"t": "text", "text": txt, "rel": "text", "size": len(txt.encode())}


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
    raw = socket.create_connection((host, port), 10); raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s = raw; s.settimeout(180); r = s.makefile("rb", BUF)   # > receiver's 120 s approval wait
    try:
        s.sendall(b"HSH3\n" + json.dumps({"name": cfg["name"], "pin": cfg["pin"], "dest": dest, "files": man}).encode() + b"\n")
        resp = readline(r)
        if resp.startswith(b"ERR"): raise PermissionError(resp[4:].decode("utf-8", "replace"))
        if resp != b"OK": raise PermissionError("Receiver declined (or PIN mismatch)")
        offs = json.loads(readline(r, MAXH))["off"]; done = sum(offs.values()); ctl.start(done); mv = memoryview(bytearray(BUF))
        for it in items:
            if it["t"] == "text": continue
            off, md = min(max(int(offs.get(it["id"], 0)), 0), it["size"]), hashlib.sha256()
            with open(it["abs"], "rb", buffering=0) as f:
                left = off
                while left:
                    n = f.readinto(mv[:min(BUF, left)])
                    if not n: raise IOError("file changed while sending")
                    md.update(mv[:n]); left -= n
                left = it["size"] - off
                while left:
                    n = f.readinto(mv[:min(BUF, left)])
                    if not n: raise IOError("file shrank while sending")
                    s.sendall(mv[:n]); md.update(mv[:n]); left -= n; done += n; ctl.tick(done, total)
            s.sendall(md.hexdigest().encode() + b"\n")
        res = readline(r).decode()
        if res != "DONE": raise IOError(res or "no reply")
        ctl.progress(total, total, 0, 0); ctl.log(f"Sent {len(items)} item(s), {hsize(total)} to {ctl.label or host}")
        for i in items: ctl.hist("out", i["rel"], i["size"], ctl.label or host)
    finally:
        s.close()


def send(host, port, items, ctl, dest=""):
    """Auto-resumes after dropped connections (cfg['retries'] attempts)."""
    for attempt in range(ctl.cfg["retries"] + 1):
        try:
            return send_once(host, port, items, ctl, dest)
        except (PermissionError, InterruptedError): raise
        except (ConnectionRefusedError, socket.gaierror):
            if attempt == 0: raise
        except (OSError, EOFError) as e:
            if attempt >= ctl.cfg["retries"] or ctl.cancel.is_set(): raise
            ctl.log(f"Connection problem ({e}); resuming in {attempt + 1}s \u2026")
            if ctl.cancel.wait(attempt + 1): raise InterruptedError("cancelled")


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
            if d.startswith(b"HSH?") and cfg.get("visible", True):
                s.sendto(json.dumps({"name": cfg["name"], "port": cfg["port"], "os": platform.system()}).encode(), a)
        except OSError: time.sleep(.05)


def scan(timeout=1.5):
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
    s.close(); return found
