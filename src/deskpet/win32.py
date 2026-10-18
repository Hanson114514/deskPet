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

#: `CreateMutexW` 返回这个错误码 = **已经有一个同名互斥体存在**（task13 的单实例判据）。
#: ⚠ 它**不是** `CreateMutexW` 的失败：句柄照样拿到了，只是不是我们创建的。
#: 所以判据是"错误码 == 183 **且** 句柄非空"，两者缺一不可。
ERROR_ALREADY_EXISTS: Final[int] = 183

#: `WaitForSingleObject` 的返回值（单实例只用到"没等待"这一种）。
WAIT_OBJECT_0: Final[int] = 0
#: `WaitForSingleObject` 的超时返回值。**它不等于失败**——`CreateMutex` 拿到
#: `WAIT_ABANDONED`（前面那个进程没能正常放锁就死了）时，锁其实已经归我们了。
WAIT_ABANDONED: Final[int] = 0x80
#: `WaitForSingleObject` 等待毫秒数：0 = 立刻返回（不当场阻塞启动）。
WAIT_TIMEOUT: Final[int] = 0x102

#: 热键编号（同一个进程内唯一即可）。
HOTKEY_ID: Final[int] = 1

#: `GetAsyncKeyState` 返回值里"**此刻**按着"的那一位（高字节的最高位）。
#:
#: ⚠ **判据必须是这一位，不是 `state != 0`**：低字节是"自上次调用以来按过"
#: 的**历史位**，它会在松开之后继续为真。写成 `state != 0` 的表现是
#: "**点一下键就永远亮着**"（task18 §4.1 头号坑）。
ASYNC_KEY_DOWN_MASK: Final[int] = 0x8000

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

    # --- 全局按键状态（task18 的键盘形态联动）
    #
    # ⚠ 返回类型**必须**是 `c_short`（16 位**有符号**）：高字节的最高位是
    # "此刻按着"的标志，声明成 `wintypes.BOOL` 或让 ctypes 自己猜（默认
    # `c_int`）都不会错得太离谱，但**负数的符号扩展**在别的写法下会漏掉
    # 那一位——显式声明是"只信返回值里该看的那一位"这条纪律的第一步。
    _user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    _user32.GetAsyncKeyState.restype = ctypes.c_short

    # --- 热键子线程建窗口用的那几个（task21 CODE-02）
    #
    # ⚠ 这几个原先在 hotkey.py 里"裸调"：只说得出名字，没有原型。
    # 不声明原型时 ctypes 把返回值当 32 位 int——`GetModuleHandleW` /
    # `CreateWindowExW` 返回的句柄在 64 位下会被**截断**，截断后的句柄拿去
    # `RegisterClassExW` / `CreateWindowExW` 时好坏取决于高位的运气
    # （典型表现是"建窗口失败"或"注册成功却收不到消息"）。
    # 原型**只允许在这一层声明**（本模块是项目唯一的 ctypes 出口）。
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL

    # --- 命名互斥体（task13 的单实例机制，D6）
    #
    # ⚠ 返回值（`HANDLE`）**必须声明**：不声明时 ctypes 按 32 位 `int` 取，
    # 64 位下句柄的高位会被截断，`ReleaseMutex` / `CloseHandle` 随后就会失败
    # （与 §4.20 那批 `HWND` 截断是同一个坑）。
    _kernel32.CreateMutexW.argtypes = [
        wintypes.LPVOID,  # LPSECURITY_ATTRIBUTES（本项目不传）
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _kernel32.ReleaseMutex.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


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


def get_module_handle() -> int:
    """本进程的模块句柄（`GetModuleHandleW(None)`）。取不到 → 0。

    ⚠ 返回类型**必须**声明成 `HMODULE`（指针宽度）：不声明时 ctypes 按 32 位
    `int` 取，64 位下高位被截断（task21 CODE-02）。取不到返回 0 而不是抛异常：
    "拿不到句柄"在调用方那里是"建窗口失败"，走的是同一条退化路径。
    """
    if _kernel32 is None:  # pragma: no cover - 非 Windows
        return 0
    return int(_kernel32.GetModuleHandleW(None) or 0)


def destroy_window(hwnd: int) -> bool:
    """销毁一个窗口（`DestroyWindow`）。句柄为 0 / 非 Windows → False。"""
    if _user32 is None or not hwnd:  # pragma: no cover - 非 Windows
        return False
    return bool(_user32.DestroyWindow(hwnd))


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



# ------------------------------------------------------------------ 完整性级别
#
# 为什么需要读这个：`GetAsyncKeyState` 受 **UIPI** 约束 ——
# **低完整性级别**的进程，在“前台窗口属于更高完整性级别”时读到的**永远是 0**。
# 用户机器上普通双击的程序都是 `Medium`；本程序若被以 `Low` 令牌启动
# （例如由另一个受限进程派生），键盘形态就会表现为
# “有焦点时正常、一点别的窗口就完全没反应”，而日志里只有 `GetAsyncKeyState` 全 0，
# 看起来像“系统不给我们按键”。排查时必须能一眼看到自己跑在哪个级别。

#: `TokenIntegrityLevel` 的已知值 → 名字。
_INTEGRITY_LEVELS: Final[dict[int, str]] = {
    0x0000: "Untrusted",
    0x1000: "Low",
    0x2000: "Medium",
    0x3000: "High",
    0x4000: "System",
    0x5000: "Protected",
}


def get_integrity_level(pid: int | None = None) -> str:
    """本进程的**完整性级别**名字（`Low` / `Medium` / `High` …）。

    读不到时返回 ``"?"``（**不抛异常**：这只是一条诊断，不该影响启动）。
    令牌里的 IL 是 `SID S-1-16-xxxx`，取**最后一个子权威值**。

    ``pid`` 为 ``None`` 时看**本进程**；给别的 pid 就查那个进程（用来核对
    "父进程是什么级别"——父是 Medium 而子是 Low，说明启动时被降权了）。
    """
    if not IS_WINDOWS:  # pragma: no cover - 非 Windows 分支
        return "?"
    try:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        # ⚠ 原型必须显式声明：不声明时 ctypes 把 HANDLE 当 32 位 int，
        # 64 位下句柄被截断，OpenProcessToken 一定失败（本文件开头的坑）。
        _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        _kernel32.OpenProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL

        target = _kernel32.GetCurrentProcessId() if pid is None else int(pid)
        process = _kernel32.OpenProcess(0x1000, False, target)
        if not process:
            return "?"
        try:
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
                return "?"
            try:
                size = wintypes.DWORD(0)
                advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
                if not size.value:
                    return "?"
                buf = ctypes.create_string_buffer(size.value)
                if not advapi32.GetTokenInformation(
                    token, 25, ctypes.cast(buf, ctypes.c_void_p), size.value, ctypes.byref(size)
                ):
                    return "?"
                sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
                count = ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte))[1]
                base = ctypes.addressof(ctypes.cast(sid, ctypes.POINTER(ctypes.c_ubyte)).contents)
                value = ctypes.cast(
                    base + 8 + 4 * (count - 1), ctypes.POINTER(ctypes.c_uint)
                ).contents.value
                return _INTEGRITY_LEVELS.get(value, hex(value))
            finally:
                _kernel32.CloseHandle(token)
        finally:
            _kernel32.CloseHandle(process)
    except Exception:  # noqa: BLE001 - 诊断绝不影响启动
        return "?"


