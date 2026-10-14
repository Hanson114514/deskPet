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
import signal
import sys

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import PROGRAM_NAME, __version__
from . import geometry
from . import logger as log
from . import paths, pet_loader, size
from .animation import UnknownActionError
from .config import ConfigStore
from .control_bar import RESERVED, ControlBar
from .cursor_watch import CursorWatch
from .errors import DataDirError
from .idle_state import IdleStateMachine, normalize_idle_seconds
from .mirror import MirrorState
from .pet_loader import PetLoadError, list_reports, resolve_pet
from .pet_spec import PetSpec
from .pet_window import PetWindow
from .rendering import FrameRenderer  # noqa: F401  （打包静态分析要收全渲染层）
from .sounds import SOUND_CHIME, SoundPlayer, normalize_volume

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

    # ------------------------------------------------------------ 开关

    @property
    def visible(self) -> bool:
        """控制栏当前是不是开着（**幂等的判据**）。"""
        return self.bar is not None and self.bar.isVisible()

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


def _print_startup_line(window: PetWindow, level_source: str, position_source: str) -> None:
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
        "pos=(%d, %d)(%s) level=%d/%d(%s) size=%dx%d mask=%s mirror=%s dpr=%.2f",
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

    window = PetWindow(
        pet,
        level,
        mirror=mirror,
        idle_state=idle_state,
        config=store,
        sounds=sound_player,
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
    window.show()

    _print_startup_line(window, level_source, position_source)

    # ------------------------------------------------------------ 音效预热与启动音（task10）
    #
    # **不能阻塞**是这一块的全部要点（task10 §4）：
    #   1. `preload()` 立即返回，预热结果走 `sound_ready(available, reason)` 信号回来；
    #   2. 启动音由 `QTimer.singleShot(80, ...)` 排，**不在主线程 sleep 或忙等**；
    #   3. 预热超过 `PRELOAD_TIMEOUT_MS` 没结果 → **另一个定时器**兜底，
    #      按"没有音效"照常启动（用户不该因为一块网卡/声卡卡住就用不上桌宠）。
    #
    # 三个定时器是**互相取消**的：谁先到就把另外两个停掉，所以 `chime` 只会响一次。
    startup_chime_timer = QTimer()
    startup_chime_timer.setSingleShot(True)
    startup_chime_timer.setTimerType(Qt.PreciseTimer)
    startup_chime_timer.timeout.connect(lambda: sound_player.play(SOUND_CHIME))

    preload_timeout_timer = QTimer()
    preload_timeout_timer.setSingleShot(True)
    preload_timeout_timer.setTimerType(Qt.PreciseTimer)

    def _on_sound_ready(available: bool, reason: str) -> None:
        """预热有结果了：**先停掉超时兜底**，再按 80 ms 排启动音。

        预热失败也照常排：`play()` 自己会静默跳过，"没声明 chime"与"文件坏了"
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

    # ------------------------------------------------------------ 尺寸控制栏（task09）
    #
    # 组装放在**窗口 show() 之后**：控制栏要读宠物的权威位置与自己所在屏幕，
    # 两者都要窗口先起来才准（agent.md §4.9 "DPI 缩放需要窗口先起来"）。
    bar_controller = ControlBarController(window)
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
        # 退出收尾。本阶段还没有热键 / 单实例锁要放（task12 / task13），
        # 收掉自己的心跳定时器、久置计时器、光标轮询与**音效设备**。
        window.stop_animation()
        # 控制栏是**独立顶层窗口**：不显式收起的话它会比宠物活得久
        # （`Qt.Tool` 不计入"最后一个主窗口"，agent.md §4.9）。
        bar_controller.close()
        idle_timer.stop()
        # 光标轮询必须**明确停掉**：它是个 150 ms 的常驻定时器，
        # 留着会让"退出后还有残留"（task07 §6 最后一条）。
        cursor_watch.stop()
        # 音效设备必须**真的释放**（task10 §4"退出"）：句柄不释放的表现是
        # "退出后音频设备被占着，立刻再启动一次就没声音了"。
        startup_chime_timer.stop()
        preload_timeout_timer.stop()
        sound_player.close()
        if heartbeat is not None:
            heartbeat.stop()
    _logger.info("%s 退出（退出码 %d）", PROGRAM_NAME, code)
    return code
