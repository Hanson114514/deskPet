"""装配层：`QApplication` 引导 + 命令行解析 + 启动编排 + 事件循环。

task02 起启动顺序**固定**为：

1. `install_crash_handler()` —— **最先**，之后任何一步炸了都有 `crash.log`；
2. `_build_parser().parse_args()` —— task03 起放在建目录之前：`--list-pets` /
   `--list-actions` 是**只读查询**，不该因为"建不出 `data/`"而失败；
3. `ensure_data_dir()` —— 地基先立起来；建不出来是**启动期致命**
   （弹窗 + 日志 + 非 0 退出），此时**还没装 logger**，所以那条致命日志只上控制台；
4. `setup_logging()` + `log_startup_banner()` —— 装控制台 + 文件（失败只降级，不抛）；
5. `ConfigStore(...).load()` —— 读配置，**永不抛异常**；
6. `resolve_pet()` —— 解出该用哪个素材包（配置来源坏包 → 回退默认形象 + 警告）；
7. 建窗口、进事件循环。

（顺序上"先建数据目录、再装文件日志"是必须的：日志文件本身就住在 `data/` 里。）

## 本 task（task03）新增的命令行

| 参数 | 语义 |
| --- | --- |
| `--pet <命名空间>` | 指定素材包（**命令行显式指定 → 坏包如实报错退出**） |
| `--sprite <命名空间或目录>` | 同一件事的另一种说法，与 `--pet` 走**同一条**解析规则 |
| `--action <动作>` | 显示哪个动作的**第一帧**（动画播放属 task05） |
| `--list-pets` | 只读查询：列出全部素材包 + 校验结论 + 音效摘要，然后退出 |
| `--list-actions` | 只读查询：列出动作名 / 帧数 / 解析后的帧率与**来源**，然后退出 |

附录 A 的"参数解析位置约定"：`--position` / `--size-level` 属于尺寸与位置 task
（task04 / task08），**不要**在这里提前实现。
"""

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from . import PROGRAM_NAME, __version__
from . import logger as log
from . import paths, pet_loader
from .config import ConfigStore
from .errors import DataDirError
from .pet_loader import PetLoadError, list_reports, resolve_pet
from .pet_spec import PetSpec
from .pet_window import PetWindow

#: 让 Python 解释器有机会执行信号处理器的"心跳"间隔（毫秒）。
#: Qt 的事件循环不会自己回到解释器，没有这个心跳时 Ctrl+C 不会及时生效。
_SIGNAL_HEARTBEAT_MS = 200

#: `--action` 的默认值。**它是"首选"而不是"必须有"**：素材里没有 `idle` 时
#: 按 `action_order` 的第一个动作退化（见 `pet_window._pick_action`）。
DEFAULT_ACTION = "idle"

#: 只读查询返回的退出码。
_EXIT_OK = 0

#: 命令行指定的素材包不可用 / 连默认形象都不可用时的退出码。
_EXIT_PET_UNAVAILABLE = 2

_logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="deskPet 桌宠（task03：素材加载与一次列全校验）",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PROGRAM_NAME} {__version__}",
    )
    parser.add_argument(
        "--pet",
        default=None,
        metavar="命名空间",
        help="指定素材包（命名空间，如 weedy）",
    )
    parser.add_argument(
        "--sprite",
        default=None,
        metavar="命名空间或目录",
        help="指定素材包（与 --pet 同一条解析规则；--pet 优先）",
    )
    parser.add_argument(
        "--action",
        default=DEFAULT_ACTION,
        metavar="动作",
        help=f"显示哪个动作的第一帧（缺省 {DEFAULT_ACTION}）",
    )
    parser.add_argument(
        "--list-pets",
        action="store_true",
        help="列出全部素材包及校验结论、音效摘要，然后退出",
    )
    parser.add_argument(
        "--list-actions",
        action="store_true",
        help="列出当前素材包的动作、帧数与帧率来源，然后退出",
    )
    parser.add_argument(
        "--exit-after",
        type=float,
        default=None,
        metavar="秒",
        help="启动后等待指定的秒数自动退出（自动验证用；正常使用不要给）",
    )
    return parser


# ------------------------------------------------------------------ 只读查询


