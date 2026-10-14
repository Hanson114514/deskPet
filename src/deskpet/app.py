"""装配层：``QApplication`` 引导 + 命令行最小解析 + 启动编排 + 事件循环。

task01 **只支持"能退出"的最小参数集**（``--help`` / ``--version`` / ``--exit-after``）。
附录 A 的"参数解析位置约定"写得很清楚：``--pet`` / ``--sprite`` / ``--list-*``
属于**加载层**（task03），``--position`` / ``--size-level`` 属于**尺寸与位置 task**
（task04 / task08）。在这里提前实现它们会跟后面的 task 互相踩，**不要**做。
"""



import argparse
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import PROGRAM_NAME, __version__
from .pet_window import PetWindow

#: 让 Python 解释器有机会执行信号处理器的"心跳"间隔（毫秒）。
#: Qt 的事件循环不会自己回到解释器，没有这个心跳时 Ctrl+C 不会及时生效。
_SIGNAL_HEARTBEAT_MS = 200


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="deskPet 桌宠（task01：透明置顶的占位窗口）",
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
    """打印一行启动状态：位置 + 尺寸 + 遮罩 + DPI 缩放。

    位置取自窗口的**权威位置**（不是回读窗口坐标）。
    只用 ASCII 标签：控制台编码随环境而变（agent.md §4.3），
    一行启动信息不该因为 GBK 控制台而把程序炸掉。（正式日志属 task02。）
    """
    position = window.base_position
    size = window.size()
    # QWidget 没有 hasMask()，遮罩要问 mask()（空区域 = 没设遮罩）。
    mask_state = "on" if not window.mask().isEmpty() else "off"
    print(
        f"{PROGRAM_NAME} {__version__} | pos=({position.x()}, {position.y()}) "
        f"size={size.width()}x{size.height()} mask={mask_state} "
        f"dpr={window.devicePixelRatioF():.2f}",
        flush=True,
    )


def run(argv: list[str] | None = None) -> int:
    """**唯一入口**：建 ``QApplication``、建窗口、进事件循环，返回退出码。

    ``argv`` 为 ``None`` 时用 ``sys.argv[1:]``（``--help`` 会照常打印并 ``SystemExit``）。
    """
    args = _build_parser().parse_args(argv)

    raw_argv = list(sys.argv) if argv is None else [sys.argv[0], *argv]
    app = QApplication.instance()
    if app is None:
        app = QApplication(raw_argv)

    app.setApplicationName(PROGRAM_NAME)
    app.setApplicationDisplayName(PROGRAM_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(True)

    window = PetWindow()
    window.showing_at_default_position()
    window.show()

    _print_startup_line(window)

    heartbeat = _install_sigint_handler(app)

    if args.exit_after is not None and args.exit_after >= 0:
        QTimer.singleShot(int(args.exit_after * 1000), window.close)

    try:
        return app.exec()
    finally:
        # 退出收尾。task01 没有热键 / 音效设备 / 单实例锁要放，
        # 只收掉自己的心跳定时器（那些属 task10 / task12 / task13）。
        if heartbeat is not None:
            heartbeat.stop()
