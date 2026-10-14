"""日志装配：控制台 + 文件 + 滚动 + 启动分隔行 + 崩溃兜底。

**为什么叫 `logger` 不叫 `logging`**：`logging` 是标准库模块名，自己起一个同名模块
会把框架内部的 import 也劫持掉（agent.md §4.13 / 迁移清单 3.9）。

本模块的两条硬约定（agent.md §2.10 / §3.7）：

1. **写日志失败绝不抛异常** —— 不能为了"记一笔"把主流程搞崩。
2. **崩溃兜底故意不 import 项目模块** —— 包自己可能正是坏掉的那一环。
   它自己复刻一遍"程序目录 + data"规则，**并且和 `paths.py` 对一次**
   （`_assert_paths_consistent()`，防两边各写一份然后分叉）。

> `paths.py` 是唯一允许用 `__file__` 推路径的模块；本模块用 `sys.argv[0]` 复刻同一
> 条规则只为**容错**，正常路径永远走 `paths.py`。
"""

import logging
import logging.handlers
import os
import sys
import threading
import time
import traceback
from pathlib import Path

#: 日志文件大小上限：256 KB（附录 C「日志上限 / 备份数 = 256 KB / 1」）。
LOG_MAX_BYTES = 256 * 1024

#: 历史日志保留份数：**只保留 1 份**（滚动后是 `deskpet.log.1`）。
LOG_BACKUP_COUNT = 1

#: 日志文件名（项目命名定案：`deskpet.log`，agent.md §0）。
LOG_FILE_NAME = "deskpet.log"

#: 崩溃记录文件名。
CRASH_FILE_NAME = "crash.log"

#: 用户数据目录名。**故意和 `paths.DATA_DIR_NAME` 各写一份**：
#: 崩溃兜底不许 import 项目模块，两边的一致性由 `_assert_paths_consistent()` 守着。
_DATA_DIR_NAME = "data"

#: 单条日志的字符上限。RotatingFileHandler 的 `maxBytes` 在 Windows 上按**字节**算，
#: 一条超长记录会导致"刚滚完又立刻触发滚动"的死循环，所以主动截断。
#: 4 KB 一定小于 `LOG_MAX_BYTES`，留足安全余量。
_LOG_RECORD_MAX_CHARS = 4096

#: 崩溃兜底的**测试缝**：自检要在子进程里把"崩溃记录往哪写"重定向到临时目录
#: （否则会落进真实仓库）。值是**数据目录本身**（`<程序目录>/data` 那一个），
#: 设了它就只认它。生产运行**绝不**设置；`paths.py` 那边没有这条缝，也不该有。
_CRASH_DIR_ENV = "DESKPET_CRASH_PROBE_DIR"

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

#: 启动分隔行的格式（task02 §2：日志里每启动一次都有一行 `===== ... =====`）。
_BANNER_FORMAT = "===== %Y-%m-%d %H:%M:%S ====="

_logger = logging.getLogger("deskpet.logger")

#: 已经装过文件 handler 的日志文件路径（防止 setup_logging 被调两次装两个 handler）。
_installed_log_path: Path | None = None

#: 崩溃兜底是否已装（幂等）。
_crash_handler_installed = False


# ------------------------------------------------------------------ 路径复刻（只依赖标准库）


def _stdlib_program_dir() -> Path:
    """崩溃兜底自己那份"程序目录"规则（**只依赖标准库**）。

    与 `paths.program_dir()` 同形：冻结时是 exe 目录；`python -m` 时是**包根**
    （本项目即 `src/`）；否则是入口脚本目录。正常启动**永远走 `paths.py`**；
    这里只是"包已经 import 不进来"时的备胎，所以它必须自己把 `-m` 这种情况也
    处理掉（否则 `python -m deskpet` 下会给出 `src/deskpet`，与 `paths.py` 漂移）。

    > 两份规则的一致性由 `_assert_paths_consistent()` 守着。task03 在 `-m` 上
    > 排查过一个"数据目录跑到 `src/` 下面"的现象：那**不是**本函数的错，
    > 而是 `-m` 下包根确实是 `src/`（见 `paths._module_entry_dir()` 的边界说明）。
    """
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        executable = getattr(sys, "executable", "") or ""
        if executable:
            return Path(executable).resolve().parent

    # `python -m 包名`：`__main__.__file__` 是 `<包目录>/__main__.py`，
    # 程序目录要的是**包根**（一路走到不在包内为止）。
    main_module = sys.modules.get("__main__")
    spec = getattr(main_module, "__spec__", None)
    main_file = getattr(main_module, "__file__", None)
    if (
        spec is not None
        and getattr(spec, "name", "").endswith("__main__")
        and main_file
    ):
        try:
            inside = Path(main_file).resolve().parent
            while (inside / "__init__.py").is_file():
                inside = inside.parent
            return inside
        except OSError:  # pragma: no cover
            pass

    entry = (sys.argv[0] if sys.argv else "") or ""
    try:
        if entry:
            return Path(entry).resolve().parent
    except OSError:  # pragma: no cover
        pass
    return Path.cwd()