def shell_execute_runas(program: str, parameters: str, directory: str) -> bool:
    """以**管理员身份**拉起 `program`（`ShellExecuteW` + `"runas"`）。

    返回是否**真的拉起成功**（`ShellExecuteW` 返回值 > 32 才算成功）。
    用户点了「否」（撤销 UAC）不算异常：返回 `False` 即可，调用方应当
    「什么都不做、继续用现在这个实例」，而不是报错退出。

    ## 为什么不用 `QProcess.startDetached()`

    `startDetached` 走 `CreateProcess`，**没有「请求提权」这个概念**
    ——想要 UAC 只能走 Shell 的 `runas` 动词，那是 `ShellExecute` 的路径。
    两者互补：task13 用前者做普通重启，task25 用这里做提权重启。
    """
    if not IS_WINDOWS:  # pragma: no cover - 非 Windows
        return False
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell32.ShellExecuteW.argtypes = [
            wintypes.HWND,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_int,
        ]
        # ⚠ 返回 `HINSTANCE`：它有**两种含义**——成功时是 >32 的假句柄，
        # 失败时是 0..32 的错误码。判据必须是 `> 32`，不能判非空。
        shell32.ShellExecuteW.restype = wintypes.HINSTANCE
        SW_SHOWNORMAL = 1
        result = shell32.ShellExecuteW(
            None, "runas", program, parameters or None, directory or None, SW_SHOWNORMAL
        )
        value = ctypes.cast(result, ctypes.c_void_p).value or 0
        if value > 32:
            logger.info("提权启动：已拉起 %s %s", program, parameters)
            return True
        # 5 = SE_ERR_ACCESSDENIED（典型情况是用户在 UAC 里点了「否」）。
        logger.info("提权启动：没有拉起（返回码 %s，5=用户取消/被拒绝）", value)
        return False
    except Exception as exc:  # noqa: BLE001 - 提权失败只能降级，不能崩
        logger.warning("提权启动失败：%s: %s", type(exc).__name__, exc)
        return False


