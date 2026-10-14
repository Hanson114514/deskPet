"""全局热键：注册、候选链回退、释放（task12）。

## D5 的最终选择（**已定案，不要再改**）

热键消息怎么进来，两条路线**择一**（task12 §4）：原生消息过滤器 / 子线程。
**选子线程**，理由是它能在**后台线程**里建一个自己的窗口并跑完整的
`GetMessageW` 循环：

```
子线程：RegisterClassExW → CreateWindowExW → RegisterHotKey(hwnd, ...)
        → while GetMessageW(...)：WM_HOTKEY → queue.put("pressed")
        → WM_QUIT → 退出循环
```

这样有好几条好处，每一条都对着 task12 的难点：

| 好处 | 说明 |
| --- | --- |
| **不碰主线程的消息钩子** | 不需要在宠物窗口上装任何东西，也就没有"隐藏期间钩子超时/被系统摘掉"的风险 |
| **唤醒是事件驱动的** | 子线程平时**阻塞**在 `GetMessageW` 上，隐藏期间 CPU 是 0；**不是** 120 ms 轮询 |
| **退出安全** | GUI 线程只发一条 `PostThreadMessageW(WM_QUIT)`，子线程自己收尾（`UnregisterHotKey` + `DestroyWindow`）后退出，`join()` 有超时兜底 |
| **注册与释放同线程** | `RegisterHotKey` / `UnregisterHotKey` 都发生在子线程，句柄归属单一，不会出现"跨线程释放不掉" |

**"120 ms 轮询"已去掉**（`CursorWatch` 那个 150 ms 是久置行为要的，与热键无关）。
GUI 线程这边只有一个 **100 ms 的 `QTimer` 从 `queue.Queue` 取件**——
它是**取件**不是**轮询系统**：队列空时那次 timeout 的开销是微秒级，
消息进来后最坏晚 100 ms 被处理（人感觉不到）。

**"分两拍处理"**（task12 §4 硬要求）：取件槽里**不直接显示窗口**，
而是 `QTimer.singleShot(0, ...)` 让出一次事件循环再执行。理由是热键回调
发生在"Qt 正在处理某个消息"的栈上，直接 `show()` 会让窗口管理器在
一次嵌套的激活流程里重入。

## 两个必须记住的坑

1. **`hWnd` 与注册时的必须完全一致**：本模块把两个句柄（`hwnd` 与线程 id）
   一起存在 `_Registration` 里，释放时原样用回去，不做任何"重新推导"。
2. **`RegisterHotKey` 的失败原因在 `GetLastError()` 里**，不在返回值里；
   `1409` 是"已被其他程序占用"（文案见 `hotkey_spec.REASON_OCCUPIED`）。
"""

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Final, NamedTuple

from PySide6.QtCore import QObject, QTimer, Signal