def _stdlib_data_dir() -> Path:
    """崩溃兜底自己那份"用户数据目录"规则（`<程序目录>/data`）。

    若设了 `_CRASH_DIR_ENV`（只在自检里设），**它就是数据目录本身** —— 用来把崩溃
    记录关进临时目录，而不是写进真实仓库。这个变量只影响崩溃记录这一条路径。
    """
    probed = os.environ.get(_CRASH_DIR_ENV, "")
    if probed:
        return Path(probed)
    return _stdlib_program_dir() / _DATA_DIR_NAME


def _assert_paths_consistent() -> None:
    """断言"崩溃兜底复刻的规则"与 `paths.py` 一致（**防漂移**，迁移清单 3.7）。

    **只在源码运行、且 `paths` 能 import** 时成立；打包后 `sys.argv[0]` 与
    `sys.executable` 是两条不同的推导路径，比对没有意义，直接跳过。
    这里也**绝不因为断言失败就阻断启动**——诊断代码不能比正主更凶。
    """
    try:
        from . import paths  # 延迟 import：崩溃路径要能在包坏掉时绕开它
    except Exception:
        return
    if paths.is_frozen():
        return

    expected = paths.data_dir()
    actual = _stdlib_data_dir()
    if expected != actual:  # pragma: no cover - 只有代码被改歪了才会走到
        print(
            f"[deskpet.logger] 崩溃兜底的数据目录与 paths.py 不一致："
            f"{actual} != {expected}",
            file=sys.stderr,
        )


# ------------------------------------------------------------------ handler


class _SafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """滚动写日志：**日志层自己出错也必须咽下去**。

    标准库的 `RotatingFileHandler.emit()` 在 `maxBytes` 用尽又不能滚动时
    （例如单条记录就超过上限）会调用 `handleError()`，默认实现会把
    "--- Logging error ---" 打到 stderr，甚至可能再抛一次。这里主动检查：
    记录超限且还没滚动过 → **丢掉这条**，绝不再触发滚动、也绝不抛。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # 先按标准的"超了就先滚"预判一次，**然后才真的写**：
            # 记录一定已经被格式器截到远小于 maxBytes（见 _TruncatingFormatter），
            # 所以滚动之后不会再立刻触发第二次。
            if getattr(self, "stream", None) is not None and self.shouldRollover(record):
                self.doRollover()
            logging.FileHandler.emit(self, record)
        except Exception:
            try:
                self.handleError(record)
            except Exception:  # pragma: no cover - handleError 自己也可能炸
                pass


class _SafeStreamHandler(logging.StreamHandler):
    """控制台 handler：控制台不存在 / 编码装不下时也不许抛。

    打包成无控制台的窗口程序后 stdout 可能是 `None` 或已关闭
    （agent.md §4.10）；那时**只丢控制台这一路**，文件那一路照常。
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = getattr(self, "stream", None)
            if stream is None:
                return
            logging.StreamHandler.emit(self, record)
        except Exception:
            try:
                self.handleError(record)
            except Exception:  # pragma: no cover
                pass


class _TruncatingFormatter(logging.Formatter):
    """给每条记录一个字符上限，保证单条一定小于滚动阈值。"""

    def __init__(self, fmt: str, datefmt: str, limit: int) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._limit = limit

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if len(text) <= self._limit:
            return text
        return text[: self._limit] + f"...[已截断，原长 {len(text)} 字符]"


