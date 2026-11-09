"""Windows-only niceties via ctypes (no extra packages). Every function is a safe no-op on other systems or on failure."""
import ctypes, os

IS_WIN = os.name == "nt"


def dpi_aware():
    """Opt out of Windows bitmap scaling so text and icons are sharp on high-DPI screens."""
    if not IS_WIN: return
    try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try: ctypes.windll.user32.SetProcessDPIAware()
        except Exception: pass


def style_titlebar(win, bg, fg, border, dark):
    """Match the native title bar / border to the theme (dark mode: Win10 20H1+; custom colours: Win11)."""
    if not IS_WIN: return
    try:
        win.update_idletasks(); hwnd = ctypes.windll.user32.GetParent(win.winfo_id()); dwm = ctypes.windll.dwmapi
        def put(attr, val): v = ctypes.c_int(val); dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        rgb = lambda h: int(h[5:7], 16) << 16 | int(h[3:5], 16) << 8 | int(h[1:3], 16)   # COLORREF 0x00BBGGRR
        put(20, 1 if dark else 0); put(35, rgb(bg)); put(36, rgb(fg)); put(34, rgb(border))
    except Exception: pass


def _drop_paths(shell32, hdrop):
    n = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0); out = []
    for i in range(n):
        ln = shell32.DragQueryFileW(hdrop, i, None, 0); buf = ctypes.create_unicode_buffer(ln + 1)
        shell32.DragQueryFileW(hdrop, i, buf, ln + 1); out.append(buf.value)
    return out


def hook_dropfiles(root, callback):
    """Let files/folders be dragged from Explorer onto the window; callback(list_of_paths) runs on the GUI thread."""
    if not IS_WIN: return False
    try:
        from ctypes import wintypes
        u32, s32 = ctypes.windll.user32, ctypes.windll.shell32
        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        setl = getattr(u32, "SetWindowLongPtrW", None) or u32.SetWindowLongW
        setl.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]; setl.restype = ctypes.c_ssize_t
        call = u32.CallWindowProcW; call.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]; call.restype = ctypes.c_ssize_t
        s32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]; s32.DragQueryFileW.restype = wintypes.UINT
        s32.DragFinish.argtypes = [wintypes.HANDLE]; s32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        root.update_idletasks(); hwnd = int(root.wm_frame(), 16); old = [0]

        def proc(h, msg, w, l):
            if msg == 0x233:   # WM_DROPFILES
                try: files = _drop_paths(s32, w)
                finally: s32.DragFinish(w)
                try: callback(files)
                except Exception: pass
                return 0
            return call(old[0], h, msg, w, l)
        cb = WNDPROC(proc); root._dnd_keepalive = cb
        old[0] = setl(hwnd, -4, ctypes.cast(cb, ctypes.c_void_p).value)   # GWL_WNDPROC
        s32.DragAcceptFiles(hwnd, True); return True
    except Exception:
        return False


def clipboard_files():
    """Paths of files copied in Explorer (Ctrl+C); [] if the clipboard has none."""
    if not IS_WIN: return []
    try:
        from ctypes import wintypes
        u32, s32 = ctypes.windll.user32, ctypes.windll.shell32
        u32.OpenClipboard.argtypes = [wintypes.HWND]; u32.GetClipboardData.argtypes = [wintypes.UINT]; u32.GetClipboardData.restype = wintypes.HANDLE
        s32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]; s32.DragQueryFileW.restype = wintypes.UINT
        if not u32.OpenClipboard(None): return []
        try:
            h = u32.GetClipboardData(15)   # CF_HDROP
            return _drop_paths(s32, h) if h else []
        finally: u32.CloseClipboard()
    except Exception:
        return []


# ---------- Windows Firewall: the usual reason a phone can't find or reach this PC ----------
RULE = "HotShare"


def exe_path():
    import sys
    return os.path.abspath(sys.executable)


def _netsh(*args):
    import subprocess
    return subprocess.run(["netsh", "advfirewall", "firewall", *args], capture_output=True, text=True, timeout=10, creationflags=0x08000000)   # no console window


def firewall_ok():
    """True if an inbound allow rule for this program exists (or we cannot tell: then we do not nag)."""
    if not IS_WIN: return True
    try:
        r = _netsh("show", "rule", f"name={RULE}", "verbose")
        return r.returncode == 0 and exe_path().lower() in (r.stdout or "").lower()
    except Exception:
        return True


def fix_firewall():
    """Replace any (possibly blocking) rules for this program by one inbound allow rule for ALL networks. Shows ONE Windows approval prompt."""
    if not IS_WIN: return False
    exe = exe_path()
    cmd = (f'netsh advfirewall firewall delete rule name=all program="{exe}" & netsh advfirewall firewall delete rule name="{RULE}" & '
           f'netsh advfirewall firewall add rule name="{RULE}" dir=in action=allow program="{exe}" enable=yes profile=any')
    try: return ctypes.windll.shell32.ShellExecuteW(None, "runas", "cmd.exe", f"/c {cmd}", None, 0) > 32
    except Exception: return False


# ---------- taskbar button progress (green bar on the app's taskbar icon) ----------
class Taskbar:
    """ITaskbarList3 through ctypes (no extra packages). Every method is a safe no-op on other systems or on failure."""
    NONE, INDETERMINATE, NORMAL, ERROR, PAUSED = 0, 1, 2, 4, 8

    def __init__(s, win):
        s.ok, s.win, s.hwnd, s.ptr, s.vt, s.state, s.val = False, win, None, None, None, None, None
        if not IS_WIN: return
        try:
            from ctypes import wintypes
            class GUID(ctypes.Structure): _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
            def guid(a, b, c, *d): g = GUID(); g.a, g.b, g.c = a, b, c; g.d[:] = d; return g
            ole = ctypes.windll.ole32; ole.CoInitialize(None)
            clsid = guid(0x56FDF344, 0xFD6D, 0x11D0, 0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
            iid = guid(0xEA1AFB91, 0x9E28, 0x4B86, 0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF)
            ole.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
            p = ctypes.c_void_p()
            if ole.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(p)) != 0 or not p.value: return
            s.ptr = p; s.vt = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            hr = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(s.vt[3]); hr(p)                                   # HrInit
            s._val = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)(s.vt[9])     # SetProgressValue
            s._st = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.c_int)(s.vt[10])                               # SetProgressState
            s.ok = True
        except Exception:
            s.ok = False

    def _h(s):
        if s.hwnd is None:
            try: s.win.update_idletasks(); s.hwnd = ctypes.windll.user32.GetParent(s.win.winfo_id()) or s.win.winfo_id()
            except Exception: return None
        return s.hwnd

    def set(s, state, done=0, total=0):
        """state: NONE / INDETERMINATE / NORMAL / ERROR / PAUSED. done/total only matter for the bar states."""
        if not s.ok: return
        try:
            h = s._h()
            if not h: return
            if state != s.state and s._st(s.ptr, h, state) == 0: s.state = state       # not cached when Windows refused (e.g. the taskbar button does not exist yet)
            if state in (s.NORMAL, s.ERROR, s.PAUSED) and total:
                v = min(1000, int(1000 * done / total))
                if v != s.val and s._val(s.ptr, h, v, 1000) == 0: s.val = v
        except Exception: pass
