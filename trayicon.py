"""系统托盘图标（零依赖）：独立线程创建隐藏窗口 + Shell_NotifyIconW。

事件模型（与 reminder 相同的队列模式，线程不触碰 tkinter）：
    events.put(("show",))  —— 用户左键点击/双击托盘图标
    events.put(("quit",))  —— 用户在托盘菜单选择"退出"

托盘失败（非 Windows 等）时 failed=True，调用方应退化为"关窗即退出"。
"""
from __future__ import annotations

import ctypes
import os
import queue
import struct
import tempfile
import threading
from ctypes import wintypes

# ---- 常量 ----
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_NULL = 0x0000
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10
NIIF_INFO = 0x1
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800

MENU_SHOW, MENU_QUIT = 1, 2


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
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
        ("guidItem", _GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                                           wintypes.UINT, wintypes.WPARAM,
                                           wintypes.LPARAM)),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


WNDPROC = WNDCLASSEXW._fields_[2][1]
LRESULT = ctypes.c_ssize_t


def _build_ico_bytes(size: int = 16) -> bytes:
    """纯 Python 生成 16x16 32bpp ICO：橙色圆角方块 + 白色感叹号。"""
    def inside(x: int, y: int) -> bool:
        return min(x, size - 1 - x) + min(y, size - 1 - y) >= 3

    def is_mark(x: int, y: int) -> bool:
        return (6 <= x <= 9 and 2 <= y <= 9) or (6 <= x <= 9 and 11 <= y <= 13)

    xor = bytearray()
    for y in range(size - 1, -1, -1):          # BMP 行序自底向上
        for x in range(size):
            if inside(x, y):
                xor += bytes((0xFF, 0xFF, 0xFF, 0xFF) if is_mark(x, y)
                             else (0x22, 0x7E, 0xE6, 0xFF))   # BGRA 橙 #E67E22
            else:
                xor += bytes((0, 0, 0, 0))
    and_mask = bytes(4 * size)                 # 每行 2 字节补齐 4 字节，全 0 = 不透明
    image_size = 40 + len(xor) + len(and_mask)
    icondir = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, image_size, 22)
    bmih = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                       image_size, 0, 0, 0, 0)
    return bytes(icondir + entry + bmih) + bytes(xor) + and_mask


def icon_file() -> str:
    """生成（若需要）并返回托盘/窗口图标文件路径。"""
    path = os.path.join(tempfile.gettempdir(), "homework_deadline.ico")
    if not os.path.exists(path):
        try:
            with open(path, "wb") as f:
                f.write(_build_ico_bytes())
        except OSError:
            path = ""   # 加载失败时回退到系统默认图标
    return path


class TrayIcon:
    """托盘图标。events 为 queue.Queue，主线程轮询 ("show",)/("quit",) 事件。"""

    _CLASS_NAME = "HomeworkDeadlineTrayWnd"
    _ICON_ID = 1
    _MSG_CALLBACK = WM_APP + 1
    _MSG_BALLOON = WM_APP + 2

    def __init__(self, tip: str, events: "queue.Queue[tuple]"):
        self.tip = tip
        self.events = events
        self.failed = False
        self._hwnd: int | None = None
        self._balloon_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._wndproc = WNDPROC(self._on_message)   # 防止回调被回收
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="TrayIcon")
        self._thread.start()

    @property
    def ok(self) -> bool:
        return not self.failed and self._thread.is_alive()

    # ---- 供主线程调用 ----
    def show_balloon(self, title: str, message: str) -> None:
        """气泡通知（经托盘线程执行）。"""
        if not self.ok or self._hwnd is None:
            return
        self._balloon_q.put((title, message))
        ctypes.windll.user32.PostMessageW(self._hwnd, self._MSG_BALLOON, 0, 0)

    def stop(self) -> None:
        """移除托盘图标并结束线程。"""
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        self._thread.join(timeout=2)

    # ---- 托盘线程内部 ----
    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        try:
            self._set_prototypes(user32)
            hinstance = kernel32.GetModuleHandleW(None)

            wc = WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinstance
            wc.lpszClassName = self._CLASS_NAME
            if not user32.RegisterClassExW(ctypes.byref(wc)):
                raise OSError("RegisterClassExW failed")

            # 永不显示的普通隐藏窗口（消息专用窗口不保证收到托盘回调）
            self._hwnd = user32.CreateWindowExW(
                0, self._CLASS_NAME, "homework_tray_hidden", 0,
                0, 0, 0, 0, None, None, hinstance, None)
            if not self._hwnd:
                raise OSError("CreateWindowExW failed")

            if not self._add_icon():
                raise OSError("Shell_NotifyIconW(NIM_ADD) failed")

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            self.failed = True

    @staticmethod
    def _set_prototypes(user32) -> None:
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HWND, ctypes.c_void_p, wintypes.HINSTANCE,
            ctypes.c_void_p]
        user32.DefWindowProcW.restype = LRESULT
        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
        user32.GetMessageW.restype = ctypes.c_int
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                      wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      wintypes.UINT]
        user32.CreatePopupMenu.restype = wintypes.HANDLE
        user32.TrackPopupMenu.restype = ctypes.c_int
        user32.TrackPopupMenu.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                          ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, wintypes.HWND,
                                          ctypes.c_void_p]
        user32.AppendMenuW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       ctypes.c_size_t, wintypes.LPCWSTR]

    def _load_icon(self):
        user32 = ctypes.windll.user32
        path = icon_file()
        hicon = None
        if path:
            hicon = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                      LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not hicon:
            hicon = user32.LoadImageW(None,
                                      "#%d" % IDI_APPLICATION, IMAGE_ICON,
                                      0, 0, LR_DEFAULTSIZE)
        return hicon

    def _add_icon(self) -> bool:
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = self._ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self._MSG_CALLBACK
        nid.hIcon = self._load_icon()
        nid.szTip = self.tip
        return bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))

    def _remove_icon(self) -> None:
        if not self._hwnd:
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = self._ICON_ID
        ctypes.windll.shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))

    def _drain_balloon_queue(self) -> None:
        shell32 = ctypes.windll.shell32
        while True:
            try:
                title, message = self._balloon_q.get_nowait()
            except queue.Empty:
                return
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._hwnd
            nid.uID = self._ICON_ID
            nid.uFlags = NIF_INFO
            nid.szInfo = message
            nid.szInfoTitle = title
            nid.dwInfoFlags = NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    def _show_menu(self) -> None:
        user32 = ctypes.windll.user32
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        user32.AppendMenuW(hmenu, MF_STRING, MENU_SHOW, "显示主窗口")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, MENU_QUIT, "退出")
        user32.SetForegroundWindow(self._hwnd)
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(hmenu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(hmenu)
        if cmd == MENU_SHOW:
            self.events.put(("show",))
        elif cmd == MENU_QUIT:
            self.events.put(("quit",))

    def _on_message(self, hwnd, msg, wparam, lparam) -> LRESULT:
        if msg == self._MSG_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.events.put(("show",))
            elif event == WM_RBUTTONUP:
                self._show_menu()
            return 0
        if msg == self._MSG_BALLOON:
            self._drain_balloon_queue()
            return 0
        if msg == WM_DESTROY:
            self._remove_icon()
            ctypes.windll.user32.PostQuitMessage(0)
            return 0
        if msg == WM_CLOSE:
            ctypes.windll.user32.DestroyWindow(hwnd)
            return 0
        return ctypes.windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
