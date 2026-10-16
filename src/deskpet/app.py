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
| `--position x,y` | 指定左上角位置（屏幕逻辑坐标）；**格式不对当场报错退出** | — |

**`--fps` 已删除，不得复活**（agent.md §0：帧率由素材的 `asset.json` 决定）。

`--list-actions` 的帧率来源（`asset.json` / 遗留兜底 / 默认 12）由 `pet_spec` 解析；
启动行同样必须打印**动作 + 每帧毫秒 + 帧率来源**（task05 §4）。

`--position` 的决定顺序（task08 §4）：**命令行 > 上次保存的 > 缺省贴右下角**。
命令行给了坏值 → `argparse` 报错退出；**配置里**的坏值 → 忽略 + 一行日志（两条别混）。
"""

import argparse
import logging
import math
import signal
import sys
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from . import PROGRAM_NAME, __version__
from . import autostart
from . import context_menu
from . import geometry
from . import hotkey as hotkey_mod
from . import logger as log
from . import paths, pet_loader, restart as restart_mod, size, single_instance
from .animation import UnknownActionError
from .config import ConfigStore
from .control_bar import RESERVED, ControlBar
from .cursor_watch import CursorWatch
from .errors import DataDirError
from .idle_state import IdleStateMachine, normalize_idle_seconds
from .key_listener import KeyListener, bindings_from_config
from .mirror import MirrorState
from .pet_loader import PetLoadError, list_reports, load_keyview, resolve_pet
from .pet_spec import DEFAULT_PET_NAMESPACE, PetSpec
from .context_menu import FORM_KEYVIEW, FORM_PET
from .keyview_spec import KeyViewSpec  # noqa: F401  （打包静态分析要收全数据层）
from .pet_window import PetWindow
from .rendering import FrameRenderer  # noqa: F401  （打包静态分析要收全渲染层）
from .settings import (
    AutostartHandler,
    AutostartOutcome,
    ConfirmHandler,
    HotkeyChanger,
    HotkeyOutcome,
    RestartHandler,
    RestartOutcome,
    SettingsWindow,
)
from .sounds import SOUND_START, SoundPlayer, normalize_volume

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

#: 启动音延迟（毫秒）：等窗口画出来之后再响，免得"开机那一下"与合成器抢时间。
#: task10 §2 第 1 条的"约 80 ms"就是这个值。
STARTUP_CHIME_DELAY_MS = 80

#: 预热超时（毫秒）：等音效预热有结果的时间上限，**超时按"没有音效"照常启动**。
#: 它是"不能阻塞"这条要求的兜底——没有它，预热一旦卡住启动音就永远不来。
PRELOAD_TIMEOUT_MS = 2000

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


def _position_arg(text: str) -> tuple[int, int]:
    """`--position` 的解析器：**格式不对当场报错**（不是忽略）。

    与"档位越界"的两套规则是同一种分法（agent.md §2.12 的精神）：

    - **命令行**给的说法不合法 → `argparse` 报用法错误、非 0 退出（本函数）；
    - **配置文件**里的坏值 → 忽略 + 一行日志，回到缺省落点
      （配置是"读取永不致命"的那一类，见 `geometry.parse_position`）。

    命令行这边"忽略"是错的：用户敲了 `--position 1,2,3` 却看到宠物贴在右下角，
    会以为程序没听他的——必须说出来。
    """
    parsed = geometry.parse_position(text)
    if parsed is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} 不是 \"x,y\" 形式的位置（例：1200,600）"
        )
    return parsed


def _restart_delay_arg(text: str) -> float:
    """`--restart-delay` 的解析器：**负数当场报错**。

    与"档位越界 / 位置格式"同一种分法（§2.12 的精神）：命令行给的说法不合法
    → `argparse` 报用法错误、非 0 退出。`0` 是**合法**的（"不等"），
    自检用它来验"不等待时抢锁会怎样"；它只是不该由用户来敲。
    """
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} 不是秒数") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"重启等待时间必须是非负的有限秒数，实际给了 {text!r}")
    return value


def _wait_before_lock(seconds: float) -> None:
    """重启时新实例**先等一会儿再抢单实例锁**（task13 §4）。

    ## 为什么必须等

    旧实例的顺序是"拉起我们 → 放锁 → 关自己"。它拉起我们的那一刻，
    **锁还占着**：我们若立刻去抢，会拿到 `ERROR_ALREADY_EXISTS`
    并把自己判成"已有实例"然后退出——用户看到的就是
    "点了重启，桌宠没了"（task13 §6 明写不许出现这一幕）。

    所以交接是**两边一起兜住**的：旧进程主动放锁 + 新进程等这一会儿。
    两边的时序都留痕（验收要能从日志里看出"先等、再抢"）。

    ## ⚠ 为什么这里可以用 `time.sleep()`

    这个等待发生在**建 `QApplication` 之前**、命令行也已经解析完，
    此时进程里没有任何事件循环、没有定时器、没有窗口——
    `QTimer` 在这里**根本没有事件循环可依附**（`app.exec()` 还没跑），
    所以"用 `QTimer.singleShot` 优雅一点"是错的：那个定时器永远不会触发。

    （对比 task10 的音频预热：那一处禁止 `sleep`，因为它在窗口 `show()` 之后、
    事件循环**已经**在跑，阻塞的就是界面本身。两处的要求不冲突。）

    时长的量级也支持这个选择：默认 1.2 秒，是**一次性**的启动等待，
    不是每帧都发生的事。日志里能看到"等了多久"，验收据此判断时序。
    """
    started = time.monotonic()
    _logger.info(
        "重启：新实例先等 %.3f 秒再抢单实例锁（工作目录 %s）",
        seconds,
        restart_mod.current_directory(),
    )
    log.log_restart_stage(log.RESTART_STAGE_WAIT, f"新实例先等 {seconds:g} 秒再抢锁")
    time.sleep(seconds)
    elapsed = time.monotonic() - started
    _logger.info("重启：等待结束（实际 %.3f 秒），现在开始抢单实例锁", elapsed)
    log.log_restart_stage(log.RESTART_STAGE_WAIT, f"等待结束（实际 {elapsed:.3f} 秒），开始抢锁")


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
        "--position",
        type=_position_arg,
        default=None,
        metavar="x,y",
        help="宠物左上角位置（屏幕逻辑坐标，如 1200,600；格式不对当场报错退出）",
    )
    parser.add_argument(
        "--exit-after",
        type=float,
        default=None,
        metavar="秒",
        help="启动后等待指定的秒数自动退出（自动验证用；正常使用不要给）",
    )
    parser.add_argument(
        restart_mod.RESTART_DELAY_ARG,
        type=_restart_delay_arg,
        default=None,
        metavar="秒",
        help=(
            "重启时由旧实例传给新实例：先等这么多秒再抢单实例锁"
            f"（缺省 {restart_mod.RESTART_DELAY_DEFAULT:g} 秒；正常使用不要给）"
        ),
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
        print("  音效（播放时在候选里随机选一个）：")
        for sound_name, filenames in spec.sounds.items():
            if not filenames:
                shown = "(索引里声明了，但没有给文件名)"
            elif len(filenames) == 1:
                shown = filenames[0]
            else:
                shown = f"{len(filenames)} 个：{'、'.join(filenames)}"
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


def _resolve_position(
    cli_position: tuple[int, int] | None,
    store: ConfigStore,
) -> tuple[tuple[int, int] | None, str]:
    """决定宠物摆在哪儿，返回 `((x, y) 或 None, 来源文字)`。

    **决定顺序（task08 §4）**：

    1. **命令行** `--position`（给了就用，它已经在解析阶段验过格式）；
    2. **上次保存的**——用 `store.has("position")` 判定，**不是**
       "读到的值 ≠ 空串"。用 `has()` 的理由与档位完全一样（agent.md §2.4 规则 5）：
       用户真的拖过、而落脚点**恰好**是缺省落点时，也必须显示"沿用上次"；
    3. 都没有 → `None`，调用方走**缺省贴右下角**。

    配置里的值格式不对 → `geometry.parse_position()` 记一行日志并返回 `None`，
    于是**回到缺省落点**，不报错、不影响启动（§2.10 第三类）。
    """
    if cli_position is not None:
        return cli_position, geometry.POSITION_SOURCE_CLI

    if store.has("position"):
        parsed = geometry.parse_position(store.get("position"))
        if parsed is None:
            # 一行日志已经在 `parse_position()` 里记过了，这里补一句"因此回退"。
            _logger.warning("配置里的 position 用不了，改用缺省落点（贴右下角）")
        else:
            return parsed, geometry.POSITION_SOURCE_CONFIG

    return None, geometry.POSITION_SOURCE_DEFAULT


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


class ControlBarController:
    """把**控制栏**接到**宠物窗口**上（task09 的"组装"那一层，`app.py` 独有）。

    ## 它要串起来的四件事

    | 事 | 怎么做 |
    | --- | --- |
    | 跟随拖动 | 拖动**按增量平移**（`drag_started` 记下宠物位置，`position_changed` 里算差值） |
    | 缩放中按钮不动 | 打开时抬宠物底边（`reserve_bottom(RESERVED)`），切档只做右下角锚点保护 |
    | 关闭回位 | 收起栏 + 把宠物放回贴屏幕底边（边距归零） |
    | 幂等 | **只持有同一个 `ControlBar` 实例**，重复打开就是"已经开着，什么都不做" |

    ## 增量跟随为什么不"重算锚点"

    `place_relative_to()` 是**打开时**用一次。拖动过程中若每动一下就重算一次，
    拖到屏幕边缘时宠物的实际位置会被夹取，而控制栏按"宠物右边 + 间距"重算
    就会与理论值错开 → **越跟越偏、来回拖会抖**（task09 §4 明写禁止）。
    增量平移的口径是"宠物动了多少，栏就动多少"，天然不累积误差。

    增量从 `position_changed` 的**当前权威位置**里算（不是回读窗口坐标，
    agent.md §4.6）：记下上一次同步时的宠物位置，每来一次算一次差值即可。
    """

    def __init__(self, window: PetWindow) -> None:
        self.window = window
        self.bar: ControlBar | None = None
        #: 上一次同步时宠物的权威位置（增量跟随的基准）。
        self._synced_pet_pos: QPoint | None = None
        #: **正在换档**：换档也会让 `position_changed` 发出来（右下角锚点不动
        #: → 左上角必然变），但那**不是用户拖动**，控制栏必须一动不动
        #: （task09 §6"本 task 最关键的一条"）。这个标志就是用来把两者分开的。
        self._stepping = False
        #: 隐藏宠物之前栏是不是开着的（task12：呼出时据此决定要不要放回来）。
        self._was_visible = False

    # ------------------------------------------------------------ 开关

    @property
    def visible(self) -> bool:
        """控制栏当前是不是开着（**幂等的判据**）。"""
        return self.bar is not None and self.bar.isVisible()

    @property
    def was_visible(self) -> bool:
        """隐藏之前它是不是开着的（task12：呼出时要不要一起放回来）。

        ⚠ 判据是 `self.bar is not None` + 一个**单独记的标志**，不是
        `isVisible()`：宠物藏起来时栏已经被 `hide()` 了，那一刻问 `isVisible()`
        永远是假，于是"呼出时把栏一起放回来"就失效了。
        """
        return self._was_visible

    def hide_for_pet(self) -> None:
        """隐藏宠物时把栏一起藏起来（**不碰位置、不碰边距**）。

        与 `close()` 的区别：`close()` 是"用户关掉了栏"，它会把底部预留边距
        归零、把宠物放回贴屏幕底边——那是**改变状态**。而隐藏是
        "保留状态不销毁"（task12 §4），呼出时要原样回来，所以这里只 `hide()`，
        连预留边距都不动。
        """
        self._was_visible = self.visible
        if self.bar is not None:
            self.bar.hide()

    def toggle(self) -> None:
        """`调整大小…` 的动作：开着就关掉，没开就打开（幂等）。"""
        if self.visible:
            self.close()
        else:
            self.open()

    def open(self) -> None:
        """打开控制栏：抬宠物 → 摆位置 → 显示 → 同步档位。

        顺序有意义：

        1. **先抬宠物**（`reserve_bottom`）：控制栏贴的是宠物**底边**，
           底边必须在算控制栏位置**之前**就定下来，否则第一次会摆在旧底边上；
        2. 再按**抬好之后**的宠物矩形摆控制栏（`place_relative_to`）；
        3. `show()` 之后**再摆一次**：`Qt.Tool` 窗口在 `show()` 之前
           `width()` / `height()` 可能是未定型的（布局还没算过），
           第一次 `place_relative_to` 可能用了错的栏尺寸。
        """
        if self.visible:
            return  # 幂等：已经开着就返回**同一个**栏，不叠第二个。

        if self.bar is None:
            self.bar = ControlBar()
            self.bar.level_up.connect(self._on_level_up)
            self.bar.level_down.connect(self._on_level_down)
            self.bar.closed.connect(self.close)
            self.bar.set_level(self.window.level)

        self.window.reserve_bottom(RESERVED)
        self.bar.set_level(self.window.level)
        self.bar.place_relative_to(self._pet_rect(), self.bar.screen_rect())
        self.bar.show()
        self.bar.raise_()
        self.bar.place_relative_to(self._pet_rect(), self.bar.screen_rect())
        self._synced_pet_pos = QPoint(self.window.base_position)

    def close(self) -> None:
        """关闭控制栏：收起栏 + 把宠物**放回原本贴屏幕底边的位置**（边距归零）。

        `reserve_bottom(0)` 只在"预留机制还生效"时才会动宠物——用户已经
        拖动过（或位置本来就是命令行/配置给的）时机制已失效，于是宠物
        **保持用户拖到的位置**，不会被这里拽回右下角（task08 §4 用户位置优先）。
        """
        if self.bar is not None:
            self.bar.hide()
        self.window.reserve_bottom(0)
        self._synced_pet_pos = None

    # ------------------------------------------------------------ 档位

    def _on_level_up(self) -> None:
        self._step(+1)

    def _on_level_down(self) -> None:
        self._step(-1)

    def _step(self, delta: int) -> None:
        """换一档：**到边界什么都没发生**（不切档、不写配置、不播动画）。

        写配置是"换档"的后果而不是"点了按钮"的后果（task09 §4：
        "`−`/`+` 到边界返回'没有变化'时**不写**"）——`PetWindow.set_size_level()`
        在档位没变时会直接返回，连锚点保护都不会做。

        ## 为什么要 `_stepping` 标志

        `set_size_level()` 会改宠物的左上角（右下角锚点不动），于是
        `position_changed` **照样发出来**。若跟随逻辑照单全收，控制栏就会
        按那个增量平移——**表现是"点 `+` 时栏跟着斜着跑，用户得追着鼠标点"**，
        正好违反本 task 最关键的那条验收。

        真平台自检当场抓到了它（栏从 `2306,1372` 一路漂到 `1974,891`）。
        对策：换档期间**挂起**跟随，换完档再重新同步基准位置。
        """
        target = self.window.level + int(delta)
        if not size.is_valid_level(target):
            return  # 到边界：按钮本来就灰着，这里再兜一层（防御）
        self._stepping = True
        try:
            self.window.set_size_level(target)
        finally:
            self._stepping = False
        self.sync()
        # 换档后宠物左上角会变（右下角锚点不动）→ 重设跟随基准。
        # **不是**"把栏挪回去"：栏本来就该待在原地（底边与右边都没动）。
        self._synced_pet_pos = QPoint(self.window.base_position)

    def sync(self) -> None:
        """把档位刷进控制栏（**只改文字与禁用态，不挪位置**）。

        "不挪位置"是"缩放中按钮不动"的另一半：换档时宠物按右下角锚点长大，
        **底边与右边不动**，而控制栏贴的就是底边、对齐的就是右边，
        所以这里**只刷文字**。若这里顺手 `place_relative_to()` 一次，
        第 10 级的宠物会比屏幕还高，控制栏就会跳走。
        """
        if self.bar is None:
            return
        self.bar.set_level(self.window.level)

    # ------------------------------------------------------------ 跟随

    def sync_follow(self) -> None:
        """宠物位置变了：按**增量**平移控制栏（`position_changed` 的槽）。

        **不用轮询**（task09 §5 明写禁止）：位置变化一定有信号，
        轮询既费 CPU 又会有"追不上"的滞后感。

        ⚠ **换档期间直接返回**：换档也会让 `position_changed` 发出来
        （右下角锚点不动 → 左上角必然变），但那不是用户拖动，
        控制栏必须一动不动（见 `_step` 的说明）。
        """
        if self._stepping:
            return
        if not self.visible or self.bar is None:
            self._synced_pet_pos = QPoint(self.window.base_position)
            return

        current = self.window.base_position
        if self._synced_pet_pos is None:
            self._synced_pet_pos = QPoint(current)
            return
        delta = current - self._synced_pet_pos
        self._synced_pet_pos = QPoint(current)
        self.bar.follow(delta.x(), delta.y())

    # ------------------------------------------------------------ 几何

    def _pet_rect(self) -> QRect:
        """宠物当前的屏幕矩形。

        尺寸取自 `PetWindow.pixel_size`（**权威**），不回读 `geometry()`
        ——切档刚发生时窗口坐标还是异步的旧值（agent.md §4.6）。
        """
        width, height = self.window.pixel_size
        pos = self.window.base_position
        return QRect(pos.x(), pos.y(), width, height)

    def reposition(self) -> None:
        """重新按宠物摆一次控制栏（窗口刚 `show()` 完、屏幕变了时用）。"""
        if self.visible and self.bar is not None:
            self.bar.place_relative_to(self._pet_rect(), self.bar.screen_rect())


class HotkeyController:
    """把**全局热键**接到"隐藏 / 呼出"上（task12 的组装层，`app.py` 独有）。

    ## 它要串起来的四件事

    | 事 | 怎么做 |
    | --- | --- |
    | 启动注册 | 按候选链试；走了回退就**写回配置**并说一句"已改用 X" |
    | 热键 = 切换 | 按一次隐藏、再按一次呼出（task12 §6 的"隐藏 → 呼出重复 20 次"） |
    | **分两拍处理** | 收到信号**不当场动窗口**，先 `QTimer.singleShot(0, ...)` 让出一次事件循环 |
    | 换热键 | 只试用户给的那一个；失败**回滚到原来那个** |

    ## 为什么必须分两拍（task12 §4 硬要求）

    热键回调发生在"Qt 正在处理某个消息"的栈上。在这一层里直接 `show()` /
    `hide()` 会让窗口管理器在**一次嵌套的激活流程**里重入：
    表现是偶尔回到最上层却拿不到焦点，或者窗口先闪一下。
    让出一次事件循环之后，切换发生在**干净的消息处理边界**上。

    ⚠ **轮询不挂在动画帧回调上**（`agent.md` §4.8 的红线）：窗口隐藏后精灵停帧、
    帧回调不再触发，热键就永远没人处理。本实现走的是
    `HotkeyManager.hotkey_pressed` **信号**（D5 = 子线程 + 队列 + 取件定时器），
    与动画帧率完全无关——隐藏期间动画定时器已经停了，热键照常到达。
    """

    def __init__(self, window: PetWindow, store: ConfigStore) -> None:
        self.window = window
        self.store = store
        self.manager = hotkey_mod.HotkeyManager(window)
        #: 控制栏控制器（隐藏时要一起藏）。`app.py` 组装完控制栏再装进来。
        self.bar_controller: ControlBarController | None = None
        #: 最近一次注册的结论（启动行与日志用）。
        self.result = hotkey_mod.RegisterResult(None, False, "")
        self.manager.hotkey_pressed.connect(self._on_hotkey_pressed)
        # 窗口的"隐藏宠物"菜单项也走这里（菜单 → 隐藏；热键 → 切换）。
        window.set_visibility_handler(self.hide)

    # ------------------------------------------------------------ 注册

    def start(self, preferred: str) -> hotkey_mod.RegisterResult:
        """启动时按候选链注册，并把"用了哪个"告诉窗口与配置。

        **写回配置**只在真的走了回退链时发生（`changed=True`）：
        首选能用时写一遍是多余的一次磁盘写。
        """
        self.result = self.manager.register(preferred)
        self.window.set_hotkey_available(self.manager.has_hotkey)
        if self.result.combo is None:
            _logger.error(
                "没有可用的呼出快捷键（%s）：隐藏功能已禁用，"
                "请先在「设置」里指定一个快捷键",
                self.result.reason,
            )
            return self.result

        if self.result.changed:
            # 说清楚"原快捷键不可用，已改用 X"（task12 §6 明写要有这个说明）。
            _logger.warning(
                "原快捷键 %s 不可用，已改用 %s", preferred, self.result.combo
            )
            self._persist(str(self.result.combo))
        _logger.info("呼出快捷键：%s", self.result.combo)
        return self.result

    def change(self, combo: str, previous: str) -> HotkeyOutcome:
        """换呼出快捷键（设置窗口那一行调它）：**成功才留下**。

        ## 顺序：先放旧的、再注册新的；失败把旧的抢回来

        `RegisterHotKey` 对同一个组合是"自己占着自己"，所以换键必须
        **先释放旧的**，否则新键还没试就注定失败（如果新旧相同则直接返回成功）。

        失败路径上一定要 `restore(previous)`：不恢复的话，用户敲了一个被占用的
        组合之后会落到**一个热键都没有**的状态 —— 那正是 task12 最怕的事情
        （藏起来叫不回来）。所以"回滚"在这里是**真的把旧热键注册回去**，
        而不是只把配置写回去（`task12 §6："绝不落到没有热键可用"`）。
        """
        if combo == previous:
            return HotkeyOutcome(True, combo, "")
        self.manager.release_hotkey()
        outcome = self.manager.try_register(combo)
        if outcome.combo is not None:
            self.window.set_hotkey_available(True)
            _logger.info("呼出快捷键已改为 %s", outcome.combo)
            return HotkeyOutcome(True, outcome.combo, "")

        # 失败：把旧的那个抢回来（**即使回滚也失败**，状态也要如实反映）。
        restored = self.manager.restore(previous)
        self.window.set_hotkey_available(self.manager.has_hotkey)
        if restored.combo is None:
            _logger.error("回滚 %s 也失败了：%s", previous, restored.reason)
            return HotkeyOutcome(
                False, None, f"{outcome.reason}（原来的 {previous} 也没能恢复）"
            )
        _logger.warning("呼出快捷键 %s 没能启用：%s（已恢复 %s）", combo, outcome.reason, previous)
        return HotkeyOutcome(False, None, outcome.reason)

    # ------------------------------------------------------------ 隐藏 / 呼出

    def hide(self) -> None:
        """`隐藏宠物` 菜单项的动作（**只隐藏，不切换**）。

        与热键的区别：热键是"切换"，菜单项是"藏起来"。
        藏不起来（没有可用热键）→ 说清楚原因，**绝不假装成功**。
        """
        if not self.window.hide_pet():
            self._report_cannot_hide()
            return
        _logger.info("宠物已隐藏（按 %s 呼出）", self.manager.combo or "（没有热键）")
        self._hide_bar()

    def toggle(self) -> None:
        """热键的动作：藏着就呼出，显示着就隐藏（**两拍处理**，见类说明）。"""
        if self.window.hidden_by_user:
            self._show()
        else:
            if not self.window.hide_pet():
                self._report_cannot_hide()
                return
            _logger.info("宠物已隐藏（按 %s 呼出）", self.manager.combo or "（没有热键）")
            self._hide_bar()

    def _on_hotkey_pressed(self) -> None:
        """收到 `WM_HOTKEY`：**先让出一次事件循环**，再执行切换。

        `singleShot(0, ...)` 就是"分两拍"的实现方式（task12 §4）：
        它把切换排到当前消息处理**结束之后**，避免在窗口管理器的
        嵌套激活流程里重入。
        """
        QTimer.singleShot(0, self.toggle)

    def _show(self) -> None:
        """呼出：显示宠物 + 把控制栏一起放回来。"""
        if not self.window.show_pet():
            return
        if self.bar_controller is not None and self.bar_controller.was_visible:
            self.bar_controller.open()

    def _hide_bar(self) -> None:
        """隐藏时把控制栏一起藏起来（它是**独立顶层窗口**，不会跟着宠物走）。

        ⚠ 这里**不能**用 `bar_controller.close()`：那会把宠物的底部预留
        边距归零、把位置改掉——隐藏是"保留状态不销毁"，位置必须原样留着。
        所以只 `hide()` 栏本身，并记住它本来是开着的。
        """
        if self.bar_controller is not None and self.bar_controller.visible:
            self.bar_controller.hide_for_pet()

    def _report_cannot_hide(self) -> None:
        """没有可用热键时把附录 B 那句话说出来（**弹窗 + 日志**）。

        用弹窗的理由：这是**用户需要知道并采取行动**的事（去「设置」里指定一个
        快捷键），属于 `agent.md §2.10` 的第二类。此刻窗口是可见的，
        模态框不会像启动期那样挡住一个还没起来的程序。
        """
        reason = context_menu.REASON_NO_HOTKEY
        _logger.warning("%s", reason)
        if self.result.reason:
            _logger.warning("原因：%s", self.result.reason)
        log.show_message(PROGRAM_NAME, reason)

    def _persist(self, combo: str) -> None:
        """把回退链选出来的组合写回配置（**写失败只记日志**）。

        写不进去不影响本次运行：热键已经在系统里注册上了。
        下次启动会再从配置里的旧值开始试一遍候选链——结果一样。
        """
        result = self.store.apply("hotkey", combo)
        if not result.ok:
            _logger.error("呼出快捷键没能写回配置：%s", result.error)

    def stop(self) -> None:
        """退出收尾：**彻底释放热键**（组合不被本进程占着）。"""
        self.manager.unregister()


class SettingsController:
    """把**设置窗口**接到宠物上（task11 的"组装"那一层，`app.py` 独有）。

    它只做四件事，每一件在 task11 §4 都有出处：

    | 事 | 怎么做 |
    | --- | --- |
    | **单例** | 只持有一个 `SettingsWindow`；重复打开 → 把已有的**前置**（`open()`） |
    | 定位 | 每次打开都 `center_on_screen()`——屏幕可能换了，位置不该记死 |
    | 刷新时机 | 打开时重新读一遍配置（`_load_from_config()`），免得窗口停在上次的值上 |
    | 释放引用 | 关掉之后把实例扔掉（`WA_DeleteOnClose`），下次打开是**一个全新的窗口** |

    ⚠ **不加 `WindowStaysOnTopHint`**：设置窗口是个普通模态对话框，
    宠物按既有约定仍在最上层飘着（用户已确认的取舍）。给设置窗口也加上
    "始终置顶"会让两个窗口抢最上层，反而更难用。
    """

    def __init__(self, store: ConfigStore, window: PetWindow) -> None:
        self._store = store
        self._window = window
        self._dialog: SettingsWindow | None = None
        #: 换呼出快捷键的处理器（task12）。**每个新窗口都要重新装**：
        #: 关一次就释放实例，下次打开是一个全新的窗口（见 `open()`）。
        self._hotkey_changer: HotkeyChanger | None = None
        #: 重启处理器与"要不要重启"的确认框（task13）。同样**每个新窗口都要重装**。
        self._restart_handler: "RestartHandler | None" = None
        self._confirm_handler: "ConfirmHandler | None" = None
        #: 开关开机自启的处理器（task14）。同样**每个新窗口都要重装**。
        self._autostart_handler: "AutostartHandler | None" = None

    def set_hotkey_changer(self, changer: "HotkeyChanger | None") -> None:
        """由 `app.py` 装上"换热键"的函数（task12）。"""
        self._hotkey_changer = changer
        if self._dialog is not None:
            self._dialog.set_hotkey_changer(changer)

    def set_restart_handler(
        self,
        restart: "RestartHandler | None",
        confirm: "ConfirmHandler | None",
    ) -> None:
        """由 `app.py` 装上"重启自己"与"问用户要不要重启"（task13）。

        ⚠ 与 `set_hotkey_changer()` 同一条教训（task12 §4）：**每个新窗口都要重装**。
        这里是"记住处理器、开窗口时再装一遍"的同一模式；忘了重装的表现是
        "第二次打开设置窗口换角色，它只写配置、静默地不重启"
        ——又一种假成功。
        """
        self._restart_handler = restart
        self._confirm_handler = confirm
        if self._dialog is not None:
            self._dialog.set_restart_handler(restart, confirm)

    def set_autostart_handler(self, handler: "AutostartHandler | None") -> None:
        """由 `app.py` 装上"开关开机自启"（task14）。

        ⚠ 与 `set_hotkey_changer()` / `set_restart_handler()` 同一条教训
        （task12 §4 / task13 §4）：**每个新窗口都要重装**。忘了重装的表现是
        "第二次打开设置窗口勾『开机自启』时**只改界面、不动系统自启项**"
        ——用户以为设好了，登录时什么都没发生（又一种假成功）。
        """
        self._autostart_handler = handler
        if self._dialog is not None:
            self._dialog.set_autostart_handler(handler)

    @property
    def dialog(self) -> "SettingsWindow | None":
        """当前那个设置窗口（没开着就是 `None`）。自检与诊断用。"""
        return self._dialog

    def open(self) -> None:
        """打开设置窗口；已经开着就**前置**（不叠第二个，task11 §6）。"""
        current = self._current_pet_namespace()

        if self._dialog is not None:
            self._dialog.reload_from_config(current)
            self._dialog.center_on_screen()
            self._dialog.raise_()
            self._dialog.activateWindow()
            return

        dialog = SettingsWindow(self._store, current_pet=current, parent=None)
        # 关掉之后**释放实例引用**：下次打开是一个全新的窗口，
        # 不会带着上一次的状态（task11 §2 第 4 条："关闭后再打开正常"）。
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        # ⚠ 每个新窗口都要重新装换热键的处理器（task12）：不装的话，
        # 第二、三次打开设置窗口时录入快捷键只会写配置、**不注册系统热键**
        # ——用户看到"设置成功"却按不出来，正是最典型的假成功。
        if self._hotkey_changer is not None:
            dialog.set_hotkey_changer(self._hotkey_changer)
        # ⚠ 同理，**每个新窗口都要重新装重启处理器**（task13）：不装的话
        # 第二、三次打开设置窗口换角色时**只会写配置、不会问也不会重启**。
        if self._restart_handler is not None or self._confirm_handler is not None:
            dialog.set_restart_handler(self._restart_handler, self._confirm_handler)
        # ⚠ 同理，**每个新窗口都要重新装自启处理器**（task14）：不装的话，
        # 第二、三次打开设置窗口勾「开机自启」**只改界面、不动系统自启项**。
        if self._autostart_handler is not None:
            dialog.set_autostart_handler(self._autostart_handler)
        dialog.finished.connect(self._on_finished)
        # 设置里改的值要**立刻**作用到运行时（音量已在 `store.config_changed`
        # 上接了；久置时间由状态机每次判定现读）。
        dialog.settings_changed.connect(self._on_settings_changed)
        self._dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def close(self) -> None:
        """退出收尾：把设置窗口收掉（模态窗口不关会让 `app.exec()` 不返回）。"""
        if self._dialog is not None:
            self._dialog.close()

    def _on_finished(self, _result: int) -> None:
        """窗口关了：**必须**清掉引用，否则下次打开会去 `raise_()` 一个已析构的对象。"""
        self._dialog = None

    def _on_settings_changed(self, key: str, value: object) -> None:
        """用户在设置里改了一项（**写入成功才会走到这里**）。"""
        _logger.info("设置已生效：%s=%r", key, value)

    def _current_pet_namespace(self) -> str:
        """配置里**真正生效**的角色（空串 = 没表态 → 默认形象）。

        界面显示"当前角色"必须用这个，而不是"窗口正在显示的那个"：
        换角色的生效方式是**重启**（task11 §5 的边界），所以"真实生效"
        永远以配置为准。
        """
        configured = str(self._store.get("pet") or "").strip()
        return configured or pet_loader.DEFAULT_PET_NAMESPACE


class RestartController:
    """把**换角色**接到"重启自己"上（task13 的组装层，`app.py` 独有）。

    ## 它要串起来的三件事

    | 事 | 怎么做 |
    | --- | --- |
    | 问用户 | `confirm()`：一个**模态**询问框（`QMessageBox.question`），默认按钮是"否" |
    | 真重启 | `restart()`：`restart.restart_self(handle)` → 成功才 `app.quit()` |
    | 失败不退出 | `restart_self()` 返回假 → **弹窗** `重启失败，请手动关掉再启动一次`，当前实例继续跑 |

    ## 为什么"关自己"必须是 `app.quit()` 而不是别的

    `quit()` 让事件循环正常收尾 → `run()` 的 `finally` 那段把热键、音效设备、
    控制栏、设置窗口一个个放掉。直接 `sys.exit()` / `os._exit()` 会跳过整段，
    表现是"重启之后音频设备被占着、新实例没声音"（task10 §4 那条坑原样复现）。

    ⚠ 顺序仍然是"**先拉起新实例、再关自己**"（task13 §4）：`restart_self()`
    内部就是那个顺序，本控制器只负责在它返回真之后调 `quit()`。

    ## 时序留痕（验收要看）

    日志里必须能看出"旧实例放锁"与"新实例先等 1.2 秒再抢锁"这两件事，
    所以三处各记一行：本控制器的 `重启：…`、`single_instance.release()` 的
    `单实例锁已交接…`、新实例的 `重启：新实例先等 … 秒再抢单实例锁`。
    """

    def __init__(self, app: QApplication, instance: single_instance.SingleInstanceHandle) -> None:
        self._app = app
        self._instance = instance
        #: 用户是不是已经同意过重启（自检观察用；**不写配置**）。
        self.requested = False

    def confirm(self, title: str, text: str) -> bool:
        """问一句"现在自动重启吗？"，返回用户同不同意。

        **默认按钮是"否"**：回车不该把用户的桌宠关掉。
        弹不出来（理论上不会：走到这里时事件循环正在跑）时按**不同意**处理
        ——宁可让用户自己重启，也不能擅自把程序关掉。
        """
        try:
            answer = QMessageBox.question(
                None,
                title,
                text,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
        except Exception as exc:  # pragma: no cover - 弹窗失败按不同意处理
            _logger.warning("重启询问框没能弹出来（%s）：按不重启处理", exc)
            return False
        return answer == QMessageBox.StandardButton.Yes

    def restart(self) -> "RestartOutcome":
        """拉新实例 → 成功才 `quit()`；失败**弹窗 + 不退出**（task13 §2 第 7 条）。

        返回结构化的 `RestartOutcome`（`settings` 用它决定状态栏说什么）：
        `started=True` 时本进程**马上就要退出了**，状态栏那句话用户大概看不到，
        但那不重要——重要的是**失败路径**上返回 `False` 让界面如实显示。
        """
        started = restart_mod.restart_self(self._instance)
        if not started:
            reason = "新实例没能起来（详情见日志）"
            _logger.error("重启失败：%s", reason)
            # 弹窗 + 日志（agent.md §2.10 第二类：用户明确要求的写操作失败）。
            # 此刻事件循环正在跑、设置窗口可见，模态框是安全的。
            log.show_message(PROGRAM_NAME, restart_mod.RESTART_FAILED_MESSAGE)
            return RestartOutcome(False, reason)

        self.requested = True
        # **只排退出，不在这里关窗口**：让设置窗口这一层的事件处理先返回，
        # 再让事件循环自己收尾（与 task12 "分两拍"同一个理由——
        # 在"Qt 正在处理某个信号"的栈上直接关窗口会让窗口管理器重入）。
        _logger.info("重启：新实例已接管，旧实例现在退出")
        QTimer.singleShot(0, self._app.quit)
        return RestartOutcome(True)


class AutostartController:
    """把**设置窗口里那一行**接到系统自启项上（task14 的组装层，`app.py` 独有）。

    ## 为什么这层薄得几乎没有逻辑

    "开机自启"的真正内容全在 `autostart` 模块里（启动文件夹怎么找、
    快捷方式怎么建、`.cmd` 怎么回退、失败原因怎么拼）。这一层只干两件事：

    | 事 | 怎么做 |
    | --- | --- |
    | 把 `autostart` 的结果翻译成界面要的 `AutostartOutcome` | `apply()` |
    | **在用户明确动作时**顺手清理启动文件夹里的遗留标记 | `open_hook()` |

    ## ⚠ 清理为什么挂在这里、而不是"读状态"里

    task14 §5 的头号红线是"**读状态路径绝不写盘**"。
    `autostart.cleanup_legacy_markers()` 是**写操作**（删文件），
    所以它只能挂在"用户明确做了某件事"上：

    | 触发点 | 为什么算"明确动作" |
    | --- | --- |
    | 用户点开设置窗口 | 他正要看/改自启设置，这时清掉旧版遗留物是这件事的一部分 |
    | 用户勾选 / 取消勾选 | 同上，而且那一刻本来就允许写 |
    | 启动时 | **不挂**：启动不是用户对自启的明确表态，而且启动路径越短越好 |

    ## 幂等

    `cleanup_legacy_markers()` 删的是**写死的几个名字**（`LEGACY_MARKERS`），
    本来就不存在时什么都不做、也不报错。所以它可以被反复调用。
    """

    def __init__(self) -> None:
        #: 最近一次操作的结论（诊断与自检观察用）。
        self.last: "AutostartOutcome | None" = None

    def cleanup(self) -> list[str]:
        """清掉启动文件夹里的遗留标记文件（**由用户明确动作触发**）。

        失败只记日志：清理类操作尽力而为，**绝不阻断设置窗口**
        （agent.md §2.10 第三类）。
        """
        try:
            return autostart.cleanup_legacy_markers()
        except Exception as exc:  # pragma: no cover - 清理不该抛，但绝不冒这个险
            _logger.warning("自启：清理遗留标记文件时出错（%s: %s）", type(exc).__name__, exc)
            return []

    def open_hook(self) -> None:
        """打开设置窗口前的那一下（**只做清理，不做别的**）。

        顺手把启动文件夹的内容记进日志：这是"那里到底有什么"的唯一线索，
        而验收要求"开启后恰好只有一个我们的文件"——留一行证据便于回看。
        """
        removed = self.cleanup()
        contents = [path.name for path in autostart.our_files()]
        _logger.info(
            "自启：打开设置窗口，启动文件夹（%s）里我们的文件：%s%s",
            autostart.startup_dir(),
            "、".join(contents) if contents else "（没有）",
            f"；顺手清掉了遗留标记文件：{'、'.join(removed)}" if removed else "",
        )

    def apply(self, enabled: bool) -> "AutostartOutcome":
        """**开 / 关**自启项（用户在设置里勾选或取消勾选）。

        ## 为什么这里也要清理一次

        勾选的那一刻本来就允许写启动文件夹，顺手清掉遗留标记文件最省事
        ——否则"用户勾了一次"之后那里可能还躺着一个旧版标记文件，
        而验收要求"**恰好只有一个**我们的文件"。清完再写，顺序不能反。

        ## 成功与"幂等成功"都是 `ok=True`

        `disable()` 在"本来就没有"时也返回成功（task14 §6 明写要幂等）。
        所以 `ok` 判的是"这次操作没有失败"，`enabled` 判的是"现在能不能用"。
        """
        self.cleanup()
        try:
            state = autostart.enable() if enabled else autostart.disable()
        except Exception as exc:
            # `enable()` 内部该兜的都兜了；真漏出来也不能让设置窗口崩掉，
            # 但**必须**如实报错（绝不显示假成功）。
            detail = f"{type(exc).__name__}: {exc}"
            _logger.error("自启：%s 失败（%s）", "开启" if enabled else "关闭", detail)
            outcome = AutostartOutcome(
                ok=False,
                enabled=not enabled,
                status_text=autostart.TEXT_ENABLED if not enabled else autostart.TEXT_DISABLED,
                reason=detail,
            )
            self.last = outcome
            return outcome

        ok = state.error is None
        outcome = AutostartOutcome(
            ok=ok,
            enabled=bool(state.enabled),
            status_text=state.status_text,
            reason=state.error or "",
        )
        self.last = outcome
        return outcome


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


def _print_startup_line(
    window: PetWindow,
    level_source: str,
    position_source: str,
    form_layout: str = "",
) -> None:
    """记一行启动状态：素材包 + 动作 + 帧率来源 + 位置/来源 + 档位/来源 + 遮罩 + DPI。

    位置取自窗口的**权威位置**（不是回读窗口坐标）。
    写进日志（同时上控制台）；**不写盘**、不碰配置。

    task04 起必须带上**档位 + 来源 + 像素尺寸**这三样：
    "宠物怎么突然变小了"这类问题，只看这一行就能判断是"沿用上次"还是"自适应"变了。

    task05 起必须带上**动作 + 每帧毫秒 + 帧率来源**：
    "怎么变快了/变慢了"只有这一行能一眼看出是 `asset.json` 改的、
    还是掉到了遗留兜底表 / 默认 12（task05 §4）。

    task08 起必须带上**位置来源**（`命令行指定` / `沿用上次` / `默认落点`）：
    "宠物怎么没在昨天的位置"与"怎么又贴在右下角了"靠它一眼分辨
    （task08 §6 有两条验收直接盯着这个文字）。

    ⚠ task12 的热键**不在这里打印**：它必须在**注册之后**才有结论，
    而这一行是在窗口 `show()` 之后、热键注册之前打的（那时还不知道结果）。
    热键那一行由 `HotkeyController.start()` 自己记（`呼出快捷键：…`）。
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
        "pos=(%d, %d)(%s) level=%d/%d(%s) size=%dx%d mask=%s mirror=%s "
        "antialias=%s dpr=%.2f form=%s layout=%s",
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
        position_source,
        window.level,
        size.LEVEL_COUNT,
        level_source,
        width,
        height,
        mask_state,
        # 镜像这一栏是 task06 加的：翻转是不是**记住了**只看这一行。
        "on" if window.mirrored else "off",
        # 抗锯齿这一栏是 task15 加的：验收要能从启动行直接读出 `antialias=on/off`
        # （开关是**重启生效**，所以每次启动都得看得见它当时是什么值）。
        # 值取自**渲染器**而不是配置：以"真正作用到出图上"的那个为准。
        "on" if window.antialias else "off",
        window.devicePixelRatioF(),
        # 形态与布局这一栏是 task17 加的：验收要能从启动行直接读出
        # "这次起的是哪个形态、用的是哪个布局"——"宠物怎么变成键盘了"
        # 与"布局怎么不是我设的那个"靠它一眼分辨。
        window.form or "宠物",
        form_layout or "（宠物形态没有布局）",
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


