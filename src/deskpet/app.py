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

## 命令行

| 参数 | 语义 | 返回时机 |
| --- | --- | --- |
| `--pet <命名空间>` | 指定素材包（**命令行显式指定 → 坏包如实报错退出**） | — |
| `--sprite <命名空间或目录>` | 同一件事的另一种说法，与 `--pet` 走**同一条**解析规则 | — |
| `--action <动作>` | **播放**哪个动作（缺省 `idle`；坏动作报错并列出可用动作） | — |
| `--list-pets` | 只读查询：列出全部素材包 + 校验结论 + 音效摘要，然后退出 | **最早**（连 `data/` 都不需要） |
| `--list-actions` | 只读查询：列出动作名 / 帧数 / 解析后的帧率与**来源**，然后退出 | **`QApplication` 之前** |
| `--size-level 1..10` | 指定尺寸档位；**越界当场报错退出**（不夹取） | — |
| `--list-sizes` | 列出 10 级的倍数与像素尺寸 + 屏幕高度 + 自适应档位 | **`QApplication` 之后**（要读屏幕） |

**`--fps` 已删除，不得复活**（agent.md §0：帧率由素材的 `asset.json` 决定）。

`--list-actions` 的帧率来源（`asset.json` / 遗留兜底 / 默认 12）由 `pet_spec` 解析；
启动行同样必须打印**动作 + 每帧毫秒 + 帧率来源**（task05 §4）。

`--position` 属 task08，**不要**在这里提前实现。
"""

import argparse
import logging
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import PROGRAM_NAME, __version__
from . import logger as log
from . import paths, pet_loader, size
from .animation import UnknownActionError
from .config import ConfigStore
from .cursor_watch import CursorWatch
from .errors import DataDirError
from .idle_state import IdleStateMachine, normalize_idle_seconds
from .mirror import MirrorState
from .pet_loader import PetLoadError, list_reports, resolve_pet
from .pet_spec import PetSpec
from .pet_window import PetWindow
from .rendering import FrameRenderer  # noqa: F401  （打包静态分析要收全渲染层）

#: 让 Python 解释器有机会执行信号处理器的"心跳"间隔（毫秒）。
#: Qt 的事件循环不会自己回到解释器，没有这个心跳时 Ctrl+C 不会及时生效。
_SIGNAL_HEARTBEAT_MS = 200

#: 久置状态机的 tick 间隔（毫秒）。计时本身是**状态机 + 定时器**（agent.md §4.8），
#: 不是"每个动画帧减一次"——后者在窗口隐藏、精灵停帧之后就永久停住了。
#: 250 ms 的粒度对"20 秒后张望"这件事足够（误差至多一个 tick）。
IDLE_TICK_MS = 250

#: `--action` 的默认值。**它是"首选"而不是"必须有"**：素材里没有 `idle` 时
#: 按 `action_order` 的第一个动作退化（见 `pet_window._pick_action`）。
DEFAULT_ACTION = "idle"

#: 只读查询返回的退出码。
_EXIT_OK = 0

#: 命令行指定的素材包不可用 / 连默认形象都不可用时的退出码。
_EXIT_PET_UNAVAILABLE = 2

_logger = logging.getLogger(__name__)


def _size_level_arg(text: str) -> int:
    """`--size-level` 的解析器：**越界当场报错**（不是夹取）。

    两条规则不要混（task04 §4 硬规则 6）：

    - **命令行**给的说法越界 → `argparse` 报用法错误、非 0 退出（本函数）；
    - **配置/程序内部**的值越界 → `size.clamp_level()` 夹到 1..10。

    命令行这边"夹取"是错的：用户敲了 11 却看到第 10 级，会以为自己看错了。
    """
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} 不是整数") from None
    if not size.is_valid_level(value):
        raise argparse.ArgumentTypeError(
            f"档位必须在 1..{size.LEVEL_COUNT} 之间，实际给了 {value}"
        )
    return value


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
        help=f"播放哪个动作（缺省 {DEFAULT_ACTION}；坏动作会列出可用动作）",
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
        "--size-level",
        type=_size_level_arg,
        default=None,
        metavar="1-10",
        help=f"尺寸档位 1-{size.LEVEL_COUNT}（越界当场报错退出）",
    )
    parser.add_argument(
        "--list-sizes",
        action="store_true",
        help="列出 10 个档位的倍数与像素尺寸、当前屏幕的自适应档位，然后退出",
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


# ------------------------------------------------------------------ 尺寸档位

#: 档位来源的显示文字（**启动信息里必须打印**，task04 §4）。
LEVEL_SOURCE_CLI = "命令行指定"
LEVEL_SOURCE_CONFIG = "沿用上次"
LEVEL_SOURCE_SCREEN = "按屏幕自适应"


def _screen_height() -> int:
    """当前屏幕的**逻辑**高度（拿不到 → 0）。

    **必须在 `QApplication` 起来之后才准**：屏幕自适应需要"真实屏幕 + DPI 缩放"
    （agent.md §4.9），所以 `--list-sizes` 不能像 `--list-actions` 那样在
    建 `QApplication` 之前返回。
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return 0
    return int(screen.geometry().height())