def get_parent_pid() -> int:
    """父进程 PID（`None`/0 读不到时返回 0）。

    用 `CreateToolhelp32Snapshot` 遍历进程表匹配 `th32ParentProcessID`——
    比 `NtQueryInformationProcess` 简单，且不需要额外权限。
    """
    if not IS_WINDOWS:  # pragma: no cover
        return 0
    try:
        TH32CS_SNAPPROCESS = 0x00000002
        INVALID = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        _kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        _kernel32.Process32FirstW.restype = wintypes.BOOL
        _kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        _kernel32.Process32NextW.restype = wintypes.BOOL

        snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return 0
        try:
            me = _kernel32.GetCurrentProcessId()
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            ok = _kernel32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                if entry.th32ProcessID == me:
                    return int(entry.th32ParentProcessID)
                ok = _kernel32.Process32NextW(snap, ctypes.byref(entry))
            return 0
        finally:
            _kernel32.CloseHandle(snap)
    except Exception:  # noqa: BLE001
        return 0


def get_async_key_state(vk: int) -> bool:
    """虚拟键码 `vk` **此刻**是不是按着的（`GetAsyncKeyState` 的薄封装）。

    ## 为什么"不抢焦点、不需要管理员权限"

    `GetAsyncKeyState` 读的是**全局**按键状态表，不装钩子、不装过滤器、
    不要求前台窗口是我们。参考实现用的就是它（task18 §1.1），本项目沿用
    ——**不许**换成 `SetWindowsHookEx`（钩子有"被系统摘掉"的风险，
    还要处理超时回调，task18 §5 明写禁止）。

    ## ⚠ 判据是高字节，不是"非零"

    返回值 16 位，两半是**两件不同的事**：

    | 位 | 含义 | 能不能当"现在按着"用 |
    | --- | --- | --- |
    | 低 8 位 | **自上次调用以来**按过（历史位） | ❌ **不能** |
    | 高 8 位最高位 | **此刻**按着 | ✅ 唯一判据 |

    写成"非零即按下"的后果是"**点一下键就永远亮着**"：低字节的历史位
    在松开之后仍然为真（与 §4.20 的 GetLastError 那类坑同源——
    **先看该看的那一位，别拿整个返回值当真**）。

    非 Windows / DLL 没加载上 → False（**不抛异常**）：键盘联动是
    可选功能，取不到就当作"没有键在按"（与 hotkey 的退化同一条纪律）。
    """
    if _user32 is None:  # pragma: no cover - 非 Windows
        return False
    state = int(_user32.GetAsyncKeyState(int(vk)))
    return (state & ASYNC_KEY_DOWN_MASK) != 0


# ------------------------------------------------------------------ 命名互斥体


def create_mutex(name: str) -> tuple[int, bool]:
    """创建一个**命名互斥体**，返回 `(句柄, 是不是我们创建的)`。

    判据是 `CreateMutexW` 的返回值 + `GetLastError()`，**两条缺一不可**：

    | 返回值 | 错误码 | 含义 |
    | --- | --- | --- |
    | 非空 | `183`（`ERROR_ALREADY_EXISTS`） | 同名互斥体**已经存在** → 别人在跑 |
    | 非空 | 其它 | 我们是第一个 → 抢到了 |
    | 空 | 任意 | **创建失败**（调用方按"保护机制不可用"处理） |

    ⚠ `ERROR_ALREADY_EXISTS` **不是失败**：句柄照样拿到了，照样要 `CloseHandle`。
    把它写成"返回空"会让第二个实例拿不到句柄去关，句柄泄漏一次。

    `ERROR_ALREADY_EXISTS` 与 `GetLastError()` 的关系见 `clear_last_error()` 的说明：
    **必须在调用前清零**，否则成功时会读到上一次调用留下的陈旧错误码——
    那会让"第一个实例"被误判成"已有实例"，桌宠直接起不来。
    """
    if _kernel32 is None:  # pragma: no cover - 非 Windows
        return 0, False
    clear_last_error()
    handle = _kernel32.CreateMutexW(None, True, name)
    already = get_last_error() == ERROR_ALREADY_EXISTS
    return int(handle or 0), (bool(handle) and not already)


def release_mutex(handle: int) -> bool:
    """释放互斥体的**所有权**（不销毁它）。句柄仍然有效，可以再 `Wait`。"""
    if _kernel32 is None or not handle:  # pragma: no cover - 非 Windows
        return False
    clear_last_error()
    return bool(_kernel32.ReleaseMutex(handle))


def close_handle(handle: int) -> bool:
    """关掉一个内核句柄。**进程退出时系统也会回收**，但主动关掉才是对的。"""
    if _kernel32 is None or not handle:  # pragma: no cover - 非 Windows
        return False
    clear_last_error()
    return bool(_kernel32.CloseHandle(handle))