def _log_sound_summary(player: SoundPlayer) -> None:
    """把音效状况**一次说清**：汇报文案 + 三条诊断可见性（task10 §4"诊断可见性"）。

    汇报文案的格式由 `SoundReport.summary()` 一处定义（**不可改**）：
    `音效 2/2` / `无音效` / `无音效（未在索引里声明：bgm.mp3）` /
    `音效 1/2（缺 rest）（无法播放：bgm.ogg）`。

    三条诊断各记一条 `INFO`/`WARNING`：**只记日志、不弹窗**
    （用户无需行动的内部退化，agent.md §2.10）。
    """
    report = player.diagnose()
    _logger.info("音效：%s", report.summary())
    # "支持哪些格式"由 **Qt 后端**说了算，所以这里把实测结果打出来，
    # 而不是让用户去猜我们的扩展名表：`--list-*` 与日志都看得到同一份事实。
    if report.decoder_formats:
        _logger.info(
            "音效：Qt 后端支持的容器 %s；音频编码 %s",
            "、".join(report.decoder_formats),
            "、".join(report.decoder_codecs) or "（问不到）",
        )
    for name in report.missing:
        _logger.warning("音效 %s：索引里声明了，但文件不在", name)
    for name, reason in report.unplayable:
        _logger.warning("音效 %s：文件在，但打不开（%s）", name, reason)
    if report.undeclared:
        _logger.info(
            "音效：sounds/ 里有 %d 个没在索引里声明的文件（不会被播放）：%s",
            len(report.undeclared),
            "、".join(report.undeclared),
        )


