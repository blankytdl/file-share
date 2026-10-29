"""Vector icon set drawn on a 24x24 grid (no fonts/emoji needed, so nothing renders as a broken box).
Primitives: ("l", pts) open polyline | ("p", pts) closed outline | ("o", cx, cy, r) circle | ("d", cx, cy, r) filled dot | ("a", cx, cy, r, start, extent) arc (tk angles: ccw from 3 o'clock)."""
ICONS = {
    "folder":   [("p", [(3, 6.5), (9.5, 6.5), (11.5, 9), (21, 9), (21, 19), (3, 19)])],
    "file":     [("p", [(6, 3), (14, 3), (19, 8), (19, 21), (6, 21)]), ("l", [(14, 3), (14, 8), (19, 8)]), ("l", [(9, 13), (16, 13)]), ("l", [(9, 17), (14, 17)])],
    "text":     [("l", [(4, 6), (20, 6)]), ("l", [(4, 12), (20, 12)]), ("l", [(4, 18), (14, 18)])],
    "apps":     [("p", [(4, 4), (10, 4), (10, 10), (4, 10)]), ("p", [(14, 4), (20, 4), (20, 10), (14, 10)]), ("p", [(4, 14), (10, 14), (10, 20), (4, 20)]), ("p", [(14, 14), (20, 14), (20, 20), (14, 20)])],
    "radar":    [("o", 12, 12, 4), ("o", 12, 12, 8.5), ("d", 12, 12, 1.6)],
    "sliders":  [("l", [(3, 7), (21, 7)]), ("d", 9, 7, 2.6), ("l", [(3, 17), (21, 17)]), ("d", 15, 17, 2.6)],
    "history":  [("o", 12, 12, 9), ("l", [(12, 7), (12, 12), (16, 14)])],
    "tray":     [("p", [(3, 13), (7, 5), (17, 5), (21, 13), (21, 19), (3, 19)]), ("l", [(3, 13), (9, 13), (10.5, 15.5), (13.5, 15.5), (15, 13), (21, 13)])],
    "close":    [("l", [(6, 6), (18, 18)]), ("l", [(18, 6), (6, 18)])],
    "check":    [("l", [(5, 13), (10, 18), (19, 7)])],
    "plus":     [("l", [(12, 5), (12, 19)]), ("l", [(5, 12), (19, 12)])],
    "phone":    [("p", [(7, 2.5), (17, 2.5), (17, 21.5), (7, 21.5)]), ("l", [(10.5, 18.5), (13.5, 18.5)])],
    "laptop":   [("p", [(4, 5), (20, 5), (20, 16), (4, 16)]), ("l", [(2, 19.5), (22, 19.5)])],
    "bluetooth": [("l", [(7, 8), (17, 16), (12, 20.5), (12, 3.5), (17, 8), (7, 16)])],
    "download": [("l", [(12, 4), (12, 16)]), ("l", [(6, 11), (12, 17), (18, 11)]), ("l", [(5, 20), (19, 20)])],
    "upload":   [("l", [(12, 20), (12, 8)]), ("l", [(6, 13), (12, 7), (18, 13)]), ("l", [(5, 4), (19, 4)])],
    "send":     [("l", [(4, 12), (20, 12)]), ("l", [(14, 6), (20, 12), (14, 18)])],
    "pause":    [("l", [(9, 5), (9, 19)]), ("l", [(15, 5), (15, 19)])],
    "play":     [("p", [(8, 5), (19, 12), (8, 19)])],
    "trash":    [("l", [(4, 7), (20, 7)]), ("p", [(6, 7), (7, 20), (17, 20), (18, 7)]), ("l", [(10, 4), (14, 4)]), ("l", [(10, 11), (10, 16)]), ("l", [(14, 11), (14, 16)])],
    "exchange": [("l", [(4, 8), (19, 8)]), ("l", [(15, 4), (19, 8), (15, 12)]), ("l", [(20, 16), (5, 16)]), ("l", [(9, 12), (5, 16), (9, 20)])],
}


def draw_tk(c, name, x, y, size, color, width=2, tags=()):
    """Draw icon `name` on tk Canvas c with top-left (x, y) and edge length `size`; returns item ids."""
    k = size / 24.0; ids = []; w = max(1.5, width * k)
    P = lambda pts: [v for px, py in pts for v in (x + px * k, y + py * k)]
    for p in ICONS[name]:
        t = p[0]
        if t == "l": ids.append(c.create_line(*P(p[1]), fill=color, width=w, capstyle="round", joinstyle="round", tags=tags))
        elif t == "p": ids.append(c.create_polygon(*P(p[1]), outline=color, fill="", width=w, joinstyle="round", tags=tags))
        elif t == "o": ids.append(c.create_oval(x + (p[1] - p[3]) * k, y + (p[2] - p[3]) * k, x + (p[1] + p[3]) * k, y + (p[2] + p[3]) * k, outline=color, width=w, tags=tags))
        elif t == "d": ids.append(c.create_oval(x + (p[1] - p[3]) * k, y + (p[2] - p[3]) * k, x + (p[1] + p[3]) * k, y + (p[2] + p[3]) * k, fill=color, outline=color, tags=tags))
    return ids
