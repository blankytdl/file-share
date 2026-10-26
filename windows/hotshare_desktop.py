#!/usr/bin/env python3
"""HotShare Desktop GUI (tkinter, custom-drawn animated widgets). Core logic lives in hotshare_core.py."""
import os, queue, socket, threading, time, tkinter as tk
from tkinter import filedialog
import hotshare_core as H

DARK = dict(bg="#0f1419", card="#1a2230", card2="#243044", track="#2a3446", text="#e8edf5", muted="#8b97a8", accent="#4c8dff", ok="#38d39f", err="#ff6b6b")
LIGHT = dict(bg="#f3f6fb", card="#ffffff", card2="#e3e9f3", track="#dfe5ef", text="#1b2430", muted="#6b7686", accent="#2f6df0", ok="#1fae7f", err="#e04b4b")
FONT = "Segoe UI"


def lerp(a, b, t):
    x = [int(a[i:i + 2], 16) for i in (1, 3, 5)]; y = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x[i] + (y[i] - x[i]) * t) for i in range(3))


def rrect(c, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class RBtn(tk.Canvas):
    def __init__(s, parent, text, cmd, P, w=96, h=36, primary=False):
        super().__init__(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        s.base = P["accent"] if primary else P["card2"]; s.col = s.base; s.w, s.h = w, h
        s.shape = rrect(s, 1, 1, w - 1, h - 1, 10, fill=s.col, outline="")
        s.label = s.create_text(w / 2, h / 2, text=text, fill="#ffffff" if primary else P["text"], font=(FONT, 10, "bold"))
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


class DevCard(tk.Frame):
    def __init__(s, parent, P, name, sub, on_toggle):
        super().__init__(parent, bg=P["bg"], padx=14, pady=9, cursor="hand2", highlightthickness=2, highlightbackground=P["bg"])
        s.P, s.sel = P, False
        s.ck = tk.Label(s, text="", bg=P["bg"], fg=P["accent"], font=(FONT, 15, "bold")); s.ck.pack(side="right")
        s.t1 = tk.Label(s, text=name, bg=P["bg"], fg=P["text"], font=(FONT, 11, "bold"), anchor="w"); s.t1.pack(anchor="w")
        s.t2 = tk.Label(s, text=sub, bg=P["bg"], fg=P["muted"], font=(FONT, 9), anchor="w"); s.t2.pack(anchor="w")
        s.ws = (s, s.t1, s.t2, s.ck); s.fading = True
        for w in s.ws:
            w.bind("<Button-1>", lambda e: s.toggle(on_toggle))
            w.bind("<Enter>", lambda e: s.paint(P["card2"]) if not s.fading else None)
            w.bind("<Leave>", lambda e: s.paint(P["card"]) if not s.fading else None)
        s.anim()

    def paint(s, c):
        for w in s.ws: w.configure(bg=c)

    def anim(s, i=1):
        if not s.winfo_exists(): return
        s.paint(lerp(s.P["bg"], s.P["card"], i / 12))
        if i < 12: s.after(18, s.anim, i + 1)
        else: s.fading = False

    def toggle(s, cb):
        s.sel = not s.sel; s.configure(highlightbackground=s.P["accent"] if s.sel else s.P["bg"]); s.ck.configure(text="\u2713" if s.sel else ""); cb()


class XCard(tk.Frame):
    def __init__(s, parent, P, title, on_cancel):
        super().__init__(parent, bg=P["card"], padx=14, pady=10)
        top = tk.Frame(s, bg=P["card"]); top.pack(fill="x")
        s.title = tk.Label(top, text=title, bg=P["card"], fg=P["text"], font=(FONT, 10, "bold")); s.title.pack(side="left")
        x = tk.Label(top, text="\u2715", bg=P["card"], fg=P["muted"], cursor="hand2"); x.pack(side="right"); x.bind("<Button-1>", lambda e: on_cancel())
        s.bar = Bar(s, P); s.bar.configure(bg=P["card"]); s.bar.pack(fill="x", pady=6)
        s.stat = tk.Label(s, text="Starting\u2026", bg=P["card"], fg=P["muted"], font=(FONT, 9), anchor="w"); s.stat.pack(anchor="w")


def main():
    cfg = H.load_cfg(); P = DARK if cfg["theme"] == "dark" else LIGHT
    os.makedirs(cfg["folder"], exist_ok=True); H.clean_partials(cfg["folder"])
    root = tk.Tk(); root.title("HotShare"); root.geometry("720x780"); root.configure(bg=P["bg"]); root.minsize(560, 600)
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
        t = tk.Label(root, text=msg, bg=P["card2"], fg=P["text"], font=(FONT, 10), padx=18, pady=9)
        def mv(i, up):
            e = 1 - (1 - i / 10) ** 3; y = 60 - 100 * e if up else -40 + 100 * e
            t.place(relx=.5, rely=1.0, anchor="s", y=int(y))
            if i < 10: root.after(16, mv, i + 1, up)
            elif up: root.after(2600, mv, 0, False)
            else: t.destroy()
        mv(0, True)

    def log(m): ui(toast, m)

    # ---- layout ----
    hdr = tk.Frame(root, bg=P["bg"]); hdr.pack(fill="x", padx=22, pady=(18, 4))
    left = tk.Frame(hdr, bg=P["bg"]); left.pack(side="left")
    tk.Label(left, text="HotShare", bg=P["bg"], fg=P["text"], font=(FONT, 24, "bold")).pack(anchor="w")
    tk.Label(left, text=f"{cfg['name']}  \u00b7  {', '.join(H.local_ips()) or 'no network'}", bg=P["bg"], fg=P["muted"], font=(FONT, 9)).pack(anchor="w")
    radar = Radar(hdr, P); radar.pack(side="right")

    def section(text, btns=()):
        f = tk.Frame(root, bg=P["bg"]); f.pack(fill="x", padx=22, pady=(14, 6))
        tk.Label(f, text=text.upper(), bg=P["bg"], fg=P["muted"], font=(FONT, 9, "bold")).pack(side="left")
        for t, c in reversed(btns): RBtn(f, t, c, P, w=84, h=28).pack(side="right", padx=3)
        return f

    def refresh():
        radar.start()
        def w():
            r = H.scan(); ui(show_devices, r)
        threading.Thread(target=w, daemon=True).start()

    def show_devices(r):
        radar.stop(); devices.clear(); devices.update(r); selected.clear()
        for w in devbox.winfo_children(): w.destroy()
        if not r: tk.Label(devbox, text="No devices found. Join the same hotspot/Wi-Fi, then Refresh.", bg=P["bg"], fg=P["muted"]).pack(pady=10)
        for i, (ip, (n, p, o)) in enumerate(r.items()):
            DevCard(devbox, P, f"{'\U0001F4F1' if o == 'Android' else '\U0001F4BB'}  {n}", f"{ip} \u00b7 {o}", lambda ip=ip: selected.symmetric_difference_update({ip})).pack(fill="x", pady=3)

    def add_ip():
        d = tk.Toplevel(root); d.title("Add device"); d.configure(bg=P["bg"]); d.transient(root)
        e = tk.Entry(d, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", font=(FONT, 12)); e.pack(padx=20, pady=(18, 8), ipady=6); e.focus()
        def ok():
            ip = e.get().strip()
            if ip: devices[ip] = ("Manual", cfg["port"], "IP"); show_devices(dict(devices))
            d.destroy()
        RBtn(d, "Add", ok, P, primary=True).pack(pady=(0, 16)); e.bind("<Return>", lambda ev: ok())

    section("Nearby devices", [("Refresh", refresh), ("Add IP", add_ip)])
    devbox = tk.Frame(root, bg=P["bg"]); devbox.pack(fill="x", padx=22)

    # ---- transfers ----
    def new_ctl(label):
        c = H.Ctl(cfg, ask, lambda *a: ui(on_prog, c, *a), log, on_text, hist); c.label = label; active.append(c); return c

    def on_prog(c, d, t, sp, eta):
        card = cards.get(c) or make_card(c)
        card.title.configure(text=c.label); card.bar.set(d / t if t else 0)
        card.stat.configure(text=f"{H.hsize(d)} / {H.hsize(t)}  \u00b7  {H.hsize(sp)}/s  \u00b7  ETA {int(eta)}s" if sp else "Finishing\u2026")

    def make_card(c):
        card = XCard(xbox, P, c.label, c.cancel.set); card.pack(fill="x", pady=4); cards[c] = card; return card

    def finish(c, ok, msg):
        card = cards.get(c) or make_card(c)
        card.title.configure(text=c.label); card.bar.set(1 if ok else card.bar.cur, done=ok)
        card.stat.configure(text=("\u2713 " if ok else "\u2717 ") + msg, fg=P["ok"] if ok else P["err"])
        root.after(9000, lambda: (card.destroy(), cards.pop(c, None)))
        if ok and c.got and cfg["open_after"] and c.incoming: open_inbox()

    def hist(d, name, size, peer):
        cfg["history"] = (cfg["history"] + [[time.strftime("%Y-%m-%d %H:%M"), d, name, size, peer]])[-200:]

    def on_text(peer, txt):
        def f():
            root.clipboard_clear(); root.clipboard_append(txt)
            w = tk.Toplevel(root); w.title(f"Text from {peer}"); w.configure(bg=P["bg"])
            tk.Label(w, text=txt[:2000], bg=P["card"], fg=P["text"], wraplength=420, justify="left", padx=16, pady=14).pack(padx=16, pady=16)
            tk.Label(w, text="Copied to clipboard", bg=P["bg"], fg=P["ok"]).pack(pady=(0, 12))
        ui(f)

    def ask(h, n, total):
        ev, res, folder = threading.Event(), [], [None]
        def dlg():
            d = tk.Toplevel(root); d.title("Incoming"); d.configure(bg=P["bg"]); d.transient(root)
            tk.Label(d, text=f"{h.get('name')} wants to send", bg=P["bg"], fg=P["muted"]).pack(padx=24, pady=(18, 0))
            tk.Label(d, text=f"{n} item(s) \u00b7 {H.hsize(total)}", bg=P["bg"], fg=P["text"], font=(FONT, 15, "bold")).pack(padx=24)
            fl = tk.Label(d, text="Save to: " + cfg["folder"], bg=P["bg"], fg=P["muted"], font=(FONT, 9)); fl.pack(pady=(8, 2))
            def change():
                f = filedialog.askdirectory(parent=d)
                if f: folder[0] = f; fl.configure(text="Save to: " + f)
            RBtn(d, "Change folder", change, P, w=120, h=28).pack(pady=4)
            def pick(v): res.append(v); ev.set(); d.destroy()
            row = tk.Frame(d, bg=P["bg"]); row.pack(pady=16)
            for t, v, pr in (("Accept", "once", True), ("Always", "always", False), ("Decline", None, False)): RBtn(row, t, lambda v=v: pick(v), P, w=90, primary=pr).pack(side="left", padx=4)
            d.protocol("WM_DELETE_WINDOW", lambda: pick(None))
        ui(dlg); ev.wait(120)
        return (res[0] if res else None), folder[0]

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
                    if ctl.label != "Incoming": ui(finish, ctl, not ctl.declined, "Declined" if ctl.declined else (f"Received {ctl.got} file(s)" if ctl.got else "Text received"))
                except Exception as e:
                    if ctl.label != "Incoming": ui(finish, ctl, False, f"{e} (partial kept, will resume)")
                finally:
                    try: c.close()
                    except OSError: pass
                    active.remove(ctl); H.save_cfg(cfg)
            threading.Thread(target=run, daemon=True).start()

    def targets():
        return [(ip, devices[ip][1], devices[ip][0]) for ip in selected if ip in devices]

    def do_send(items):
        t = targets()
        if not items: return
        if not t: return toast("Select a device first")
        for ip, port, name in t:
            def w(ip=ip, port=port, name=name):
                ctl = new_ctl("\u2192 " + name); ctl.incoming = False
                try: H.send(ip, port, items, ctl, dest.get().strip()); ui(finish, ctl, True, "Sent")
                except InterruptedError: ui(finish, ctl, False, "Cancelled")
                except Exception as e: ui(finish, ctl, False, str(e))
                finally: active.remove(ctl); H.save_cfg(cfg)
            threading.Thread(target=w, daemon=True).start()

    def send_text():
        d = tk.Toplevel(root); d.title("Send text"); d.configure(bg=P["bg"]); d.transient(root)
        t = tk.Text(d, width=48, height=8, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", padx=10, pady=10); t.pack(padx=18, pady=(18, 8))
        try: t.insert("1.0", root.clipboard_get())
        except tk.TclError: pass
        RBtn(d, "Send", lambda: (do_send([H.text_item(t.get("1.0", "end").strip())]) if t.get("1.0", "end").strip() else None, d.destroy()), P, primary=True).pack(pady=(0, 16))

    def open_inbox():
        try: os.startfile(cfg["folder"]) if os.name == "nt" else os.system(f'xdg-open "{cfg["folder"]}" &')
        except OSError: pass

    def show_hist():
        w = tk.Toplevel(root); w.title("History"); w.configure(bg=P["bg"]); w.geometry("620x420")
        l = tk.Listbox(w, bg=P["card"], fg=P["text"], relief="flat", highlightthickness=0, font=(FONT, 10)); l.pack(fill="both", expand=True, padx=14, pady=14)
        for t, d, n, s, p in reversed(cfg["history"]): l.insert("end", f"{t}   {'\u2193 from' if d == 'in' else '\u2191 to'} {p}   {n}   ({H.hsize(s)})")

    def settings():
        w = tk.Toplevel(root); w.title("Settings"); w.configure(bg=P["bg"]); w.transient(root)
        v = {k: tk.StringVar(value=str(cfg[k])) for k in ("name", "folder", "port", "pin", "retries")}
        b = {k: tk.BooleanVar(value=cfg[k]) for k in ("auto", "visible", "open_after")}; th = tk.StringVar(value=cfg["theme"])
        rows = (("name", "Device name"), ("folder", "Default save folder"), ("port", "Port"), ("pin", "Shared PIN"), ("retries", "Auto-resume attempts"))
        for i, (k, l) in enumerate(rows):
            tk.Label(w, text=l, bg=P["bg"], fg=P["muted"]).grid(row=i, column=0, sticky="w", padx=16, pady=6)
            tk.Entry(w, textvariable=v[k], width=32, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat").grid(row=i, column=1, padx=6, ipady=4)
        RBtn(w, "Browse", lambda: v["folder"].set(filedialog.askdirectory(parent=w) or v["folder"].get()), P, w=70, h=28).grid(row=1, column=2, padx=8)
        for j, (k, l) in enumerate((("visible", "Visible to nearby devices"), ("auto", "Auto-accept everything"), ("open_after", "Open folder after receiving"))):
            tk.Checkbutton(w, text=l, variable=b[k], bg=P["bg"], fg=P["text"], selectcolor=P["card"], activebackground=P["bg"], activeforeground=P["text"]).grid(row=5 + j, column=1, sticky="w")
        tk.OptionMenu(w, th, "dark", "light").grid(row=8, column=1, sticky="w", pady=6); tk.Label(w, text="Theme (restart)", bg=P["bg"], fg=P["muted"]).grid(row=8, column=0, sticky="w", padx=16)
        RBtn(w, f"Clear trusted ({len(cfg['trusted'])})", lambda: (cfg["trusted"].clear(), H.save_cfg(cfg)), P, w=140, h=28).grid(row=9, column=1, sticky="w", pady=6)
        def ok():
            try: port, ret = int(v["port"].get()), int(v["retries"].get())
            except ValueError: return toast("Port/attempts must be numbers")
            cfg.update(name=v["name"].get(), folder=v["folder"].get(), port=port, pin=v["pin"].get(), retries=ret, theme=th.get(), **{k: b[k].get() for k in b})
            os.makedirs(cfg["folder"], exist_ok=True); H.save_cfg(cfg); w.destroy(); toast("Saved (restart to apply port/theme)")
        RBtn(w, "Save", ok, P, primary=True).grid(row=10, column=1, pady=14, sticky="w")

    section("Send", [])
    row = tk.Frame(root, bg=P["bg"]); row.pack(fill="x", padx=22)
    for t, c in (("\U0001F4C4  Files", lambda: do_send(H.collect(filedialog.askopenfilenames()))), ("\U0001F4C1  Folder", lambda: do_send(H.collect([filedialog.askdirectory()]))), ("\u270F\uFE0F  Text", send_text)):
        RBtn(row, t, c, P, w=130, h=42, primary=True).pack(side="left", padx=(0, 8))
    dest = tk.Entry(root, bg=P["card"], fg=P["text"], insertbackground=P["text"], relief="flat", font=(FONT, 10)); dest.pack(fill="x", padx=22, pady=(10, 0), ipady=7)
    tk.Label(root, text="Optional: subfolder to create on the receiving device (e.g. Photos/Trip)", bg=P["bg"], fg=P["muted"], font=(FONT, 8)).pack(anchor="w", padx=24)
    section("Transfers", [("Cancel all", lambda: [c.cancel.set() for c in active])])
    xbox = tk.Frame(root, bg=P["bg"]); xbox.pack(fill="x", padx=22)
    foot = tk.Frame(root, bg=P["bg"]); foot.pack(side="bottom", fill="x", padx=22, pady=16)
    for t, c in (("History", show_hist), ("Inbox", open_inbox), ("Settings", settings)): RBtn(foot, t, c, P, w=90).pack(side="left", padx=(0, 8))

    pump(); threading.Thread(target=server, daemon=True).start(); threading.Thread(target=H.discovery_server, args=(cfg,), daemon=True).start()
    refresh(); root.mainloop(); H.save_cfg(cfg)


if __name__ == "__main__":
    main()