def _log_keyview_summary(keyview: "KeyViewSpec | None", namespace: str) -> None:
    """把键盘形态的状况**一次说清**（与 `_log_sound_summary` 同一个模式）。

    **只记日志、不弹窗**：KV 不可用是"用户无需行动的内部退化"
    （agent.md §2.10 第三类），菜单项灰掉本身就是给用户的信号。

    ⚠ 段缺失时**一条日志都不记**：那与"没有 `sounds`"完全同义，
    记一句"这个包没有键盘形态"只会让日志变噪音（task17 §6 有一条验收
    专门盯"段缺失时日志里既没有错误也没有警告"）。
    """
    if keyview is None:
        return
    _logger.info(
        "键盘形态：%d 个键位、%d 个可用布局（%s）、%d 只手、%d 个头，画布 %dx%d",
        len(keyview.keys),
        len(keyview.layout_names),
        "、".join(keyview.layout_names),
        len(keyview.hands),
        len(keyview.heads),
        keyview.cell[0],
        keyview.cell[1],
    )
    unused = [
        key_id
        for key_id in keyview.keys
        if not any(key_id in layout.key_ids for layout in keyview.layouts.values())
    ]
    if unused:
        # 不参与任何布局的键位是**潜在的问题**：素材里写了、配置也可能配了，
        # 但画面里根本没有它（多半是 `layouts` 漏了）。**只记日志**。
        _logger.info(
            "键盘形态：这些键位不在任何布局里（配了也不会亮）：%s", "、".join(unused)
        )


