"""装配层：``QApplication`` 引导 + 命令行最小解析 + 启动编排 + 事件循环。

task02 起启动顺序**固定**为：

1. `install_crash_handler()` —— **最先**，之后任何一步炸了都有 `crash.log`；
2. `ensure_data_dir()` —— 地基先立起来；建不出来是**启动期致命**
   （弹窗 + 日志 + 非 0 退出），此时**还没装 logger**，所以那条致命日志只上控制台；
3. `setup_logging()` + `log_startup_banner()` —— 装控制台 + 文件（失败只降级，不抛）；
4. `ConfigStore(...).load()` —— 读配置，**永不抛异常**；
5. 建窗口、进事件循环。

（顺序上"先建数据目录、再装文件日志"是必须的：日志文件本身就住在 `data/` 里。）

附录 A 的"参数解析位置约定"写得很清楚：``--pet`` / ``--sprite`` / ``--list-*``
属于**加载层**（task03），``--position`` / ``--size-level`` 属于**尺寸与位置 task**
（task04 / task08）。在这里提前实现它们会跟后面的 task 互相踩，**不要**做。
"""


import argparse
import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import PROGRAM_NAME, __version__
from . import logger as log
from . import paths
from .config import ConfigStore
from .errors import DataDirError
from .pet_window import PetWindow

#: 让 Python 解释器有机会执行信号处理器的"心跳"间隔（毫秒）。
#: Qt 的事件循环不会自己回到解释器，没有这个心跳时 Ctrl+C 不会及时生效。
_SIGNAL_HEARTBEAT_MS = 200

_logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="deskPet 桌宠（task02：路径 / 配置 / 日志）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROGRAM_NAME} {__version__}",
    )
    parser.add_argument(
        "--exit-after",
        type=float,
        default=None,
        metavar="秒",
        help="启动后等待指定的秒数自动退出（自动验证用；正常使用不要给）",
    )
    return parser


def _install_sigint_handler(app: QApplication) -> QTimer | None:
    """把 SIGINT（Ctrl+C）转成"退出请求"，并返回心跳定时器。

    装不上（非主线程 / 平台不支持）时**只降级**，不阻断启动。
    """
    def _request_quit(signum: int, frame: object) -> None:
        app.quit()

    try:
        signal.signal(signal.SIGINT, _request_quit)
    except (ValueError, OSError):
        return None

    heartbeat = QTimer()
    heartbeat.setInterval(_SIGNAL_HEARTBEAT_MS)
    heartbeat.timeout.connect(lambda: None)  # 只为把控制权还给解释器
    heartbeat.start()
    return heartbeat


def _print_startup_line(window: PetWindow) -> None:
    """记一行启动状态：位置 + 尺寸 + 遮罩 + DPI 缩放。

    位置取自窗口的**权威位置**（不是回读窗口坐标）。
    写进日志（同时上控制台）；**不写盘**、不碰配置。
    """
    position = window.base_position
    size = window.size()
    # QWidget 没有 hasMask()，遮罩要问 mask()（空区域 = 没设遮罩）。
    mask_state = "on" if not window.mask().isEmpty() else "off"
    _logger.info(
        "%s %s | pos=(%d, %d) size=%dx%d mask=%s dpr=%.2f",
        PROGRAM_NAME,
        __version__,
        position.x(),
        position.y(),
        size.width(),
        size.height(),
        mask_state,
        window.devicePixelRatioF(),
    )


def _log_config_summary(store: ConfigStore) -> None:
    """把"配置是从哪来的"记一笔。

    只记**来源**，不逐项刷值：配置里可能有将来才用到的字段，
    启动期把它们全打出来只会让日志变噪音。**读操作只读**（不写盘、不修复）。
    """
    values = store.to_dict()
    from_file = [key for key in values if store.has(key)]
    _logger.info(
        "配置：%s（%d/%d 项来自文件，其余为默认值）",
        store.path,
        len(from_file),
        len(values),
    )


def run(argv: list[str] | None = None) -> int:
    """**唯一入口**：建 ``QApplication``、建窗口、进事件循环，返回退出码。

    ``argv`` 为 ``None`` 时用 ``sys.argv[1:]``（``--help`` 会照常打印并 ``SystemExit``）。
    """
    # 崩溃兜底必须**最先**装：下面任何一步炸掉都要留得下痕迹（agent.md §2.10）。
    log.install_crash_handler()

    # 先把用户数据目录准备好：它是配置与日志的地基，建不出来就是**启动期致命**
    # （弹窗 + 日志 + 非 0 退出）。这里**还没有装 logger**，所以致命日志只能上控制台——
    # 故意的：日志文件本身就住在 data/ 里，先建目录、再谈往它里面写。
    try:
        paths.ensure_data_dir()
    except DataDirError as exc:
        logging.getLogger(__name__).critical("用户数据目录不可用，启动中止：%s", exc)
        log.show_fatal_error(str(exc))
        return 1

    # 日志装配本身永不抛异常；装不上文件 handler 只丢那一路。
    log.setup_logging()
    log.log_startup_banner()
    _logger.info("%s %s 启动（%s）", PROGRAM_NAME, __version__, paths.program_dir())

    args = _build_parser().parse_args(argv)

    raw_argv = list(sys.argv) if argv is None else [sys.argv[0], *argv]
    app = QApplication.instance()
    if app is None:
        app = QApplication(raw_argv)

    app.setApplicationName(PROGRAM_NAME)
    app.setApplicationDisplayName(PROGRAM_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(True)

    # 读配置（永不抛异常）。task02 **只建立这条链路**：
    # 档位不解释（task04）、位置不夹取（task08）、热键不注册（task12）。
    store = ConfigStore()
    store.load()
    _log_config_summary(store)

    window = PetWindow()
    window.showing_at_default_position()
    window.show()

    _print_startup_line(window)

    heartbeat = _install_sigint_handler(app)

    if args.exit_after is not None and args.exit_after >= 0:
        QTimer.singleShot(int(args.exit_after * 1000), window.close)

    try:
        code = app.exec()
    finally:
        # 退出收尾。task01 没有热键 / 音效设备 / 单实例锁要放，
        # 只收掉自己的心跳定时器（那些属 task10 / task12 / task13）。
        if heartbeat is not None:
            heartbeat.stop()
    _logger.info("%s 退出（退出码 %d）", PROGRAM_NAME, code)
    return code