def _run_list_pets() -> int:
    """`--list-pets`：只读查询。**不建数据目录、不装日志、不建窗口**。"""
    reports = list_reports()
    if not reports:
        print("没有找到任何素材包。")
        print(f"  自带素材根：{paths.bundled_pets_dir()}")
        print(f"  用户素材根：{paths.user_pets_dir()}")
        return _EXIT_OK

    print(f"找到 {len(reports)} 个素材包：")
    for report in reports:
        print(f"  · {report.namespace}")
        print(f"      展示名：{report.display_name}")
        print(f"      来源：{report.source}")
        print(f"      校验：{report.conclusion()}")
        print(f"      音效：{report.sound_summary()}")
        for issue in report.issues:
            print(f"      ! {issue}")
        for warning in report.warnings:
            print(f"      ~ {warning}")
    return _EXIT_OK


def _run_list_actions(spec: PetSpec) -> int:
    """`--list-actions`：只读查询，打印动作 / 帧数 / 帧率与**来源**。"""
    print(f"素材包：{spec.namespace}（{spec.display_name}，来源 {spec.source}）")
    print(f"  画布 cell：{spec.cell[0]}×{spec.cell[1]}")
    print(f"  动作数：{len(spec.actions)}")
    print("  动作（按索引里的顺序）：")
    for name in spec.action_order:
        action = spec.actions[name]
        print(
            f"    · {name}：{action.frame_count} 帧，"
            f"{action.fps:g} fps（每帧 {action.frame_ms:.4f} ms，来源：{action.fps_source.label}）"
        )
    if spec.sounds:
        print("  音效：")
        for sound_name, filename in spec.sounds.items():
            shown = filename if filename else "(索引里声明了，但没有给文件名)"
            print(f"    · {sound_name} → {shown}")
    else:
        print("  音效：无音效")
    return _EXIT_OK