class FormController:
    """把**形态切换**接到配置上（task17 的组装层，`app.py` 独有）。

    ## 它要串起来的三件事

    | 事 | 怎么做 |
    | --- | --- |
    | 切形态 | 调 `window.set_form()`（真正切画面的那一步） |
    | 记住 | **切成功才写** `config.form`（写失败如实报错，绝不显示假成功） |
    | 布局回退 | 当前布局不可用时**回退到可用那个 + 一条日志**，不是直接灰掉 |

    ## 布局回退为什么在这里

    "可用性按布局判"这条规则（task17 §3.1）需要"哪些布局可用"这个信息，
    而它只有 `KeyViewSpec` 有（`pet_loader` 已经按图在不在筛过一遍）。
    `pet_window` 不该知道"配置里那个布局名还在不在"——那是装配层的事。
    """

    def __init__(self, window: PetWindow, store: ConfigStore) -> None:
        self.window = window
        self.store = store
        #: 启动时定下来的布局名（**已经在可用集合里**）。
        self.layout: str = ""

    def resolve_layout(self) -> str:
        """定下用哪个布局：配置里的 > 清单里的第一个；配的那个不可用则**回退**。

        返回最终采用的布局名（KV 不可用时是空串）。**只读，不写盘**
        ——"改完立刻生效"由调用方按需写回。
        """
        keyview = self.window.keyview
        if keyview is None:
            return ""
        available = keyview.layout_names
        if not available:  # pragma: no cover - KeyViewSpec 的非空由解析保证
            return ""

        configured = str(self.store.get("keyview_layout") or "").strip()
        if configured and configured in available:
            return configured
        if configured:
            # ⚠ **回退到可用那个 + 一条日志**，不是直接灰掉（task17 §6 明写）。
            _logger.warning(
                "配置里的键盘布局 %r 不可用（可用：%s），已回退到 %r",
                configured,
                "、".join(available),
                available[0],
            )
        return available[0]

    def restore(self) -> str:
        """启动时按配置恢复上次的形态；返回**实际生效**的形态名。

        ⚠ 恢复是**读操作**：配置里写着 `keyview` 但素材包换了、KV 不可用时，
        这里**只记日志并留在宠物形态**，**不写盘**（读操作只读，§2.10）。
        """
        self.layout = self.resolve_layout()
        configured = str(self.store.get("form") or "").strip()
        if not configured:
            return FORM_PET
        if configured != FORM_KEYVIEW:
            # 未知形态名：按"没表态"处理（配置是"读取永不致命"的那一类）。
            _logger.warning("配置里的形态 %r 不认识，按宠物形态继续", configured)
            return FORM_PET
        if not self.window.set_form(FORM_KEYVIEW):
            _logger.warning(
                "配置里的形态是键盘形态，但这个素材包没有可用的 KV，留在宠物形态"
            )
            return FORM_PET
        _logger.info("已按配置恢复到键盘形态（布局 %s）", self.layout)
        return FORM_KEYVIEW

    def switch(self, form: str) -> bool:
        """菜单项的动作：**先切、切成功了再记住**；返回是不是真的切了。

        ## 为什么写配置放在切成功之后

        反过来的话，配置里会留下一个"下次启动要恢复、但这次没生效"的形态
        ——正是本项目最忌讳的那种**状态分叉**。切失败（KV 不可用）时
        `set_form()` 返回假，这里直接返回，**一个字节都不写**。
        """
        if not self.window.set_form(form):
            return False
        self.persist(form)
        return True

    def persist(self, form: str) -> None:
        """把形态写进配置（**只在真的切了之后调**）。

        写失败按 §2.10 第二类处理：这是**用户明确要求的操作**，
        没记住就必须让他知道（`ERROR` 日志 + 系统原文）。
        `ConfigStore.set()` 自己不抛异常，所以这里不会炸。
        """
        if not self.store.set("form", form):
            reason = self.store.last_error("form") or "未知原因"
            _logger.error(
                "记住形态失败（%s）：%s（本次仍然切过去了，只是下次启动不会恢复）",
                form or "宠物",
                reason,
            )