from . import hotkey_spec, win32
from .hotkey_spec import (
    ComboError,
    MODIFIER_ORDER,  # noqa: F401  （对外转发：维护者只需要认一个模块）
    REASON_NEED_MODIFIER,
    REASON_OCCUPIED,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 常量

#: 呼出快捷键的**默认值**。⚠ 与 `config.DEFAULTS["hotkey"]` **同源**：
#: `config.py` 模块末尾有断言守着两者一致（真值只允许一处）。
DEFAULT_HOTKEY: Final[str] = "Ctrl+Alt+Shift+P"

#: 候选回退链（task12 §4 逐字）。注册失败时**按这个顺序**往下试。
CANDIDATES: Final[tuple[str, ...]] = (
    "Ctrl+Alt+Shift+P",
    "Ctrl+Alt+Shift+O",
    "Win+Alt+P",
    "Ctrl+Alt+F12",
    "Ctrl+Shift+F12",
)

#: GUI 线程"取件"的间隔（毫秒）。**这不是轮询系统**：取的是一个内存队列，
#: 空了就是一次微秒级的空转；有消息时最坏迟 100 ms 被处理。
PICKUP_INTERVAL_MS: Final[int] = 100

#: 等待子线程收尾的上限（秒）。`UnregisterHotKey` + `DestroyWindow` 都是快的，
#: 给 2 秒已经非常宽裕；超时只记警告，绝不 `join()` 到天荒地老。
SHUTDOWN_TIMEOUT_S: Final[float] = 2.0

#: 等子线程执行一次任务的上限（秒）。任务只是几次 Win32 调用，瞬间就该回来；
#: 这个上限是"线程卡死时别把界面一起拖住"的兜底。
TASK_TIMEOUT_S: Final[float] = 3.0

#: 我们自己约定的"执行队列里那个任务"消息（`WM_APP` 以上不会与系统撞车）。
WM_RUN_TASK: Final[int] = 0x8000 + 1

#: 注册失败：**窗口属于另一个线程**。
#: ⚠ 这是本 task 实测踩到并解释清楚的一个坑（详见 `_HotkeyThread.call`）：
#: `RegisterHotKey` 必须在**拥有该窗口的那个线程里**调用，否则系统直接拒收。
ERROR_INVALID_WINDOW_FOR_THREAD: Final[int] = 1408


# ------------------------------------------------------------------ 结果


class RegisterResult(NamedTuple):
    """一次注册尝试的结论（task12 §4：`register()` 返回 `(combo, changed, reason)`）。

    | 字段 | 语义 |
    | --- | --- |
    | `combo` | 真的注册上的组合；**一个都没成就是 `None`** |
    | `changed` | 最终组合**与首选不同**（= 走了回退链，调用方据此写回配置） |
    | `reason` | 首选失败的原因（成功于首选时是空串），给用户看的一句话 |

    `combo is None` 是**最危险的状态**（藏起来就再也叫不回来），
    所以调用方必须把它变成"禁用 `隐藏宠物` + 附录 B 那句提示"。
    """

    combo: str | None
    changed: bool
    reason: str = ""


@dataclass
class _Registration:
    """当前那次注册的**完整现场**（释放时必须原样用回去）。"""

    combo: str
    mods: int
    vk: int
    hwnd: int


# ------------------------------------------------------------------ 子线程


class _HotkeyThread(threading.Thread):
    """在自己的窗口上注册热键，并把 `WM_HOTKEY` 放进队列。

    ## ⚠ 为什么注册不能直接调（本 task 实测踩到的坑）

    `RegisterHotKey` **必须在拥有该窗口的那个线程里调用**。从别的线程调，
    系统不返回 1409（已被占用）也不返回成功，而是返回
    **`1408`：`无效窗口；它属于另一线程`**（`ctypes.FormatError(1408)`）。

    实测记录（`tools/probe_task12*.py`）：

    | 调用位置 | 结果 |
    | --- | --- |
    | 主线程，`hwnd=None` | `True` |
    | 主线程，子线程建出来的 `hwnd` | `False`，`err=1408` |
    | **子线程内**，同一个 `hwnd` | `True`，`err=0` |

    这个错误码很能迷惑人：`1408` 与"已被占用"的 `1409` 只差一位，
    粗心看日志会得出"快捷键都被占用了"的错误结论。对策就是本类的
    `call()`：所有 Win32 调用都投给子线程、由它自己执行。

    于是本类的公开方法**只做两件事**：往任务队列里放一个闭包，
    再用 `PostThreadMessageW(WM_RUN_TASK)` 把子线程从 `GetMessageW` 里叫醒。
    """

    def __init__(self, out_queue: "queue.Queue[str]") -> None:
        super().__init__(name="deskpet-hotkey", daemon=True)
        self._out = out_queue
        #: 子线程建好的窗口句柄（注册与释放在同一个线程里用）。
        self.hwnd: int | None = None
        #: 子线程自己的线程 id（`PostThreadMessageW` 要它）。
        self.thread_id: int | None = None
        #: 线程**已经把窗口建好**（或明确失败）——注册方据此决定要不要等。
        self.ready = threading.Event()
        #: 注册失败的原因（成功时空串）。
        self.error: str = ""
        #: 真正注册上的组合（未注册成功时为空串）。
        self.registered: str = ""
        #: 待执行的任务与它们的回执（跨线程的唯一通道）。
        self._tasks: "queue.Queue" = queue.Queue()
        self._results: "queue.Queue" = queue.Queue()

    # ------------------------------------------------------------ 线程体

    def run(self) -> None:  # pragma: no cover - 需要真 Windows 消息循环
        if not win32.is_available():
            self.error = "这一层需要 Windows（此刻不可用）"
            self.ready.set()
            return

        self.thread_id = _current_thread_id()
        try:
            self.hwnd = _create_message_window()
        except OSError as exc:
            self.error = f"建消息窗口失败：{exc}"
            self.ready.set()
            return
        self.ready.set()

        try:
            while True:
                message = win32.get_message()
                if message is None:
                    break  # WM_QUIT 或 GetMessageW 出错，都该收工
                if message[0] == win32.WM_HOTKEY:
                    self._out.put("pressed")
                elif message[0] == WM_RUN_TASK:
                    self._run_one_task()
        finally:
            # 走到这里一定是因为收到了 WM_QUIT（或消息循环出错）：
            # **必须**在这里释放窗口与热键——它们是本线程的资源。
            self._release_window()

    def _run_one_task(self) -> None:
        """执行队列里的一个任务并把结果回执出去（**在子线程里**跑）。"""
        try:
            task = self._tasks.get_nowait()
        except queue.Empty:  # pragma: no cover - 消息与任务总是一起投的
            return
        try:
            self._results.put((True, task()))
        except Exception as exc:  # pragma: no cover - 防御：任务不该抛
            self._results.put((False, exc))

    # ------------------------------------------------------------ 跨线程调用

    def call(self, task, timeout: float = TASK_TIMEOUT_S):
        """把 `task()` 放到**子线程**里执行，等它返回。

        这是本类唯一的跨线程入口：**所有** Win32 调用都必须经过它，
        否则就会撞上上面那个 `1408`（窗口属于另一线程）。
        超时/线程已死 → 返回 `None`，调用方按"这一步没成"处理。
        """
        if not self.is_alive() or self.thread_id is None:
            return None
        self._tasks.put(task)
        if not win32.post_thread_message(self.thread_id, WM_RUN_TASK):
            return None
        try:
            ok, value = self._results.get(timeout=timeout)
        except queue.Empty:  # pragma: no cover - 子线程卡死时才会到
            logger.warning("热键线程 %.1f 秒没有回执（这一步按失败处理）", timeout)
            return None
        if not ok:  # pragma: no cover - 任务内部抛异常（不该发生）
            logger.warning("热键线程里的任务出错：%r", value)
            return None
        return value

    # ------------------------------------------------------------ 注册 / 释放

    def register(self, combo: str, mods: int, vk: int) -> bool:
        """在子线程的窗口上注册，返回是否成功；原因在 `last_error()` 里。

        组合名与错误码都在**子线程**里落定，再由回执带回来——这样
        "谁注册的"与"谁读错误码"是同一个线程，`GetLastError` 才有意义
        （它是线程局部的，见 `win32.clear_last_error` 的说明）。
        """
        def _do() -> tuple[bool, int]:
            ok = win32.register_hotkey(self.hwnd, win32.HOTKEY_ID, mods, vk)
            return bool(ok), win32.get_last_error()

        result = self.call(_do)
        if result is None:
            self._last_error = ERROR_INVALID_WINDOW_FOR_THREAD
            return False
        ok, error = result
        self._last_error = error
        if ok:
            self.registered = combo
        return ok

    def release(self) -> None:
        """释放热键（走子线程）。"""
        if not self.registered:
            return

        def _do() -> bool:
            return win32.unregister_hotkey(self.hwnd, win32.HOTKEY_ID)

        self.call(_do)
        self.registered = ""

    def _release_window(self) -> None:
        """**只能在本线程的 `run()` 收尾处调用**（此时已经在子线程里）。"""
        if self.hwnd is None:
            return
        if self.registered:
            win32.unregister_hotkey(self.hwnd, win32.HOTKEY_ID)
            self.registered = ""
        _destroy_message_window(self.hwnd)
        self.hwnd = None

    def last_error(self) -> int:
        """最近一次注册失败的错误码（0 = 成功或还没试过）。"""
        return self._last_error

    #: 最近一次注册失败的错误码。
    _last_error: int = 0


#: 子线程那个窗口的类名（**同一个进程里只注册一次类**）。
_WINDOW_CLASS_NAME: Final[str] = "DeskPetHotkeyWindow"

#: 消息窗口的样式：`HWND_MESSAGE`（`-3`）——不可见、不出现在任务栏，
#: 只用来收消息。这正是热键要的形态。
_HWND_MESSAGE: Final[int] = -3

#: `WNDCLASSEXW` 的 `style` 用不到，给 0；回调给 `None`（不处理任何窗口消息，
#: 热键消息本来就投到**线程队列**，不会进窗口过程）。
_class_registered = False

#: 窗口过程对象的**强引用**（见 `_register_window_class` 的说明：没人引用它，
#: 消息一到就是野指针）。
_WINDOW_PROC = None


def _current_thread_id() -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetCurrentThreadId())  # type: ignore[attr-defined]


