#!/usr/bin/env python3
"""HotShare Desktop GUI (tkinter; custom-drawn icons, rounded cards, animations, themes). Logic lives in hotshare_core.py."""
import os, queue, socket, sys, threading, time, tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont
import hotshare_core as H
import hotshare_win as WIN
from hotshare_icons import draw_tk


def lerp(a, b, t):
    x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]; y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x[i] + (y[i] - x[i]) * t) for i in range(3))


def _mk(bg, a1, a2, dark=True):
    if dark:
        t = dict(dark=True, bg=bg, side=lerp(bg, "#ffffff", .03), card=lerp(bg, "#ffffff", .075), card2=lerp(bg, "#ffffff", .15), track=lerp(bg, "#ffffff", .17), text="#e8edf5", muted="#8d99ab", ok="#38d39f", err="#ff6b6b", warn="#f5b84b")
    else:
        t = dict(dark=False, bg=bg, side=lerp(bg, "#000000", .035), card="#ffffff", card2=lerp(bg, "#000000", .07), track=lerp(bg, "#000000", .11), text="#1b2430", muted="#66728a", ok="#12a37a", err="#d94646", warn="#b97800")
    t.update(accent=a1, accent2=a2, hover=lerp(t["card"], a1, .12), line=lerp(bg, t["text"], .12)); return t


THEME_ORDER = ["Midnight", "Crimson", "Ocean", "Emerald", "Sunset", "Orchid", "Daylight"]
THEMES = {"Midnight": _mk("#0f1419", "#4c8dff", "#7c5cff"), "Crimson": _mk("#150b0d", "#ff4d5e", "#ff7a45"), "Ocean": _mk("#0a1620", "#22d3ee", "#3b82f6"), "Emerald": _mk("#0c1713", "#34d399", "#22c55e"),
          "Sunset": _mk("#181110", "#fb923c", "#f43f5e"), "Orchid": _mk("#130f1d", "#c084fc", "#ec4899"), "Daylight": _mk("#eef2f8", "#2f6df0", "#6a4ce0", False)}
P, SC, FONT, BUSY = {}, [1.0], ["Segoe UI"], [False]   # BUSY: a transfer is running -> keep animations light so they never compete with it