class KeyListenerController:
    """把**按键监听**接到宠物窗口上（task18 的组装层，`app.py` 独有）。

    ## 它要串起来的三件事

    | 事 | 怎么做 |
    | --- | --- |
    | **启用条件** | 形态 == `keyview` **且** 窗口可见 **且** 没被挂起（三件事同时成立才轮询） |
    | **喂状态** | 监听器 `keys_changed` -> 窗口的**合并复绘**（不在信号处理里直接重绘） |
    | **停得干净** | 任一条件不成立 -> `set_enabled(False)`（停表 + `pressed` 清空，CPU 0） |

    ## 为什么启用条件放在这里、不放在窗口里

    "什么时候该轮询"是三件事的**合取**，其中两件（可见性、形态）由装配层驱动，
    第三件（挂起）是 task19 的录入界面要用的接口（task18 4.4）。
    窗口只管"收到变化之后做什么"——把合取放在窗口里就多一处要对齐的状态。

    ## 挂起接口

    `set_suspended(True)` 给 task19 的录入用（录一个键的时候不能让那个键
    当场点亮）；本卡只**提供**它，不接线（task18 5 明写"不许做设置界面"）。
    """

    def __init__(self, window: PetWindow, store: ConfigStore) -> None:
        self.window = window
        self.store = store
        #: 绑定表：**启动时从配置读一次**（`keyview_keys`）。
        #: 配置是内存快照，运行期改磁盘不生效——与档位 / 镜像同一条规矩（agent.md 2.4）。
        self.listener = KeyListener(
            bindings=bindings_from_config(store.get("keyview_keys"))
        )
        #: **临时挂起**（task19 录入用）。与形态 / 可见性分开，恢复时各还各的。
        self._suspended = False
        window.set_key_listener(self.listener)
        # 窗口的可见性变化驱动启停（隐藏 = 没人看，白烧 CPU，2.13 同一条纪律）。
        window.visibility_changed.connect(lambda _visible: self.sync())

    # ------------------------------------------------------------ 启停

    def set_suspended(self, suspended: bool) -> None:
        """临时挂起 / 恢复轮询（task19 的录入界面用）。"""
        self._suspended = bool(suspended)
        self.sync()

    @property
    def suspended(self) -> bool:
        """现在是不是被临时挂起了（诊断与自检用）。"""
        return self._suspended

    def should_poll(self) -> bool:
        """**启用条件**：形态 == KV 且窗口可见且没被挂起。

        三个条件**都要成立**（task18 4.4）。判据是 `window.form` / `window.isVisible()`，
        不另记布尔：多一个开关就多一处会对不上的状态（agent.md 2.13 同一条理由）。
        """
        return (
            self.window.form == FORM_KEYVIEW
            and self.window.isVisible()
            and not self._suspended
        )

    def sync(self) -> None:
        """按启用条件让监听器转 / 停（**唯一改它状态的地方**）。

        停下来时监听器自己会**清空 `pressed`**（`KeyListener._sync`），
        于是回来时不会带着一个"陈旧的按下"（task18 8 第 3 条）。
        """
        self.listener.set_enabled(self.should_poll())

    def stop(self) -> None:
        """退出收尾：停表（与其它驱动一样，`finally` 里做）。"""
        self.listener.stop()

