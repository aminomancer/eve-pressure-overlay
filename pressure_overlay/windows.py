import ctypes
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from threading import Event, Thread

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

TOPMOST = wintypes.HWND(-1)
NO_SIZE = 0x0001
NO_MOVE = 0x0002
NO_ACTIVATE = 0x0010
FRAME_CHANGED = 0x0020
SHOW_WINDOW = 0x0040
EX_TRANSPARENT = 0x00000020
EX_LAYERED = 0x00080000
EX_NO_ACTIVATE = 0x08000000
EX_STYLE = -20


class BlendFunction(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WindowClass(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NotifyIconData(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class IconInfo(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
user32.GetDC.argtypes = (wintypes.HWND,)
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = (wintypes.HWND, wintypes.HDC)
user32.UpdateLayeredWindow.argtypes = (
    wintypes.HWND,
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    ctypes.POINTER(wintypes.SIZE),
    wintypes.HDC,
    ctypes.POINTER(wintypes.POINT),
    wintypes.DWORD,
    ctypes.POINTER(BlendFunction),
    wintypes.DWORD,
)
gdi32.CreateCompatibleDC.argtypes = (wintypes.HDC,)
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.SelectObject.argtypes = (wintypes.HDC, wintypes.HGDIOBJ)
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteDC.argtypes = (wintypes.HDC,)
gdi32.AddFontResourceExW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p)
user32.SetWindowPos.argtypes = (
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetForegroundWindow.argtypes = ()
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.MonitorFromRect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
user32.MonitorFromRect.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
shell32.SHGetFolderPathW.argtypes = (
    wintypes.HWND,
    ctypes.c_int,
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
)
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
kernel32.LocalFree.restype = ctypes.c_void_p
_CRYPT_ARGS = (
    ctypes.POINTER(DataBlob),
    ctypes.c_void_p,
    ctypes.POINTER(DataBlob),
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(DataBlob),
)
crypt32.CryptProtectData.argtypes = _CRYPT_ARGS
crypt32.CryptUnprotectData.argtypes = _CRYPT_ARGS
user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = (ctypes.POINTER(WindowClass),)
user32.CreateWindowExW.argtypes = (
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
)
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = (wintypes.HWND,)
user32.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = (wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR)
user32.TrackPopupMenu.argtypes = (
    wintypes.HMENU,
    wintypes.UINT,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    ctypes.c_void_p,
)
user32.TrackPopupMenu.restype = ctypes.c_int
user32.DestroyMenu.argtypes = (wintypes.HMENU,)
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.CreateIconIndirect.argtypes = (ctypes.POINTER(IconInfo),)
user32.CreateIconIndirect.restype = wintypes.HICON
user32.DestroyIcon.argtypes = (wintypes.HICON,)
shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NotifyIconData))
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
gdi32.CreateDIBSection.argtypes = (
    wintypes.HDC,
    ctypes.POINTER(BitmapInfoHeader),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
)
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = (
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
)
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.DeleteObject.argtypes = (wintypes.HANDLE,)


@dataclass(frozen=True)
class EveClient:
    handle: int
    pilot: str
    bounds: tuple[int, int, int, int]


def process_name(pid):
    process = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return None
    try:
        path = ctypes.create_unicode_buffer(32768)
        length = wintypes.DWORD(len(path))
        if kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
            return Path(path.value).name.casefold()
        return None
    finally:
        kernel32.CloseHandle(process)


class EveForeground:
    """Read the foreground window's identity and geometry through Windows."""

    def __init__(self):
        self.last_window = None
        self.last_is_eve = False
        self.last_client = None

    def current(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            self.last_window = None
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Our own menu or dialog in front still counts as being in the game.
        if pid.value == kernel32.GetCurrentProcessId():
            return self.last_client
        self.last_client = self.client(hwnd, pid)
        return self.last_client

    def client(self, hwnd, pid):
        identity = (hwnd, pid.value)
        if identity != self.last_window:
            self.last_window = identity
            self.last_is_eve = process_name(pid.value) == "exefile.exe"
        if not self.last_is_eve:
            return None

        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, title, len(title))
        if title.value == "EVE":
            pilot = ""
        elif title.value.startswith("EVE - "):
            pilot = title.value[len("EVE - ") :].strip()
        else:
            return None
        rect, origin = wintypes.RECT(), wintypes.POINT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)) or not user32.ClientToScreen(
            hwnd, ctypes.byref(origin)
        ):
            return None
        if rect.right < 1 or rect.bottom < 1:
            return None
        return EveClient(
            hwnd, pilot, (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
        )


DOCUMENTS, START_MENU, DESKTOP = 5, 2, 0x10  # CSIDL values


def shell_folder(csidl, fallback):
    buffer = ctypes.create_unicode_buffer(260)
    result = shell32.SHGetFolderPathW(None, csidl, None, 0, buffer)
    return Path(buffer.value) if result == 0 else fallback


def game_log_folder():
    return shell_folder(DOCUMENTS, Path.home() / "Documents") / "EVE" / "logs" / "Gamelogs"


def write_shortcut(link, target, arguments, folder, icon):
    """Python can't reach the shell's IShellLink without pywin32, so PowerShell writes it."""
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:EP_LINK); "
        "$s.TargetPath = $env:EP_TARGET; $s.Arguments = $env:EP_ARGS; "
        "$s.WorkingDirectory = $env:EP_FOLDER; $s.IconLocation = $env:EP_ICON; $s.Save()"
    )
    env = os.environ | {
        "EP_LINK": str(link),
        "EP_TARGET": str(target),
        "EP_ARGS": arguments,
        "EP_FOLDER": str(folder),
        "EP_ICON": str(icon),
    }
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=env,
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _crypt(function, data):
    buffer = ctypes.create_string_buffer(data, len(data))
    source = DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output = DataBlob()
    # CRYPTPROTECT_UI_FORBIDDEN
    if not function(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))