def apply_theme(name): P.clear(); P.update(THEMES.get(name, THEMES["Midnight"]))
def px(n): return max(1, int(round(n * SC[0])))
def F(size, bold=False): return (FONT[0], size, "bold" if bold else "normal")
def short(p, n=44): return p if len(p) <= n else p[:n // 3] + "..." + p[-(n - n // 3 - 3):]
def res(name): return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def pick_font(root):
    have = set(tkfont.families(root))
    return next((f for f in ("Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Ubuntu", "Noto Sans", "DejaVu Sans") if f in have), "TkDefaultFont")


def rrect(c, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def _all(w):
    for c in w.winfo_children():
        yield c; yield from _all(c)


# ============================ widgets ============================
class Icon(tk.Canvas):
    def __init__(s, parent, name, size=20, color=None, bg=None):
        s.size = px(size); super().__init__(parent, width=s.size, height=s.size, bg=bg or parent["bg"], highlightthickness=0); s.show(name, color or P["text"])

    def show(s, name, color): s.delete("all"); s.name = name; draw_tk(s, name, 0, 0, s.size, color, width=2)


class RBtn(tk.Canvas):
    def __init__(s, parent, text, cmd, w=None, h=36, primary=False, icon=None):
        fnt = tkfont.Font(family=FONT[0], size=10, weight="bold"); tw = fnt.measure(text) if text else 0
        h, ic, gap = px(h), px(18), (px(8) if icon and text else 0); content = (ic if icon else 0) + gap + tw
        w = px(w) if w else (content + px(30) if text else h)
        super().__init__(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        s.base = P["accent"] if primary else P["card2"]; s.col = s.base; s.fg = "#ffffff" if primary else P["text"]
        s.shape = rrect(s, 1, 1, w - 1, h - 1, h / 2.4, fill=s.col, outline=""); x = (w - content) / 2   # icon+label centred as one group
        if icon: draw_tk(s, icon, x, (h - ic) / 2, ic, s.fg)
        if text: s.create_text(x + (ic + gap if icon else 0), h / 2, text=text, fill=s.fg, anchor="w", font=F(10, True))
        s.bind("<Enter>", lambda e: s.fade(lerp(s.base, "#ffffff", .15))); s.bind("<Leave>", lambda e: s.fade(s.base))
        s.bind("<ButtonPress-1>", lambda e: s.fade(lerp(s.base, "#000000", .25), 3))
        s.bind("<ButtonRelease-1>", lambda e: (s.fade(s.base, 4), cmd() if 0 <= e.x <= w and 0 <= e.y <= h else None))

    def fade(s, target, steps=6):
        start = s.col
        def go(i=1):
            if not s.winfo_exists(): return
            s.col = lerp(start, target, i / steps); s.itemconfigure(s.shape, fill=s.col)
            if i < steps: s.after(16, go, i + 1)
        go()


class Bar(tk.Canvas):
    def __init__(s, parent, h=10, shine=False):
        super().__init__(parent, height=px(h), bg=parent["bg"], highlightthickness=0)
        s.h, s.cur, s.tgt, s.run, s.done, s.shine, s.ph = px(h), 0.0, 0.0, False, False, shine, 0.0; s.bind("<Configure>", lambda e: s.draw())

    def set(s, v, done=False):
        s.tgt, s.done = max(0.0, min(1.0, v)), done
        if not s.run: s.run = True; s.step()

    def step(s):
        if not s.winfo_exists(): return
        s.cur += (s.tgt - s.cur) * .25; s.ph = (s.ph + .035) % 1
        if abs(s.tgt - s.cur) < .002: s.cur = s.tgt
        s.draw()
        if abs(s.tgt - s.cur) > 0 or (s.shine and not s.done and 0 < s.cur < 1): s.after(60 if BUSY[0] else 33, s.step)
        else: s.run = False

    def draw(s):
        s.delete("all"); w = max(s.winfo_width(), 2); r = s.h / 2
        rrect(s, 0, 0, w, s.h, r, fill=P["track"], outline="")
        if s.cur <= 0: return
        fw = max(s.h, w * s.cur); rrect(s, 0, 0, fw, s.h, r, fill=P["ok"] if s.done else P["accent"], outline="")
        if s.shine and not s.done and fw > s.h * 3:
            x0 = s.ph * (fw + s.h * 2) - s.h * 2; a, b = max(x0, r), min(x0 + s.h * 2, fw - r)
            if b - a > 2: s.create_rectangle(a, 1, b, s.h - 1, fill=lerp(P["accent"], "#ffffff", .4), outline="")


class Radar(tk.Canvas):
    def __init__(s, parent):
        s.sz = px(44); super().__init__(parent, width=s.sz, height=s.sz, bg=parent["bg"], highlightthickness=0); s.t, s.on = 0.0, False; s.draw()

    def start(s):
        if not s.on: s.on = True; s.loop()

    def stop(s): s.on = False

    def loop(s):
        if not s.winfo_exists(): return
        s.t = (s.t + .025) % 1; s.draw()
        if s.on: s.after(30, s.loop)
        else: s.t = 0; s.draw()

    def draw(s):
        c, m = s.sz / 2, s.sz / 2; s.delete("all"); s.create_oval(c - m * .2, c - m * .2, c + m * .2, c + m * .2, fill=P["accent"], outline="")
        for k in range(3):
            f = (s.t + k / 3) % 1 if s.on else .3 + k * .25; r = m * (.3 + .7 * f)
            s.create_oval(c - r, c - r, c + r, c + r, outline=lerp(P["accent"], P["side"], f if s.on else .7), width=2)


class RCard(tk.Canvas):
    """Rounded card (or dashed drop-zone outline) hosting widgets in .inner."""
    def __init__(s, parent, fill=None, pad=14, r=16, dash=False):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, height=px(20))
        s.fill, s.pad, s.r, s.edge, s.dash, s.ph = fill or P["card"], px(pad), px(r), None, dash, 0
        s.inner = tk.Frame(s, bg=s.fill); s.win = s.create_window(s.pad, s.pad, window=s.inner, anchor="nw")
        s.inner.bind("<Configure>", lambda e: s.relayout()); s.bind("<Configure>", lambda e: s.relayout())
        if dash: s.march()

    def relayout(s):
        if not s.winfo_exists(): return
        w = max(s.winfo_width(), 2); h = s.inner.winfo_reqheight() + 2 * s.pad
        if int(float(s["height"])) != h: s.configure(height=h)
        s.itemconfigure(s.win, width=max(1, w - 2 * s.pad)); s.delete("bg")
        if s.dash: rrect(s, 2, 2, w - 2, h - 2, s.r, fill=s.fill, outline=P["accent"], width=2, dash=(px(7), px(5)), tags="bg")
        else: rrect(s, 1, 1, w - 1, h - 1, s.r, fill=s.fill, outline=s.edge or s.fill, width=2 if s.edge else 1, tags="bg")
        s.tag_lower("bg")

    def march(s):   # marching-ants border
        if not s.winfo_exists(): return
        if not BUSY[0]: s.ph = (s.ph + 1) % 12; s.itemconfigure("bg", dashoffset=s.ph)
        s.after(120, s.march)

    def paint(s, fill, edge=None):
        old = s.fill; s.fill, s.edge = fill, edge; s.inner.configure(bg=fill)
        for k in _all(s.inner):
            try:
                if str(k.cget("bg")).lower() == old.lower(): k.configure(bg=fill)
            except tk.TclError: pass
        s.relayout()


def fade_in(card, steps=10):
    tgt = card.fill
    def go(i=1):
        if not card.winfo_exists(): return
        card.paint(lerp(P["bg"], tgt, i / steps))
        if i < steps: card.after(18, go, i + 1)
    go()


class ScrollFrame(tk.Frame):
    def __init__(s, parent):
        super().__init__(parent, bg=parent["bg"])
        s.sb = tk.Canvas(s, width=px(8), bg=parent["bg"], highlightthickness=0); s.sb.pack(side="right", fill="y")
        s.cv = tk.Canvas(s, bg=parent["bg"], highlightthickness=0, yscrollcommand=s._sync); s.cv.pack(side="left", fill="both", expand=True)
        s.inner = tk.Frame(s.cv, bg=parent["bg"]); s.win = s.cv.create_window((0, 0), window=s.inner, anchor="nw")
        s.inner.bind("<Configure>", lambda e: s.cv.configure(scrollregion=s.cv.bbox("all"))); s.cv.bind("<Configure>", lambda e: s.cv.itemconfigure(s.win, width=e.width))
        s.sb.bind("<B1-Motion>", lambda e: s.cv.yview_moveto(e.y / max(1, s.sb.winfo_height()) - .05)); s.sb.bind("<Button-1>", lambda e: s.cv.yview_moveto(e.y / max(1, s.sb.winfo_height()) - .05))

    def _sync(s, a, b):
        a, b = float(a), float(b); s.sb.delete("all"); h = s.sb.winfo_height()
        if b - a < .999: rrect(s.sb, 1, a * h, px(7), max(b * h, a * h + px(28)), px(3), fill=P["track"], outline="")


class DevCard(RCard):
    def __init__(s, parent, name, sub, kind, on_toggle):
        super().__init__(parent, pad=11, r=14); i = s.inner; s.sel, s.fading = False, True
        s.ic = Icon(i, "phone" if kind == "Android" else "laptop", 28, P["accent"], P["card"]); s.ic.pack(side="left", padx=(px(2), px(12)))
        txt = tk.Frame(i, bg=P["card"]); txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=short(name, 22), bg=P["card"], fg=P["text"], font=F(10, True), anchor="w").pack(anchor="w")
        tk.Label(txt, text=sub, bg=P["card"], fg=P["muted"], font=F(8), anchor="w").pack(anchor="w")
        s.ck = Icon(i, "check", 22, P["ok"], P["card"]); s.ck.pack(side="right"); s.ck.delete("all")
        for w in [s, i, *_all(i)]:
            w.configure(cursor="hand2"); w.bind("<Button-1>", lambda e: s.toggle(on_toggle)); w.bind("<Enter>", lambda e: s.hover(True)); w.bind("<Leave>", lambda e: s.hover(False))
        fade_in(s); s.after(220, lambda: setattr(s, "fading", False))

    def hover(s, on):
        if not s.fading and not s.sel: s.paint(P["hover"] if on else P["card"])

    def toggle(s, cb):
        s.sel = not s.sel; s.paint(lerp(P["card"], P["accent"], .16) if s.sel else P["card"], P["accent"] if s.sel else None)
        s.ck.show("check", P["ok"]) if s.sel else s.ck.delete("all"); cb()


class QueueRow(RCard):
    def __init__(s, parent, entry, on_remove):
        super().__init__(parent, pad=10, r=14); s.entry = entry; i = s.inner; kind = entry["kind"]
        Icon(i, {"folder": "folder", "text": "text"}.get(kind, "file"), 24, P["accent"], P["card"]).pack(side="left", padx=(px(2), px(12)))
        x = Icon(i, "close", 18, P["muted"], P["card"]); x.pack(side="right", padx=(px(8), px(2))); x.configure(cursor="hand2")
        x.bind("<Button-1>", lambda e: on_remove(s)); x.bind("<Enter>", lambda e: x.show("close", P["err"])); x.bind("<Leave>", lambda e: x.show("close", P["muted"]))
        mid = tk.Frame(i, bg=P["card"]); mid.pack(side="left", fill="x", expand=True)
        tk.Label(mid, text=short(entry["name"], 54), bg=P["card"], fg=P["text"], font=F(10, True), anchor="w").pack(anchor="w")
        meta = f"{entry['count']} files   -   {H.hsize(entry['size'])}" if kind == "folder" else ("Text   -   " if kind == "text" else "") + H.hsize(entry["size"])
        tk.Label(mid, text=meta, bg=P["card"], fg=P["muted"], font=F(9), anchor="w").pack(anchor="w"); fade_in(s)


class XCard(RCard):
    MAXROWS = 40

    def __init__(s, parent, title, up, on_cancel, on_pause):
        super().__init__(parent, pad=14); i = s.inner; s.rows, s.sizes, s.open, s.cancel_cb, s.pause_cb, s.mode = [], [], False, on_cancel, on_pause, "run"
        top = tk.Frame(i, bg=P["card"]); top.pack(fill="x")
        Icon(top, "upload" if up else "download", 22, P["accent"], P["card"]).pack(side="left")
        s.title = tk.Label(top, text=title, bg=P["card"], fg=P["text"], font=F(11, True)); s.title.pack(side="left", padx=(px(10), 0))
        s.count = tk.Label(top, text="", bg=P["card"], fg=P["muted"], font=F(9)); s.count.pack(side="left", padx=(px(10), 0))
        x = Icon(top, "close", 18, P["muted"], P["card"]); x.pack(side="right"); x.configure(cursor="hand2"); x.bind("<Button-1>", lambda e: s.cancel_cb())
        s.pbtn = Icon(top, "pause", 18, P["text"], P["card"]); s.pbtn.pack(side="right", padx=(0, px(12))); s.pbtn.configure(cursor="hand2"); s.pbtn.bind("<Button-1>", lambda e: s.pause_cb())
        s.bar = Bar(i, h=10, shine=True); s.bar.pack(fill="x", pady=(px(12), px(6)))
        low = tk.Frame(i, bg=P["card"]); low.pack(fill="x")
        s.stat = tk.Label(low, text="Starting...", bg=P["card"], fg=P["muted"], font=F(9), anchor="w"); s.stat.pack(side="left")
        s.tog = tk.Label(low, text="", bg=P["card"], fg=P["accent"], font=F(9, True), cursor="hand2"); s.tog.pack(side="right"); s.tog.bind("<Button-1>", lambda e: s.toggle())
        s.fbox = tk.Frame(i, bg=P["card"]); fade_in(s)

    def set_mode(s, mode):
        if mode == s.mode: return
        s.mode = mode
        if mode == "done": s.pbtn.pack_forget(); return
        s.pbtn.pack(side="right", padx=(0, px(12)))
        s.pbtn.show("play", P["ok"]) if mode == "paused" else s.pbtn.show("pause", P["text"])

    def set_files(s, names, sizes):
        for w in s.fbox.winfo_children(): w.destroy()
        s.rows, s.sizes = [], list(sizes); n = len(names); s.count.configure(text=f"{n} file{'s' if n != 1 else ''}")
        for k in range(min(n, s.MAXROWS)):
            f = tk.Frame(s.fbox, bg=P["card"]); f.pack(fill="x", pady=px(3))
            ic = Icon(f, "text" if names[k] == "Text message" else "file", 18, P["muted"], P["card"]); ic.pack(side="left", anchor="n")
            mid = tk.Frame(f, bg=P["card"]); mid.pack(side="left", fill="x", expand=True, padx=px(10))
            tk.Label(mid, text=short(names[k], 56), bg=P["card"], fg=P["text"], font=F(9), anchor="w").pack(anchor="w")
            bar = Bar(mid, h=5); bar.pack(fill="x", pady=(px(3), 0))
            st = tk.Label(f, text="", bg=P["card"], fg=P["muted"], font=F(9), width=7, anchor="e"); st.pack(side="right")
            s.rows.append(dict(ic=ic, bar=bar, st=st, last=-1, done=False))
        if n > s.MAXROWS: tk.Label(s.fbox, text=f"... and {n - s.MAXROWS} more", bg=P["card"], fg=P["muted"], font=F(9)).pack(anchor="w", pady=px(4))
        s.open = n <= 4; s._show()

    def _show(s):
        if s.open: s.fbox.pack(fill="x", pady=(px(10), 0))
        else: s.fbox.pack_forget()
        s.tog.configure(text=("Hide files" if s.open else f"Show files ({len(s.sizes)})") if s.sizes else "")

    def toggle(s): s.open = not s.open; s._show()

    def update_files(s, fdone, all_done=False):
        for k, r in enumerate(s.rows):
            size, d = s.sizes[k], (fdone[k] if k < len(fdone) else 0); done = all_done or (size > 0 and d >= size)
            key = -2 if done else (int(100 * d / size) if size else 0)
            if key == r["last"]: continue
            r["last"] = key
            if done:
                r["bar"].set(1, True); r["st"].configure(text="Done", fg=P["ok"])
                if not r["done"]: r["done"] = True; r["ic"].show("check", P["ok"])
            else:
                r["bar"].set(d / size if size else 0); r["st"].configure(text=f"{key}%" if size else "", fg=P["muted"])


# ============================ application ============================
def main():
    WIN.dpi_aware()
    cfg = H.load_cfg(); os.makedirs(cfg["folder"], exist_ok=True); H.clean_partials(cfg["folder"])
    cfg["theme"] = {"dark": "Midnight", "light": "Daylight"}.get(cfg["theme"], cfg["theme"])
    root = tk.Tk(); root.title("HotShare"); SC[0] = max(1.0, float(root.winfo_fpixels("1i")) / 96.0); FONT[0] = pick_font(root); apply_theme(cfg["theme"])
    root.geometry(f"{px(1080)}x{px(740)}"); root.minsize(px(900), px(600))
    try: root.iconbitmap(res("icon.ico"))
    except Exception:
        try: root.iconphoto(True, tk.PhotoImage(file=res("logo2.png")))
        except Exception: pass
    q = queue.Queue(); devices, cards, done_, active, selected, queue_, W = {}, {}, {}, [], set(), [], {}

    def ui(fn, *a): q.put((fn, a))
    def pump():
        try:
            while True:
                fn, a = q.get_nowait()
                try: fn(*a)
                except Exception as e: print("ui error:", e)
        except queue.Empty: pass
        root.after(25, pump)

    def toast(msg):
        t = tk.Label(root, text=msg, bg=P["accent"], fg="#ffffff", font=F(10, True), padx=px(22), pady=px(10))
        def mv(i, up):
            if not t.winfo_exists(): return
            e = 1 - (1 - i / 10) ** 3; t.place(relx=.5, rely=1.0, anchor="s", y=int(px(60) - px(100) * e if up else -px(40) + px(100) * e))
            if i < 10: root.after(16, mv, i + 1, up)
            elif up: root.after(2400, mv, 0, False)
            else: t.destroy()
        mv(0, True)

    def log(m): ui(toast, m)

    def dialog(title):
        d = tk.Toplevel(root); d.title(title); d.configure(bg=P["bg"]); d.transient(root); WIN.style_titlebar(d, P["bg"], P["text"], P["accent"], P["dark"]); return d

    def label(parent, text, size=9, bold=False, color=None, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color or P["muted"], font=F(size, bold), **kw)

    # ------------------------------ queue ------------------------------
    def build_entry(p):
        p = os.path.normpath(p.strip().strip('"'))
        if not (os.path.isdir(p) or os.path.isfile(p)): return None
        items = H.collect([p])
        return dict(kind="folder" if os.path.isdir(p) else "file", name=os.path.basename(p) or p, path=p, items=items, size=sum(i["size"] for i in items), count=len(items)) if items else None

    def add_paths(paths):
        def w():
            es = [e for e in (build_entry(p) for p in paths) if e and not any(x["path"] == e["path"] for x in queue_)]
            ui(add_entries, es)
        threading.Thread(target=w, daemon=True).start()

    def add_entries(es):
        if not es: return toast("Nothing new to add")
        for e in es: queue_.append(e); QueueRow(W["qbox"], e, remove_row).pack(fill="x", pady=px(4))
        sync_queue()

    def add_text(t):
        b = t.encode(); queue_.append(dict(kind="text", name=short(t.strip().splitlines()[0], 60), path=None, text=t, items=[H.text_item(t)], size=len(b), count=1))
        QueueRow(W["qbox"], queue_[-1], remove_row).pack(fill="x", pady=px(4)); sync_queue()

    def remove_row(row): queue_.remove(row.entry); row.destroy(); sync_queue()
    def clear_queue():
        queue_.clear()
        for w in W["qbox"].winfo_children():
            if isinstance(w, QueueRow): w.destroy()
        sync_queue()

    def sync_queue():
        n = sum(e["count"] for e in queue_); size = sum(e["size"] for e in queue_)
        W["qcount"].configure(text=f"{len(queue_)} item(s)" if queue_ else "")
        if queue_: W["qempty"].pack_forget()
        else: W["qempty"].pack(anchor="w", pady=px(6))
        tg = [devices[ip][0] for ip in selected if ip in devices]
        to = ("   to   " + ", ".join(short(t, 16) for t in tg[:2]) + (f" +{len(tg) - 2}" if len(tg) > 2 else "")) if tg else "   -   select a device on the left"
        W["info"].configure(text=(f"{n} file(s)   -   {H.hsize(size)}" + to) if queue_ else "Nothing to send yet")

    def paste():
        files = WIN.clipboard_files()
        if files: add_paths(files); return toast(f"Pasting {len(files)} item(s) from clipboard")
        try: t = root.clipboard_get()
        except tk.TclError: t = ""
        if not t.strip(): return toast("Clipboard is empty")
        add_paths([t]) if os.path.exists(t.strip().strip('"')) else add_text(t)

    def pick_files():
        f = filedialog.askopenfilenames(title="Choose files to send", parent=root)
        if f: add_paths(list(f))

    def pick_folder():
        f = filedialog.askdirectory(title="Choose a folder to send", parent=root)
        if f: add_paths([f])

    def add_text_dialog():
        d = dialog("Add text"); t = tk.Text(d, width=52, height=8, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", padx=px(12), pady=px(12), font=F(10)); t.pack(padx=px(22), pady=(px(20), px(10)))
        try: t.insert("1.0", root.clipboard_get())
        except tk.TclError: pass
        def go():
            v = t.get("1.0", "end").strip()
            if v: add_text(v)
            d.destroy()
        RBtn(d, "Add to list", go, primary=True, icon="plus").pack(pady=(0, px(18)))

    # ------------------------------ transfers ------------------------------
    def title_of(c): return ("From " if c.incoming and c.label != "Incoming" else "") + c.label

    def make_card(c):
        card = XCard(W["xbox"], title_of(c), not c.incoming, lambda: discard(c), lambda: toggle_pause(c)); card.pack(fill="x", pady=px(6)); cards[c] = card; return card

    def update_speed():
        if W.get("speed") is not None and W["speed"].winfo_exists():
            W["speed"].configure(text=f"{H.hsize(sum(c.last[2] for c in active if c.state not in ('waiting', 'reconnecting')))}/s   -   {len(active)} active" if active else "Idle")

    def on_files(c):
        card = cards.get(c) or make_card(c); card.title.configure(text=title_of(c)); card.set_files(c.names, c.sizes)

    def on_prog(c, d, t, sp, eta):
        card = cards.get(c) or make_card(c); card.title.configure(text=title_of(c)); card.bar.set(d / t if t else 0)
        card.stat.configure(text=f"{H.hsize(d)} of {H.hsize(t)}   -   {H.hsize(sp)}/s   -   {int(eta)}s left" if sp else "Finishing...", fg=P["muted"]); card.update_files(c.fdone); update_speed()

    def on_state(c, st, msg):
        card = cards.get(c) or make_card(c)
        if st in ("reconnecting", "waiting"): card.stat.configure(text=msg, fg=P["warn"])
        elif st == "connecting": card.stat.configure(text=msg, fg=P["muted"])

    def remove_card(c):
        card = cards.pop(c, None); done_.pop(c, None)
        if card is not None and card.winfo_exists(): card.destroy()

    def paint_done(card, c, state, msg):
        ok = state == "ok"; card.title.configure(text=title_of(c)); card.bar.set(1 if ok else card.bar.cur, done=ok); card.update_files(c.fdone, all_done=ok)
        word = {"ok": "Done - ", "paused": "Paused - ", "cancelled": "Cancelled - "}.get(state, "Stopped - ")
        card.stat.configure(text=word + msg, fg=P["ok"] if ok else (P["warn"] if state == "paused" else P["err"])); card.set_mode("paused" if state == "paused" else "done")

    def finish(c, state, msg):
        card = cards.get(c) or make_card(c); done_[c] = (state, msg); paint_done(card, c, state, msg); update_speed()
        if state != "paused": root.after(15000, remove_card, c)
        if state == "ok" and c.got and cfg["open_after"] and c.incoming: open_inbox()

    def hist(d, name, size, peer):
        cfg["history"] = (cfg["history"] + [[time.strftime("%Y-%m-%d %H:%M"), d, name, size, peer]])[-200:]

    def new_ctl(label, incoming):
        c = H.Ctl(cfg, ask, lambda *a: ui(on_prog, c, *a), log, on_text, hist); c.label, c.incoming = label, incoming
        c.on_files = lambda: ui(on_files, c); c.on_state = lambda st, m: ui(on_state, c, st, m); active.append(c); BUSY[0] = True; return c

    def toggle_pause(c):
        state = done_.get(c, (None,))[0]
        if state == "paused": return resume(c)
        if state is not None: return
        if c.incoming and c.key: H.PAUSED.add(c.key)      # the sender will wait until we resume
        c.pause.set(); card = cards.get(c)
        if card: card.stat.configure(text="Pausing...", fg=P["warn"])

    def resume(c):
        if c.incoming: H.PAUSED.discard(c.key); remove_card(c); toast("Resumed - the sender reconnects in a moment")
        elif c.job: job = c.job; remove_card(c); start_job(job)

    def discard(c):   # the X button: stop and throw the transfer (and its cached partial data) away
        state = done_.get(c, (None,))[0]
        if state is None: c.discard = True; c.cancel.set(); return
        if c.job: H.drop_job(c.job["id"])
        if c.incoming:
            H.PAUSED.discard(c.key)
            for p in c.parts: H._rm(p)
        remove_card(c)

    def start_job(job):
        ctl = new_ctl("To " + job["name"], False); ctl.job = job; H.save_job(job)    # saved first: survives a crash / power cut
        def w():
            try: H.send(job["host"], job["port"], job["items"], ctl, job["dest"]); H.drop_job(job["id"]); ui(finish, ctl, "ok", "sent")
            except InterruptedError: H.drop_job(job["id"]); ui(finish, ctl, "cancelled", "cancelled; cached data discarded")
            except H.Paused: ui(finish, ctl, "paused", "press Resume to continue")
            except H.ConnectionLost as e: ui(finish, ctl, "paused", f"{e}. Press Resume when the other device is back")
            except PermissionError as e: H.drop_job(job["id"]); ui(finish, ctl, "fail", str(e))
            except Exception as e: ui(finish, ctl, "paused", f"{e} - press Resume to retry")
            finally: active.remove(ctl); BUSY[0] = bool(active); H.save_cfg(cfg); ui(update_speed)
        threading.Thread(target=w, daemon=True).start()

    def restore_jobs():   # transfers interrupted by a crash / power cut / closing the app
        for job in H.load_jobs():
            job["items"] = [it for it in job["items"] if it["t"] == "text" or os.path.exists(H.lp(it.get("abs", "")))]
            if not job["items"]: H.drop_job(job["id"]); continue
            c = H.Ctl(cfg); c.label, c.incoming, c.job = "To " + job["name"], False, job
            c.names = ["Text message" if it["t"] == "text" else it["rel"] for it in job["items"]]; c.sizes = [it["size"] for it in job["items"]]; c.fdone = [0] * len(c.names)
            card = make_card(c); card.set_files(c.names, c.sizes); done_[c] = ("paused", "interrupted earlier - press Resume to continue"); paint_done(card, c, *done_[c])

    def clear_cache_ui():
        if active: return toast("Finish or cancel the running transfers first")
        freed = H.clear_cache(cfg["folder"])
        for c in [c for c, (st, m) in done_.items() if st == "paused"]: remove_card(c)
        toast(f"Cleared {H.hsize(freed)} of cached partial data and the interrupted-transfer list")

    def on_text(peer, txt):
        def f():
            root.clipboard_clear(); root.clipboard_append(txt); w = dialog(f"Text from {peer}")
            label(w, f"Text from {peer}").pack(padx=px(22), pady=(px(16), px(4)), anchor="w")
            tk.Label(w, text=txt[:2000], bg=P["card"], fg=P["text"], wraplength=px(460), justify="left", padx=px(16), pady=px(14), font=F(10)).pack(padx=px(22))
            label(w, "Copied to clipboard", 9, True, P["ok"]).pack(pady=(px(10), px(16)))
        ui(f)

    def ask(h, n, total):
        ev, out, folder = threading.Event(), [], [None]
        def dlg():
            d = dialog("Incoming transfer"); Icon(d, "download", 34, P["accent"]).pack(pady=(px(22), px(6)))
            label(d, f"{h.get('name')} wants to send", 10).pack(padx=px(30))
            label(d, f"{n} item(s)   -   {H.hsize(total)}", 16, True, P["text"]).pack(padx=px(30))
            fl = label(d, "Save to: " + short(cfg["folder"], 50)); fl.pack(pady=(px(10), px(4)))
            def change():
                f = filedialog.askdirectory(parent=d, initialdir=cfg["folder"], title="Save this transfer to...")
                if f: folder[0] = f; fl.configure(text="Save to: " + short(f, 50))
            RBtn(d, "Choose folder", change, h=30, icon="folder").pack(pady=px(4))
            def pick(v): out.append(v); ev.set(); d.destroy()
            row = tk.Frame(d, bg=P["bg"]); row.pack(pady=px(20), padx=px(22))
            for t, v, pr in (("Accept", "once", True), ("Always", "always", False), ("Decline", None, False)): RBtn(row, t, lambda v=v: pick(v), w=100, primary=pr).pack(side="left", padx=px(4))
            d.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        ui(dlg); ev.wait(120)
        return (out[0] if out else None), folder[0]

    def server():
        try:
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20); s.bind(("", cfg["port"])); s.listen(32)
        except Exception as e: return ui(toast, f"Cannot receive: {e} (is the port already in use?)")
        while True:
            c, _ = s.accept()
            def run(c=c):
                ctl = new_ctl("Incoming", True)
                try:
                    H.handle(c, ctl)
                    if not ctl.silent and ctl.label != "Incoming":
                        ui(finish, ctl, "fail" if ctl.declined else "ok", "Declined" if ctl.declined else (f"received {ctl.got} file(s)" if ctl.got else "text received"))
                except H.Paused: ui(finish, ctl, "paused", "partial data kept. Press Resume and the sender continues")
                except InterruptedError: ui(finish, ctl, "cancelled", "cancelled; cached data discarded")
                except Exception as e:
                    if ctl.label != "Incoming" and not ctl.silent: ui(finish, ctl, "fail", f"{e} - partial data kept, the sender reconnects on its own")
                finally:
                    try: c.close()
                    except OSError: pass
                    active.remove(ctl); BUSY[0] = bool(active); H.save_cfg(cfg); ui(update_speed)
            threading.Thread(target=run, daemon=True).start()

    def send_queue():
        t = [(ip, devices[ip][1], devices[ip][0]) for ip in selected if ip in devices]
        if not queue_: return toast("Add files, a folder or text first")
        if not t: return toast("Select a device on the left first")
        items = [it for e in queue_ for it in e["items"]]; dest = W["dest"].get().strip()
        for ip, port, name in t: start_job(H.new_job(ip, port, name, [dict(it) for it in items], dest))
        clear_queue()

    # ------------------------------ devices ------------------------------
    def refresh():
        W["radar"].start()
        def after_scan(r):   # merge: devices added by hand (Add IP) and the current selection survive a scan
            m = {k: v for k, v in devices.items() if v[2] == "IP"}; m.update(r); show_devices(m, keep=True)
        def w():
            r = H.scan(port=cfg["port"]); ui(after_scan, r)
        threading.Thread(target=w, daemon=True).start()

    def show_devices(r, keep=False):
        W["radar"].stop(); devices.clear(); devices.update(r)
        if not keep: selected.clear()
        else: selected.intersection_update(devices)
        box = W["devbox"]
        for w in box.winfo_children(): w.destroy()
        if not r:
            e = tk.Frame(box, bg=P["side"]); e.pack(pady=px(18)); Icon(e, "radar", 36, P["muted"], P["side"]).pack()
            label(e, "No devices found", 11, True, P["text"]).pack(pady=(px(8), px(2)))
            label(e, "Join the same hotspot / Wi-Fi and open\nHotShare on the other device, then Scan.", 9, justify="center").pack()
        for ip, (n, p, o) in r.items():
            c = DevCard(box, n, f"{ip}   -   {o or 'device'}", o, lambda ip=ip: (selected.symmetric_difference_update({ip}), sync_queue())); c.pack(fill="x", pady=px(4))
            if ip in selected: c.toggle(lambda: None)
        sync_queue()

    def add_ip():
        d = dialog("Add device by IP"); label(d, "IP address of the other device").pack(padx=px(22), pady=(px(18), px(4)), anchor="w")
        e = tk.Entry(d, bg=P["card2"], fg=P["text"], insertbackground=P["text"], relief="flat", font=F(12), width=26); e.pack(padx=px(22), ipady=px(7)); e.focus()
        def ok():
            ip = e.get().strip()
            if ip: devices[ip] = ("Manual", cfg["port"], "IP"); show_devices(dict(devices), keep=True)
            d.destroy()
        RBtn(d, "Add device", ok, primary=True, icon="plus").pack(pady=px(16)); e.bind("<Return>", lambda ev: ok())

    # ------------------------------ folder / dialogs ------------------------------
    def open_inbox():
        try: os.startfile(cfg["folder"]) if os.name == "nt" else os.system(f'xdg-open "{cfg["folder"]}" &')
        except OSError: pass

    def choose_folder():
        f = filedialog.askdirectory(title="Choose where received files are saved", initialdir=cfg["folder"], parent=root)
        if f:
            cfg["folder"] = os.path.normpath(f); os.makedirs(cfg["folder"], exist_ok=True); H.save_cfg(cfg)
            W["path"].configure(text=short(cfg["folder"], 30)); toast("Receiving into " + os.path.basename(cfg["folder"]))

    def show_hist():
        w = dialog("History"); w.geometry(f"{px(680)}x{px(440)}")
        l = tk.Listbox(w, bg=P["card"], fg=P["text"], relief="flat", highlightthickness=0, font=F(10), selectbackground=P["accent"]); l.pack(fill="both", expand=True, padx=px(16), pady=px(16))
        if not cfg["history"]: l.insert("end", "  Nothing transferred yet.")
        for t, d, n, s, p in reversed(cfg["history"]): l.insert("end", f"  {t}    {'Received from' if d == 'in' else 'Sent to'} {p}    {n}    ({H.hsize(s)})")

    def settings():
        w = dialog("Settings")
        v = {k: tk.StringVar(value=str(cfg[k])) for k in ("name", "folder", "port", "pin", "reconnect_s")}; b = {k: tk.BooleanVar(value=cfg[k]) for k in ("auto", "visible", "open_after")}
        for i, (k, l) in enumerate((("name", "Device name"), ("folder", "Save folder"), ("port", "Port"), ("pin", "Shared PIN"), ("reconnect_s", "Keep reconnecting for (seconds)"))):
            label(w, l).grid(row=i, column=0, sticky="w", padx=px(22), pady=px(7))
            tk.Entry(w, textvariable=v[k], width=34, bg=P["card2"], fg=P["text"], insertbackground=P["text"], relief="flat", font=F(10)).grid(row=i, column=1, padx=px(6), ipady=px(5))
        RBtn(w, "Browse", lambda: v["folder"].set(filedialog.askdirectory(parent=w) or v["folder"].get()), h=30, icon="folder").grid(row=1, column=2, padx=px(10))
        for j, (k, l) in enumerate((("visible", "Visible to nearby devices"), ("auto", "Auto-accept everything"), ("open_after", "Open folder after receiving"))):
            tk.Checkbutton(w, text=l, variable=b[k], bg=P["bg"], fg=P["text"], selectcolor=P["card"], activebackground=P["bg"], activeforeground=P["text"], font=F(10)).grid(row=5 + j, column=1, sticky="w", pady=px(2))
        RBtn(w, f"Clear trusted devices ({len(cfg['trusted'])})", lambda: (cfg["trusted"].clear(), H.save_cfg(cfg)), h=30).grid(row=8, column=1, sticky="w", pady=px(8))
        cl = label(w, f"Transfer cache: {H.hsize(H.cache_size(cfg['folder']))}"); cl.grid(row=9, column=0, sticky="w", padx=px(22), pady=px(8))
        RBtn(w, "Clear cache", lambda: (clear_cache_ui(), cl.configure(text=f"Transfer cache: {H.hsize(H.cache_size(cfg['folder']))}")), h=30, icon="trash").grid(row=9, column=1, sticky="w")
        def ok():
            try: port, rs = int(v["port"].get()), int(v["reconnect_s"].get())
            except ValueError: return toast("Port and reconnect time must be numbers")
            cfg.update(name=v["name"].get(), folder=v["folder"].get(), port=port, pin=v["pin"].get(), reconnect_s=max(10, rs), **{k: b[k].get() for k in b})
            os.makedirs(cfg["folder"], exist_ok=True); H.save_cfg(cfg); W["path"].configure(text=short(cfg["folder"], 30)); w.destroy(); toast("Saved (restart to apply a new port)")
        RBtn(w, "Save", ok, primary=True, icon="check").grid(row=10, column=1, pady=px(16), sticky="w")

    def set_theme(name):
        if name == cfg["theme"]: return
        cfg["theme"] = name; H.save_cfg(cfg); apply_theme(name); build(); toast("Theme: " + name)

    # ------------------------------ layout ------------------------------
    def build():
        for w in root.winfo_children(): w.destroy()
        cards.clear(); root.configure(bg=P["bg"])
        side = tk.Frame(root, bg=P["side"], width=px(330)); side.pack(side="left", fill="y"); side.pack_propagate(False)
        tk.Frame(root, bg=P["line"], width=1).pack(side="left", fill="y")
        mainf = tk.Frame(root, bg=P["bg"]); mainf.pack(side="left", fill="both", expand=True)

        # --- sidebar: brand ---
        bf = tk.Frame(side, bg=P["side"]); bf.pack(fill="x", padx=px(20), pady=(px(22), px(6)))
        try:
            nm = min(("logo.png", "logo2.png", "logo3.png"), key=lambda f: abs({"logo.png": 44, "logo2.png": 66, "logo3.png": 88}[f] - 44 * SC[0])); img = tk.PhotoImage(file=res(nm))
            lg = tk.Label(bf, image=img, bg=P["side"]); lg.image = img; lg.pack(side="left", padx=(0, px(14)))
        except Exception: Icon(bf, "exchange", 40, P["accent"], P["side"]).pack(side="left", padx=(0, px(14)))
        t = tk.Frame(bf, bg=P["side"]); t.pack(side="left", fill="x", expand=True)
        tk.Label(t, text="HotShare", bg=P["side"], fg=P["text"], font=F(17, True), anchor="w").pack(anchor="w")
        tk.Label(t, text=short(f"{cfg['name']}  -  {', '.join(H.local_ips()[:1]) or 'no network'}", 34), bg=P["side"], fg=P["muted"], font=F(9), anchor="w").pack(anchor="w")

        # --- sidebar: receive-into card ---
        rc = RCard(side, pad=14); rc.pack(fill="x", padx=px(18), pady=(px(14), 0)); ri = rc.inner
        top = tk.Frame(ri, bg=P["card"]); top.pack(fill="x")
        Icon(top, "folder", 26, P["accent"], P["card"]).pack(side="left", padx=(0, px(12)))
        col = tk.Frame(top, bg=P["card"]); col.pack(side="left", fill="x", expand=True)
        tk.Label(col, text="RECEIVE INTO", bg=P["card"], fg=P["muted"], font=F(8, True), anchor="w").pack(anchor="w")
        W["path"] = tk.Label(col, text=short(cfg["folder"], 30), bg=P["card"], fg=P["text"], font=F(10, True), anchor="w"); W["path"].pack(anchor="w")
        br = tk.Frame(ri, bg=P["card"]); br.pack(fill="x", pady=(px(12), 0))
        RBtn(br, "Change", choose_folder, h=32, icon="folder", primary=True).pack(side="left", padx=(0, px(8))); RBtn(br, "Open", open_inbox, h=32, icon="tray").pack(side="left")

        # --- sidebar: devices ---
        dh = tk.Frame(side, bg=P["side"]); dh.pack(fill="x", padx=px(20), pady=(px(20), px(8)))
        tk.Label(dh, text="NEARBY DEVICES", bg=P["side"], fg=P["muted"], font=F(9, True)).pack(side="left")
        W["radar"] = Radar(dh); W["radar"].pack(side="right", padx=(px(6), 0)); RBtn(dh, "Scan", refresh, h=30, icon="radar").pack(side="right")
        RBtn(dh, "", add_ip, w=30, h=30, icon="plus").pack(side="right", padx=px(6))
        foot = tk.Frame(side, bg=P["side"]); foot.pack(side="bottom", fill="x", padx=px(18), pady=px(16))
        sf = ScrollFrame(side); sf.pack(fill="both", expand=True, padx=px(14)); W["devbox"] = sf.inner

        # --- sidebar: footer (themes + history/settings) ---
        sw = tk.Frame(foot, bg=P["side"]); sw.pack(fill="x", pady=(0, px(12)))
        for name in THEME_ORDER:
            c = tk.Canvas(sw, width=px(26), height=px(26), bg=P["side"], highlightthickness=0, cursor="hand2"); a = THEMES[name]["accent"]
            c.create_oval(px(5), px(5), px(21), px(21), fill=a, outline="")
            if name == cfg["theme"]: c.create_oval(px(2), px(2), px(24), px(24), outline=P["text"], width=2)
            c.bind("<Button-1>", lambda e, n=name: set_theme(n)); c.pack(side="left", padx=px(2))
        fr = tk.Frame(foot, bg=P["side"]); fr.pack(fill="x")
        RBtn(fr, "History", show_hist, icon="history").pack(side="left", padx=(0, px(8))); RBtn(fr, "Settings", settings, icon="sliders").pack(side="left")

        # --- main: sticky action bar ---
        bar = tk.Frame(mainf, bg=P["side"]); bar.pack(side="bottom", fill="x"); tk.Frame(bar, bg=P["line"], height=1).pack(fill="x")
        bi = tk.Frame(bar, bg=P["side"]); bi.pack(fill="x", padx=px(26), pady=px(14))
        W["info"] = tk.Label(bi, text="", bg=P["side"], fg=P["text"], font=F(10, True)); W["info"].pack(side="left")
        RBtn(bi, "Send", send_queue, h=40, w=130, primary=True, icon="send").pack(side="right")

        # --- main: status strip ---
        st = tk.Frame(mainf, bg=P["bg"]); st.pack(fill="x", padx=px(26), pady=(px(16), 0)); dot = tk.Canvas(st, width=px(10), height=px(10), bg=P["bg"], highlightthickness=0)
        dot.create_oval(1, 1, px(10) - 1, px(10) - 1, fill=P["ok"], outline=""); dot.pack(side="left")
        tk.Label(st, text=f"Ready to receive   -   {', '.join(H.local_ips()[:1]) or 'no network'}:{cfg['port']}", bg=P["bg"], fg=P["muted"], font=F(9)).pack(side="left", padx=px(10))
        W["speed"] = tk.Label(st, text="Idle", bg=P["bg"], fg=P["text"], font=F(10, True)); W["speed"].pack(side="right")

        # --- main: scrolling content ---
        sfm = ScrollFrame(mainf); sfm.pack(fill="both", expand=True); body = sfm.inner
        dz = RCard(body, fill=P["bg"], pad=22, r=18, dash=True); dz.pack(fill="x", padx=px(26), pady=(px(26), px(8))); zi = dz.inner
        Icon(zi, "upload", 40, P["accent"], P["bg"]).pack()
        tk.Label(zi, text="Drop files or folders here", bg=P["bg"], fg=P["text"], font=F(15, True)).pack(pady=(px(8), px(2)))
        tk.Label(zi, text="drag from Explorer, press Ctrl+V to paste, or use the buttons", bg=P["bg"], fg=P["muted"], font=F(10)).pack()
        zb = tk.Frame(zi, bg=P["bg"]); zb.pack(pady=(px(16), 0))
        for t, ic, c, pr in (("Files", "file", pick_files, True), ("Folder", "folder", pick_folder, True), ("Paste", "tray", paste, False), ("Text", "text", add_text_dialog, False)):
            RBtn(zb, t, c, h=40, w=112, primary=pr, icon=ic).pack(side="left", padx=px(5))

        qh = tk.Frame(body, bg=P["bg"]); qh.pack(fill="x", padx=px(26), pady=(px(20), px(6)))
        tk.Label(qh, text="TO SEND", bg=P["bg"], fg=P["muted"], font=F(9, True)).pack(side="left"); W["qcount"] = tk.Label(qh, text="", bg=P["bg"], fg=P["accent"], font=F(9, True)); W["qcount"].pack(side="left", padx=px(10))
        RBtn(qh, "Clear all", clear_queue, h=30, icon="close").pack(side="right")
        W["qbox"] = tk.Frame(body, bg=P["bg"]); W["qbox"].pack(fill="x", padx=px(26))
        W["qempty"] = tk.Label(W["qbox"], text="Nothing queued. Add files above, then pick a device and press Send.", bg=P["bg"], fg=P["muted"], font=F(10))
        tk.Label(body, text="Put it into this subfolder on the other device (optional)", bg=P["bg"], fg=P["muted"], font=F(9)).pack(anchor="w", padx=px(28), pady=(px(10), px(4)))
        W["dest"] = tk.Entry(body, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", font=F(10)); W["dest"].pack(fill="x", padx=px(26), ipady=px(8))

        xh = tk.Frame(body, bg=P["bg"]); xh.pack(fill="x", padx=px(26), pady=(px(24), px(6)))
        tk.Label(xh, text="TRANSFERS", bg=P["bg"], fg=P["muted"], font=F(9, True)).pack(side="left"); RBtn(xh, "Cancel all", lambda: [c.cancel.set() for c in active], h=30, icon="close").pack(side="right"); RBtn(xh, "Clear cache", clear_cache_ui, h=30, icon="trash").pack(side="right", padx=px(8))
        W["xbox"] = tk.Frame(body, bg=P["bg"]); W["xbox"].pack(fill="x", padx=px(26), pady=(0, px(26)))

        # --- restore state after a (re)build ---
        for e in queue_: QueueRow(W["qbox"], e, remove_row).pack(fill="x", pady=px(4))
        show_devices(dict(devices), keep=True)
        for c, (state, msg) in list(done_.items()):   # finished / paused cards survive a theme change
            card = make_card(c)
            if c.names: card.set_files(c.names, c.sizes)
            paint_done(card, c, state, msg)
        for c in list(active):
            card = make_card(c)
            if c.names: card.set_files(c.names, c.sizes); card.update_files(c.fdone)
            if c.last[1]: on_prog(c, *c.last)
        WIN.style_titlebar(root, P["bg"], P["text"], P["accent"], P["dark"])

    def wheel(e):
        try: w = root.winfo_containing(e.x_root, e.y_root)
        except Exception: w = None
        step = -1 if (getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0) else 1
        while w is not None:
            if isinstance(w, ScrollFrame): w.cv.yview_scroll(step * 2, "units"); return
            w = getattr(w, "master", None)
    for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"): root.bind_all(ev, wheel)
    root.bind_all("<Control-v>", lambda e: paste() if not isinstance(e.widget, (tk.Entry, tk.Text)) else None)

    build(); restore_jobs(); pump(); WIN.hook_dropfiles(root, lambda files: ui(add_paths, files))
    threading.Thread(target=server, daemon=True).start(); threading.Thread(target=H.discovery_server, args=(cfg,), daemon=True).start()
    refresh(); root.mainloop(); H.save_cfg(cfg)


if __name__ == "__main__":
    main()