def _report_resolution_problems(issues: tuple, fell_back: bool, config_pet: str | None) -> None:
    """把解析素材包时攒下的问题**一次性全部**写进日志（§2.10 第三类）。"""
    for issue in issues:
        _logger.warning("素材包问题：%s", issue)
    if fell_back:
        _logger.warning(
            "配置里的素材包 %r 不可用，已回退到默认形象 %s（本次会把配置写回默认形象）",
            config_pet,
            pet_loader.DEFAULT_PET_NAMESPACE,
        )


def _persist_pet_fallback(store: ConfigStore, config_pet: str | None) -> None:
    """配置里的角色不可用 → **把配置写回"默认形象"**（task13 §4 的"§3.16 顺带项"）。

    ## 为什么必须写回

    不写回的话磁盘上永远留着那个坏命名空间，于是**每次启动**都要再走一遍
    "解析失败 → 回退默认形象 → 警告"——用户明明每次看到的都是默认形象，
    配置里却写着别人的名字。task13 §6 的验收就是这一条：
    "坏包 → 配置被改回默认形象（重启后不再踩同一个坑）"。

    ## ⚠ 只在**真的启动**这条路径上写，只读查询绝不写

    `--list-pets` / `--list-actions` 是只读查询（agent.md §2.10："读操作只读"），
    它们也会解析素材包、也会撞上"配置里的包坏了"，但**一个字节都不许写**。
    所以本函数由调用方在 `query_only=False` 时才会走到。

    ## 写回的值是**空串**，不是 `"weedy"`

    `config.pet` 的默认值是空串 = "用户还没表态"，而默认形象的真值只在
    `pet_spec.DEFAULT_PET_NAMESPACE` 一处（§4.1）。写一个 `"weedy"` 进去
    等于把默认形象的名字抄到第二个地方——哪天默认形象换人，配置文件里
    会留着一个陈旧的、看起来像"用户主动选的"值。空串回到缺省路径才是对的。

    ## 返回值与失败

    写失败**只记日志、不阻断启动**：能跑起来比"配置干净"重要得多，
    而且失败原因已经在日志里了（下次启动会再试一遍）。
    """
    if not config_pet:
        return
    result = store.apply("pet", "")
    if result.ok:
        _logger.info("配置里的角色 %r 不可用，已把配置改回默认形象（写为空串）", config_pet)
    else:
        _logger.error(
            "配置里的角色 %r 不可用，但没能把配置改回默认形象：%s"
            "（本次照常使用默认形象 %s）",
            config_pet,
            result.error,
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
        if result.fell_back and not query_only:
            # 配置里的包坏了 → 把配置写回默认形象（task13 §4 的顺带项）。
            # ⚠ `query_only` 那条分支**一个字节都不写**：只读查询就是只读。
            _persist_pet_fallback(store, config_pet)
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

    # ------------------------------------------------------------ 重启等待（task13）
    #
    # **新进程先等 `--restart-delay` 再抢单实例锁**（task13 §2 第 8 条 / §4）。
    # 这个等待是"锁的交接"的一半（另一半是旧进程主动放锁）：
    # 旧进程拉起我们之后还要跑完退出收尾，那段时间里锁还占着。
    #
    # ⚠ 位置在 `--list-*` **之后**：只读查询不该为一次重启等待 1.2 秒，
    # 更不该被"已有实例"拒绝（task13 §6 第 2 条）。
    #
    # ⚠ 只有"真的是被重启拉起来的"才等：参数缺省时一秒都不等，
    # 于是"双击启动"的日常路径不受影响。
    if args.restart_delay is not None and args.restart_delay > 0:
        _wait_before_lock(args.restart_delay)

    # ------------------------------------------------------------ 单实例保护（task13）
    #
    # 位置在**只读查询之后、建窗口之前**（§13 + §16 的硬约束）。
    #
    # ⚠ `--list-sizes` 是特例：它必须晚于 `QApplication`（要读真实屏幕），
    # 于是它在下面**更靠后**才返回。但它是**只读查询**，同样不许被单实例拒绝
    # （task13 §2 第 2 条把三个 `--list-*` 并列写在一起）。
    # 所以这里抢到锁之后**先在下面那条路径上放掉**——见 `_release_for_query()`。
    #
    # 抢不到（`None`）= 已经有实例在跑 → **安静退出、返回 0**：
    # 不弹窗、不写错误日志（这是**正常**情况，用户双击了第二次而已）。
    #
    # ⚠ `--list-sizes` 是特例：它是**只读查询**，同样不许被单实例拒绝
    # （task13 §2 第 2 条把三个 `--list-*` 并列写在一起），但它必须晚于
    # `QApplication`（要读真实屏幕），所以**不能**像另外两个那样提前返回。
    # 对策：这条路径**压根不抢锁**——锁的意思是"我要开一个宠物窗口"，
    # 而它根本不开窗口。第一版写成"抢了再放"，实测第二个实例会先被
    # `acquire()` 判成"已有实例"直接返回，那个 `release()` 永远走不到。
    query_only_run = bool(args.list_sizes)

    instance = (
        single_instance.SingleInstanceHandle(held=False, available=False, name="(只读查询)")
        if query_only_run
        else single_instance.acquire()
    )
    if instance is None:
        return _EXIT_OK

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
        #
        # **它压根不抢单实例锁**（见上面 `query_only_run` 的说明）：
        # 锁是"我打算开一个窗口"的意向声明，而这条路径只打印、不开窗口。
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
        # 用法错误也要**把锁还回去**：本进程马上退出，进程消亡时系统本来也会
        # 回收句柄；主动放掉是为了"退出与句柄回收之间没有那个窗口期"
        # （放锁 = 释放所有权 + 关句柄，见 `single_instance`）。
        instance.release()
        return _EXIT_PET_UNAVAILABLE

    level, level_source = _resolve_size_level(args.size_level, store, pet.cell)
    # 位置的决定顺序与档位同构：命令行 > 上次保存的 > 缺省落点（task08 §4）。
    position, position_source = _resolve_position(args.position, store)

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

    # 音效（task10）：**按素材索引的 `sounds` 声明**构建，没有声明就是没有声音。
    # 音量取自配置 `volume`（越界夹取；类型不合法的值配置层已经回退成默认值）。
    # ⚠ 所有播放器调用都在 GUI 线程（`QMediaPlayer` 不是线程安全的，agent.md §4.9）：
    # 本进程**不建任何音频线程**，播放全部经由 `SoundPlayer.play()` 这一条路径。
    sound_player = SoundPlayer(pet, normalize_volume(store.get("volume")))

    # 键盘形态（task17）：**与 sounds 同级的可选段**。没有 / 坏掉都只让
    # 菜单项灰掉，**绝不阻止启动**（§2.10 第三类：可恢复 → 记日志 + 退化）。
    # ⚠ 段缺失 / 为 `null` 时既不报错也不警告（`load_keyview` 返回空列表对）。
    keyview, kv_issues, kv_warnings = load_keyview(pet.root)
    for issue in kv_issues:
        _logger.warning("键盘形态不可用：%s", issue)
    for warning in kv_warnings:
        _logger.info("键盘形态：%s", warning)
    _log_keyview_summary(keyview, pet.namespace)

    window = PetWindow(
        pet,
        level,
        mirror=mirror,
        idle_state=idle_state,
        config=store,
        sounds=sound_player,
        # 边缘抗锯齿（task15）：**启动时读一次**配置。
        # 生效方式是**重启生效**，所以这里没有"开关变了要重绘"的通道
        # （用户拍板，见 task15 §1/§5：少一条动态通道就少一处会对不上的状态）。
        antialias=bool(store.get("antialias")),
        # 键盘形态（task17 §4.1 的形态接缝）。`None` = 没有可用的 KV。
        keyview=keyview,
    )
    window.play_action(action)
    if position is None:
        window.showing_at_default_position()
        # 缺省落点 → **底部预留机制生效**（task09 §4）：打开控制栏时把宠物
        # 抬起来，关闭时放回贴屏幕底边。
        window.set_reserved(True)
    else:
        # `user_positioned=True`：这个位置是用户自己的意思（命令行或上次落脚点），
        # 于是缩放不会把它弹回右下角缺省落点（task08 §4 硬规则），
        # 底部预留机制也不生效（task09 §4）。
        window.show_at(position[0], position[1], user_positioned=True)
    # 形态（task17）：**在 show() 之前恢复**，于是用户看到的第一眼就是他上次的形态
    # （先 show 再切会白闪一下宠物形态）。
    #
    # ⚠ 恢复顺序在**播放动作之后**：`play_action()` 定的是"宠物形态显示哪个动作"，
    # 与形态无关（KV 形态下它只是把动画状态摆好，切回来时接着播）。
    form_controller = FormController(window, store)
    form_controller.restore()
    # `开启KV` / `退出KV` 的动作 = 切形态 + **切成功了才记住**（见 `switch()`）。
    window.set_form_handler(form_controller.switch)

    window.show()

    _print_startup_line(window, level_source, position_source, form_controller.layout)

    # ------------------------------------------------------------ 音效预热与启动音（task10）
    #
    # **不能阻塞**是这一块的全部要点（task10 §4）：
    #   1. `preload()` 立即返回，预热结果走 `sound_ready(available, reason)` 信号回来；
    #   2. 启动音由 `QTimer.singleShot(80, ...)` 排，**不在主线程 sleep 或忙等**；
    #   3. 预热超过 `PRELOAD_TIMEOUT_MS` 没结果 → **另一个定时器**兜底，
    #      按"没有音效"照常启动（用户不该因为一块网卡/声卡卡住就用不上桌宠）。
    #
    # 三个定时器是**互相取消**的：谁先到就把另外两个停掉，所以启动音只会响一次。
    startup_chime_timer = QTimer()
    startup_chime_timer.setSingleShot(True)
    startup_chime_timer.setTimerType(Qt.PreciseTimer)
    startup_chime_timer.timeout.connect(lambda: sound_player.play(SOUND_START))

    preload_timeout_timer = QTimer()
    preload_timeout_timer.setSingleShot(True)
    preload_timeout_timer.setTimerType(Qt.PreciseTimer)

    def _on_sound_ready(available: bool, reason: str) -> None:
        """预热有结果了：**先停掉超时兜底**，再按 80 ms 排启动音。

        预热失败也照常排：`play()` 自己会静默跳过，"没声明 start"与"文件坏了"
        都不该让启动流程分叉（音效是可选功能，agent.md §2.10 第三类）。

        **只排一次**：`settle()` 之后会再发一次 `sound_ready`（把异步结论带过来），
        那时不该把已经排好的启动音重置一遍。
        """
        preload_timeout_timer.stop()
        if not available:
            _logger.info("本次运行没有可用音效：%s", reason)
        if not startup_chime_timer.isActive():
            startup_chime_timer.start(STARTUP_CHIME_DELAY_MS)

    def _on_preload_timeout() -> None:
        """预热超时：**不等了**，按"没有音效"照常启动，并留一条日志说明白了。

        顺手调一次 `settle()`：`QMediaPlayer.setSource()` 是**异步**加载的，
        超时这一刻"没报错"的声音就该按能播算（`sounds.SoundPlayer.settle()`），
        否则一个只是加载慢的好音效会被永久判死。
        """
        startup_chime_timer.stop()
        sound_player.settle()
        _logger.warning(
            "音效预热超过 %d ms 没有结果，本次按没有音效继续（程序照常可用）",
            PRELOAD_TIMEOUT_MS,
        )

    sound_player.sound_ready.connect(_on_sound_ready)
    preload_timeout_timer.timeout.connect(_on_preload_timeout)
    preload_timeout_timer.start(PRELOAD_TIMEOUT_MS)
    # 预热排在窗口 `show()` 之后：启动音与窗口出现**同时**发生，
    # 用户从双击图标到听见声音是一条连贯的体验（task10 §6"启动时不卡"）。
    sound_player.preload()
    # `preload()` 里的加载是**异步**的（`QMediaPlayer.setSource()` 立即返回），
    # 所以"能播几个"要等一拍才定得下来（`settle()`）。汇总日志放在 settle 之后，
    # 免得它报的是"还没结论"的中间态。
    sound_player.settle()
    _log_sound_summary(sound_player)

    # ------------------------------------------------------------ 全局热键（task12）
    #
    # 组装放在**窗口 show() 之后**：热键注册失败时要弹提示、要写配置，
    # 两件事都希望"用户已经能看到宠物"（隐藏功能才有意义）。
    #
    # ⚠ 注册失败**绝不阻断启动**：没有热键只是"不能隐藏"，桌宠照常能用
    # （`agent.md §2.10` 第三类：可恢复 → 记日志 + 退化）。真的危险的是
    # "没有热键却允许隐藏"，那一条由 `hide_pet()` 拦住。
    hotkey_controller = HotkeyController(window, store)
    hotkey_result = hotkey_controller.start(str(store.get("hotkey") or hotkey_mod.DEFAULT_HOTKEY))
    if hotkey_result.combo is None:
        _logger.error("隐藏功能不可用：%s", hotkey_result.reason)

    # ------------------------------------------------------------ 按键联动（task18）
    #
    # 组装放在**窗口 show() 之后**：启用条件里有一条是"窗口可见"，
    # 它要窗口先起来才问得准（agent.md 4.9 同一条纪律）。
    #
    # ⚠ **不做**：不装键盘钩子、不开忙等线程（参考实现那条不 sleep 的 while）。
    # 这里是一条 8 ms 的 `QTimer` 轮询（实测 28.9 us/轮 约 0.36% 单核），
    # 只问**真正被绑定的**那几个 VK（task18 4.2）。
    key_controller = KeyListenerController(window, store)
    # 形态一切换就要重新判"该不该轮询"：进 KV 且可见 -> 开；回宠物 -> 停。
    # 挂在 `set_form` 之后（`FormController.switch` / `restore` 都走它）。
    _original_switch = form_controller.switch

    def _switch_form_with_keys(form: str) -> bool:
        """切形态 + **重新判一次该不该轮询**（task18 4.4 的启用条件）。

        ⚠ 顺序是"先切、成功了再同步"：切失败（KV 不可用）时形态没变，
        启用条件自然还是原来的结论，不需要为失败路径单独写一遍。
        """
        switched = _original_switch(form)
        key_controller.sync()
        return switched

    window.set_form_handler(_switch_form_with_keys)
    # 启动时那一次也要同步：配置里记的是 KV 形态时，监听器要**当场**转起来。
    key_controller.sync()
    _logger.info(
        "按键联动：%s（绑定 %d 个键、%d 个 VK，轮询间隔 %d ms）",
        "已启用" if key_controller.should_poll() else "未启用（不在键盘形态 / 窗口不可见）",
        sum(len(ids) for ids in key_controller.listener.bindings.values()),
        len(key_controller.listener.bindings),
        key_controller.listener.interval_ms,
    )
    # ------------------------------------------------------------ 尺寸控制栏（task09）
    #
    # 组装放在**窗口 show() 之后**：控制栏要读宠物的权威位置与自己所在屏幕，
    # 两者都要窗口先起来才准（agent.md §4.9 "DPI 缩放需要窗口先起来"）。
    bar_controller = ControlBarController(window)
    # 隐藏/呼出时要连控制栏一起处理（task12）：藏起来两个都藏、
    # 呼出来则**只在藏之前它是开着的**才放回来。
    hotkey_controller.bar_controller = bar_controller
    # `调整大小…` 的动作 = 开关控制栏 **+ 重置静置计时**（task09 §4 "菜单项"）。
    # 重置的理由：用户刚跟界面交互过，接着就张望会显得"没在听"。
    # 用 `IdleStateMachine.on_mouse_moved()` 而不是直接改内部字段——
    # "怎么算一次交互"的真值只有一个（§2.15）。
    def _toggle_resize() -> None:
        idle_state.on_mouse_moved()
        bar_controller.toggle()

    window.set_resize_handler(_toggle_resize)
    # 跟随：位置变化信号驱动**增量平移**，绝不轮询（task09 §5）。
    window.position_changed.connect(lambda _pos: bar_controller.sync_follow())
    bar_controller.reposition()

    # ------------------------------------------------------------ 设置窗口（task11）
    #
    # 组装与单例都在 `SettingsController` 里；这里只负责"菜单项点下来做什么"。
    settings_controller = SettingsController(store, window)

    def _open_settings() -> None:
        """`设置…` 的动作 = **打开/前置设置窗口** + 重置静置计时。

        重置的理由与 `调整大小…` 一样（task09 §4 的"菜单项"那条）：
        用户刚跟界面交互过，紧接着就张望会显得"没在听"。
        真值仍然只有一个（`IdleStateMachine.on_mouse_moved()`）。

        ⚠ 顺序：**先清理启动文件夹的遗留标记文件，再打开窗口**（task14）。
        `autostart.cleanup_legacy_markers()` 是写操作，它只能挂在
        "用户明确做了某件事"上——点开设置窗口正是那件事。反过来的话，
        窗口会先按"那里还有一个遗留文件"的状态渲染一次，白闪一下。
        """
        idle_state.on_mouse_moved()
        autostart_controller.open_hook()
        settings_controller.open()

    window.set_settings_handler(_open_settings)
    # 呼出快捷键那一行（task12）：界面**不碰** RegisterHotKey，
    # 它只问一句"这个组合能不能用"，注册与回滚都在 HotkeyController 里。
    settings_controller.set_hotkey_changer(hotkey_controller.change)
    # 换角色那一行（task13）：界面**不碰** QProcess，也不自己弹询问框，
    # 它只问"用户同意重启吗"与"帮我重启"。两件事都在 RestartController 里。
    restart_controller = RestartController(app, instance)
    settings_controller.set_restart_handler(
        restart_controller.restart, restart_controller.confirm
    )
    # 开机自启那一行（task14）：界面**不碰启动文件夹**，它只问一句
    # "帮我开/关一下"。快捷方式生成、`.cmd` 回退、失败原因都在 `autostart` 里。
    autostart_controller = AutostartController()
    settings_controller.set_autostart_handler(autostart_controller.apply)

    # 音量"改完立刻生效"（task10 §2 第 3 条）：挂在配置的 `config_changed` 上。
    # ⚠ 这条只在**程序内改配置**（`ConfigStore.set()` / `reload()`）时触发；
    # task10 阶段"手改 config.json"要生效**仍需重启**（`ConfigStore` 是内存快照，
    # 与 task07 的 `idle_seconds` 同一个道理）。task11 的设置界面会走 `set()`，
    # 于是滑块一动这里就跟着变。
    def _on_config_changed(key: str, value: object) -> None:
        if key == "volume":
            applied = sound_player.set_volume(normalize_volume(value))
            _logger.info("音量已改为 %d", applied)

    store.config_changed.connect(_on_config_changed)

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
        # 退出收尾。收掉自己的心跳定时器、久置计时器、光标轮询、
        # **全局热键**、单实例锁与音效设备。
        window.stop_animation()
        # ⚠ **热键必须彻底释放**（task12 §4）：不释放的表现是"组合被本进程占着"，
        # 退出后立刻重启，同一个组合注册不上（验收里专门盯这一条）。
        # 放在**最前面**：它比别的收尾更怕"来不及做"。
        hotkey_controller.stop()
        # 控制栏是**独立顶层窗口**：不显式收起的话它会比宠物活得久
        # （`Qt.Tool` 不计入"最后一个主窗口"，agent.md §4.9）。
        bar_controller.close()
        # 设置窗口是**模态**的：不关掉的话 `app.exec()` 不会返回
        # （退出时用户手里正好开着设置的场合）。
        settings_controller.close()
        idle_timer.stop()
        # 按键轮询必须**明确停掉**（task18）：与光标轮询一样是个常驻定时器，
        # 留着会让"退出后还有残留"（task07 §6 最后一条的同一条纪律）。
        key_controller.stop()
        # 光标轮询必须**明确停掉**：它是个 150 ms 的常驻定时器，
        # 留着会让"退出后还有残留"（task07 §6 最后一条）。
        cursor_watch.stop()
        # 音效设备必须**真的释放**（task10 §4"退出"）：句柄不释放的表现是
        # "退出后音频设备被占着，立刻再启动一次就没声音了"。
        startup_chime_timer.stop()
        preload_timeout_timer.stop()
        sound_player.close()
        # 单实例锁（task13）：走重启路径时它**已经在 `restart_self()` 里放掉了**
        # （`release()` 幂等，这里再调一次没有副作用）；正常退出这条路
        # 就靠这一句把锁还回去——不还的话"关掉桌宠再启动"会被告知"已有实例"。
        instance.release()
        if heartbeat is not None:
            heartbeat.stop()
    _logger.info("%s 退出（退出码 %d）", PROGRAM_NAME, code)
    return code
