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