# ------------------------------------------------------------------ 启动编排


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
    """记一行启动状态：素材包 + 动作 + 位置 + 尺寸 + 遮罩 + DPI 缩放。

    位置取自窗口的**权威位置**（不是回读窗口坐标）。
    写进日志（同时上控制台）；**不写盘**、不碰配置。
    """
    position = window.base_position
    size = window.size()
    # QWidget 没有 hasMask()，遮罩要问 mask()（空区域 = 没设遮罩）。
    mask_state = "on" if not window.mask().isEmpty() else "off"
    _logger.info(
        "%s %s | pet=%s action=%s pos=(%d, %d) size=%dx%d mask=%s dpr=%.2f",
        PROGRAM_NAME,
        __version__,
        window.pet.namespace,
        window.action,
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


def _report_resolution_problems(issues: tuple, fell_back: bool, config_pet: str | None) -> None:
    """把解析素材包时攒下的问题**一次性全部**写进日志（§2.10 第三类）。"""
    for issue in issues:
        _logger.warning("素材包问题：%s", issue)
    if fell_back:
        _logger.warning(
            "配置里的素材包 %r 不可用，已回退到默认形象 %s"
            "（本次**不写配置**：回写配置属 task11）",
            config_pet,
            pet_loader.DEFAULT_PET_NAMESPACE,
        )


def _resolve_or_die(
    cli_spec: str | None,
    store: ConfigStore,
    *,
    query_only: bool,
) -> PetSpec:
    """解出该用哪个素材包；**该报错时当场报错并退出进程**。

    规则来自 `迁移清单.md` §1（"角色与素材"）：

    - 命令行（`--pet` / `--sprite`）显式给了一个说法 → 坏包**如实报错 + 非 0 退出**，
      绝不偷偷换形象；
    - 配置 `pet` 指向坏包 → **回退默认形象 + 一条警告**，**不阻断启动**；
    - 命令行与配置都没表态（含 `--pet ""`：空串 = 用户还没表态，§2.4）→ 用默认形象；
    - 连默认形象都不可用 → 启动期致命（"假装能跑"比报错更糟）。

    `query_only=True` 用于 `--list-actions` / `--list-pets` 这类**只读查询**：
    此时**还没有 `QApplication`**，所以错误只上 stderr（`log.show_fatal_error()`
    在没有 app 时本来也只返回 `False`；何况给一个命令行查询弹模态框没有意义，
    而且 `dialog.exec()` 会**阻塞住进程**——task03 实际踩到）。
    """
    config_pet = store.get("pet") if store.has("pet") else None

    def _die(message: str) -> None:
        _logger.error("%s", message)
        if query_only:
            print(message, file=sys.stderr)
        else:
            log.show_fatal_error(message)
        raise SystemExit(_EXIT_PET_UNAVAILABLE)

    if cli_spec is not None:
        # 命令行**表态过**（哪怕给的是空串）→ 不许拿配置或默认形象顶替。
        result = resolve_pet(cli_spec, None)
        if result.spec is None:
            detail = "\n".join(f"  - {issue}" for issue in result.issues)
            _die(f"命令行指定的素材包 {cli_spec!r} 不可用：\n{detail}")
        _report_resolution_problems(result.issues, result.fell_back, config_pet)
        return result.spec  # type: ignore[return-value]

    result = resolve_pet(None, config_pet)
    if result.spec is not None:
        _report_resolution_problems(result.issues, result.fell_back, config_pet)
        return result.spec

    detail = "\n".join(f"  - {issue}" for issue in result.issues) or "  - （没有拿到任何原因）"
    _die(
        f"没有任何可用的素材包（连默认形象 {pet_loader.DEFAULT_PET_NAMESPACE} "
        f"都不可用）：\n{detail}"
    )
    raise AssertionError("unreachable")  # pragma: no cover - _die 一定抛


def run(argv: list[str] | None = None) -> int:
    """**唯一入口**：解析命令行、按需只读查询或建窗口进事件循环。

    ``argv`` 为 ``None`` 时用 ``sys.argv[1:]``（``--help`` 会照常打印并 ``SystemExit``）。

    两条命令的层次**故意不同**（`迁移清单.md` 第三部分 3.2 "只读查询最先"）：

    - `--list-pets`：连 `data/` 与日志都不需要，**最先**返回；
    - `--list-actions`：需要配置（要解出素材包），但**在 `QApplication` 之前**返回——
      于是它既用不上 Qt，也不会因为一句命令行错误弹出模态框。
    """
    # 崩溃兜底必须**最先**装：下面任何一步炸掉都要留得下痕迹（agent.md §2.10）。
    log.install_crash_handler()

    # 命令行解析放在"建数据目录"之前：只读查询不该被"建不出 data/"挡住。
    args = _build_parser().parse_args(argv)

    # `--pet` 优先；两者都没给时是 `None`（"没表态"，不是空串）。
    cli_spec = args.pet if args.pet is not None else args.sprite

    if args.list_pets:
        return _run_list_pets()

    # 先把用户数据目录准备好：它是配置与日志的地基，建不出来就是**启动期致命**
    # （弹窗 + 日志 + 非 0 退出）。这里**还没有 QApplication**，所以致命日志只能上控制台——
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

    # 读配置（永不抛异常）。task02 起 **只建立这条链路**：
    # 档位不解释（task04）、位置不夹取（task08）、热键不注册（task12）。
    store = ConfigStore()
    store.load()
    _log_config_summary(store)

    if args.list_actions:
        # **只读查询走完就返回**：下面才建 `QApplication`。
        return _run_list_actions(_resolve_or_die(cli_spec, store, query_only=True))

    raw_argv = list(sys.argv) if argv is None else [sys.argv[0], *argv]
    app = QApplication.instance()
    if app is None:
        app = QApplication(raw_argv)

    app.setApplicationName(PROGRAM_NAME)
    app.setApplicationDisplayName(PROGRAM_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(True)

    pet = _resolve_or_die(cli_spec, store, query_only=False)

    # 起窗口之前先把动作名定死：起完窗口再报错就会留下一个关不掉的对话框
    # （`show_fatal_error()` 是模态的、会阻塞事件循环，task03 实测踩到）。
    #
    # 这里**不用弹窗**、直接上 stderr + 非 0 退出：`--action` 是命令行给的说法，
    # 与 `--pet` 给坏名字是同一类**用法错误**，用户就坐在终端前面。
    # （启动期致命那两处仍然弹窗：那里用户可能是双击启动的，没有终端可看。）
    action = str(args.action).strip() or DEFAULT_ACTION
    if not pet.has_action(action):
        available = "、".join(pet.action_order) or "（一个都没有）"
        message = f"素材包 {pet.namespace} 里没有动作 {action!r}；可用动作：{available}"
        _logger.error("%s", message)
        print(message, file=sys.stderr)
        return _EXIT_PET_UNAVAILABLE

    window = PetWindow(pet)
    window.show_action_frame(action, 0)
    window.showing_at_default_position()
    window.show()

    _print_startup_line(window)

    heartbeat = _install_sigint_handler(app)

    if args.exit_after is not None and args.exit_after >= 0:
        QTimer.singleShot(int(args.exit_after * 1000), window.close)

    try:
        code = app.exec()
    finally:
        # 退出收尾。本阶段没有热键 / 音效设备 / 单实例锁要放，
        # 只收掉自己的心跳定时器（那些属 task10 / task12 / task13）。
        if heartbeat is not None:
            heartbeat.stop()
    _logger.info("%s 退出（退出码 %d）", PROGRAM_NAME, code)
    return code