def _create_message_window() -> int:  # pragma: no cover - 需要 Windows
    """在**当前线程**建一个消息窗口，返回句柄。

    分成两句 `if` 是有原因的：`create_window` 失败必须**自己**报出来
    （返回 0 时不抛异常，只是句柄为 0），否则后面 `RegisterHotKey(0, ...)`
    会把热键投到一个不存在的窗口上，表现是"注册成功但永远收不到"。
    """
    global _class_registered
    if not _class_registered:
        _register_window_class()
        _class_registered = True
    hwnd = _create_window(_WINDOW_CLASS_NAME)
    if not hwnd:
        raise OSError("CreateWindowExW 返回了空句柄")
    return hwnd


def _register_window_class() -> None:  # pragma: no cover - 需要 Windows
    """注册窗口类。

    ⚠ **窗口过程必须在模块级持有引用**（`_WINDOW_PROC`）：`WNDCLASSEXW` 里
    只传了一个函数指针，Python 这一侧如果没人引用那个回调对象，它会被回收，
    消息一到就是**野指针**（表现出来是偶发崩溃，不是异常）。
    """
    global _WINDOW_PROC
    import ctypes
    from ctypes import wintypes

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
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

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.ATOM

    _WINDOW_PROC = _make_window_proc()

    info = WNDCLASSEXW()
    info.cbSize = ctypes.sizeof(WNDCLASSEXW)
    info.lpfnWndProc = ctypes.cast(_WINDOW_PROC, ctypes.c_void_p)
    info.hInstance = ctypes.windll.kernel32.GetModuleHandleW(None)  # type: ignore[attr-defined]
    info.lpszClassName = _WINDOW_CLASS_NAME
    if not user32.RegisterClassExW(ctypes.byref(info)):
        # 已经注册过（同一个进程跑第二遍）不是错误：继续用已有的类。
        if ctypes.get_last_error() != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            raise OSError(f"RegisterClassExW 失败（错误 {ctypes.get_last_error()}）")


