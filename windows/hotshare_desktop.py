#!/usr/bin/env python3
"""HotShare Desktop GUI (tkinter, custom-drawn icons/cards/animations). Core logic lives in hotshare_core.py."""
import os, queue, socket, sys, threading, time, tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont
import hotshare_core as H
from hotshare_icons import draw_tk

DARK = dict(bg="#0f1419", card="#1a2230", card2="#243044", hover="#223049", track="#2a3446", text="#e8edf5", muted="#8b97a8", accent="#4c8dff", ok="#38d39f", err="#ff6b6b")
LIGHT = dict(bg="#f3f6fb", card="#ffffff", card2="#e3e9f3", hover="#eaf0fb", track="#dfe5ef", text="#1b2430", muted="#6b7686", accent="#2f6df0", ok="#1fae7f", err="#e04b4b")
FONT = "Segoe UI"


def res(name):
    return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), name)


def lerp(a, b, t):
    x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]; y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x[i] + (y[i] - x[i]) * t) for i in range(3))


def rrect(c, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


def short(p, n=44):
    return p if len(p) <= n else p[:14] + "..." + p[-(n - 17):]


class Icon(tk.Canvas):
    def __init__(s, parent, name, size=22, color="#ffffff", bg=None):
        super().__init__(parent, width=size, height=size, bg=bg or parent["bg"], highlightthickness=0)
        s.size, s.name = size, name; s.show(name, color)

    def show(s, name, color):
        s.delete("all"); s.name = name; draw_tk(s, name, 1, 1, s.size - 2, color)


class RBtn(tk.Canvas):
    def __init__(s, parent, text, cmd, P, w=None, h=36, primary=False, icon=None):
        tw = tkfont.Font(family=FONT, size=10, weight="bold").measure(text) if text else 0
        w = w or ((30 if icon else 14) + tw + 16 if text else h)
        super().__init__(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        s.base = P["accent"] if primary else P["card2"]; s.col = s.base; s.fg = "#ffffff" if primary else P["text"]
        s.shape = rrect(s, 1, 1, w - 1, h - 1, h / 2.4, fill=s.col, outline="")
        x = 14
        if icon:
            if text: draw_tk(s, icon, x, (h - 18) / 2, 18, s.fg)
            else: draw_tk(s, icon, (w - 18) / 2, (h - 18) / 2, 18, s.fg)
            x += 26
        if text: s.create_text(x, h / 2, text=text, fill=s.fg, anchor="w", font=(FONT, 10, "bold"))
        s.bind("<Enter>", lambda e: s.fade(lerp(s.base, "#ffffff", .14)))
        s.bind("<Leave>", lambda e: s.fade(s.base))
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
    def __init__(s, parent, P, h=10):
        super().__init__(parent, height=h, bg=parent["bg"], highlightthickness=0)
        s.P, s.h, s.cur, s.tgt, s.run, s.done = P, h, 0.0, 0.0, False, False
        s.bind("<Configure>", lambda e: s.draw())

    def set(s, v, done=False):
        s.tgt, s.done = max(0.0, min(1.0, v)), done
        if not s.run: s.run = True; s.step()

    def step(s):
        if not s.winfo_exists(): return
        s.cur += (s.tgt - s.cur) * .25
        if abs(s.tgt - s.cur) < .002: s.cur, s.run = s.tgt, False
        s.draw()
        if s.run: s.after(16, s.step)

    def draw(s):
        s.delete("all"); w = max(s.winfo_width(), 2); r = s.h / 2
        rrect(s, 0, 0, w, s.h, r, fill=s.P["track"], outline="")
        if s.cur > 0: rrect(s, 0, 0, max(s.h, w * s.cur), s.h, r, fill=s.P["ok"] if s.done else s.P["accent"], outline="")


class Radar(tk.Canvas):
    def __init__(s, parent, P):
        super().__init__(parent, width=48, height=48, bg=parent["bg"], highlightthickness=0); s.P, s.t, s.on = P, 0.0, False; s.draw()

    def start(s):
        if not s.on: s.on = True; s.loop()

    def stop(s): s.on = False

    def loop(s):
        if not s.winfo_exists(): return
        s.t = (s.t + .025) % 1; s.draw()
        if s.on: s.after(30, s.loop)
        else: s.t = 0; s.draw()

    def draw(s):
        s.delete("all"); s.create_oval(19, 19, 29, 29, fill=s.P["accent"], outline="")
        for k in range(3):
            f = (s.t + k / 3) % 1 if s.on else .3 + k * .25
            r = 7 + 17 * f; s.create_oval(24 - r, 24 - r, 24 + r, 24 + r, outline=lerp(s.P["accent"], s.P["bg"], f if s.on else .7), width=2)


class RCard(tk.Canvas):
    """Rounded card: draws a rounded background and hosts widgets in .inner (sized to content)."""
    def __init__(s, parent, P, fill=None, pad=14, r=16):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, height=20)
        s.P, s.fill, s.pad, s.r, s.edge, s.kids = P, fill or P["card"], pad, r, None, []
        s.inner = tk.Frame(s, bg=s.fill); s.win = s.create_window(pad, pad, window=s.inner, anchor="nw")
        s.inner.bind("<Configure>", lambda e: s.relayout()); s.bind("<Configure>", lambda e: s.relayout())

    def relayout(s):
        if not s.winfo_exists(): return
        w = max(s.winfo_width(), 2); h = s.inner.winfo_reqheight() + 2 * s.pad
        if int(float(s["height"])) != h: s.configure(height=h)
        s.itemconfigure(s.win, width=max(1, w - 2 * s.pad)); s.delete("bg")
        rrect(s, 1, 1, w - 1, h - 1, s.r, fill=s.fill, outline=s.edge or s.fill, width=2 if s.edge else 1, tags="bg"); s.tag_lower("bg")

    def paint(s, fill, edge=None):
        s.fill, s.edge = fill, edge; s.inner.configure(bg=fill)
        for k in s.kids:
            try: k.configure(bg=fill)
            except tk.TclError: pass
        s.relayout()


class ScrollFrame(tk.Frame):
    def __init__(s, parent, P):
        super().__init__(parent, bg=P["bg"])
        s.cv = tk.Canvas(s, bg=P["bg"], highlightthickness=0); s.cv.pack(side="left", fill="both", expand=True)
        s.inner = tk.Frame(s.cv, bg=P["bg"]); s.win = s.cv.create_window((0, 0), window=s.inner, anchor="nw")
        s.inner.bind("<Configure>", lambda e: s.cv.configure(scrollregion=s.cv.bbox("all")))
        s.cv.bind("<Configure>", lambda e: s.cv.itemconfigure(s.win, width=e.width))
        s.cv.bind_all("<MouseWheel>", lambda e: s.cv.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        s.cv.bind_all("<Button-4>", lambda e: s.cv.yview_scroll(-1, "units")); s.cv.bind_all("<Button-5>", lambda e: s.cv.yview_scroll(1, "units"))


class DevCard(RCard):
    def __init__(s, parent, P, name, sub, kind, on_toggle):
        super().__init__(parent, P, pad=12); i = s.inner; s.sel, s.fading = False, True
        s.ic = Icon(i, "phone" if kind == "Android" else "laptop", 28, P["accent"], P["card"]); s.ic.pack(side="left", padx=(2, 14))
        txt = tk.Frame(i, bg=P["card"]); txt.pack(side="left", fill="x", expand=True)
        s.t1 = tk.Label(txt, text=name, bg=P["card"], fg=P["text"], font=(FONT, 11, "bold"), anchor="w"); s.t1.pack(anchor="w")
        s.t2 = tk.Label(txt, text=sub, bg=P["card"], fg=P["muted"], font=(FONT, 9), anchor="w"); s.t2.pack(anchor="w")
        s.ck = Icon(i, "check", 24, P["ok"], P["card"]); s.ck.pack(side="right"); s.ck.delete("all")
        s.kids = [i, s.ic, txt, s.t1, s.t2, s.ck]
        for w in (s, i, s.ic, txt, s.t1, s.t2, s.ck):
            w.configure(cursor="hand2"); w.bind("<Button-1>", lambda e: s.toggle(on_toggle))
            w.bind("<Enter>", lambda e: s.hover(True)); w.bind("<Leave>", lambda e: s.hover(False))
        s.anim()

    def hover(s, on):
        if not s.fading and not s.sel: s.paint(s.P["hover"] if on else s.P["card"])

    def anim(s, i=1):
        if not s.winfo_exists(): return
        s.paint(lerp(s.P["bg"], s.P["card"], i / 12))
        if i < 12: s.after(18, s.anim, i + 1)
        else: s.fading = False

    def toggle(s, cb):
        s.sel = not s.sel; s.paint(lerp(s.P["card"], s.P["accent"], .14) if s.sel else s.P["card"], s.P["accent"] if s.sel else None)
        if s.sel: s.ck.show("check", s.P["ok"])
        else: s.ck.delete("all")
        cb()


class XCard(RCard):
    def __init__(s, parent, P, title, up, on_cancel):
        super().__init__(parent, P); i = s.inner
        top = tk.Frame(i, bg=P["card"]); top.pack(fill="x")
        Icon(top, "upload" if up else "download", 20, P["accent"], P["card"]).pack(side="left")
        s.title = tk.Label(top, text=title, bg=P["card"], fg=P["text"], font=(FONT, 10, "bold")); s.title.pack(side="left", padx=8)
        x = Icon(top, "close", 18, P["muted"], P["card"]); x.pack(side="right"); x.configure(cursor="hand2"); x.bind("<Button-1>", lambda e: on_cancel())
        s.bar = Bar(i, P); s.bar.pack(fill="x", pady=(10, 6))
        s.stat = tk.Label(i, text="Starting...", bg=P["card"], fg=P["muted"], font=(FONT, 9), anchor="w"); s.stat.pack(anchor="w")


def main():
    cfg = H.load_cfg(); P = DARK if cfg["theme"] == "dark" else LIGHT
    os.makedirs(cfg["folder"], exist_ok=True); H.clean_partials(cfg["folder"])
    root = tk.Tk(); root.title("HotShare"); root.geometry("780x860"); root.configure(bg=P["bg"]); root.minsize(620, 640)
    try: root.iconbitmap(res("icon.ico"))
    except Exception:
        try: root.iconphoto(True, tk.PhotoImage(file=res("logo.png")))
        except Exception: pass
    q = queue.Queue(); devices, cards, active, selected = {}, {}, [], set()

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
        t = tk.Label(root, text=msg, bg=P["accent"], fg="#ffffff", font=(FONT, 10, "bold"), padx=20, pady=10)
        def mv(i, up):
            if not t.winfo_exists(): return
            e = 1 - (1 - i / 10) ** 3; t.place(relx=.5, rely=1.0, anchor="s", y=int(60 - 100 * e if up else -40 + 100 * e))
            if i < 10: root.after(16, mv, i + 1, up)
            elif up: root.after(2400, mv, 0, False)
            else: t.destroy()
        mv(0, True)

    def log(m): ui(toast, m)

    # ---------- layout ----------
    foot = tk.Frame(root, bg=P["bg"]); foot.pack(side="bottom", fill="x", padx=22, pady=14)
    sf = ScrollFrame(root, P); sf.pack(fill="both", expand=True); body = sf.inner

    hdr = tk.Frame(body, bg=P["bg"]); hdr.pack(fill="x", padx=22, pady=(20, 4))
    try: logo = tk.PhotoImage(file=res("logo.png")); lg = tk.Label(hdr, image=logo, bg=P["bg"]); lg.image = logo; lg.pack(side="left", padx=(0, 14))
    except Exception: Icon(hdr, "exchange", 40, P["accent"]).pack(side="left", padx=(0, 14))
    left = tk.Frame(hdr, bg=P["bg"]); left.pack(side="left")
    tk.Label(left, text="HotShare", bg=P["bg"], fg=P["text"], font=(FONT, 22, "bold")).pack(anchor="w")
    tk.Label(left, text=f"{cfg['name']}   -   {', '.join(H.local_ips()) or 'no network'}", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).pack(anchor="w")
    radar = Radar(hdr, P); radar.pack(side="right")

    def section(text, btns=()):
        f = tk.Frame(body, bg=P["bg"]); f.pack(fill="x", padx=22, pady=(20, 8))
        tk.Label(f, text=text.upper(), bg=P["bg"], fg=P["muted"], font=(FONT, 9, "bold")).pack(side="left")
        for t, ic, c in reversed(btns): RBtn(f, t, c, P, h=30, icon=ic).pack(side="right", padx=3)
        return f

    # ---------- receive-into card (folder chooser) ----------
    sc = RCard(body, P); sc.pack(fill="x", padx=22, pady=(14, 0)); si = sc.inner
    Icon(si, "folder", 28, P["accent"], P["card"]).pack(side="left", padx=(2, 14))
    col = tk.Frame(si, bg=P["card"]); col.pack(side="left", fill="x", expand=True)
    tk.Label(col, text="RECEIVE INTO", bg=P["card"], fg=P["muted"], font=(FONT, 8, "bold"), anchor="w").pack(anchor="w")
    pathlbl = tk.Label(col, text=short(cfg["folder"]), bg=P["card"], fg=P["text"], font=(FONT, 11, "bold"), anchor="w"); pathlbl.pack(anchor="w")

    def open_inbox():
        try: os.startfile(cfg["folder"]) if os.name == "nt" else os.system(f'xdg-open "{cfg["folder"]}" &')
        except OSError: pass

    def choose_folder():
        f = filedialog.askdirectory(title="Choose where received files are saved", initialdir=cfg["folder"], parent=root)
        if f:
            cfg["folder"] = os.path.normpath(f); os.makedirs(cfg["folder"], exist_ok=True); H.save_cfg(cfg)
            pathlbl.configure(text=short(cfg["folder"])); toast("Saving received files to " + os.path.basename(cfg["folder"]))
    RBtn(si, "Open", open_inbox, P, h=34, icon="tray").pack(side="right", padx=(8, 0))
    RBtn(si, "Change", choose_folder, P, h=34, icon="folder", primary=True).pack(side="right")

    # ---------- devices ----------
    def refresh():
        radar.start()
        def w():
            r = H.scan(); ui(show_devices, r)
        threading.Thread(target=w, daemon=True).start()

    def show_devices(r):
        radar.stop(); devices.clear(); devices.update(r); selected.clear()
        for w in devbox.winfo_children(): w.destroy()
        if not r:
            e = tk.Frame(devbox, bg=P["bg"]); e.pack(pady=14); Icon(e, "radar", 34, P["muted"]).pack()
            tk.Label(e, text="No devices found", bg=P["bg"], fg=P["text"], font=(FONT, 11, "bold")).pack(pady=(6, 0))
            tk.Label(e, text="Join the same hotspot / Wi-Fi, open HotShare on the other device, then Scan.", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).pack()
        for ip, (n, p, o) in r.items():
            DevCard(devbox, P, n, f"{ip}   -   {o or 'device'}", o, lambda ip=ip: selected.symmetric_difference_update({ip})).pack(fill="x", pady=4)

    def add_ip():
        d = tk.Toplevel(root); d.title("Add device by IP"); d.configure(bg=P["bg"]); d.transient(root)
        tk.Label(d, text="IP address of the other device", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).pack(padx=22, pady=(18, 4), anchor="w")
        e = tk.Entry(d, bg=P["card2"], fg=P["text"], insertbackground=P["text"], relief="flat", font=(FONT, 12), width=28); e.pack(padx=22, ipady=7); e.focus()
        def ok():
            ip = e.get().strip()
            if ip: devices[ip] = ("Manual", cfg["port"], "IP"); show_devices(dict(devices))
            d.destroy()
        RBtn(d, "Add device", ok, P, primary=True, icon="plus").pack(pady=16); e.bind("<Return>", lambda ev: ok())

    section("Nearby devices", [("Scan", "radar", refresh), ("Add IP", "plus", add_ip)])
    devbox = tk.Frame(body, bg=P["bg"]); devbox.pack(fill="x", padx=22)

    # ---------- transfers plumbing ----------
    def new_ctl(label):
        c = H.Ctl(cfg, ask, lambda *a: ui(on_prog, c, *a), log, on_text, hist); c.label = label; active.append(c); return c

    def make_card(c):
        card = XCard(xbox, P, c.label, not c.incoming, c.cancel.set); card.pack(fill="x", pady=5); cards[c] = card; return card

    def on_prog(c, d, t, sp, eta):
        card = cards.get(c) or make_card(c); card.title.configure(text=c.label); card.bar.set(d / t if t else 0)
        card.stat.configure(text=f"{H.hsize(d)} of {H.hsize(t)}   -   {H.hsize(sp)}/s   -   {int(eta)}s left" if sp else "Finishing...")

    def finish(c, ok, msg):
        card = cards.get(c) or make_card(c); card.title.configure(text=c.label)
        card.bar.set(1 if ok else card.bar.cur, done=ok); card.stat.configure(text=("Done - " if ok else "Stopped - ") + msg, fg=P["ok"] if ok else P["err"])
        root.after(9000, lambda: (card.destroy(), cards.pop(c, None)))
        if ok and c.got and cfg["open_after"] and c.incoming: open_inbox()

    def hist(d, name, size, peer):
        cfg["history"] = (cfg["history"] + [[time.strftime("%Y-%m-%d %H:%M"), d, name, size, peer]])[-200:]

    def on_text(peer, txt):
        def f():
            root.clipboard_clear(); root.clipboard_append(txt)
            w = tk.Toplevel(root); w.title(f"Text from {peer}"); w.configure(bg=P["bg"])
            tk.Label(w, text=f"Text from {peer}", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).pack(padx=22, pady=(16, 4), anchor="w")
            tk.Label(w, text=txt[:2000], bg=P["card"], fg=P["text"], wraplength=440, justify="left", padx=16, pady=14, font=(FONT, 10)).pack(padx=22)
            tk.Label(w, text="Copied to clipboard", bg=P["bg"], fg=P["ok"], font=(FONT, 9, "bold")).pack(pady=(10, 16))
        ui(f)

    def ask(h, n, total):
        ev, res_, folder = threading.Event(), [], [None]
        def dlg():
            d = tk.Toplevel(root); d.title("Incoming transfer"); d.configure(bg=P["bg"]); d.transient(root)
            Icon(d, "download", 34, P["accent"]).pack(pady=(20, 6))
            tk.Label(d, text=f"{h.get('name')} wants to send", bg=P["bg"], fg=P["muted"], font=(FONT, 10)).pack(padx=28)
            tk.Label(d, text=f"{n} item(s)  -  {H.hsize(total)}", bg=P["bg"], fg=P["text"], font=(FONT, 16, "bold")).pack(padx=28)
            fl = tk.Label(d, text="Save to: " + short(cfg["folder"], 50), bg=P["bg"], fg=P["muted"], font=(FONT, 9)); fl.pack(pady=(10, 4))
            def change():
                f = filedialog.askdirectory(parent=d, initialdir=cfg["folder"], title="Save this transfer to...")
                if f: folder[0] = f; fl.configure(text="Save to: " + short(f, 50))
            RBtn(d, "Choose folder", change, P, h=30, icon="folder").pack(pady=4)
            def pick(v): res_.append(v); ev.set(); d.destroy()
            row = tk.Frame(d, bg=P["bg"]); row.pack(pady=18, padx=20)
            for t, v, pr in (("Accept", "once", True), ("Always", "always", False), ("Decline", None, False)): RBtn(row, t, lambda v=v: pick(v), P, w=96, primary=pr).pack(side="left", padx=4)
            d.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        ui(dlg); ev.wait(120)
        return (res_[0] if res_ else None), folder[0]

    def server():
        try:
            s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("", cfg["port"])); s.listen(32)
        except Exception as e:
            return ui(toast, f"Cannot receive: {e} (is the port already in use?)")
        while True:
            c, _ = s.accept()
            def run(c=c):
                ctl = new_ctl("Incoming"); ctl.incoming = True
                try:
                    H.handle(c, ctl)
                    if ctl.label != "Incoming": ui(finish, ctl, not ctl.declined, "Declined" if ctl.declined else (f"received {ctl.got} file(s)" if ctl.got else "text received"))
                except Exception as e:
                    if ctl.label != "Incoming": ui(finish, ctl, False, f"{e} (partial kept, will resume)")
                finally:
                    try: c.close()
                    except OSError: pass
                    active.remove(ctl); H.save_cfg(cfg)
            threading.Thread(target=run, daemon=True).start()

    def do_send(items):
        t = [(ip, devices[ip][1], devices[ip][0]) for ip in selected if ip in devices]
        if not items: return
        if not t: return toast("Select a device first")
        for ip, port, name in t:
            def w(ip=ip, port=port, name=name):
                ctl = new_ctl("To " + name); ctl.incoming = False
                try: H.send(ip, port, items, ctl, dest.get().strip()); ui(finish, ctl, True, "sent")
                except InterruptedError: ui(finish, ctl, False, "cancelled")
                except Exception as e: ui(finish, ctl, False, str(e))
                finally: active.remove(ctl); H.save_cfg(cfg)
            threading.Thread(target=w, daemon=True).start()

    def send_text():
        d = tk.Toplevel(root); d.title("Send text"); d.configure(bg=P["bg"]); d.transient(root)
        t = tk.Text(d, width=50, height=8, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", padx=12, pady=12, font=(FONT, 10)); t.pack(padx=22, pady=(20, 10))
        try: t.insert("1.0", root.clipboard_get())
        except tk.TclError: pass
        def go():
            v = t.get("1.0", "end").strip()
            if v: do_send([H.text_item(v)])
            d.destroy()
        RBtn(d, "Send", go, P, primary=True, icon="send").pack(pady=(0, 18))

    # ---------- send ----------
    section("Send")
    sc2 = RCard(body, P); sc2.pack(fill="x", padx=22); s2 = sc2.inner
    row = tk.Frame(s2, bg=P["card"]); row.pack(fill="x")
    for t, ic, c in (("Files", "file", lambda: do_send(H.collect(filedialog.askopenfilenames(title="Choose files to send")))),
                     ("Folder", "folder", lambda: do_send(H.collect([filedialog.askdirectory(title="Choose a folder to send")]))), ("Text", "text", send_text)):
        RBtn(row, t, c, P, h=42, primary=True, icon=ic, w=128).pack(side="left", padx=(0, 10))
    tk.Label(s2, text="Put it into this subfolder on the other device (optional)", bg=P["card"], fg=P["muted"], font=(FONT, 9)).pack(anchor="w", pady=(14, 4))
    dest = tk.Entry(s2, bg=P["card2"], fg=P["text"], insertbackground=P["text"], relief="flat", font=(FONT, 10)); dest.pack(fill="x", ipady=7)

    section("Transfers", [("Cancel all", "close", lambda: [c.cancel.set() for c in active])])
    xbox = tk.Frame(body, bg=P["bg"]); xbox.pack(fill="x", padx=22, pady=(0, 20))

    # ---------- history / settings ----------
    def show_hist():
        w = tk.Toplevel(root); w.title("History"); w.configure(bg=P["bg"]); w.geometry("660x440")
        l = tk.Listbox(w, bg=P["card"], fg=P["text"], relief="flat", highlightthickness=0, font=(FONT, 10), selectbackground=P["accent"]); l.pack(fill="both", expand=True, padx=16, pady=16)
        if not cfg["history"]: l.insert("end", "  Nothing transferred yet.")
        for t, d, n, s, p in reversed(cfg["history"]): l.insert("end", f"  {t}    {'Received from' if d == 'in' else 'Sent to'} {p}    {n}    ({H.hsize(s)})")

    def settings():
        w = tk.Toplevel(root); w.title("Settings"); w.configure(bg=P["bg"]); w.transient(root)
        v = {k: tk.StringVar(value=str(cfg[k])) for k in ("name", "folder", "port", "pin", "retries")}
        b = {k: tk.BooleanVar(value=cfg[k]) for k in ("auto", "visible", "open_after")}; th = tk.StringVar(value=cfg["theme"])
        rows = (("name", "Device name"), ("folder", "Save folder"), ("port", "Port"), ("pin", "Shared PIN"), ("retries", "Auto-resume attempts"))
        for i, (k, l) in enumerate(rows):
            tk.Label(w, text=l, bg=P["bg"], fg=P["muted"], font=(FONT, 9)).grid(row=i, column=0, sticky="w", padx=22, pady=7)
            tk.Entry(w, textvariable=v[k], width=34, bg=P["card2"], fg=P["text"], insertbackground=P["text"], relief="flat").grid(row=i, column=1, padx=6, ipady=5)
        RBtn(w, "Browse", lambda: v["folder"].set(filedialog.askdirectory(parent=w) or v["folder"].get()), P, h=30, icon="folder").grid(row=1, column=2, padx=10)
        for j, (k, l) in enumerate((("visible", "Visible to nearby devices"), ("auto", "Auto-accept everything"), ("open_after", "Open folder after receiving"))):
            tk.Checkbutton(w, text=l, variable=b[k], bg=P["bg"], fg=P["text"], selectcolor=P["card"], activebackground=P["bg"], activeforeground=P["text"]).grid(row=5 + j, column=1, sticky="w", pady=2)
        tk.Label(w, text="Theme (restart)", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).grid(row=8, column=0, sticky="w", padx=22); tk.OptionMenu(w, th, "dark", "light").grid(row=8, column=1, sticky="w", pady=6)
        RBtn(w, f"Clear trusted devices ({len(cfg['trusted'])})", lambda: (cfg["trusted"].clear(), H.save_cfg(cfg)), P, h=30).grid(row=9, column=1, sticky="w", pady=6)
        def ok():
            try: port, ret = int(v["port"].get()), int(v["retries"].get())
            except ValueError: return toast("Port and attempts must be numbers")
            cfg.update(name=v["name"].get(), folder=v["folder"].get(), port=port, pin=v["pin"].get(), retries=ret, theme=th.get(), **{k: b[k].get() for k in b})
            os.makedirs(cfg["folder"], exist_ok=True); H.save_cfg(cfg); pathlbl.configure(text=short(cfg["folder"])); w.destroy(); toast("Saved (restart to apply port/theme)")
        RBtn(w, "Save", ok, P, primary=True, icon="check").grid(row=10, column=1, pady=16, sticky="w")

    RBtn(foot, "History", show_hist, P, icon="history").pack(side="left", padx=(0, 8))
    RBtn(foot, "Settings", settings, P, icon="sliders").pack(side="left")

    pump(); threading.Thread(target=server, daemon=True).start(); threading.Thread(target=H.discovery_server, args=(cfg,), daemon=True).start()
    refresh(); root.mainloop(); H.save_cfg(cfg)


if __name__ == "__main__":
    main()