def _resolve_size_level(
    cli_level: int | None,
    store: ConfigStore,
    cell: tuple[int, int],
) -> tuple[int, str]:
    """决定用哪一档，返回 `(档位, 来源文字)`。

    **决定顺序（task04 §4 的硬约定）**：

    1. **命令行** `--size-level`（给了就用，它已经在解析阶段验过范围）；
    2. **上次保存的**——用 `store.has("size_level")` 判定，**不是**
       "读到的值 ≠ 默认值"。用户主动把档位选在默认的第 6 级时 `has()` 为真，
       来源必须显示"沿用上次"而不是"按屏幕自适应"（agent.md §2.4 规则 5）；
    3. 都没有 → **按屏幕自适应**。

    配置里的值越界（例如手改成了 99）走 `clamp_level()` 夹取 + 一条警告：
    配置是"读取永不致命"的那一类（§2.10 第三类），不能因为它起不来。
    """
    if cli_level is not None:
        return size.clamp_level(cli_level), LEVEL_SOURCE_CLI

    if store.has("size_level"):
        raw = store.get("size_level")
        level = size.clamp_level(raw)
        if level != raw:
            _logger.warning("配置里的 size_level=%r 越界，已夹到第 %d 级", raw, level)
        return level, LEVEL_SOURCE_CONFIG

    return size.level_for_screen(_screen_height(), cell), LEVEL_SOURCE_SCREEN


def _run_list_sizes(cell: tuple[int, int]) -> int:
    """`--list-sizes`：只读查询，列出 10 个档位 + 当前屏幕的自适应档位。

    **依赖 `cell`**（像素尺寸按帧的实际尺寸算），所以只能在**素材解出来之后**跑。
    又因为它要读屏幕，必须在 **`QApplication` 建好之后**——
    这也是它比 `--list-actions` 晚一步的原因。
    """
    height = _screen_height()
    adaptive = size.level_for_screen(height, cell)

    print(f"画布 cell：{cell[0]}×{cell[1]}")
    print(f"当前屏幕逻辑高度：{height}")
    print(f"自适应档位：第 {adaptive} 级（目标占比 {size.SCREEN_TARGET_RATIO:.2f}）")
    print(f"档位（共 {size.LEVEL_COUNT} 级）：")
    for level in range(1, size.LEVEL_COUNT + 1):
        width, pixel_height = size.pixel_size(cell, level)
        marks = []
        if level == adaptive:
            marks.append("自适应")
        if level == size.DEFAULT_LEVEL:
            marks.append("默认")
        suffix = f"  ← {'、'.join(marks)}" if marks else ""
        print(
            f"    {level:>2}. ×{size.ratio_text(level)}  "
            f"{width}×{pixel_height}{suffix}"
        )
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