def _make_window_proc():  # pragma: no cover - 需要 Windows
    """窗口过程：一律交回系统默认处理（我们只收线程队列里的热键消息）。

    ⚠ **四个参数与返回值都必须声明成指针宽度**（`WPARAM` / `LPARAM` /
    `LRESULT` 在 64 位下都是 8 字节）。实测踩到：把 `lParam` 当 `int` 收，
    64 位下 `DefWindowProcW` 直接抛
    `ctypes.ArgumentError: argument 4: OverflowError: int too long to convert`
    ——回调里的异常**不会**冒泡到调用方，只往 stderr 打一行
    "Exception ignored while calling ctypes callback function"，很容易被当成噪音。
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.DefWindowProcW.restype = ctypes.c_void_p  # LRESULT：指针宽度

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_void_p,  # LRESULT
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    )

    def _proc(hwnd, message, wparam, lparam):  # noqa: ANN001
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    return WNDPROC(_proc)


def _create_window(class_name: str) -> int:  # pragma: no cover - 需要 Windows
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = [
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
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    return int(
        user32.CreateWindowExW(
            0,
            class_name,
            "deskpet hotkey",
            0,
            0,
            0,
            0,
            0,
            _HWND_MESSAGE,
            None,
            ctypes.windll.kernel32.GetModuleHandleW(None),  # type: ignore[attr-defined]
            None,
        )
        or 0
    )


def _destroy_message_window(hwnd: int) -> None:  # pragma: no cover - 需要 Windows
    import ctypes

    ctypes.windll.user32.DestroyWindow(hwnd)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ 管理器


class HotkeyManager(QObject):
    """全局热键的**唯一入口**（注册 / 释放 / 转发按下事件）。

    | 信号 | 含义 |
    | --- | --- |
    | `hotkey_pressed()` | 收到 `WM_HOTKEY`（GUI 线程发出，可以安全动界面） |

    ⚠ **收到信号不等于可以直接显示窗口**：调用方要先让出一次事件循环
    （`QTimer.singleShot(0, ...)`），见模块开头的"分两拍处理"。
    """

    hotkey_pressed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread: _HotkeyThread | None = None
        self._registration: _Registration | None = None
        #: 已经注册上的组合（一个都没成就是 `None`）。
        self._combo: str | None = None
        #: 取件定时器：只在真的注册上了之后才转（没有热键就没有件可取）。
        self._pickup = QTimer(self)
        self._pickup.setInterval(PICKUP_INTERVAL_MS)
        self._pickup.setTimerType(_precise_timer_type())
        self._pickup.timeout.connect(self.pickup)
        #: 最近一次注册失败的说明（界面的提示文案用它）。
        self._last_reason: str = ""

    # ------------------------------------------------------------ 只读

    @property
    def combo(self) -> str | None:
        """当前注册上的组合；`None` = 一个都没有。"""
        return self._combo

    @property
    def has_hotkey(self) -> bool:
        """有没有可用的呼出快捷键（菜单可用性的判据）。"""
        return self._combo is not None

    @property
    def reason(self) -> str:
        """最近一次注册失败的原因（给界面用；成功时是空串）。"""
        return self._last_reason

    # ------------------------------------------------------------ 注册

    def register(self, preferred: str) -> RegisterResult:
        """按**候选链**依次尝试，返回 `(combo, changed, reason)`。

        顺序（task12 §4 逐字）：

        1. 先试调用方给的 `preferred`（配置里的那个）；
        2. 失败 → 按 `CANDIDATES` **依次**往下试，**跳过与 preferred 相同的那个**
           （免得把同一个组合试两遍）；
        3. 全失败 → `combo=None` + 首选失败的原因。

        `preferred` 自己**不是合法组合**（配置文件被人改坏了）→ 不报错退出，
        按"读取永不致命"处理：记一条警告，然后**当它是空的**直接走候选链
        （§2.10 第三类：用户手改配置不该让程序起不来）。

        ⚠ 本方法**只在主线程调用**，且调用前窗口/事件循环已经就绪。
        """
        self._last_reason = ""
        if not win32.is_available():
            self._last_reason = "这一层需要 Windows（此刻不可用）"
            logger.warning("全局热键不可用：%s", self._last_reason)
            return RegisterResult(None, False, self._last_reason)

        candidates = self._candidate_list(preferred)
        if not candidates:
            self._last_reason = "没有可用的呼出快捷键"
            return RegisterResult(None, False, self._last_reason)

        thread = self._ensure_thread()
        if thread is None:
            return RegisterResult(None, False, self._last_reason)

        first_reason = ""
        for index, combo in enumerate(candidates):
            try:
                mods, vk = hotkey_spec.parse_combo(combo)
            except ComboError as exc:
                if index == 0:
                    first_reason = exc.reason
                logger.warning("候选快捷键 %r 不合法，跳过：%s", combo, exc.reason)
                continue

            if thread.register(combo, mods, vk):
                self._combo = combo
                self._registration = _Registration(combo, mods, vk, int(thread.hwnd or 0))
                self._pickup.start()
                changed = combo != preferred
                if changed:
                    logger.info(
                        "原快捷键 %s 不可用（%s），已改用 %s", preferred, first_reason, combo
                    )
                return RegisterResult(combo, changed, first_reason)

            reason = _failure_reason(thread.last_error())
            if index == 0:
                first_reason = reason
            logger.warning("快捷键 %s 注册不上：%s", combo, reason)

        self._last_reason = first_reason or "没有可用的呼出快捷键"
        logger.warning("所有候选快捷键都注册不上：%s", self._last_reason)
        return RegisterResult(None, False, self._last_reason)

    def try_register(self, combo: str) -> RegisterResult:
        """**只试这一个**组合（换热键用，task12 §6"失败必须回滚到原来那个"）。

        与 `register()` 的区别：这里**不做候选链回退**。用户在设置里明确指定了
        一个快捷键，注册不上就该如实告诉他"这个被占用了"，而不是偷偷换成别的
        ——那样用户按自己设的键没反应，会以为程序坏了。
        """
        self._last_reason = ""
        if not win32.is_available():
            self._last_reason = "这一层需要 Windows（此刻不可用）"
            return RegisterResult(None, False, self._last_reason)

        if self._registration is not None:
            # 同一个组合就是当前生效的那个 → 什么都不用做。
            if self._registration.combo == combo:
                return RegisterResult(combo, False, "")

        thread = self._ensure_thread()
        if thread is None:
            return RegisterResult(None, False, self._last_reason)

        try:
            mods, vk = hotkey_spec.parse_combo(combo)
        except ComboError as exc:
            self._last_reason = exc.reason
            return RegisterResult(None, False, exc.reason)

        if thread.register(combo, mods, vk):
            self._registration = _Registration(combo, mods, vk, int(thread.hwnd or 0))
            self._combo = combo
            self._pickup.start()
            return RegisterResult(combo, True, "")

        error = thread.last_error()
        self._last_reason = _failure_reason(error)
        return RegisterResult(None, False, self._last_reason)

    def release_hotkey(self) -> None:
        """把**当前这一个**热键释放掉（但不拆掉线程）。换热键时先放再注册。"""
        if self._thread is None or self._registration is None:
            self._registration = None
            self._combo = None
            self._pickup.stop()
            return
        self._thread.release()
        self._registration = None
        self._combo = None
        self._pickup.stop()

    def restore(self, combo: str) -> RegisterResult:
        """把热键**恢复成** `combo`（换热键失败时的回滚路径）。

        "回滚到原来那个"必须真的做出来：失败路径上我们可能已经
        `release_hotkey()` 过了，光把配置写回去并不能让键盘重新生效。
        """
        return self.try_register(combo)

    # ------------------------------------------------------------ 释放

    def unregister(self) -> None:
        """**彻底释放**：热键 + 消息窗口 + 子线程（退出路径**必须**调用）。

        不释放的表现是"组合被本进程占着"——退出后立刻重启，
        同一个组合注册不上（task12 §6 最后两条验收盯着它）。
        """
        self._pickup.stop()
        thread = self._thread
        self._thread = None
        self._registration = None
        self._combo = None
        if thread is None:
            return

        if thread.thread_id is not None and thread.is_alive():
            win32.post_thread_message(thread.thread_id, win32.WM_QUIT)
        thread.join(SHUTDOWN_TIMEOUT_S)
        if thread.is_alive():  # pragma: no cover - 正常情况下到不了
            logger.warning("热键线程没有在 %.1f 秒内退出（继续退出主流程）", SHUTDOWN_TIMEOUT_S)
        thread.release()

    # ------------------------------------------------------------ 取件

    def pickup(self) -> None:
        """把队列里的"按下"转成 **GUI 线程**的信号（`QTimer` 的槽）。

        空队列时开销是微秒级；**不是**轮询系统状态（D5 的说明见模块开头）。
        """
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return
            self.hotkey_pressed.emit()

    # ------------------------------------------------------------ 内部

    def _candidate_list(self, preferred: str) -> list[str]:
        """首选 + 候选链（去重，保持顺序）。"""
        text = str(preferred or "").strip()
        ordered: list[str] = []
        if text:
            ordered.append(text)
        for combo in CANDIDATES:
            if combo not in ordered:
                ordered.append(combo)
        return ordered

    def _ensure_thread(self) -> _HotkeyThread | None:
        """子线程还在不在？不在就起一个并等它把窗口建好。"""
        if self._thread is not None and self._thread.is_alive():
            return self._thread

        thread = _HotkeyThread(self._queue)
        thread.start()
        # 等"窗口建好了没有"：给 5 秒足够宽裕（真失败会立刻 set）。
        if not thread.ready.wait(5.0):  # pragma: no cover - 正常情况下瞬间就绪
            self._last_reason = "热键线程启动超时"
            logger.warning("热键线程启动超时")
            return None
        if thread.hwnd is None:
            self._last_reason = thread.error or "热键线程没能建出消息窗口"
            logger.warning("全局热键不可用：%s", self._last_reason)
            return None
        self._thread = thread
        return thread


def _failure_reason(error: int) -> str:
    """注册失败的错误码 → 给用户看的一句话（**文案真值在 `hotkey_spec`**）。

    `1409` 是"已被其他程序占用"——task12 §4 明写了逐字文案。
    其余错误码（含我们实测踩到的 `1408`"窗口属于另一线程"）如实带出数字，
    不猜原因：**编一个听起来合理的原因是更糟的做法**。
    """
    if error == win32.ERROR_HOTKEY_ALREADY_REGISTERED:
        return REASON_OCCUPIED
    return f"注册快捷键失败（错误 {error}）"


def _precise_timer_type():
    """`Qt.PreciseTimer`（延迟导入，见模块开头的"最小依赖"说明）。

    `QTimer` 默认是 `CoarseTimer`，会把 100 ms 上取整到 15.6 ms 的节拍上
    （agent.md §4.9 记过 33→46.8 的教训）。取件间隔不敏感，但**照样用精确
    定时器**：少一个"为什么晚了半拍"的猜测。
    """
    from PySide6.QtCore import Qt

    return Qt.PreciseTimer
