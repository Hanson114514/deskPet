"""Win32 API 薄绑定层：**全局唯一的 `ctypes` 出口**。

## 为什么要单独一层

task12 引入了本项目第一批 Win32 调用（`RegisterHotKey` / `GetMessageW`）。
把它们收在一处有三个好处：

1. **非 Windows 上导入不炸**：本模块只在 Windows 上加载 DLL，
   没加载到时下面那些函数**照常存在**，只是返回"不可用"（`False` / `None`）。
   于是 `hotkey` / `hotkey_filter` 的纯逻辑部分在任何平台上都能被自检导入。
2. **`wintypes` 只管类型、不管原型**（实测）：结构体（`POINT` / `MSG`）
   它的定义是对的，但函数原型一个都没有 —— 直接调 `ctypes.windll.*`
   有两个经典坑：① 句柄被按 32 位 `int` **截断**（64 位下 `HWND` 是高地址）；
   ② `GetMessageW` 的返回值是 `BOOL`，**-1 也是"真"**，不声明签名的写法会漏掉它。
   本模块用 `ctypes.WinDLL(..., use_last_error=True)` + 显式 `argtypes` /
   `restype` 把这两件事一次性钉死。
3. **测试可以打桩**：自检里 `monkeypatch` 本模块的函数就能验"注册失败 →
   沿候选链回退""占用 → 1409"这些分支，不需要真的占用一个系统热键。

⚠ 本模块**只依赖标准库**，与 `logger` 的崩溃兜底同一条纪律。
"""

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Final

logger = logging.getLogger(__name__)

#: 当前平台是不是 Windows（DLL 真的加载上了才算）。
IS_WINDOWS: Final[bool] = False

# ------------------------------------------------------------------ 常量

#: 注册时"在这个窗口线程的消息队列里投一条 `WM_HOTKEY`"（`hWnd` 给 `None` 时用）。
MOD_NOREPEAT: Final[int] = 0x4000

#: 收到全局热键的消息号。
WM_HOTKEY: Final[int] = 0x0312
#: `PostThreadMessageW` 一条它，`GetMessageW` 就返回 0（线程收工）。
WM_QUIT: Final[int] = 0x0012

#: 系统级错误码：热键已被占用（文案见 task12 §4）。
ERROR_HOTKEY_ALREADY_REGISTERED: Final[int] = 1409

#: 热键编号（同一个进程内唯一即可）。
HOTKEY_ID: Final[int] = 1

#: `GetMessageW` 的返回值：**-1 表示出错**，它不是"有消息"。
_GETMESSAGE_ERROR: Final[int] = -1

# ------------------------------------------------------------------ 加载

_user32 = None
_kernel32 = None

if sys.platform == "win32":  # pragma: no cover - 平台分支，Windows 上必真
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        _user32 = None
        _kernel32 = None
    IS_WINDOWS = _user32 is not None and _kernel32 is not None


class POINT(ctypes.Structure):
    """光标位置（逻辑坐标，与 `QCursor.pos()` 同一套）。"""

    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    """消息结构（`GetMessageW` 的第二个参数）。"""

    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


if IS_WINDOWS:  # pragma: no cover - 原型声明，Windows 上才跑
    # ⚠ 必须显式声明：不声明时 ctypes 把返回值当 32 位 int，
    # 64 位下 `HWND` 级的高地址会被截断（`GetActiveWindow` 就会踩到）。
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.UnregisterHotKey.restype = wintypes.BOOL

    _user32.GetMessageW.argtypes = [
        ctypes.POINTER(MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.GetMessageW.restype = ctypes.c_int  # 故意用 c_int：-1 是错误码
    _user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.PostThreadMessageW.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL


# ------------------------------------------------------------------ 封装


def is_available() -> bool:
    """Win32 这一层能不能用（非 Windows / DLL 加载失败 → 假）。"""
    return IS_WINDOWS


def get_last_error() -> int:
    """最近一次 Win32 调用的错误码（0 = 没有错误）。"""
    if _kernel32 is None:  # pragma: no cover - 非 Windows
        return 0
    return int(ctypes.get_last_error())


def clear_last_error() -> None:
    """把线程的"最近错误"清零。

    ⚠ **必须在每次调用前清**：Windows 的 `GetLastError` 是**线程局部**且
    **只有失败才写**——成功时它保留上一次的值。实测踩到：`RegisterHotKey`
    明明成功（返回真），紧接着读到的却是别的调用留下的 `1408`
    （`ERROR_HOTKEY_NOT_REGISTERED`），日志里于是出现"注册成功但报 1408"
    这种自相矛盾的记录。判据永远是**返回值**，错误码只在返回假时才有意义。
    """
    if _kernel32 is None:  # pragma: no cover - 非 Windows
        return
    ctypes.set_last_error(0)


def register_hotkey(hwnd: int | None, hotkey_id: int, mods: int, vk: int) -> bool:
    """注册一个系统级热键（`hWnd=None` → 投递给**当前线程**的消息队列）。

    失败原因不在返回值里，调用方紧接着读 `get_last_error()`。
    """
    if _user32 is None:  # pragma: no cover - 非 Windows
        return False
    clear_last_error()
    return bool(_user32.RegisterHotKey(hwnd, hotkey_id, mods, vk))


def unregister_hotkey(hwnd: int | None, hotkey_id: int) -> bool:
    """释放一个热键（`hWnd` 必须与注册时**完全一致**）。"""
    if _user32 is None:  # pragma: no cover - 非 Windows
        return False
    clear_last_error()
    return bool(_user32.UnregisterHotKey(hwnd, hotkey_id))


def get_message() -> tuple[int, int, int] | None:
    """**阻塞**取一条本线程的消息，返回 `(message, wParam, lParam)`。

    返回 `None` = `WM_QUIT`（该收工了）或 `GetMessageW` 报错（-1）。
    两个都要停：前者是正常退出，后者继续转只会**忙等**。
    """
    if _user32 is None:  # pragma: no cover - 非 Windows
        return None
    message = MSG()
    result = int(_user32.GetMessageW(ctypes.byref(message), None, 0, 0))
    if result in (0, _GETMESSAGE_ERROR):
        return None
    return (int(message.message), int(message.wParam), int(message.lParam))


def post_thread_message(thread_id: int, message: int, wparam: int = 0, lparam: int = 0) -> bool:
    """给指定线程的消息队列投一条消息（不经过任何窗口）。

    本项目用它做两件事：把子线程从 `GetMessageW` 里叫醒去**执行任务**
    （`hotkey.WM_RUN_TASK`），以及让它**有序退出**（`WM_QUIT`）。
    """
    if _user32 is None:  # pragma: no cover - 非 Windows
        return False
    return bool(_user32.PostThreadMessageW(thread_id, message, wparam, lparam))


def get_cursor_pos() -> tuple[int, int] | None:
    """Win32 拿到的光标位置（**物理**像素）。拿不到 → `None`。

    本项目的光标轮询走 Qt（`QCursor.pos()`，逻辑像素），所以这里**不用于
    常规轮询**；它是"全局热键真的送达了"这类自检里唯一能独立取证的来源。
    """
    if _user32 is None:  # pragma: no cover - 非 Windows
        return None
    point = POINT()
    if not _user32.GetCursorPos(ctypes.byref(point)):
        return None
    return (int(point.x), int(point.y))