def _print_startup_line(window: PetWindow, level_source: str) -> None:
    """记一行启动状态：素材包 + 动作 + 帧率来源 + 位置 + 档位/来源/像素尺寸 + 遮罩 + DPI。

    位置取自窗口的**权威位置**（不是回读窗口坐标）。
    写进日志（同时上控制台）；**不写盘**、不碰配置。

    task04 起必须带上**档位 + 来源 + 像素尺寸**这三样：
    "宠物怎么突然变小了"这类问题，只看这一行就能判断是"沿用上次"还是"自适应"变了。

    task05 起必须带上**动作 + 每帧毫秒 + 帧率来源**：
    "怎么变快了/变慢了"只有这一行能一眼看出是 `asset.json` 改的、
    还是掉到了遗留兜底表 / 默认 12（task05 §4）。
    """
    position = window.base_position
    width, height = window.pixel_size
    # QWidget 没有 hasMask()，遮罩要问 mask()（空区域 = 没设遮罩）。
    mask_state = "on" if not window.mask().isEmpty() else "off"
    animation = window.animation
    action = window.pet.action(animation.render_action)
    source = action.fps_source.label if action is not None else "（未知）"
    _logger.info(
        "%s %s | pet=%s action=%s %s mode=%s frames=%d frame_ms=%.4f fps_source=%s "
        "pos=(%d, %d) level=%d/%d(%s) size=%dx%d mask=%s mirror=%s dpr=%.2f",
        PROGRAM_NAME,
        __version__,
        window.pet.namespace,
        animation.action,
        animation.render_action,
        animation.mode.value,
        animation.frame_count,
        animation.frame_ms,
        source,
        position.x(),
        position.y(),
        window.level,
        size.LEVEL_COUNT,
        level_source,
        width,
        height,
        mask_state,
        # 镜像这一栏是 task06 加的：翻转是不是**记住了**只看这一行。
        "on" if window.mirrored else "off",
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

    if args.list_sizes:
        # `--list-sizes` **必须晚于 `QApplication`**：屏幕自适应要读真实屏幕
        # （agent.md §4.9 "DPI 缩放需要窗口先起来"）。它也不能像 `--list-actions`
        # 那样在建 app 之前返回，否则"自适应档位"这一行没有屏幕可读。
        return _run_list_sizes(pet.cell)

    # 起窗口之前先把动作名定死：起完窗口再报错就会留下一个关不掉的对话框
    # （`show_fatal_error()` 是模态的、会阻塞事件循环，task03 实测踩到）。
    #
    # 这里**不用弹窗**、直接上 stderr + 非 0 退出：`--action` 是命令行给的说法，
    # 与 `--pet` 给坏名字是同一类**用法错误**，用户就坐在终端前面。
    # （启动期致命那两处仍然弹窗：那里用户可能是双击启动的，没有终端可看。）
    #
    # 可用动作列表由 `animation.UnknownActionError` 统一带出来（**真值只有一处**），
    # 这里先问一下素材、省得白建一个窗口再拆掉。
    action = str(args.action).strip() or DEFAULT_ACTION
    if not pet.has_action(action):
        error = UnknownActionError(pet.namespace, action, pet.action_order)
        _logger.error("%s", error)
        print(str(error), file=sys.stderr)
        return _EXIT_PET_UNAVAILABLE

    level, level_source = _resolve_size_level(args.size_level, store, pet.cell)

    # 镜像状态表：**启动时从配置读一次**（task06 §2 第 6 条：重启后翻转状态还在）。
    # 读操作只读——不会因为"打开程序"就把 mirror 写回配置。
    mirror = MirrorState(store)

    # 久置状态机（task07）：`idle_seconds` 通过**回调现读**，不在构造时快照——
    # 于是程序内改配置（`set()` / `reload()`，将来 task11 那类"改完立刻生效"）不用重建状态机。
    # ⚠ 但**只改磁盘上的 `config.json` 不会生效**：`ConfigStore` 是内存快照，
    # 那种改法要重启（task07 §6 第 2 条也是按"重启后生效"写的）。
    # 读出来的值由 `normalize_idle_seconds()` 兜底 + 夹下限（配置是"读取永不致命"
    # 的那一类，绝不能让静置行为因为它失效）。
    idle_state = IdleStateMachine(lambda: normalize_idle_seconds(store.get("idle_seconds")))

    window = PetWindow(pet, level, mirror=mirror, idle_state=idle_state)
    window.play_action(action)
    window.showing_at_default_position()
    window.show()

    _print_startup_line(window, level_source)

    heartbeat = _install_sigint_handler(app)

    # 光标轮询 → 状态机 → 动作切换，这条链路的三个环节分别住在
    # `cursor_watch`（怎么知道动了）/ `idle_state`（该不该切）/
    # `pet_window`（切到哪个动作）里。**计时不挂在动画帧回调上**（agent.md §4.8）。
    cursor_watch = CursorWatch()
    cursor_watch.moved.connect(idle_state.on_mouse_moved)
    cursor_watch.start()

    idle_timer = QTimer()
    idle_timer.setInterval(IDLE_TICK_MS)
    idle_timer.timeout.connect(idle_state.on_tick)
    idle_timer.start()

    if args.exit_after is not None and args.exit_after >= 0:
        QTimer.singleShot(int(args.exit_after * 1000), window.close)

    try:
        code = app.exec()
    finally:
        # 退出收尾。本阶段没有热键 / 音效设备 / 单实例锁要放，
        # 只收掉自己的心跳定时器、久置计时器与光标轮询
        # （音效 / 热键 / 单实例锁属 task10 / task12 / task13）。
        window.stop_animation()
        idle_timer.stop()
        # 光标轮询必须**明确停掉**：它是个 150 ms 的常驻定时器，
        # 留着会让"退出后还有残留"（task07 §6 最后一条）。
        cursor_watch.stop()
        if heartbeat is not None:
            heartbeat.stop()
    _logger.info("%s 退出（退出码 %d）", PROGRAM_NAME, code)
    return code