def protect(data):
    """Encrypt bytes so only the current Windows user can read them."""
    return _crypt(crypt32.CryptProtectData, data)


def unprotect(data):
    return _crypt(crypt32.CryptUnprotectData, data)


def window_handle(widget):
    return user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT


def overlay_style(hwnd, *, click_through=False):
    previous = user32.GetWindowLongW(hwnd, EX_STYLE)
    style = previous | EX_LAYERED | EX_NO_ACTIVATE
    style = style | EX_TRANSPARENT if click_through else style & ~EX_TRANSPARENT
    ctypes.set_last_error(0)
    if not user32.SetWindowLongW(hwnd, EX_STYLE, style) and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    user32.SetWindowPos(hwnd, TOPMOST, 0, 0, 0, 0, NO_SIZE | NO_MOVE | NO_ACTIVATE | FRAME_CHANGED)


def draw_layered(hwnd, width, height, pixels):
    """Show premultiplied top-down BGRA pixels as the whole window, with per-pixel alpha."""
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    header = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), width, -height, 1, 32)
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(memory, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    try:
        if not bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        ctypes.memmove(bits, pixels, len(pixels))
        previous = gdi32.SelectObject(memory, bitmap)
        size, origin = wintypes.SIZE(width, height), wintypes.POINT(0, 0)
        blend = BlendFunction(0, 0, 255, 1)  # AC_SRC_OVER with AC_SRC_ALPHA
        drawn = user32.UpdateLayeredWindow(
            hwnd,
            screen,
            None,
            ctypes.byref(size),
            memory,
            ctypes.byref(origin),
            0,
            ctypes.byref(blend),
            2,  # ULW_ALPHA
        )
        error = ctypes.get_last_error()
        gdi32.SelectObject(memory, previous)
        if not drawn:
            raise ctypes.WinError(error)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def private_font(path):
    """Make a font file usable by name in this process only."""
    return gdi32.AddFontResourceExW(str(path), 0x10, None) > 0  # FR_PRIVATE


def position(hwnd):
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top


def cursor_position():
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def work_area(x, y, width=1, height=1):
    rect = wintypes.RECT(x, y, x + width, y + height)
    monitor = user32.MonitorFromRect(ctypes.byref(rect), 2)  # MONITOR_DEFAULTTONEAREST
    info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
    user32.GetMonitorInfoW(monitor, ctypes.byref(info))
    return info.rcWork


def move_window(hwnd, x, y):
    user32.SetWindowPos(hwnd, TOPMOST, x, y, 0, 0, NO_SIZE | NO_ACTIVATE)


def show_window(hwnd):
    user32.SetWindowPos(hwnd, TOPMOST, 0, 0, 0, 0, NO_SIZE | NO_MOVE | NO_ACTIVATE | SHOW_WINDOW)


def hide_window(hwnd):
    user32.ShowWindow(hwnd, 0)  # SW_HIDE


def meter_icon(size=32):
    """Three short meter bars on a dark tile, as a 32-bit alpha icon."""
    header = BitmapInfoHeader(ctypes.sizeof(BitmapInfoHeader), size, -size, 1, 32)
    bits = ctypes.c_void_p()
    color = gdi32.CreateDIBSection(None, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
    if not color:
        return None
    pixels = bytearray(size * size * 4)
    bars = ((7, 11, 26, 0x8CCFEF), (14, 18, 20, 0xEDBB76), (21, 25, 23, 0xF59487))
    for y in range(1, size - 1):
        for x in range(1, size - 1):
            rgb = 0x101419
            for top, bottom, right, bar in bars:
                if top <= y < bottom and 6 <= x < right:
                    rgb = bar
            offset = (y * size + x) * 4
            pixels[offset : offset + 4] = bytes((rgb & 0xFF, rgb >> 8 & 0xFF, rgb >> 16, 255))
    ctypes.memmove(bits, bytes(pixels), len(pixels))
    mask = gdi32.CreateBitmap(size, size, 1, 1, bytes(size * size // 8))
    icon = user32.CreateIconIndirect(ctypes.byref(IconInfo(True, 0, 0, mask, color)))
    gdi32.DeleteObject(mask)
    gdi32.DeleteObject(color)
    return icon


class TrayIcon:
    """Notification-area icon whose menu picks are posted to events."""

    MOVE, SETTINGS, QUIT = 1, 2, 3
    CALLBACK = 0x8001  # WM_APP + 1

    def __init__(self, tooltip):
        self.events = SimpleQueue()
        self.moving = False
        self.tooltip = tooltip
        self.hwnd = None
        self.icon = None
        self.ready = Event()
        Thread(target=self.run, daemon=True).start()
        self.ready.wait(2)

    def notify(self, action):
        data = NotifyIconData(cbSize=ctypes.sizeof(NotifyIconData), hWnd=self.hwnd, uID=1)
        data.uFlags = 0x1 | 0x2 | 0x4  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = self.CALLBACK
        data.hIcon = self.icon
        data.szTip = self.tooltip[:127]
        shell32.Shell_NotifyIconW(action, ctypes.byref(data))

    def set_tooltip(self, tooltip):
        if tooltip != self.tooltip and self.hwnd:
            self.tooltip = tooltip
            self.notify(1)  # NIM_MODIFY

    def show_menu(self):
        """Open the tray menu at the cursor, as if the tray icon was right-clicked."""
        if self.hwnd:
            user32.PostMessageW(self.hwnd, self.CALLBACK, 0, 0x0205)

    def menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0x8 if self.moving else 0, self.MOVE, "Move overlay")
        user32.AppendMenuW(menu, 0, self.SETTINGS, "Settings…")
        user32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
        user32.AppendMenuW(menu, 0, self.QUIT, "Quit")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # The menu only closes on an outside click while its owner is in the foreground.
        user32.SetForegroundWindow(self.hwnd)
        flags = 0x0100 | 0x0080 | 0x0002  # TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON
        choice = user32.TrackPopupMenu(menu, flags, point.x, point.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if choice:
            self.events.put(choice)

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == self.CALLBACK and lparam in (0x0202, 0x0205):  # left or right button up
            self.menu()
            return 0
        if message == self.taskbar_created:
            self.notify(0)  # NIM_ADD after Explorer restarts
            return 0
        if message == 0x0010:  # WM_CLOSE
            self.notify(2)  # NIM_DELETE
            user32.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:  # WM_DESTROY
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def run(self):
        self.procedure = WNDPROC(self.window_proc)
        instance = kernel32.GetModuleHandleW(None)
        name = "EvePressureTray"
        window_class = WindowClass(cbSize=ctypes.sizeof(WindowClass), lpfnWndProc=self.procedure)
        window_class.hInstance = instance
        window_class.lpszClassName = name
        user32.RegisterClassExW(ctypes.byref(window_class))
        self.taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self.hwnd = user32.CreateWindowExW(
            0, name, "EVE Pressure", 0, 0, 0, 0, 0, None, None, instance, None
        )
        self.icon = meter_icon() or user32.LoadIconW(None, 32512)  # IDI_APPLICATION
        self.notify(0)  # NIM_ADD
        self.ready.set()
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
        if self.icon:
            user32.DestroyIcon(self.icon)

    def close(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, 0x0010, 0, 0)


class LockHotkey:
    """Register a global shortcut without observing or sending keyboard input."""

    def __init__(self):
        self.events = SimpleQueue()
        self.thread_id = None
        Thread(target=self.run, daemon=True).start()

    def run(self):
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.thread_id = kernel32.GetCurrentThreadId()
        shortcuts = ((0x0003, "Ctrl+Alt+F10"), (0x0006, "Ctrl+Shift+F10"))
        for modifiers, name in shortcuts:
            if user32.RegisterHotKey(None, 1, modifiers | 0x4000, 0x79):  # MOD_NOREPEAT, VK_F10
                self.events.put(name)
                break
        else:
            self.events.put("")
            return
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312:  # WM_HOTKEY
                    self.events.put(None)
        finally:
            user32.UnregisterHotKey(None, 1)

    def close(self):
        if self.thread_id is not None:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)  # WM_QUIT