# ------------------------------------------------------------------ 装配


def _console_stream():
    """挑一个能用的控制台流；一个都没有就返回 `None`（只丢控制台这一路）。"""
    for candidate in (sys.stdout, sys.stderr):
        if candidate is None:
            continue
        try:
            if candidate.closed:
                continue
        except Exception:  # pragma: no cover - 某些环境下没有 closed 属性
            pass
        return candidate
    return None


def setup_logging(level: int = logging.INFO) -> None:
    """装根 logger：控制台 handler（存在时）+ 文件 handler（`utf-8`）。

    - 文件固定写在 `<程序目录>/data/deskpet.log`（**不是**资源目录，agent.md §5 断言）；
    - 滚动参数固定 `maxBytes=256*1024` / `backupCount=1` / `encoding="utf-8"`；
    - 会**幂等**：重复调用不会装两个文件 handler；
    - **永不抛异常**：连 `data/` 都建不出来时只留一条 stderr 线索，
      由调用方（`app.run()`）去走 `ensure_data_dir()` 的致命路径（agent.md §2.10）。
    """
    global _installed_log_path

    root = logging.getLogger()
    if level is not None:
        root.setLevel(level)

    formatter = _TruncatingFormatter(_LOG_FORMAT, _LOG_DATE_FORMAT, _LOG_RECORD_MAX_CHARS)

    # --- 控制台（存在才装；重复调用不重复装）
    stream = _console_stream()
    if stream is not None and not any(
        isinstance(handler, _SafeStreamHandler) for handler in root.handlers
    ):
        console = _SafeStreamHandler(stream)
        console.setFormatter(formatter)
        root.addHandler(console)

    # --- 文件
    target: Path | None
    try:
        from . import paths

        target = paths.log_path()
        target.parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # 连路径都问不出来 / 目录建不了
        target = None
        print(f"[deskpet.logger] 无法准备日志文件：{exc}", file=sys.stderr)

    if target is None:
        return

    if _installed_log_path == target and any(
        isinstance(handler, _SafeRotatingFileHandler) and Path(handler.baseFilename) == target
        for handler in root.handlers
    ):
        return

    try:
        file_handler = _SafeRotatingFileHandler(
            target,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        # 写日志失败绝不抛异常（agent.md §2.10）。
        print(f"[deskpet.logger] 日志文件不可用：{target}（{exc}）", file=sys.stderr)
        return

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    _installed_log_path = target


def log_startup_banner() -> None:
    """追加一行 `===== YYYY-MM-DD HH:MM:SS =====` 作为本次启动的分隔。

    **刻意绕开 logger 的格式器**：这一行是纯分隔线，不该被加上
    `时间 级别 logger名` 前缀（那样就不再是"一行 `=====`"了）。
    直接写文件 handler 的流，并立刻 `flush`（崩溃时才不会丢掉它）。

    **写日志失败绝不抛异常**（agent.md §2.10）：文件没准备好、控制台不存在，
    都只是"少了一条分隔线"，不能让启动失败。
    """
    line = time.strftime(_BANNER_FORMAT)
    try:
        for handler in logging.getLogger().handlers:
            if isinstance(handler, _SafeRotatingFileHandler):
                handler.stream.write(line + "\n")
                handler.flush()
    except Exception:  # pragma: no cover - 日志层不该抛，但绝不冒这个险
        pass


# ------------------------------------------------------------------ 致命错误提示


def show_fatal_error(message: str) -> bool:
    """启动期致命错误：**能弹就弹**一个模态窗口，返回是否真的弹出来了。

    Qt 只在**确实需要**时导入（本模块平时不许依赖 Qt）；没有 `QApplication` 时
    返回 `False`，调用方仍要**写日志 + 非 0 退出**（agent.md §2.10 第一类）。
    """
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return False

    app = QApplication.instance()
    if app is None:
        return False

    try:
        from . import PROGRAM_NAME

        dialog = QMessageBox()
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle(f"{PROGRAM_NAME} 启动失败")
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.exec()
        return True
    except Exception:  # pragma: no cover - 弹窗失败不改变"致命"这个结论
        return False


def show_message(title: str, message: str) -> bool:
    """一条**用户需要知道并采取行动**的提示（不是致命错误）。

    与 `show_fatal_error()` 同一条纪律：Qt 只在确实需要时导入，
    没有 `QApplication` / 弹不出来时返回 `False`，**调用方仍要写日志**
    （`agent.md §2.10`：需要用户行动的弹窗 + 日志）。

    ⚠ 它是**模态**的（`dialog.exec()`），所以**只允许在事件循环已经跑起来、
    窗口可见**的时候调用。启动期的致命错误走 `show_fatal_error()`，
    只读查询与命令行用法错误走 stderr + 退出码（那些都发生在建 app 之前）。
    """
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except Exception:
        return False

    if QApplication.instance() is None:
        return False

    try:
        dialog = QMessageBox()
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.exec()
        return True
    except Exception:  # pragma: no cover - 弹不出来不改变"已经记了日志"这个事实
        return False


# ------------------------------------------------------------------ 崩溃兜底


def _write_crash_record(text: str) -> Path | None:
    """把一段文本追加进 `<程序目录>/data/crash.log`；**绝不抛异常**。

    自己复刻"程序目录 + data"规则（不 import `paths`），目录建不出来时退到
    临时目录——**诊断信息的价值在于它还能写出来**。返回实际写入的文件。
    """
    candidates: list[Path] = [_stdlib_data_dir()]
    try:
        import tempfile

        candidates.append(Path(tempfile.gettempdir()) / "deskpet-crash")
    except Exception:  # pragma: no cover
        pass

    for directory in candidates:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / CRASH_FILE_NAME
            with target.open("a", encoding="utf-8", errors="backslashreplace") as handle:
                handle.write(text)
            return target
        except Exception:
            continue

    print("[deskpet.logger] 连崩溃记录都写不出来", file=sys.stderr)
    return None


def _format_crash_text(exc_type: type[BaseException], exc: BaseException, tb: object) -> str:
    """把异常格式化成崩溃记录的一段（含时间戳与完整 traceback）。"""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    body = "".join(traceback.format_exception(exc_type, exc, tb))  # type: ignore[arg-type]
    return f"===== {stamp} =====\n{body}\n"


def install_crash_handler() -> None:
    """装崩溃兜底：未捕获异常写 `crash.log`，**再交给原处理器**（不吞掉）。

    三件事：

    1. `sys.excepthook` —— 主线程未捕获异常；
    2. `threading.excepthook` —— 子线程未捕获异常（将来引入线程时不再静默）；
    3. `logging.raiseExceptions = False` —— 日志层内部错误不要反过来炸主流程。

    幂等；**只依赖标准库**。
    """
    global _crash_handler_installed
    if _crash_handler_installed:
        return

    logging.raiseExceptions = False

    previous_hook = sys.excepthook

    def _excepthook(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: object,
    ) -> None:
        try:
            target = _write_crash_record(_format_crash_text(exc_type, exc, tb))
            if target is not None:
                print(f"[deskpet.logger] 崩溃记录已写入 {target}", file=sys.stderr)
        except Exception:  # pragma: no cover - 兜底里的兜底
            pass
        try:
            previous_hook(exc_type, exc, tb)
        except Exception:  # pragma: no cover
            pass

    sys.excepthook = _excepthook

    if hasattr(threading, "excepthook"):
        previous_thread_hook = threading.excepthook

        def _thread_excepthook(args: object) -> None:
            try:
                _write_crash_record(
                    _format_crash_text(
                        getattr(args, "exc_type", BaseException),
                        getattr(args, "exc_value", BaseException()),
                        getattr(args, "exc_traceback", None),
                    )
                )
            except Exception:  # pragma: no cover
                pass
            try:
                previous_thread_hook(args)  # type: ignore[arg-type]
            except Exception:  # pragma: no cover
                pass

        threading.excepthook = _thread_excepthook  # type: ignore[assignment]

    _crash_handler_installed = True


# ------------------------------------------------------------------ 模块级调用

# 约定：**模块级调用一律写在函数定义之后**（曾经写反导致 NameError，等于没有诊断）。
_assert_paths_consistent()
