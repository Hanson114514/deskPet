"""宠物主窗口：无边框 / 置顶 / 透明背景 / 可拖动 / 按档位缩放 / **按素材帧率播放动画**。

task01 交付了**最小可运行骨架**（占位图形 + 拖 + 退出）；task03 起窗口显示
**真实素材的第一帧**，占位模块 `placeholder.py` 已随之删除；
task04 起窗口尺寸由**尺寸档位**决定（10 级、屏幕自适应、右下角锚点）；
task05 起窗口**持续播放动画**——帧推进由 `animation.Animation` 决定，
本文件只负责"什么时候问下一帧、把哪一帧画出来"；
task06 起**右键菜单**（换动作 / 休息 / 镜像翻转）也接在这里：
菜单本身在 `context_menu.py` 里现造，本文件只负责"什么时候弹、弹完做什么"。
task08 起**拖动是正式交互**：只有**按在人物身上**才起拖（靠 `setMask` 的真穿透，
点击透明区时窗口根本收不到事件），松手把落脚点**夹取后**写进配置。

## 拖动与命中的分工（task08）

| 事 | 住哪 |
| --- | --- |
| 区域生成与命中判定 | `hit_test.region_for_frame()` / `hit_test.hit_test()` |
| 基准位置 / 上次方向 / 方向→动作 | `drag.DragState` / `drag.direction_action()` |
| 夹取 / 锚点 / 缺省落点 / `"x,y"` 解析 | `geometry` |
| 鼠标事件、切动作、写配置 | **本文件**（事件处理器搬不走） |

**本文件不重复做命中判定**：能收到 `mousePressEvent` 就说明按下点落在遮罩区域内
（区域外由系统级穿透），再调一次 `hit_test()` 等于造第二份判定真值。
`hit_test()` 留给自检与诊断用。

## 镜像的两条衍生数据都要跟着失效（agent.md §4.7 / §3.20）

翻转之后**两样东西**必须重算，少一样就出坑：

1. **帧缓存**：键含镜像维度（`rendering.FrameRenderer.cache_key()`）；
2. **`QRegion` 遮罩**：按**镜像后的图**重算（`apply_mask()` 收的就是镜像后的图）。

`_on_mirror_changed()` 是这两件事的唯一落点。

**三条硬约定**：

1. **位置用自己记的基准算，绝不回读窗口实时坐标**（agent.md §4.6）。
   权威位置是 ``PetWindow._base_pos``，只有 ``move_to()`` 会写它。
   回读 ``self.pos()`` / ``self.geometry()`` 会拿到**异步生效的旧值**，
   表现就是"拖动只走一小截"。
2. **两种坐标不可混用**：位移看 ``event.globalPosition()``（屏幕坐标），
   命中判定看 ``event.position()``（窗口内坐标）。
3. **切档顺序：先重建该档的帧，再改窗口尺寸**（`迁移清单.md` §4 第 5 条）。
   反了会短暂留白或裁边——本文件的 `set_size_level()` 是唯一改档入口，
   顺序写在那一处，别在别处再拼一遍。

## 动画怎么驱动（task05 §4 的调度约定）

**单次 `QTimer` + 每帧重排**（``AnimationDriver``）：每一帧结束后按
`Animation.advance()` 的返回值 `start(next_ms)` 重排下一次，**不是**固定 16 ms 空转。
`advance()` 返回 `None`（定格 / 一次性动作停在末帧 / 片段播完）→ **不 `start()`**，
定时器就此停住、画面不再重绘。

**"不排下一次"就是这么表达的**：定时器的 `isActive()` 就是"还在播吗"。
不需要额外的布尔开关——多一个开关就多一处能对不上的状态。
"""


import logging
from collections import OrderedDict

from PySide6.QtCore import QElapsedTimer, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QContextMenuEvent,
    QGuiApplication,
    QImage,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QRegion,
    QShortcut,
)
from PySide6.QtWidgets import QWidget

from typing import Callable, Final

from . import PROGRAM_NAME, geometry, hit_test, size
from . import context_menu, keyview_render
from .animation import MAX_DT_MS, Animation
from .config import ConfigStore
from .context_menu import FORM_KEYVIEW, FORM_PET, MenuState
from .drag import DEFAULT_DIRECTION, DragController
from .idle_state import IdleStateMachine
from .key_listener import KeyListener  # noqa: F401  （装配层用；打包静态分析要收全）
from .keyview_spec import KeyViewSpec
from .mirror import MirrorState
from .pet_spec import PetSpec
from .rendering import FrameRenderer
from .sounds import SOUND_REST, SOUND_WAVING, SoundPlayer

logger = logging.getLogger(__name__)

#: 默认落点距屏幕右下角的留白（逻辑单位）。**真值在 `size.DEFAULT_MARGIN`**，
#: 这里只做引用（同一个约定不要在两处各写一个 24）。
DEFAULT_MARGIN = size.DEFAULT_MARGIN

#: 本 task 显示的动作（动画播放属 task05，这里只取第一帧）。
DEFAULT_ACTION = "idle"

#: `坐下休息` 切到的动作，以及 `站起来` 切回的动作（task06 §2 第 7 条）。
#: 动作名用 2025 定案的下划线写法（`sit` 已作废，**不做别名**，agent.md §0）。
REST_ACTION = "rest"
REST_STANDUP_ACTION = DEFAULT_ACTION

#: 久置状态机各阶段对应的动作名。**真值是 `idle_state.Stage.value` 本身**
#: （阶段名与动作名同形就是为此），这里只做一次显式核对，不另立一张表。
IDLE_ACTION = DEFAULT_ACTION

#: 双击手势播的动作（task10 §4"双击手势"）：**完整播放一次**，播完回 `idle`。
WAVING_ACTION = "waving"

#: 双击手势的片段写法：整段一次（等价 `("loop", 1)`）。
#: **不裁帧**——首帧是抬手（起势）、末帧是收手（收势），衔接 `idle` 更自然。
WAVING_SEGMENT = ((WAVING_ACTION, None, ("loop", 1)),)


class _Content:
    """**形态接缝**（task17 §4.1）：一个形态要回答的三件事。

    用户 2026 定案的是**方案 B**：同一个 `PetWindow`，"当前画面"抽成 content，
    加"形态（form）"概念——**不新开窗口**（`KeyViewWindow` 那条不要实现）。

    三个问题的答案就是全部接缝：

    | 问题 | 宠物形态 | 键盘形态 |
    | --- | --- | --- |
    | 画布多大（`cell`）？ | `pet.cell` | `keyview.cell` |
    | 这一档该画哪张图？ | `renderer.frame(动作, 帧号, 档位, 镜像)` | `compose(档位, pressed)` |
    | 该不该镜像？ | 按状态记忆 | **永不**（镜像只作用于 idle/waiting/rest） |

    ⚠ **尺寸与位置共用**宠物形态那一套：切形态**不重置档位、不重置位置**
    （用户 2026 定案第 3 条）。所以这两个属性**都不含档位决策**，
    档位仍然只由 `PetWindow._apply_level()` 一处说了算。
    """

    #: 形态名（`context_menu.FORM_PET` / `FORM_KEYVIEW`）。
    form: str = FORM_PET

    #: 画布尺寸（**所有布局共用**）。
    cell: tuple[int, int] = (0, 0)

    def image(self, level: int, mirrored: bool) -> QImage:
        """这一档、这个镜像态下该显示的图。**空图 = 没有图可画**（降级）。"""
        raise NotImplementedError

    def mirrored_for(self, action: str) -> bool:
        """这个形态下 `action` 该不该镜像。**键盘形态恒为 `False`**。"""
        return False


class PetContent(_Content):
    """宠物形态的画源：就是原来的 `FrameRenderer` 那一套（行为一个字没改）。

    ⚠ 它**持有** `FrameRenderer` 与 `Animation`（帧号从动画来），
    所以"切回宠物形态从原来那一帧接着播"是天然的：
    动画状态从未被切形态动过（只停表，见 `PetWindow.set_form()`）。
    """

    def __init__(self, pet: PetSpec, renderer: FrameRenderer, animation: Animation) -> None:
        self.form = FORM_PET
        self.pet = pet
        self.renderer = renderer
        self.animation = animation
        self.cell = pet.cell

    def image(self, level: int, mirrored: bool) -> QImage:
        pixmap = self.renderer.frame(
            self.animation.render_action, self.animation.index, level, mirrored
        )
        if pixmap.isNull():
            return QImage()
        return pixmap.toImage()


class KeyViewContent(_Content):
    """键盘形态的画源：`keyview_render.KeyViewRenderer` 的分层合成。

    ## 尺寸与镜像是怎么被"共用"的

    - **尺寸**：`cell` 是 `keyview.cell`（所有布局共用一份）→ 切形态/切布局
      **都不改窗口尺寸**，于是锚点与控制栏零风险（task17 §3.3）；
    - **镜像**：恒为假。镜像只作用于 `idle` / `waiting` / `rest`（agent.md §0），
      键盘形态没有"状态"这回事，**翻转它没有任何意义**。
      ⚠ 这不是"暂不支持"，是**语义上不该有**——所以是硬 `False`，不是配置项。
    """

    def __init__(
        self,
        spec: KeyViewSpec,
        level: int | None = None,
        layout: str = "",
    ) -> None:
        self.form = FORM_KEYVIEW
        self.spec = spec
        self.renderer = keyview_render.KeyViewRenderer(spec, level)
        self.cell = spec.cell
        #: **当前按下的键 id，按按下顺序**（最早在前）。
        #: ⚠ K1 只到这里为止："物理键 → 这个元组"是 **task18** 的事。
        self.pressed: tuple[str, ...] = ()
        #: 当前布局的**有效键 id 集合**（task19）：布局之外的键**不亮、也不画**。
        #:
        #: ⚠ 这一维**必须**有：`background` 里烤的是全部键帽（16 个），
        #: `layouts.<名>.rows` 才是"这一档画面里真的有哪几个键"。
        #: 没有它的话，4 键布局下按一个 8 键布局才有的键照样会亮
        #: ——画面与布局对不上，用户会以为"布局没生效"。
        self._layout: str = ""
        self._active: frozenset[str] = frozenset()
        self.set_layout(layout or (spec.layout_names[0] if spec.layout_names else ""))

    def image(self, level: int, mirrored: bool) -> QImage:
        # 出图前**再滤一次**：`pressed` 可能来自别的入口（`set_pressed()` 是
        # 程序入口，谁都能调），滤在这里则"画面上的键永远在当前布局里"是一条
        # 与入口无关的保证。集合判定是 O(1)，代价可以忽略。
        return self.renderer.image(
            level, tuple(key for key in self.pressed if key in self._active)
        )

    def layout_names(self) -> tuple[str, ...]:
        """这个包有哪些可用布局（菜单项灰不灰、回退到哪个都看它）。"""
        return self.spec.layout_names

    # ------------------------------------------------------------ 布局（task19）

    @property
    def layout(self) -> str:
        """当前布局名（`""` = 没有可用布局）。"""
        return self._layout

    @property
    def active_keys(self) -> frozenset[str]:
        """当前布局里**有效**的键 id（布局之外的键不亮）。"""
        return self._active

    def set_layout(self, name: str) -> bool:
        """换成另一个布局；返回**画面真的变了吗**。

        ⚠ 切布局**不改 `cell``**（所有布局共用一份，§2.23）→ 窗口尺寸、
        锚点、控制台预留**全都不动**，所以这里**不碰 `_apply_level()`**：
        由调用方决定要不要重绘（映射变了要重绘一次，见 `PetWindow.set_layout`）。
        """
        spec = self.spec.layout(name)
        if spec is None:
            return False
        active = frozenset(spec.key_ids)
        if name == self._layout and active == self._active:
            return False
        self._layout = name
        self._active = active
        # 布局换小之后，原来按着的、现在不在布局里的键要**当场消失**
        # （否则画面上留着一个这个布局根本没有的键）。
        self.pressed = tuple(key for key in self.pressed if key in active)
        return True


def region_for_image(image: QImage | None) -> QRegion | None:
    """按 ``image`` 的**不透明像素**生成一个窗口区域。

    task08 起**实现搬到了 `hit_test.region_for_frame()`**（agent.md §2.8：
    "唯一入口……不要写第二份"），这里只保留转发——旧名字在 task01–07 的
    自检里被用过，改名会让那些回归脚本无谓地红掉。

    **新代码请直接用 `hit_test`**（它还多了 `mirrored` 参数）。
    """
    return hit_test.region_for_frame(image)


class AnimationDriver(QObject):
    """把 `Animation` 挂到 `QTimer` 上：**每帧结束后按返回值重排下一次**。

    这是 task05 §4"调度"那一节的全部实现，规则只有四条：

    1. `singleShot` 定时器，每次回调只推进**一帧**，然后按 `advance()` 的返回值
       `start(next_ms)` 重排——**不是**固定间隔空转；
    2. `advance()` 返回 `None` → **不 `start()`**（定格 / 一次性动作停在末帧 /
       片段播完都走这一条），定时器自己停住；
    3. 间隔夹在 `[MIN_INTERVAL_MS, MAX_INTERVAL_MS]`：下限防忙等，
       上限保证退出 / 信号被及时处理（agent.md §4.9）；
    4. `dt` 用 `QElapsedTimer` 实测，交给 `Animation.advance(dt)`，
       由它在内部夹到 `MAX_DT_MS`（休眠唤醒后不跳帧）。

    **`QTimer` 不保证准点**，所以这里不写"每 83 ms 加一帧"这种假设：
    `start(next_ms)` 的语义是"至少过这么久再叫我"，抖动不会被放大
    （agent.md §4.9 第一条）。
    """

    #: 调度间隔下限（毫秒）：防忙等（task05 §4）。
    MIN_INTERVAL_MS: Final[int] = 4
    #: 调度间隔上限（毫秒）：保证退出 / 信号被及时处理。
    MAX_INTERVAL_MS: Final[int] = 250

    #: 需要显示某一帧（**帧号可能与上一帧相同**，由调用方自己判要不要重绘）。
    frame_due = Signal()

    def __init__(self, animation: Animation, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.animation = animation
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        # ⚠ **必须用 `Qt.PreciseTimer`**：Windows 的默认定时器精度是 15.6 ms，
        # `Qt.CoarseTimer`（Qt 的默认值）会把 33 ms 的请求**向上取整到 46.8 ms**，
        # 于是"30 fps 的素材放出来只有 21 fps"——实测 2 秒只换了 43 帧（应 60 帧）。
        # `PreciseTimer` 实测 33.8 ms/帧，与素材的 33.3 ms 只差半毫秒。
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._on_timeout)
        #: 上一次推进的时刻（算 `dt` 用）。**只在 `_on_timeout` 里读**。
        self._clock = QElapsedTimer()
        #: 已推进的帧数（自检与诊断用；不代表"换了几张不同的图"）。
        self.advanced_count = 0

    # ------------------------------------------------------------ 对外

    def start(self) -> None:
        """开始播放：先立刻显示当前帧，再按 `frame_ms` 排下一次。"""
        self._clock.start()
        self.frame_due.emit()
        self._schedule(self.animation.frame_ms)

    def stop(self) -> None:
        """停表（**不改动画状态**：定格与"播完了"是两件事，见 `PetWindow.hold`）。"""
        self._timer.stop()

    @property
    def running(self) -> bool:
        """是否还在排下一次换帧（**定格时为 `False`**）。"""
        return self._timer.isActive()

    @property
    def interval_ms(self) -> int:
        """当前排定的间隔（毫秒）；没在跑时为 `-1`。"""
        return self._timer.interval() if self._timer.isActive() else -1

    # ------------------------------------------------------------ 内部

    def _on_timeout(self) -> None:
        """一帧的回调：**先算 dt → 推进 → 显示 → 再排下一次**。"""
        elapsed = float(self._clock.restart())
        next_ms = self.animation.advance(elapsed)
        self.advanced_count += 1
        self.frame_due.emit()
        if next_ms is None:
            # 定格 / 播完 → **不排下一次**。定时器就此停住，画面不再重绘。
            return
        self._schedule(next_ms)

    def _schedule(self, next_ms: float) -> None:
        """按 `next_ms` 排下一次；夹到本类声明的上下限之内。"""
        interval = int(round(float(next_ms)))
        interval = max(self.MIN_INTERVAL_MS, min(self.MAX_INTERVAL_MS, interval))
        self._timer.start(interval)


#: 合并复绘的节拍（毫秒，task18 4.3）。
#:
#: 收到 `keys_changed` **不当场重绘**：一次重绘（遮罩 + 半透明窗口）实测约 **41 ms**
#: （agent.md 4.9），高速连打时连打会积压成一串没人看得清的中间态。
#: 这里用一个**单次**定时器把它合并。
#:
#: ⚠ **是节流不是防抖**（task21 UX-11）：定时器已经在等就**不重置**，
#: 于是"持续输入"期间画面每 `REPAINT_INTERVAL_MS` 照样刷一次；重置（防抖）
#: 会让持续输入期间一次都不刷。
REPAINT_INTERVAL_MS: Final[int] = 16

#: 按下之后、判定为 **Dragging** 之前要越过的位移（屏幕逻辑像素，task21 UX-03）。
#:
#: 鼠标按下那一刻**只进入 `Pressed`**（什么都没变）；越过这个阈值才真的接管
#: 动作控制权、挂起静置逻辑、切指针。这样"只点一下"不会把休息姿势打断，
#: 而真拖动只多等 4 px（人手抖一下就到了，跟手感不受影响）。
DRAG_START_DISTANCE: Final[int] = 4

#: 窗口区域（`QRegion`）缓存**最少**留几条（task21 UX-05）。
#:
#: 键是"这张渲染图的身份"：同一张图区域永远一样，重算只是白花
#: `createAlphaMask()` 的时间。实际上限是 `max(这个数, 当前动作帧数) + 换档余量`
#: （见 `_region_limit()`）—— 写死一个数的话，帧数比它多的动作（`idle` 90 帧、
#: `waiting` 240 帧）会在**同一轮播放里自己把自己挤掉**，等于没有缓存。
REGION_CACHE_LIMIT: Final[int] = 64

#: 上面那个上限之外**额外**留的余量（够放下一档过渡的两三个动作）。
REGION_CACHE_HEADROOM: Final[int] = 32


class PetWindow(QWidget):
    """宠物主窗口（task05：按素材帧率持续播放当前动作）。"""

    #: 窗口位置变了（拖动结束 / 夹取后），agent.md §2.2 的信号表。
    #: **只在真的变了时发**：位置没变的"点一下没拖动"走的是写配置那条路径。
    position_changed = Signal(QPoint)

    #: 隐藏 / 呼出（task12，agent.md §2.2 的信号表）。`True` = 现在可见。
    #: 控制栏等"跟着宠物一起走"的窗口挂在它上面（藏起来要一起藏）。
    visibility_changed = Signal(bool)

    def __init__(
        self,
        pet: PetSpec,
        level: int | None = None,
        parent: QWidget | None = None,
        mirror: MirrorState | None = None,
        idle_state: IdleStateMachine | None = None,
        config: ConfigStore | None = None,
        sounds: SoundPlayer | None = None,
        antialias: bool = False,
        keyview: KeyViewSpec | None = None,
        keyview_layout: str = "",
    ) -> None:
        super().__init__(parent)

        #: 当前显示的素材包。
        self.pet = pet
        #: 权威位置（左上角，屏幕逻辑坐标）。**只由 move_to() 写**。
        self._base_pos = QPoint(0, 0)
        #: 拖动控制器（task08）：基准位置 / 上次方向 / 开始与结束信号。
        #: 鼠标事件处理器留在本类，状态与信号收在 `drag` 里（自检能不开窗口直接测）。
        self.drag_controller = DragController(self)
        #: 用户**拖动过**之后为真：位置一旦由用户决定就以它为准，
        #: 缩放**不得弹回右下角缺省落点**（task08 §4 硬规则）。
        self._user_positioned = False
        #: **按下那一刻**是不是 `manual` 坐姿（双击判据，见 `mouseDoubleClickEvent`）。
        self._manual_rest_at_press = False
        #: **按下但还没越过拖动阈值**（task21 UX-03 的 `Pressed` 态）。
        #: 此时窗口还没接管任何东西：姿势不动、静置计时照走、指针不变。
        #: 越过 `DRAG_START_DISTANCE` 才交给 `_begin_drag()`（→ `Dragging`）。
        self._press_pending = False
        #: `Pressed` 态里按下的**局部**坐标（只为了交给 `DragState.grab_offset`）。
        self._press_local = QPoint(0, 0)
        #: **画面 → QRegion** 的 LRU 缓存（task21 UX-05）。
        #: 键是 `(id(渲染图), 宽, 高, 阈值)`：同一张图的区域永远一样，
        #: 重算只是白花 `createAlphaMask()` 的时间。
        #: ⚠ **不能直接拿 `QImage` 当字典键**（PySide6 里它不可哈希，实测
        #: `TypeError: unhashable type`）。所以键是"这张图的身份"，并且缓存**同时
        #: 持有图本身的强引用**（`_region_owner`）——否则图被回收、`id()` 被复用，
        #: 会拿另一张图的区域去设遮罩（"可点区域与看到的画面不一致"）。
        self._region_cache: "OrderedDict[tuple[int, int, int, int], QRegion | None]" = OrderedDict()
        #: 与 `_region_cache` 同键的**图本身**（强引用，见上面的说明）。
        self._region_owner: "OrderedDict[tuple[int, int, int, int], QImage]" = OrderedDict()
        #: 真的算过多少次区域（诊断 / 自检用：换帧命中缓存时它**不该涨**）。
        self.region_builds = 0
        #: **上一次夹取**用的那个可见矩形（下一轮并进 `_clamp_box`，见那里）。
        #: `None` = 还没夹过。
        self._clamp_box_cache: tuple[int, int, int, int] | None = None
        #: 动作包络（`所有动作首帧` 的可见块并集）与它属于哪个档位。
        #: 档位对不上就重算——换档之后可见块的**大小与位置都会变**。
        self._envelope_box: tuple[int, int, int, int] | None = None
        self._envelope_level: int = -1
        #: 双击手势正在进行中（`wave_once()` 置真，播完由 `_on_sequence_done` 清掉）。
        #: ⚠ **不能**拿 `animation.in_sequence` 代替：`sequence_done` 发出时
        #: 片段表已经被清空，那一刻问它永远是假（见 `_on_sequence_done`）。
        self._gesture_active = False
        self._quit_shortcuts: list[QShortcut] = []

        #: 镜像状态表（task06）。没给就自己造一个"永远不翻转"的
        #: ——`MirrorState` 需要一个 `ConfigStore`，自检里常常没有配置，
        #: 所以这里允许 `None` 并退化（`_mirror` 为 `None` 时恒不翻转）。
        self._mirror = mirror

        #: 配置 store（task08）：**松手时**把落脚点写进 `position`。
        #: `None` = 没有配置可写（自检里常见），此时只记 DEBUG 日志、不报错。
        self._config = config

        #: 音效播放器（task10）。`None` = 没有音效可播（自检里常见）——
        #: 此时"休息有声音""双击有 waving 音效"都**静默**，不影响任何其他行为。
        #: ⚠ 所有播放器调用都在 GUI 线程（`QMediaPlayer` 不是线程安全的，§4.9）。
        self._sounds = sounds

        #: `调整大小…` 的处理函数（task09）：`app.py` 装进来，负责开关控制栏。
        #: `None` = 本窗口没有控制栏（自检里常见）→ 菜单项**灰掉**，
        #: 而不是给用户一个点了没反应的项（§2.10：绝不显示假成功）。
        self._resize_handler: Callable[[], None] | None = None

        #: `设置…` 的处理函数（task11）：`app.py` 装进来，负责"打开/前置设置窗口"。
        #: 与 `_resize_handler` 同一种做法：**没装就灰掉**。
        self._settings_handler: Callable[[], None] | None = None

        #: `隐藏宠物` 的处理函数（task12）：`app.py` 装进来。
        self._visibility_handler: Callable[[], None] | None = None

        #: 现在有没有可用的呼出快捷键（task12）。`False` → `隐藏宠物` 灰掉。
        #: ⚠ 默认 `False` 是**安全的那一侧**：没有热键时藏起来叫不回来，
        #: 所以"不知道"必须按"没有"处理（`app.py` 注册完热键会立刻置真）。
        self._hotkey_available = False
        #: 当前是不是被用户藏起来了（**保留状态不销毁**，见 `hide_pet()`）。
        self._hidden = False

        #: **底部预留边距**（task09 的"缩放中按钮不动"机制）：窗口底边距屏幕底边的
        #: 逻辑单位数。`0` = 贴屏幕底边（缺省落点的样子）；`RESERVED` = 抬起来给
        #: 控制栏腾地方。**它只在"没被用户显式指定位置"时生效**（见 `_reserved`）。
        self._bottom_margin = 0
        #: **已经施加在权威位置上的**预留量。与 `_bottom_margin` 分开是必须的：
        #: 权威位置里已经含了上一次抬升，重算基准底边时得先把它还回去
        #: （否则每次调用都会再抬一次，见 `_reapply_bottom_margin`）。
        self._applied_margin = 0
        #: 预留机制**是否生效**。用户拖动过 / 命令行或配置给了位置 → `False`：
        #: 位置是用户自己的意思，程序不许按"贴屏幕底边"那套去抬它（task09 §4）。
        self._reserved = False

        #: 当前显示的帧（**已缩放、已按需镜像**，窗口尺寸就是它的尺寸）。
        self._frame_image = QImage()
        #: 当前动作名（**权威来源是 `self._animation.action`**，这里保留一份是为了
        #: 让"窗口显示的是哪个动作"在窗口自己身上问得到）。
        self._action = _pick_action(pet, DEFAULT_ACTION)
        #: 当前帧序号（**权威来源是 `self._animation.index`**）。
        self._frame_index = 0
        #: "手动坐下休息"（右键菜单点出来的休息）。
        #: ⚠ 它的**权威来源是 `self._idle` 状态机的 `manual` 开关**（task07 接线后）；
        #: 本字段只是"没有状态机时"（自检里 `idle_state=None`）的退化副本，
        #: 两者由 `manual_rest` 属性统一读出，不要把判断写在这个字段上。
        self._manual_rest = False

        #: 久置状态机（task07）。**没有配置/没有定时器时可以是 `None`**
        #: ——窗口本身不负责"什么时候 tick"，那由 `app.py` 的 `QTimer` 驱动；
        #: 这里只做两件事：把输入转成状态机的输入、按 `stage_changed` 切动作。
        self._idle: IdleStateMachine | None = idle_state
        if self._idle is not None:
            self._idle.stage_changed.connect(self._on_stage_changed)

        #: 渲染器（帧 → 目标尺寸 + 缓存）。缓存键含档位**与镜像**（task06 已补齐）。
        #:
        #: `antialias` 由 `app.py` 在**启动时读一次配置**传进来（task15）。
        #: ⚠ 生效方式是**重启生效**：本类**没有**"开关变了"的通道，
        #: 运行期状态与 task15 之前完全相同（用户拍板，见 task15 §1 与 §5）。
        self._renderer = FrameRenderer(
            pet, self._device_pixel_ratio(), antialias=antialias
        )
        #: 当前档位。`None` 表示还没定（构造时要么给 level、要么自适应）。
        self._level = 0

        #: 帧推进器（纯逻辑）+ 驱动它的定时器（本文件唯一的调度点）。
        self._animation = Animation(pet, self._action)
        self._driver = AnimationDriver(self._animation, self)

        # ---------------------------------------------------------- 形态（task17）
        #: 键盘形态的素材（`None` = 这个包没有可用的 KV → 菜单项灰掉）。
        self._keyview = keyview
        #: `退出KV` 的处理函数（`app.py` 装进来，负责写配置 + 切形态）。
        #: 与 `_resize_handler` 等同一个模式：**没装就灰掉**。
        self._form_handler: Callable[[str], None] | None = None
        #: 宠物形态的画源（**永远存在**，即使 KV 可用——切回来就是它）。
        self._pet_content = PetContent(pet, self._renderer, self._animation)
        #: KV 形态的画源（可缺）。构造放在**渲染器之后**：它要按档位预合成。
        #:
        #: `keyview_layout` 是**启动时读一次**的布局（task19）：空串 = 清单里
        #: 第一个（与 `FormController.resolve_layout()` 的兜底一致）。
        #: 运行期换布局走 `set_layout()`，不重建画源（帧缓存照样能用）。
        self._keyview_content = (
            KeyViewContent(keyview, layout=keyview_layout)
            if keyview is not None
            else None
        )
        #: **当前画源**。默认是宠物形态；`set_form()` 是唯一改它的地方。
        #: ⚠ `_content.cell` 是尺寸的真值（宠物 = `pet.cell`，KV = `keyview.cell`），
        #: 而档位仍然只由 `_apply_level()` 一处决定——两条别混。
        self._content: _Content = self._pet_content
        self._driver.frame_due.connect(self._on_frame_due)

        # ---------------------------------------------------------- 按键联动（task18）
        #: 按键监听器（`app.py` 装进来）。`None` = 没有监听（自检里常见）——
        #: 此时 `pressed` 只能由 `set_pressed()` 直接喂（K1 的入口，行为与 task17 相同）。
        #: ⚠ 本类**不创建**监听器：它的生命周期（随形态与可见性启停）是装配层的事，
        #: 窗口只管"收到信号之后做什么"。
        self._key_listener: QObject | None = None
        #: **合并复绘**的单次定时器（task18 4.3）。收到按键变化**不当场重绘**：
        #: 一次重绘（遮罩 + 半透明窗口）实测约 41 ms，连打会积压成一串没人看得清的中间态。
        #: 期间再来变化只**重置**定时器，不叠加——所以 1 秒内 N 次变化最多出 1/16 ms 次重绘。
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setTimerType(Qt.PreciseTimer)
        self._repaint_timer.setInterval(REPAINT_INTERVAL_MS)
        self._repaint_timer.timeout.connect(self._on_repaint_due)
        #: 真的重绘了几次（自检与诊断用："合并复绘"那条验收直接盯它）。
        self.repaint_count = 0
        #: 收到过几次按键变化信号（与 `repaint_count` 比出来的就是合并率）。
        self.pressed_signal_count = 0
        #: 片段序列播完 → 收尾（双击挥手播完回 `idle`，task10 §2 第 4 条）。
        self._animation.sequence_done.connect(self._on_sequence_done)
        #: 已经显示过的帧号（**用来跳过无变化的重绘**，见 `_on_frame_due`）。
        self._shown_index: int | None = None
        #: 已经显示过的镜像态（**与 `_shown_index` 同理**：镜像变了就要重绘）。
        self._shown_mirrored: bool | None = None

        if self._mirror is not None:
            # 镜像一变，帧与遮罩都要重取重算（本文件的唯一落点）。
            self._mirror.mirror_changed.connect(self._on_mirror_changed)

        self._setup_window()
        self._apply_level(self._resolve_initial_level(level))
        self._install_quit_shortcuts()

    # ------------------------------------------------------------ 装配

    def _device_pixel_ratio(self) -> float:
        """当前屏幕的 DPI 缩放因子（拿不到就当 1.0）。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
        return float(ratio) if ratio else 1.0

    def _screen_height(self) -> int:
        """当前屏幕的**逻辑**高度（拿不到 → 0，由 size 模块退回默认档位）。

        Qt 的 `geometry()` 给的就是逻辑值，直接交给 `size.level_for_screen()`；
        **不要**再乘一遍 `devicePixelRatio()`（乘了就是缩放两次，agent.md §4.9）。
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return 0
        return int(screen.geometry().height())

    def _screen_area(self) -> tuple[int, int, int, int]:
        """屏幕矩形的 `(x, y, w, h)`（逻辑单位）；拿不到 → 全 0。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return (0, 0, 0, 0)
        area = screen.geometry()
        return (area.x(), area.y(), area.width(), area.height())

    def _resolve_initial_level(self, level: int | None) -> int:
        """构造期的档位：给了就用（夹取），没给就**按屏幕自适应**。"""
        if level is None:
            return size.level_for_screen(self._screen_height(), self.pet.cell)
        return size.clamp_level(level)

    def _setup_window(self) -> None:
        # 无边框 + 始终置顶 + 不进任务栏（Qt.Tool）。
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        # D1：素材本身就是透明背景 PNG，运行时**没有任何色键步骤**。
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # ⚠ Qt 不为 Qt.Tool 窗口设置 WA_QuitOnClose，而 Qt.Tool **不计入**
        # "最后一个主窗口"，于是关掉窗口后事件循环不会退出（进程留在后台）。
        # 这里显式打开它，Esc / Ctrl+Q / --exit-after 才真能干净退出。
        self.setAttribute(Qt.WA_QuitOnClose, True)
        self.setWindowTitle(PROGRAM_NAME)

    def _install_quit_shortcuts(self) -> None:
        """Esc 与 Ctrl+Q 退出。窗口拿到焦点时生效。"""
        for sequence in ("Esc", "Ctrl+Q"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self.close)
            self._quit_shortcuts.append(shortcut)

    # ------------------------------------------------------------ 档位与尺寸

    @property
    def level(self) -> int:
        """当前尺寸档位（1..10）。"""
        return self._level

    @property
    def pixel_size(self) -> tuple[int, int]:
        """当前档位下的内容像素尺寸（= 窗口尺寸）。

        ⚠ **尺寸来源是 `self._content.cell`，不是 `self.pet.cell`**（task17 §4.1
        的第 1 处接缝）：宠物形态 = `pet.cell`，KV 形态 = `keyview.cell`。
        档位本身**不重置**——两个形态共用同一档（用户 2026 定案第 3 条）。
        """
        return size.pixel_size(self._content.cell, self._level)

    @property
    def size_level(self) -> int:
        """`level` 的别名（说"档位"时两种叫法都有人用，统一指同一个值）。"""
        return self._level

    @property
    def antialias(self) -> bool:
        """边缘抗锯齿开没开（task15）。

        **取渲染器上的那个值**，不是再读一遍配置：启动行要打印的是
        "真正作用到出图上的"状态，两者万一不一致时以渲染器为准
        （这正是"不要有第二份真值"的意思）。
        """
        return self._renderer.antialias

    def _apply_level(self, level: int) -> None:
        """**切档的唯一入口**：先把该档的帧渲染好，再改窗口尺寸、再重设遮罩。

        ## 顺序硬约束（`迁移清单.md` §4 第 5 条）

        1. **先取该档的帧**（`FrameRenderer.frame()`，会按需缩放 + 进缓存）；
        2. 再 `setFixedSize()` 改窗口尺寸；
        3. 最后 `update()` 重绘 + `apply_mask()` 重算区域。

        反过来的话，窗口先变小/变大而帧还没换，中间会露出**空白或裁边**。

        **首次调用**（构造期）时窗口还没有位置，只定尺寸、不动位置；
        后续切档一律走 `set_size_level()` 的锚点保护。
        """
        self._level = size.clamp_level(level)
        mirrored = self.mirrored

        # **按动画当前那一帧**重新渲染（不是 `self._frame_index` 快照）：
        # 动画正在播时帧号一直在动，切档必须跟上动画的当前帧，
        # 否则会出现"换了档但画面停在旧帧"。
        #
        # ⚠ task17 起画面来源是 **`self._content.image()`**（第 2 处接缝）：
        # 宠物形态走 `FrameRenderer`（与原来逐字节相同），
        # KV 形态走 `KeyViewRenderer.compose()`。
        image = self._content.image(self._level, mirrored)
        if not image.isNull():
            # 宠物形态那条路径：渲染器返回的是**共享的** `QPixmap`，
            # 转成 `QImage` 时 Qt 会做一次深拷贝，于是 `_frame_image`
            # 与缓存互不影响（后续要画/要算遮罩）。
            # KV 形态返回的本来就是新合成的 `QImage`，同样可以安全持有。
            self._frame_image = image
            self._frame_index = self._animation.index
            self._action = self._animation.render_action
            self._shown_index = self._animation.index
            self._shown_mirrored = mirrored

        width, height = self.pixel_size
        self.setFixedSize(width, height)
        # 换档 = 衍生数据失效（agent.md §4.7）：遮罩必须按**新尺寸的帧**重算。
        self.apply_mask(self._frame_image)
        self.update()

    def set_size_level(self, level: int) -> None:
        """换档，并保持**右下角锚点不动**（向"左上"方向长大）。

        锚点在切档**之前**从 `_base_pos` + 当前尺寸算出来，切完档再按新尺寸
        反推左上角，最后夹到屏幕内——**夹取发生在写进权威位置之前**
        （agent.md §4.5）。整个过程只经由 `move_to()` 写位置。

        **换档会写配置**（task09 §4）：档位写 `size_level`，位置写 `position`。
        两条都**只在真的换档时**发生——档位没变时上面那句 `return` 已经拦掉了，
        所以"`−`/`+` 点到边界"不会碰磁盘（§6 有一条验收专门盯这个）。
        """
        target = size.clamp_level(level)
        if target == self._level:
            return

        anchor = size.anchor_of(
            self._base_pos.x(), self._base_pos.y(), *self.pixel_size
        )
        self._apply_level(target)
        left, top = anchor.top_left_for(*self.pixel_size)
        self._move_clamped(left, top)
        # 换档会改左上角（右下角锚点不动），所以这也是"位置变了"的一处：
        # 与松手一样写一次配置（**不逐帧写**，task08 §4 硬规则）。
        # 用户拖动过之后位置就以他的为准，**绝不重新算右下角缺省落点**。
        self._persist_position(self.base_position)
        # 档位也写回去（重启后仍是这个档位，task09 §2 第 11 条）。
        self._persist_level()

    def _persist_level(self) -> None:
        """把当前档位写进配置 `size_level`（**只在换档时调一次**）。

        写失败按 §2.10 第二类处理：记 `ERROR` 并把系统原因原样带出来——
        换档是用户明确要求的操作，没记住就必须让他知道。
        `ConfigStore.set()` 自己**不抛异常**（返回 `False`），所以这里不会炸。

        没有 `ConfigStore` 时（自检里常见）只记 `DEBUG`：那种情况下
        "记不住档位"是测试的设定，不是产品故障。
        """
        if self._config is None:
            logger.debug("没有配置 store，跳过档位持久化：level=%d", self._level)
            return
        if not self._config.set("size_level", int(self._level)):
            reason = self._config.last_error("size_level") or "未知原因"
            logger.error("记住尺寸档位失败（第 %d 级）：%s", self._level, reason)

    def _move_clamped(self, left: int, top: int) -> None:
        """夹到屏幕内再写权威位置（越界值不进 `_base_pos`，agent.md §4.5）。

        ## 夹的是**可见人物**，不是透明画布（task21 UX-06）

        第一版把窗口（= 素材的透明画布，整块 `cell`）塞进屏幕，于是人物与
        屏幕边之间永远隔着画布四周的透明留白——拖到边上看着根本没贴到。
        现在按**当前画面里真正有像素的那块矩形**（`visible_bounds`）夹：
        允许透明留白跑去屏幕外，人物边缘可以真的贴住屏幕边。

        ⚠ **抓得回来**这条红线不能破：人物的可见块永远**整块**留在屏幕内
        （不是"留一角"）。所以哪怕可见块因为换帧/换镜像而变化，用户在任何
        时候都点得到人物。
        """
        inner_x, inner_y, inner_w, inner_h = self._clamp_box()
        # `clamp_position()` 的语义是"把 宽 × 高 这块夹进屏幕、返回左上角"。
        # 传可见块时先把它换算成"这块自己要落在哪"，再减回窗口偏移。
        x, y = geometry.clamp_position(
            left + inner_x, top + inner_y, inner_w, inner_h, self._screen_area()
        )
        self.move_to(x - inner_x, y - inner_y)
        # 记下这一次夹取用的那一块：下一次夹取会把它并进来（见 `_clamp_box`）。
        self._clamp_box_cache = (inner_x, inner_y, inner_w, inner_h)

    def _clamp_box(self) -> tuple[int, int, int, int]:
        """夹取时该用的"可见矩形" `(窗口内偏移x, 偏移y, 宽, 高)`。

        默认是整块画布 `(0, 0, 宽, 高)`；算得出可见块时取这三者的并集：

        1. **当前这一帧**的可见块；
        2. **本档位下所有动作首帧**的包络（`_action_bounds_envelope`）；
        3. **上一次夹取用的那一块**（`_clamp_box_cache`）。

        ## 为什么光看当前帧不够（task21 自检挖出来的两条真 bug）

        可见块是**逐帧/逐动作**变的——同一档位下 `idle` 是 `(46,41,39,56)`，
        `running_right` 是 `(40,41,39,57)`，`rest` 是 `(38,70,55,33)`（躺姿往左、往下伸）。
        只按当前帧夹的后果实测过两次：

        - **拖动中**切到 `running_*`，可见块当场出右下角；
        - **松手回** `idle`、或**切到** `rest`/`waiting`，同样出屏（`808 > 800`）。

        于是"人物永远点得到"这条红线被换帧打破。补了 ②（覆盖所有动作）与
        ③（覆盖"松手回上一个姿势"）之后，夹取只会**更保守**，任何时候都在屏内。

        ⚠ ③ 这个缓存**只在夹取时更新**（`_move_clamped`），不跟帧变化走——
        跟帧走就等于又变成"逐帧取并集"，窗口会被拖得来回挪。
        """
        bounds = self.visible_bounds
        if bounds is None:
            width, height = self.pixel_size
            return (0, 0, width, height)
        # 当前这一帧 + **本档位下所有动作首帧的包络** + 上一次夹取用的那一块。
        # 三者取并集：缺了包络，切到躺姿（`rest` 的可见块比 `idle` 更靠左/更低，
        # 实测 808 > 800）就会出屏；缺了上一次，松手回 `idle` 那一下也会出屏
        # （task21 自检两条都抓到过）。
        envelope = self._action_bounds_envelope()
        box = _union_box(bounds, envelope) if envelope is not None else bounds
        previous = self._clamp_box_cache
        if previous is None:
            return box
        return _union_box(previous, box)

    def _action_bounds_envelope(self) -> tuple[int, int, int, int] | None:
        """**本档位下所有动作首帧**的可见块并集（按档位缓存，换档自动作废）。

        ## 为什么用"每个动作的首帧"而不是"每一帧"

        首帧已经是每个动作里**最像"这个人站在哪儿"**的一帧，且算这 7 张图一共
        只要几十毫秒（只在换档后的第一次夹取时发生，不进逐帧路径）。全帧扫描
        （`weedy.waiting` 240 帧）会把换档拖成肉眼可见的停顿——而"可点区域"
        并不要求包络精确，多留一点点透明边距完全可以接受。

        算不出来的动作（缺图 / 坏图）**跳过**，不影响其它动作。
        """
        if self._envelope_level == self._level:
            return self._envelope_box
        box: tuple[int, int, int, int] | None = None
        level = self._level
        try:
            for action in self.pet.action_order:
                if self.pet.action(action) is None:
                    continue
                # ⚠ 渲染层给的是 `QPixmap`，而区域是**按 `QImage` 算的**（`_region_for`
                # 的键也基于图对象）——先转成图，否则 `region_for_frame()` 直接判空，
                # 整个包络静默变成 `None`（task21 自检抓到：夹取退回"只看当前帧"）。
                pixmap = self._renderer.frame(action, 0, level, False)
                if pixmap.isNull():
                    continue
                bounds = hit_test.visible_bounds(
                    hit_test.region_for_frame(
                        pixmap.toImage(), alpha_threshold=self._mask_threshold
                    )
                )
                if bounds is None:
                    continue
                box = bounds if box is None else _union_box(box, bounds)
        except Exception:  # pragma: no cover - 素材异常时退化成"当前这一帧"
            logger.debug("算动作包络失败，退化为当前帧", exc_info=True)
            box = None
        self._envelope_level = level
        self._envelope_box = box
        return box

    @property
    def visible_bounds(self) -> tuple[int, int, int, int] | None:
        """**当前画面**里真正有像素的那块矩形 `(x, y, w, h)`（窗口内逻辑坐标）。

        取当前帧区域的外接矩形（`hit_test.visible_bounds()`）——与遮罩同一个
        来源，所以"看得到的边缘"与"贴边用的边缘"永远是同一条线。
        算不出来（空帧 / 没有 app）→ `None`，调用方退回整块画布。
        """
        if self._frame_image.isNull():
            return None
        return hit_test.visible_bounds(self._region_for(self._frame_image))

    # ------------------------------------------------------------ 显示

    @property
    def action(self) -> str:
        """当前显示的动作名（**与动画一致**，回退之后的实际值）。"""
        return self._animation.action

    @property
    def frame_index(self) -> int:
        """当前显示的帧序号（与动画一致）。"""
        return self._frame_index

    @property
    def animation(self) -> Animation:
        """帧推进器（只读；**切换动作请用 `play_action()`**，它会同步驱动与画面）。"""
        return self._animation

    @property
    def frame_image(self) -> QImage:
        """当前显示的帧（**已缩放、已按需镜像**；只读，空图 = 没有图可画）。"""
        return self._frame_image

    # ------------------------------------------------------------ 形态（task17）

    @property
    def form(self) -> str:
        """当前形态（`""` = 宠物，`"keyview"` = 键盘）。**取画源上的值**。

        "取画源"而不是"再记一个字符串"：多一个字段就多一处会对不上的状态
        （agent.md §2.13 "不另设布尔开关"是同一条理由）。
        """
        return self._content.form

    @property
    def content(self) -> "_Content":
        """当前画源（只读；**切形态请用 `set_form()`**）。"""
        return self._content

    @property
    def keyview(self) -> KeyViewSpec | None:
        """这个包的键盘形态素材；`None` = 没有可用的 KV（菜单项灰掉）。"""
        return self._keyview

    @property
    def keyview_renderer(self) -> "keyview_render.KeyViewRenderer | None":
        """KV 合成器（只读；**改按键状态请用 `set_pressed()`**）。"""
        content = self._keyview_content
        return content.renderer if content is not None else None

    @property
    def keyview_available(self) -> bool:
        """现在**能不能**切到键盘形态（能切过去吗）。

        判据两条，**都要成立**：

        1. 这个包有可用的 KV 素材（有至少一个可用布局）；
        2. 装了切形态的处理器（`set_form_handler()`）——与 `调整大小…` 等项
           完全同一个模式：**没装处理器就是灰的**，不给用户一个点了没反应的项。

        ⚠ 它是"**能不能切**"，不是"**现在是不是**"：判断当前形态请读 `form`。
        """
        return self._keyview_content is not None and self._form_handler is not None

    def set_form_handler(self, handler: Callable[[str], None] | None) -> None:
        """装上 `开启KV` / `退出KV` 的处理函数（`app.py` 用）。

        `None` 卸下 → 菜单项重新灰掉。处理器收的是**目标形态名**，
        于是"开启"与"退出"走同一个函数（菜单项本来就只有一个 id）。
        """
        self._form_handler = handler

    def set_form(self, form: str) -> bool:
        """切形态；返回**是不是真的切了**（已经是这个形态 → `False`）。

        ## 三件事，顺序有意义

        1. **换画源**（`_content`）；
        2. **进 KV 停表、回宠物接着播**：`set_form()` **只动定时器，不动动画状态**
           ——`Animation` 的帧号从未被碰过，所以"从原来那一帧接着播"是天然的。
           ⚠ "还在播吗"仍然只由 `timer.isActive()` 表达（§2.13），
           **不许**另设布尔开关；
        3. **重画 + 重算遮罩**：走 `_apply_level()`（它会按新画源重新取图、
           重设窗口尺寸、重算 `QRegion`）。

        ⚠ **档位与位置都不重置**（用户 2026 定案第 3 条）：
        `_apply_level()` 收的是**当前**档位，窗口位置一个字都没动。
        两个形态的 `cell` 不同（540×420 vs 500×500）会让像素尺寸变，
        但**锚点是右下角**，所以脚底与右边不动（§2.12）——正是控制栏不动的前提。

        ⚠ 切不过去（KV 不可用）时**什么都不做**并返回 `False`：
        菜单项本来就是灰的，这里再兜一层，绝不显示假成功。
        """
        if form == self._content.form:
            return False
        if form == FORM_PET:
            target: _Content = self._pet_content
        elif form == FORM_KEYVIEW:
            if self._keyview_content is None:
                logger.warning("这个素材包没有可用的键盘形态，拒绝切换")
                return False
            target = self._keyview_content
        else:  # pragma: no cover - 形态名只有那两个，真到了也只是拒绝
            logger.warning("未知形态 %r，拒绝切换", form)
            return False

        # ⚠ **锚点必须在换画源之前取**（task17 实测踩到的第一号坑）：
        # `anchor_of()` 要的是**旧形态**的右下角，而 `self.pixel_size` 读的是
        # `self._content.cell` —— 先换画源再取锚点，拿到的是**新尺寸**算出的
        # "旧左上角 + 新尺寸"，于是 `top_left_for()` 反推出来的还是原来的左上角
        # （实测：切过去位置一动不动，锚点漂了 30~60 px）。
        # 真平台自检当场抓到这一幕（宠物(775,675) → KV(805,615)）。
        anchor = size.anchor_of(
            self._base_pos.x(), self._base_pos.y(), *self.pixel_size
        )
        self._content = target
        if target.form == FORM_KEYVIEW:
            # 进 KV：停表（KV 没有帧推进这回事），但**不动动画状态**。
            self._driver.stop()
            # 画面状态（`_shown_index` 等）全部作废：两个形态的画面来源不同，
            # 留着旧值会让下一次 `_on_frame_due` 误判"没变、不用重绘"。
            self._shown_index = None
            self._shown_mirrored = None
        else:
            self._shown_index = None
            self._shown_mirrored = None

        # 重取图 + 改窗口尺寸 + 重算遮罩，**一处都不另写**。
        #
        # ## ⚠ 必须做锚点保护（task17 §6：「切形态后右下角锚点漂移 = 0」）
        #
        # 两个形态的 `cell` 不同（`weedy` 500×500 vs KV 540×420），所以切形态**必然**
        # 改变窗口像素尺寸。`_apply_level()` 只改尺寸**不动位置** —— 那是
        # "切档"的语义（位置由 `set_size_level()` 单独做锚点保护）。
        # 真平台自检当场抓到：不做保护的话左上角不动、**右下角会漂**
        # （实测 宠物(775,675) → KV(805,615)）。
        #
        # 所以这里照抄 `set_size_level()` 那三步：**切之前取锚点 → 切完反推左上角
        # → 夹取后写权威位置**。夹取在 `_move_clamped()` 里、**早于**写权威位置
        # （§4.5）。「右下角锚点不动」是 §2.12 的硬约定，形态之间同样成立。
        # （锚点已在上面、**换画源之前**取好了，见那儿的说明。）
        self._apply_level(self._level)
        left, top = anchor.top_left_for(*self.pixel_size)
        self._move_clamped(left, top)
        if target.form == FORM_PET:
            # 回宠物：从**原来那一帧**接着播（动画状态没被动过，§2.13）。
            self._driver.start()
        logger.info(
            "形态已切到 %s（档位 %d 保留，尺寸 %dx%d）",
            target.form or "宠物",
            self._level,
            *self.pixel_size,
        )
        return True

    def set_layout(self, name: str) -> bool:
        """换键盘布局（task19 §4.5）；返回**画面真的变了吗**。

        ## 为什么这里**不**做锚点保护（与 `set_form()` 正好相反）

        `cell` 是**所有布局共用**的一份（§2.23），所以切布局**不改窗口尺寸**
        → 锚点、位置、控制栏预留**一样都不用动**。这正是"切布局便宜、
        切形态贵"的那条区别。

        ⚠ 不在 KV 形态时**也照常换**（只记状态、不重绘）：与 `set_pressed()`
        同一条理由——用户可能先在宠物形态下把布局配好，再切过去；
        那时 `set_form()` 的 `_apply_level()` 会按新布局出一张图。
        """
        content = self._keyview_content
        if content is None:
            return False
        changed = content.set_layout(name)
        if not changed:
            return False
        if self._content.form == FORM_KEYVIEW:
            # 重绘一次。⚠ **返回值不是判据**：`_repaint_current()` 返回的是
            # "图变了没有"，而"布局真的换了"由 `content.set_layout()` 回答
            # ——当前没按任何键时两套布局的画面本来就可能一模一样。
            self._repaint_current()
        return True

    @property
    def keyview_layout(self) -> str:
        """当前键盘布局名（`""` = 没有可用布局）。**只读**。"""
        content = self._keyview_content
        return content.layout if content is not None else ""

    @property
    def keyview_active_keys(self) -> frozenset[str]:
        """当前布局里有效的键 id（诊断与自检用）。**只读**。"""
        content = self._keyview_content
        return content.active_keys if content is not None else frozenset()

    def toggle_form(self) -> bool:
        """在两个形态之间切一次（菜单项的动作）；返回是不是真的切了。

        ⚠ **不写配置**：持久化由 `app.py` 装的处理器负责。
        本方法只做"切过去"这一件事，于是自检可以不起 `app.py` 就验它。
        """
        target = FORM_PET if self._content.form == FORM_KEYVIEW else FORM_KEYVIEW
        return self.set_form(target)

    def set_key_listener(self, listener: "QObject | None") -> None:
        """装上按键监听器（task18 的装配接缝，`app.py` 用）。

        `None` 卸下。装上之后：`keys_changed` -> **合并复绘** -> `set_pressed()`。

        ⚠ **本类不启停监听器**：什么时候该轮询（形态 == KV 且窗口可见且没被挂起）
        是装配层的判断（task18 4.4），窗口这里只回答"收到变化之后做什么"。
        """
        if self._key_listener is not None:
            try:
                self._key_listener.keys_changed.disconnect(self._on_keys_changed)
            except (RuntimeError, TypeError):  # pragma: no cover - 已经断开/对象没了
                pass
        self._key_listener = listener
        if listener is not None:
            listener.keys_changed.connect(self._on_keys_changed)

    @property
    def key_listener(self) -> "QObject | None":
        """当前装着按键监听器吗（只读；改它请用 `set_key_listener()`）。"""
        return self._key_listener

    @property
    def repaint_pending(self) -> bool:
        """合并复绘定时器还在等吗（诊断与自检用）。"""
        return self._repaint_timer.isActive()

    def _on_keys_changed(self, pressed: tuple) -> None:
        """收到"按下的键变了"：**记下状态、起一次节流定时器**，不当场重绘。

        ## 为什么不当场重绘（task18 4.3）

        一次重绘（重算 `QRegion` + 半透明窗口合成）实测约 **41 ms**（agent.md 4.9）。
        在信号处理里直接 `update()` / `apply_mask()` 会积压：高速连打时
        事件队列里排的是一串**没人看得清的中间态**，越追越晚。

        ## 为什么状态**当场**写进 content

        图像要的是"最终那一份 `pressed`"，不是"每一次变化的回放"。
        所以这里立刻把状态写进画源（不重绘），到点时画的就已经是**最新**的状态
        ——中间那些变化天然被合并掉了，这正是"合并"的语义。

        ## ⚠ 是**节流**不是防抖（task21 UX-11）

        第一版每次都 `start()`（= 重置计时）：只要用户不停地敲键，每次变化都把
        重绘再推迟 16 ms，**持续输入期间画面一直不刷新**（一停才画）。
        现在**定时器已经在等就不再重置**：上一拍起算 16 ms 后必画一次，
        画的是那一刻的最新状态。判据：`ts = time.perf_counter()` →
        `repaint_count` 每 `REPAINT_INTERVAL_MS` 至少长一次，与信号密度无关。
        """
        self.pressed_signal_count += 1
        content = self._keyview_content
        if content is not None:
            # ⚠ 与 `set_pressed()` 同一条过滤（布局之外的键不进来）：
            # 监听器那边虽然也按布局滤过一遍（`KeyviewController.rebind`），
            # 但"画面上的键永远在当前布局里"这条保证只该有**一处**实现。
            content.pressed = tuple(
                key
                for key in keyview_render.pressed_tuple(pressed)
                if key in content.active_keys
            )
        # **节流**：已经在等就不要再 `start()`（那会把它重置成"最后一次变化之后
        # 再等 16 ms"，持续输入就永远画不出来）。没在等才起一拍。
        if not self._repaint_timer.isActive():
            self._repaint_timer.start()

    def _on_repaint_due(self) -> None:
        """合并窗口到点：**现在**才重绘 + 重算遮罩。

        不在 KV 形态时直接返回：那种情况下监听器本来就该是停的（收到信号是装配层
        的时序问题），这里再兜一层，绝不白画一张没人看的图。
        """
        if self._content.form != FORM_KEYVIEW:
            return
        self.repaint_count += 1
        self._repaint_current()
    def set_pressed(self, pressed: object = ()) -> bool:
        """设置键盘形态的按键状态（**K1 的对外入口**）；返回画面变没变。

        `pressed` 是**已按下的键 id，按按下顺序**（最早在前）。
        "物理键 → 这个元组"是 **task18** 的事，本方法只接受结果。

        ⚠ **按当前布局过滤**（task19）：布局之外的键**不进来**。
        过滤放在这里（而不是只放在 `image()` 里）是为了让 `pressed`
        这个**对外可读的状态**与画面一致——否则 4 键布局下按一个 8 键
        布局才有的键，画面不亮但 `pressed` 里躺着它，两份状态对不上。

        不在 KV 形态时**只记住状态、不重绘**：切过去时 `_apply_level()`
        会按新状态出一张图，不需要在后台白画一张没人看的图。
        """
        content = self._keyview_content
        if content is None:
            return False
        state = tuple(
            key
            for key in keyview_render.pressed_tuple(pressed)
            if key in content.active_keys
        )
        if state == content.pressed:
            return False
        content.pressed = state
        if self._content.form != FORM_KEYVIEW:
            return False
        return self._repaint_current()

    @property
    def pressed(self) -> tuple[str, ...]:
        """当前按下的键 id（**只读**；改它请用 `set_pressed()`）。"""
        content = self._keyview_content
        return content.pressed if content is not None else ()

    # ------------------------------------------------------------ 镜像

    @property
    def mirror(self) -> MirrorState | None:
        """镜像状态表（只读；没有配置时是 `None`，此时恒不翻转）。"""
        return self._mirror

    @property
    def mirrored(self) -> bool:
        """当前动作**此刻**该不该镜像。

        判据全在 `MirrorState.is_on()` 一处（适用范围外恒为 `False`），
        本类不自己判"这个动作能不能镜像"——那是 `mirror.can_mirror()` 的事。
        """
        if self._mirror is None:
            return False
        # ⚠ 键盘形态**永不**镜像（第 3 处接缝的语义部分，见 `KeyViewContent`）：
        # 镜像只作用于 idle / waiting / rest（agent.md §0），
        # KV 没有"状态"这回事，翻转它没有任何意义。
        if self._content.form == FORM_KEYVIEW:
            return False
        return self._mirror.is_on(self._animation.render_action)

    def _on_mirror_changed(self, action: str, on: bool) -> None:
        """镜像变了：**帧与遮罩一起重取重算**（agent.md §4.7 的唯一落点）。

        只对**当前正在显示的动作**起作用：用户翻的是别的状态时，
        当前画面不该被改动（"按状态记忆"的意思就是各管各的）。

        重取帧走 `_repaint_current()`，它会把镜像并进缓存键与遮罩。
        """
        if action != self._animation.render_action:
            # 翻的是别的状态：只更新记忆，画面不动（切回该状态时自然生效）。
            return
        self._repaint_current()

    def _repaint_current(self) -> bool:
        """按**当前形态 / 当前画面状态 / 当前档位**重取图并重设遮罩。

        两条路径：

        - **宠物形态**：`show_action_frame()` 重新问渲染层要图
          （键含镜像，所以拿到的确实是翻过的那张）；
        - **键盘形态**：直接问 `_content.image()`——那里会带上 `pressed`
          （task18 改 `pressed` 之后调的就是这一条）。

        返回**是否真的换上了新图**（与 `show_action_frame()` 同一个语义）。
        """
        if self._content.form == FORM_KEYVIEW:
            image = self._content.image(self._level, False)
            if image.isNull():
                return False
            self._frame_image = image
            self._shown_mirrored = False
            # 换画面 = 衍生数据必须失效重算（agent.md §4.7）：遮罩是上一张图的
            # 形状就会出现"画面变了但可点区域没变"。
            self.apply_mask(self._frame_image)
            self.update()
            return True
        return self.show_action_frame(self._animation.render_action, self._animation.index)

    def show_action_frame(self, action: str, index: int) -> bool:
        """显示 ``action`` 的第 ``index`` 帧（按当前档位缩放 + **按需镜像**），并重设窗口遮罩。

        返回**是否真的换上了新帧**。取不到图（动作不存在 / 帧号越界 /
        文件读不出来）时**保留原来的画面**并返回 ``False``——绝不把已经
        画好的东西擦掉，也绝不抛异常（agent.md §2.8 的降级约定）。

        **镜像在这里生效**：翻转的是渲染层给出的图，`apply_mask()` 收的也是
        这张**已经翻过**的图，于是"能点到的范围"与"看到的画面"永远一致
        （task06 §4："镜像与掩码"）。

        **不改窗口尺寸**：尺寸只由档位决定（`set_size_level()`）。
        帧的实际像素尺寸与 `cell` 不一致时（只是个警告，§2.5），
        渲染层按**真实图**的尺寸缩放，所以窗口尺寸仍以 `cell` 的档位换算为准。
        """
        if self._content.form == FORM_KEYVIEW:
            # 键盘形态没有"第几帧"这回事：画面由 `pressed` 与档位决定。
            # 走 `_repaint_current()` 而不是这里（`_on_frame_due` 在 KV 形态下
            # 永远不会被触发——切换形态时表已经停了，见 `set_form()`）。
            # ⚠ **不许带尾逗号**：`return x,` 返回的是**元组** `(x,)`，恒为真，
            # "没换上图"会被报成 True（task21 UX-10）。
            return bool(self._repaint_current())

        mirrored = self.mirrored
        image = self._content.image(self._level, mirrored)
        if image.isNull():
            return False

        self._frame_index = int(index)
        self._action = action
        self._frame_image = image
        self._shown_mirrored = mirrored
        # 换帧之后**衍生数据必须失效重算**（agent.md §4.7）：遮罩是上一帧的形状
        # 就会出现"人物变了但可点区域没变"。
        self.apply_mask(self._frame_image)
        self.update()
        return True

    # ------------------------------------------------------------ 动画

    def play_action(
        self,
        action: str,
        *,
        fps_override: float | None = None,
        auto_start: bool = True,
    ) -> None:
        """把窗口切到 ``action`` 并开始播放（**换动作的唯一入口**）。

        动作不存在 → `animation.UnknownActionError`（**带可用动作列表**），
        由调用方（`app.py`）决定怎么报错——本函数不吞异常，
        因为"用户敲了一个不存在的动作"必须如实说出来（task05 §2 第 4 条）。

        ``fps_override`` 只用于调试，**不写回配置**（task05 §4）。
        ``auto_start=False`` 时只摆好第一帧不排定时器（自检用）。
        """
        self._animation.set_action(action)
        if fps_override is not None:
            self._animation.set_fps_override(fps_override)

        # 换动作 = 手势被打断：标志清掉，免得下一个一次性动作播完时
        # 被当成"手势播完"而切回 idle（`_on_sequence_done` 的判据）。
        self._gesture_active = False
        self._action = self._animation.action
        self._shown_index = None
        self._shown_mirrored = None
        # 先把第一帧放上去，再开表：开表后 `frame_due` 会立刻来一次，
        # 那一次会因为"帧号没变"而跳过重绘（`_on_frame_due`）。
        self.show_action_frame(self._animation.render_action, self._animation.index)
        if auto_start:
            self._driver.start()
        else:
            self._driver.stop()

    def stop_animation(self) -> None:
        """停表但**保留当前画面**（定格是另一回事，见 `hold_frame()`）。"""
        self._driver.stop()

    @property
    def animation_driver_running(self) -> bool:
        """动画定时器还在不在转（诊断与自检用）。

        task12 用它验两件事：**隐藏期间表已停**（不白烧 CPU）、
        **呼出后表又转起来**（帧回调恢复）。判据就是 `timer.isActive()`，
        没有第二个布尔开关（agent.md §2.13："还在播吗就是 `isActive()`"）。
        """
        return self._driver.running

    @property
    def animation_driver(self) -> AnimationDriver:
        """帧推进器（只读；**改播放状态请用 `play_action()` / `hold_frame()`**）。

        与 `animation` 属性同一个用意：自检要能挂 `frame_due` 观察真实帧节奏
        （task15 的真平台节奏就是靠它量的——探针定时器的间隔不代表帧间隔）。
        """
        return self._driver

    def hold_frame(self, index: int) -> None:
        """定格在第 ``index`` 帧；**画面完全不动，也不再排下一次换帧**。

        越界帧号 → `animation.FrameIndexError`（**报错而不是画空白**）。
        """
        self._animation.hold(index)
        self._driver.stop()
        self.show_action_frame(self._animation.render_action, self._animation.index)

    def release_hold(self) -> None:
        """解除定格并继续播放。"""
        self._animation.release_hold()
        self._driver.start()

    def _on_frame_due(self) -> None:
        """`AnimationDriver` 说"该显示这一帧了"。

        **帧号没变就不重绘**：单帧动作与"区间只有一帧"的片段会让推进原地不动，
        这时每 33 ms 重画同一张图纯属浪费（还白白偏高 CPU）。
        遮罩同理——同一帧的形状没必要重算（agent.md §2.8"同动作同档位内复用"）。

        **镜像也算"变没变"**：换镜像走的是 `_repaint_current()`（信号驱动），
        正常不需要在这里兜；但把镜像并进判据只有好处——万一将来有人
        不动信号直接改了镜像表，画面也不会停在旧的那一面。
        """
        index = self._animation.index
        render_action = self._animation.render_action
        mirrored = self.mirrored
        if (
            index == self._shown_index
            and render_action == self._action
            and mirrored == self._shown_mirrored
        ):
            return
        if self.show_action_frame(render_action, index):
            self._shown_index = index
        else:
            # 这一帧读不出来（文件坏了 / 被删了）：**保留上一张画面**并停表，
            # 免得每 33 ms 重试一次同样的读盘失败（§2.10 第三类：退化 + 记日志）。
            self._shown_index = None
            self._driver.stop()

    # ------------------------------------------------------------ 位置

    @property
    def base_position(self) -> QPoint:
        """权威位置（只读）。调用方**不要**用 ``pos()`` 代替它。"""
        return QPoint(self._base_pos)

    @property
    def user_positioned(self) -> bool:
        """位置是不是**用户自己定的**（拖动过）。

        task08 §4 硬规则："用户拖动过之后，缩放**不得弹回右下角缺省落点**"。
        判据就是这一个布尔——`app.py` 启动时按"命令行 / 配置 / 缺省"决定位置，
        而运行期的切档只做锚点保护，绝不重新算落点。
        """
        return self._user_positioned

    @property
    def dragging(self) -> bool:
        """当前是不是正在拖动（"拖动途中双击被忽略"看的就是它）。"""
        return self.drag_controller.active

    def move_to(self, x: int, y: int) -> None:
        """把窗口移到屏幕逻辑坐标 ``(x, y)``，并更新权威位置。

        **本函数不夹取**：它接受调用方算好的最终位置（`_move_clamped()` 已经夹过）。
        夹取属"摆放策略"而不是"搬运"——放在这里会让"拖到边缘被夹住"与
        "换档后重算锚点"两种意图混在一起，说不清是谁夹的。

        位置变了就发 `position_changed(pos)`（agent.md §2.2 的信号表）：
        拖动结束 / 夹取后需要一次**完整的位置校正通知**（agent.md §4.6，
        处理"拖到边缘被夹住、位置没变但仍需同步"这类情况）。
        """
        pos = QPoint(int(x), int(y))
        changed = pos != self._base_pos
        self._base_pos = pos
        self.move(pos)
        if changed:
            self.position_changed.emit(QPoint(pos))

    def showing_at_default_position(self) -> None:
        """贴屏幕**右下角**缺省落点（右下各留 ``DEFAULT_MARGIN`` 逻辑单位）。

        落点算法在 `geometry.default_position()`（**唯一实现**）；
        用**整块屏幕**的 ``geometry()`` 而不是排除任务栏的 ``availableGeometry()``，
        因为"边距 24 逻辑单位"是从**屏幕边缘**量起的（agent.md §4.10）。
        """
        width, height = self.pixel_size
        x, y = geometry.default_position(width, height, self._screen_area())
        self.move_to(x, y)

    # ------------------------------------------------------------ 底部预留（task09）

    def set_resize_handler(self, handler: Callable[[], None] | None) -> None:
        """装上 `调整大小…` 的处理函数（`app.py` 用；`None` 卸下 → 菜单项重新灰掉）。"""
        self._resize_handler = handler

    def set_settings_handler(self, handler: Callable[[], None] | None) -> None:
        """装上 `设置…` 的处理函数（task11；`None` 卸下 → 菜单项重新灰掉）。

        与 `set_resize_handler()` 同一个模式：**菜单项的可用性 = 装没装处理器**。
        `app.py` 用闭包把"打开/前置设置窗口 + 重置静置计时"接进来。
        """
        self._settings_handler = handler

    # ------------------------------------------------------------ 隐藏 / 呼出（task12）

    def set_visibility_handler(self, handler: Callable[[], None] | None) -> None:
        """装上 `隐藏宠物` 的处理函数（task12）。`None` → 菜单项**灰掉**。

        与 `调整大小…` / `设置…` 完全同一个模式。⚠ 这里的"灰掉"还有一个
        **更硬的理由**：一个可用的呼出快捷键都没有时，隐藏出去就再也叫不回来，
        所以那种情况下**必须**禁用（task12 §6），不能只靠处理器在不在。
        """
        self._visibility_handler = handler

    def set_hotkey_available(self, available: bool) -> None:
        """告诉窗口"现在有没有可用的呼出快捷键"（决定 `隐藏宠物` 能不能点）。

        `False` → 菜单项禁用 + 一句提示。**判据是"真有热键"，不是"装了处理器"**：
        处理器永远装着，但候选链全军覆没时藏起来就是灾难（task12 §4）。
        """
        self._hotkey_available = bool(available)

    @property
    def hidden_by_user(self) -> bool:
        """当前是不是"被用户藏起来了"（诊断与自检用）。"""
        return self._hidden

    def hide_pet(self) -> bool:
        """隐藏宠物（**保留状态不销毁**）；返回是否真的藏起来了。

        ## 三条约定（task12 §4）

        1. **保留状态不销毁**：只 `hide()`，窗口、帧缓存、档位、位置**全部留着**，
           呼出时是瞬时的（省掉重新解码几十张 PNG）。验收里"隐藏→呼出 20 次
           不变慢"盯的就是这条。
        2. **没有可用热键就拒绝隐藏**（返回 `False`）：藏出去叫不回来比什么都糟。
        3. **只停表，不拆状态**：动画定时器停掉（隐藏期间没人看，白烧 CPU），
           但 `Animation` 的帧号保留——呼出时从**原来那一帧**接着播。
        """
        if self._hidden:
            return True  # 幂等：已经藏着了
        if not self._hotkey_available:
            # 绝不显示假成功：返回 False，调用方据此提示"没有可用的呼出快捷键"。
            logger.warning("拒绝隐藏宠物：没有可用的呼出快捷键（藏起来就叫不回来了）")
            return False
        self._hidden = True
        self._driver.stop()
        self.hide()
        self.visibility_changed.emit(False)
        return True

    def show_pet(self) -> bool:
        """呼出宠物：恢复可见 + **重新声明置顶**；返回是否真的显示了。

        ## 为什么要"重新声明置顶"

        `WindowStaysOnTopHint` 是**创建窗口时**交给系统的属性，窗口在隐藏期间
        可能被系统换掉底层窗口（任务栏重建、桌面切换、别的程序抢最上层），
        只 `show()` 有几率回来时**不在最上层**。这里用
        `setWindowFlag(..., True)` 重新声明一次，再 `raise_()` 补一下。

        ⚠ 改窗口标志会让系统**重建**底层窗口（Qt 会 `hide()` 再显示），
        所以顺序必须是"先改标志、再 `show()`"——反了就成了"显示完立刻被重建藏掉"。
        """
        if not self._hidden:
            # 没藏着也把置顶补一次：这条路径可能被别的入口调用（诊断用）。
            self.raise_()
            return self.isVisible()
        self._hidden = False
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.show()
        self.raise_()
        # 呼出后从**原来那一帧**接着播（动画状态没被动过）。
        self._driver.start()
        self.visibility_changed.emit(True)
        return True

    def set_reserved(self, on: bool) -> None:
        """打开 / 关闭"底部预留机制"（task09 §4："用户显式指定位置时不生效"）。

        `app.py` 只在**走缺省落点**时打开它；命令行 `--position`、配置里的
        上次落脚点、以及用户拖动过，都会把它关掉——那三种情况下位置是
        用户自己的意思，程序不许按"贴屏幕底边"那套去抬它。
        """
        self._reserved = bool(on)
        if not self._reserved:
            # 关掉机制时把已经抬起来的宠物**放回去**（边距归零）。
            self._reset_bottom_margin()

    @property
    def bottom_margin(self) -> int:
        """当前底部预留边距（逻辑单位）；`0` = 贴屏幕底边。"""
        return self._bottom_margin

    @property
    def reserved(self) -> bool:
        """底部预留机制是否生效。

        判据是"位置**不是**用户自己的意思"：`app.py` 走缺省落点时置真，
        走命令行 `--position` / 配置里的位置 / 用户拖动过时置假
        （task09 §4："用户显式指定位置时，底部边距机制**不生效**"）。
        """
        return self._reserved

    def reserve_bottom(self, margin: int) -> None:
        """打开 / 收起**底部预留**：把宠物整体上移（或放回）指定的逻辑单位数。

        ## 这是"缩放中按钮不动"的实现本体

        控制栏贴的是宠物的**底边**。切档时宠物按**右下角锚点不动**长大
        （§2.12：脚底与右边不动），所以只要**底边固定**，控制栏就一动不动。
        打开控制栏时把底边抬起来给控制栏腾地方，此后切档不会再改这个底边——
        用户从第 1 级连点到第 10 级，**鼠标不用移动**就能一直点到 `+`
        （task09 §6 最关键的一条）。

        ## 为什么不重算缺省落点

        `showing_at_default_position()` 算的是"贴屏幕右下角的**左上角**"，
        直接调它在切档后会把整块重新贴一遍右边缘，底边照样会动
        （第 10 级的 1000 px 高窗口在 1440 的屏幕上根本放不下），
        所以这里**只改纵向**：右边与底边都按锚点保持。

        ``margin`` 由调用方给（`app.py` 传 `control_bar.RESERVED`）——
        **真值只允许在 `control_bar.py` 定义一处**（task09 §4），
        本模块不写第二份常量，免得两处迟早对不上。

        用户显式指定过位置（`reserved` 为假）时**什么都不做**：位置由用户决定。
        """
        margin = max(0, int(margin))
        if not self._reserved:
            return
        if margin == self._bottom_margin:
            return
        self._bottom_margin = margin
        self._reapply_bottom_margin()

    def _reapply_bottom_margin(self) -> None:
        """按当前**底边**重算左上角（只动纵向，右边与底边都不变）。

        ## 基准底边必须"先减掉已经抬起来的那部分"

        权威位置里**已经含**了当前的预留量，所以"基准底边"是
        `当前位置的底边 + 旧的边距`（把那一次抬升还回去）。
        直接拿当前位置当基准，每次调用都会再抬一次——
        第 2 次就把宠物抬到屏幕外了（实测：776 → 724 → 672 …）。
        这条**必须幂等**：打开控制栏与切档都会调它。

        **不用 `showing_at_default_position()`**：那个算的是"贴屏幕右下角的
        左上角"，会把整块重新贴一遍右边缘；而这里只按**纵向**调整，
        右边与底边都保持（用户拖过的位置也不会被拽回缺省落点）。
        """
        width, height = self.pixel_size
        area = self._screen_area()
        base_bottom = self._base_pos.y() + height + self._applied_margin
        target_top = base_bottom - self._bottom_margin - height
        x, y = size.clamp_position(
            self._base_pos.x(), target_top, width, height, area
        )
        self._applied_margin = self._bottom_margin
        self.move_to(x, y)

    def _reset_bottom_margin(self) -> None:
        """把已经抬起来的宠物放回去（边距归零），并清掉"已施加"的记账。"""
        if self._bottom_margin == 0 and self._applied_margin == 0:
            return
        self._bottom_margin = 0
        self._reapply_bottom_margin()

    def show_at(self, x: int, y: int, *, user_positioned: bool = False) -> None:
        """把窗口摆到 ``(x, y)``，**夹取后**再写权威位置（agent.md §4.5）。

        `app.py` 决定"命令行 / 上次保存的 / 缺省"三条来源后统一走这里。
        ``user_positioned=True`` 表示"这个位置是用户自己的意思"
        （命令行给了 `--position`，或配置里有上次落脚点），
        于是后续缩放不会把它弹回缺省落点（task08 §4 硬规则）。
        """
        self._move_clamped(int(x), int(y))
        if user_positioned:
            self._user_positioned = True
            # 位置是用户自己的意思 → 底部预留机制不生效（task09 §4）。
            self.set_reserved(False)

    def drag_by(self, dx: int, dy: int) -> None:
        """按**按下时记下的基准位置 + 位移**摆放窗口（**夹取后**才写权威位置）。

        ``dx`` / ``dy`` 是相对**按下点**的累计位移
        （``当前全局坐标 - 按下时的全局坐标``），不是相邻两次移动的增量。
        基准位置是自己记的，因此**不回读窗口实时坐标**（agent.md §4.6）。

        夹取发生在这里、且**早于** `move_to()` 写权威位置——这就是
        §4.5"越界值不能存进权威位置"在拖动路径上的落点。
        """
        state = self.drag_controller.state
        self._move_clamped(
            state.base_pos.x() + int(dx),
            state.base_pos.y() + int(dy),
        )

    # ------------------------------------------------------------ 拖动（task08）

    def _press(self, local_pos: QPoint, global_pos: QPoint) -> bool:
        """鼠标左键按下 → **`Pressed`（候选拖动）**，此刻还什么都没动（task21 UX-03）。

        返回是否进入了候选态（已经在拖动 / 已经在候选态 → `False`）。

        ## 为什么按下不能直接算拖动

        第一版在按下那一刻就：挂起静置逻辑、退出 `manual` 坐姿、切 `idle`——
        于是用户**只是点了一下**（没挪鼠标），休息中的宠物也会突然站起来换姿势
        （用户 2026 实测反馈）。按下只是"可能要拖"，**位移才是拖动**。

        ## `Pressed` 态里什么都不碰

        姿势不动、静置计时照走、指针不变。唯一记下来的是"按在了窗口内的哪一点"
        与按下的全局坐标——越过 `DRAG_START_DISTANCE` 之后交给 `_begin_drag()`。
        """
        if self.drag_controller.active or self._press_pending:
            return False
        self._press_pending = True
        self._press_local = QPoint(local_pos)
        # 先把基准记在 DragState 里（`begin()` 还会再写一次，值相同、无副作用）：
        # 判定越过阈值的那一次移动要靠 `press_global` 算"离按下点多远"。
        self.drag_controller.state.press_global = QPoint(global_pos)
        return True

    def _begin_drag(self, press_global: QPoint) -> bool:
        """越过阈值 → **`Dragging`（确定拖动）**：接管动作控制权、切指针形状。

        返回是否真的开始了（已经在拖动中 → `False`，不重复发 `drag_started`）。

        三件事的顺序有意义：

        1. **先挂起静置逻辑**（动作控制权归拖动），再切跑动动作——
           反了的话状态机可能在两者之间插一次 `stage_changed`，动作闪一下；
        2. **退出 `manual` 坐姿**：用户拖动 = "别一直坐着了"（task08 §2 第 8 条），
           这一步同时会把阶段切回 `IDLE`；
        3. 指针换成"移动"形状（松手还原）。

        ⚠ **`begin()` 要收下按下那一刻的全局坐标**（不是现在这个）：`DragState`
        的位置公式是"基准 + 累计位移"，而"基准"是**按下时**的窗口位置。
        传现在这个坐标会把前 `DRAG_START_DISTANCE` 像素的位移吞掉
        （窗口会从那一点起"多走"一小截之外的东西——表现是拖动起步时一顿）。
        """
        if self.drag_controller.active:
            return False
        self._press_pending = False

        self.set_action_suspended(True)
        # 拖动开始就先摆好 idle 的姿势：还没位移时"该播什么"没有依据，
        # 与其停在 `rest` 的躺姿上，不如从 `idle` 起（松手也是回 `idle`）。
        #
        # ⚠ **键盘形态下不碰动作**（task17）：KV 没有"当前动作"这回事，
        # `play_action()` 会切到宠物形态的动作上——而那个动作在 KV 里根本看不见，
        # 等切回来时会莫名其妙地换了个姿势。拖动本身照常可用（形态无关）。
        if self._content.form != FORM_KEYVIEW:
            self.exit_manual_rest()
            if self.pet.has_action(IDLE_ACTION):
                self.play_action(IDLE_ACTION)
        # 按下点用 `Pressed` 记下的那一个：基准位置必须配"按下那一刻"的全局坐标。
        self.drag_controller.begin(self._press_local, press_global, self._base_pos)
        self.setCursor(Qt.SizeAllCursor)
        return True

    def _apply_drag(self, global_pos: QPoint) -> None:
        """拖动中的一次移动：算位置 → **夹取** → 写权威位置 → 按方向切动作。

        ⚠ **键盘形态只移动窗口、绝不碰动作**（task21 UX-09）：KV 没有"当前动作",
        而 `play_action('running_*')` 不但会换掉切回宠物形态时看到的那一帧，
        还会**把帧定时器拉起来**——闭着形态分支时，拖完一次 KV 再退出 KV，
        人物会从一个莫名其妙的位置接着跑。所以形态判断放在**最前面**，
        连 `drag_controller.update()` 的动作结果都不看。
        """
        target, action = self.drag_controller.update(global_pos)
        # 夹取在 `_move_clamped()` 里，**早于** `move_to()` 写权威位置（§4.5）。
        self._move_clamped(target.x(), target.y())
        if self._content.form == FORM_KEYVIEW:
            return
        if action is None:
            # 纯竖直移动：沿用上次方向（不切、不闪）——`direction_action()` 的返回值
            # 表达的就是"没有依据"，不是"该停了"。
            return
        if self.pet.has_action(action) and action != self._animation.action:
            self.play_action(action)

    def _finish_drag(self, global_pos: QPoint) -> None:
        """松手：**夹取后**记住落脚点、写配置、恢复静置、还原指针。

        "**只点了一下没拖动**"也走完整条路径（agent.md §4.6）：
        那一次拖动的位置可能因为贴边被夹住而没变，但仍然需要一次
        完整的位置校正通知（`move_to()` 里已经处理"变了才发信号"，
        写配置则每次都写——值没变时 `ConfigStore.set()` 自己短路，不落盘）。
        """
        if not self.drag_controller.active:
            return

        state = self.drag_controller.state
        final = state.target_position(global_pos)
        self._move_clamped(final.x(), final.y())
        # 松手回 `idle`（task08 §2 第 3 条）。键盘形态下不碰动作（见 `_begin_drag`）。
        if self._content.form != FORM_KEYVIEW and self.pet.has_action(IDLE_ACTION):
            self.play_action(IDLE_ACTION)

        self._user_positioned = True
        # 用户拖动过 → 位置由他决定，底部预留机制从此不生效（task09 §4）。
        # ⚠ 这**不是**"把宠物放回贴屏幕底边"——控制栏关闭时才回位（`app.py`），
        # 那时机制已失效，于是保持用户拖到的位置（"用户位置优先"，task08 §4）。
        self.set_reserved(False)
        position = self.base_position
        self.drag_controller.finish(position)
        self.set_action_suspended(False)
        self.unsetCursor()
        self._persist_position(position)

    def _persist_position(self, pos: QPoint) -> None:
        """把落脚点写进配置（**只在松手与换档时**，绝不逐帧写）。

        写的是**夹取后**的权威位置（§4.5）。写失败按 §2.10 第二类报错：
        记 `ERROR` 日志并把系统原因原样带出来——拖动是用户明确要求的操作，
        它失败了必须让用户知道"这次没记住"。
        `ConfigStore.set()` 自己**不抛异常**（它返回 `False`），所以这里不会炸。

        没有 `ConfigStore` 时（自检里常常没有）只记 `DEBUG` 并返回：
        那种情况下"记不住位置"是测试的设定，不是产品故障。
        """
        if self._config is None:
            logger.debug("没有配置 store，跳过位置持久化：%s", geometry.format_position(pos.x(), pos.y()))
            return
        text = geometry.format_position(pos.x(), pos.y())
        if not self._config.set("position", text):
            reason = self._config.last_error("position") or "未知原因"
            logger.error("记住宠物位置失败（%s）：%s", text, reason)

    def persist_position(self) -> bool:
        """把**当前**权威位置写进配置；返回是否写到盘上。

        供"换档时也写一次"与上层（`app.py` 的退出收尾）使用。
        夹取已经在 `move_to()` 之前发生过了，这里不再夹。
        """
        self._persist_position(self.base_position)
        return self._config is not None

    def set_action_suspended(self, suspended: bool) -> None:
        """把**动作控制权**交给外部（拖动中）或收回。

        例外的第 ② 条（task07 §4）：挂起期间计时**归零但不切动作**，
        由拖动逻辑按方向播跑动。**恢复时计时从零重新等**——
        否则刚松手就立刻张望，用户会觉得"它一直在等着我松手"。
        """
        if self._idle is not None:
            self._idle.set_suspended(bool(suspended))

    # ------------------------------------------------------------ 右键菜单

    @property
    def manual_rest(self) -> bool:
        """当前是不是"手动坐下休息"（右键菜单点出来的那次）。

        **权威来源是久置状态机的 `manual` 开关**（task07）：菜单文字与
        "自动计时该不该管它"都由它决定。没有状态机时退回本类自己的退化字段。
        """
        if self._idle is not None:
            return self._idle.manual
        return self._manual_rest

    @property
    def idle_state(self) -> IdleStateMachine | None:
        """久置状态机（只读；没有接线时是 `None`）。**计时不在这里推进**。"""
        return self._idle

    def _on_stage_changed(self, old: object, new: object) -> None:
        """久置阶段变了：切到**同名动作**（`idle` / `waiting` / `rest`）。

        阶段名与动作名同形（`idle_state.Stage.value`），所以映射就是 `str(new)`，
        **不另立一张映射表**——两张表迟早会对不上。

        素材里没有这个动作时**什么都不做**（"缺动作就不切"，agent.md §2.5：
        不许为找不到的动作硬塞替身）；被挂起时也不可能收到本信号
        （状态机自己在挂起期间不发 `stage_changed`，见例外的第 ② 条）。

        ## 休息音效的**唯一落点**就在这里

        主动休息（右键菜单 → `IdleStateMachine.enter_manual()`）与久置自动休息
        最终都会走到"阶段变成 `REST`"这一步，于是 task10 §2 第 2 条那条
        "两者**共用**同一个逻辑名 `rest`"在这里天然成立：
        声音跟着**阶段**走，不跟着"是谁点的"走。写两处必然有一天只响一个。
        """
        action = str(getattr(new, "value", new))
        if not self.pet.has_action(action):
            return
        # ⚠ 键盘形态下不切动作（task17）：久置状态机照常在跑（计时与阶段都更新），
        # 但**画面归 KV 管**。等切回宠物形态时，`_on_frame_due` 会按动画当前动作
        # 出图，于是"回宠物形态时动作是对的"这条自动成立。
        # ⚠ 音效也不响：休息音效属于"切到休息这个动作"，KV 里没有这个动作。
        if self._content.form == FORM_KEYVIEW:
            return
        self.play_action(action)
        if action == REST_ACTION:
            self._play_sound(SOUND_REST)

    def _play_sound(self, name: str) -> bool:
        """播一个逻辑名；**没有播放器就静默**，返回"有没有真的播"。

        唯一的音效出口：`play()` 自己保证"任何一步失败都静默跳过"
        （task10 §4），所以这里不需要 try/except，也不要在这里做任何判断——
        判断（声明没声明、文件在不在）全在 `SoundPlayer` 一处，两处判迟早分叉。
        """
        if self._sounds is None:
            logger.debug("没有音效播放器，跳过音效 %s", name)
            return False
        return self._sounds.play(name)

    def exit_manual_rest(self) -> bool:
        """退出 `manual`（主动坐姿）并站起来；返回是否真的退出了。

        退出方式有三条（task07 §4）：**再次点菜单 / 双击宠物 / 拖动**。
        task08 已把拖动这条接上（task21 起是 `_begin_drag`：越过 `DRAG_START_DISTANCE` 才起拖）。
        """
        if self._idle is None:
            if not self._manual_rest:
                return False
            self._manual_rest = False
            if self.pet.has_action(REST_STANDUP_ACTION):
                self.play_action(REST_STANDUP_ACTION)
            return True
        return self._idle.exit_manual()

    def on_mouse_moved(self) -> None:
        """把"鼠标动了"转成状态机的输入（由 `app.py` 的光标轮询驱动）。"""
        if self._idle is not None:
            self._idle.on_mouse_moved()

    def can_rest(self) -> bool:
        """素材里有没有 `rest`（"缺动作就不切"，agent.md §2.5）。"""
        return self.pet.has_action(REST_ACTION)

    def menu_state(self) -> MenuState:
        """现算一份菜单状态（**每次弹出前调一次**，勾选态与文字都按此刻的值）。

        `mirror_on` 只在适用范围内才有意义，所以这里把它与 `can_mirror`
        一起给出去——适用范围外两者分别是"假"与"不可点"。

        `resize_available` **task09 起恒为真**：`调整大小…` 已经接线
        （`set_resize_handler()` 装了处理器才为真——没装时仍然灰掉，
        这样"自检里没接控制栏"的窗口不会给用户一个点了没反应的菜单项）。
        `settings_available` 同理（task11：`set_settings_handler()`）。

        `visibility_available`（task12）判的是**两件事都要成立**：装了处理器
        **并且**真的有可用的呼出快捷键。只看处理器是不够的——候选链全军覆没时
        藏起来就叫不回来了（`hide_pet()` 也会再拦一道）。
        """
        action = self._animation.render_action
        # 键盘形态下不镜像（见 `mirrored` 属性）：`can_mirror` 是"这一项能不能点"，
        # 所以在 KV 里它必须是假。
        can_mirror = (
            self._mirror is not None
            and self._content.form != FORM_KEYVIEW
            and self._mirror.can_mirror(action)
        )
        in_keyview = self._content.form == FORM_KEYVIEW
        return MenuState(
            action=action,
            manual_rest=self.manual_rest,
            mirror_on=can_mirror and self.mirrored,
            can_mirror=can_mirror,
            # ⚠ 键盘形态下 `坐下休息` / `镜像翻转` 都**没有意义**（KV 没有动作、
            # 也不镜像）→ 两项都灰掉。不这么做的话，用户在 KV 里点一次"坐下"
            # 会切到宠物形态的动作上——那是个**跨形态的假动作**，
            # 与"绝不显示假成功"直接冲突。
            can_rest=self.can_rest() and not in_keyview,
            resize_available=self._resize_handler is not None,
            settings_available=self._settings_handler is not None,
            visibility_available=(
                self._visibility_handler is not None and self._hotkey_available
            ),
            visibility_reason="" if self._hotkey_available else context_menu.REASON_NO_HOTKEY,
            form=self._content.form,
            kv_available=self.keyview_available,
        )

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """右键：在**鼠标处**弹菜单（task06 §4"定位"）。

        两条行为契约落在这里：

        - **拖动中右键不弹菜单**：拖动是"按下 → 移动 → 松开"的连续操作，
          中途插一个菜单会把这次拖动切成两段（松手时窗口停在半路）。
          取舍是"**明确忽略拖动中的右键**"——比"打断拖动"更不容易让用户困惑
          （task06 §4 允许两种做法，选一种并记在 agent.md）。
          菜单**不进入**拖动状态，所以它也不会顺带改位置。
        - **菜单以宠物窗口为 parent**（`build_menu(self, ...)`）：不设 parent
          的话它只是一条顶层窗口，在"始终置顶"的宠物旁边会掉到别的窗口后面。
        """
        if self.drag_controller.active:
            event.accept()
            return

        menu = context_menu.build_menu(self, self.menu_state())
        # 每一次弹出都是**新建的菜单**：选完就当次丢弃，不留跨次状态。
        menu.setAttribute(Qt.WA_DeleteOnClose, True)
        chosen = menu.exec(event.globalPos())
        self._handle_menu_choice(context_menu.action_id(chosen))

    def _handle_menu_choice(self, item_id: str) -> None:
        """菜单选完了：按**稳定 id** 分派（**不看文字**）。

        没选（`Esc` / 点菜单外 → `item_id` 为空串）就什么都不做——
        "关闭且不做任何事"是 task06 §4 明写的契约。
        """
        if not item_id:
            return
        if item_id == context_menu.ID_FORM:
            # task17：`开启KV` / `退出KV` 交给 `app.py` 装的处理器
            # （它负责**写配置** + 调 `toggle_form()`）。
            # 没装（自检）时菜单项本来就是灰的，到不了这里；真到了也什么都不做。
            if self._form_handler is not None:
                # ⚠ 条件表达式末尾**不能**留逗号：括起来的多行表达式一旦有尾逗号
                # 就变成一元元组 `("keyview",)`，形态名再往下走就成了
                # "未知形态 ('keyview',)，拒绝切换"（task19 实测踩到）。
                target = (
                    FORM_PET
                    if self._content.form == FORM_KEYVIEW
                    else FORM_KEYVIEW
                )
                self._form_handler(target)
        elif item_id == context_menu.ID_MIRROR:
            self.toggle_mirror()
        elif item_id == context_menu.ID_ACTION:
            self.toggle_rest()
        elif item_id == context_menu.ID_RESIZE:
            # task09：`调整大小…` 交给 `app.py` 装的处理器（开关控制栏 + 重置静置计时）。
            # 没装（自检）时菜单项本来就是灰的，到不了这里；真到了也什么都不做。
            if self._resize_handler is not None:
                self._resize_handler()
        elif item_id == context_menu.ID_SETTINGS:
            # task11：`设置…` 同理，处理器由 `app.py` 装（打开/前置设置窗口）。
            if self._settings_handler is not None:
                self._settings_handler()
        elif item_id == context_menu.ID_VISIBILITY:
            # task12：`隐藏宠物`。没装处理器 / 没有可用热键时菜单项本来就是灰的，
            # 真到了这里也只走同一条 `hide_pet()`（它自己还会再拦一道）。
            if self._visibility_handler is not None:
                self._visibility_handler()
            else:
                self.hide_pet()
        elif item_id == context_menu.ID_QUIT:
            self.close()

    def toggle_mirror(self) -> bool:
        """翻转**当前动作**的镜像；返回是否成功（写失败 → `False`）。

        写失败时 `MirrorState.toggle()` **不改内存、不发信号**，
        所以画面与勾选都保持原样，不会出现"看着翻过去了、其实没记住"
        （agent.md §2.10 第二类：绝不显示假成功）。
        """
        if self._mirror is None:
            return False
        return self._mirror.toggle(self._animation.render_action)

    def toggle_rest(self) -> None:
        """`坐下休息` ↔ `站起来`（**同一个菜单项**，id 恒为 `action`）。

        task07 起它接的是**状态机的 `manual` 模式**：点一次进入主动坐姿
        （计时停摆、鼠标怎么动都不影响它），再点一次站起来回 `auto`。
        菜单文字由 `MenuState.manual_rest` 现算，所以切完不用手工改文字。

        素材里没有 `rest` 时**什么都不做**（"缺动作就不切"，
        agent.md §2.5：不许为找不到的动作硬塞替身）。
        """
        if not self.can_rest():
            return

        if self._idle is not None:
            # 状态机知情：进入/退出 `manual` 由它同时**重置静置计时**
            # （否则"站起来"之后可能立刻又被自动计时拽回休息）。
            if self._idle.manual:
                self._idle.exit_manual()
            else:
                self._idle.enter_manual()
            return

        # 没有状态机（自检）时的退化路径：只做一次手动动作切换。
        if self._manual_rest:
            self._manual_rest = False
            self.play_action(REST_STANDUP_ACTION)
        else:
            self._manual_rest = True
            self.play_action(REST_ACTION)

    # ------------------------------------------------------------ 遮罩

    def apply_mask(self, image: QImage | None) -> bool:
        """按 ``image`` 的不透明像素设置窗口区域。

        返回**是否真的设上了遮罩**。区域为空 / 算不出来 → 退化为
        ``clearMask()``（整帧可点），**永不抛异常**。

        实现转发给 `hit_test.region_for_frame()`（**区域生成的唯一入口**）。
        收进来的图**已经翻过**（镜像在渲染层做），所以"能点到的范围"
        与"看到的画面"永远一致，这里不需要再管镜像（agent.md §2.14）。

        ## ⚠ 阈值跟着抗锯齿开关走（task15 实测补上的一半）

        开了抗锯齿时必须用**低阈值**（`hit_test.ALIASED_ALPHA_THRESHOLD`），
        否则抗锯齿**等于白做**：过渡像素的 alpha 大多在 32~96，
        而 `createAlphaMask()` 的阈值是 128 → 实测 2074 个新像素里
        **只有 53 个能上屏**，肉眼看不出任何变化（用户先反馈"感觉没变化"，
        随后量出这条因果）。

        关着的时候**必须用默认阈值**：那是 task15 之前的既有行为，
        改它会动到"关掉就与以前一模一样"那条基线（§0/§2.8/§4.9/§4.12）。

        ## ⚠ 区域要**缓存**（task21 UX-05）

        遮罩窗口每换一次形状，合成器就要重做一次（agent.md §4.9 量过：
        真窗口比纯调度多约 8 ms/帧）。而**同一张渲染图**的区域永远一样——
        素材是逐帧固定的。所以按"图"缓存区域：换帧命中缓存时连
        `createAlphaMask()` 都不必重算，只做一次 `setMask()`。
        判据：连播一轮之后 `region_builds` 只涨一轮的量，而不是每帧都涨。
        """
        region = self._region_for(image)
        if region is None or region.isEmpty():
            self.clearMask()
            return False
        self.setMask(region)
        return True

    def _region_for(self, image: QImage | None):
        """取 ``image`` 的窗口区域，**按图缓存**（键就是图本身）。

        渲染层对同一帧返回的本来就是**同一个对象**，所以"身份"就足以当键
        （见 `_region_cache` 的说明：`QImage` 在 PySide6 里**不可哈希**）。
        缓存按 LRU 封顶（``REGION_CACHE_LIMIT``）：1~10 档来回切时区域大小差着
        两个数量级，留太多等于白吃内存——而**单帧**的区域只有几 KB 量级，
        逐帧播放永远只用到"当前这一张"。
        """
        # ⚠ 键里**必须**带 `id(image)` 的身份，不能只用尺寸：同一档位下所有帧的
        # 尺寸都一样，只用 `(宽, 高, 阈值)` 会让整轮播放共用第一个区域的区域
        # （"能点到的范围"与"看到的画面"当场分叉，§2.8 的红线）。
        key = (id(image), int(image.width()), int(image.height()), self._mask_threshold)
        if key in self._region_cache:
            self._region_cache.move_to_end(key)
            return self._region_cache[key]
        region = hit_test.region_for_frame(
            image, alpha_threshold=self._mask_threshold
        )
        self.region_builds += 1
        self._region_cache[key] = region
        self._region_owner[key] = image
        limit = self._region_limit()
        while len(self._region_cache) > limit:
            self._region_cache.popitem(last=False)
            self._region_owner.popitem(last=False)
        return region

    def _region_limit(self) -> int:
        """这个窗口的区域缓存该留几条（**跟着当前动作的帧数走**）。

        单个 `QRegion` 只有几 KB 量级，几十条也只占一小块内存；而"帧数放不下"
        会让一轮播放里反复重算（与 task16 修过的渲染缓存是同一个坑）。
        """
        action = self.pet.action(self._animation.action)
        frames = action.frame_count if action is not None else 0
        return max(REGION_CACHE_LIMIT, int(frames)) + REGION_CACHE_HEADROOM

    @property
    def _mask_threshold(self) -> int:
        """当前该用的遮罩 alpha 阈值（**真值只有这一处**）。"""
        if self._renderer.antialias:
            return hit_test.ALIASED_ALPHA_THRESHOLD
        return hit_test.DEFAULT_ALPHA_THRESHOLD

    # ------------------------------------------------------------ 事件

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            # DPI≠100% 时 Qt 会把逻辑尺寸的位图**再补放大一次**（窗口的逻辑尺寸
            # 与 `_frame_image` 的像素尺寸本来一致，但设备像素更多）。
            # 默认的 `SmoothPixmapTransform` 是 **关**的，那次补放大走**最近邻**，
            # 于是边缘又冒出等比放大的粗台阶——task15 §2 第 8 条点名的现象。
            # 这一行是**独立的真修复**：与抗锯齿开关无关、任何设置下都生效。
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            # `_frame_image` 是**按当前档位缩放后**的图，尺寸与窗口一致，按 (0, 0) 摆放。
            # 它未标注 DPR（见 pet_loader.load_frame_image / rendering 的说明），
            # 所以这里也**不要**自己乘 devicePixelRatio —— 那是缩放两次（agent.md §4.9）。
            painter.drawImage(0, 0, self._frame_image)
        finally:
            painter.end()

    def showEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """窗口真的显示了 -> **通知一次**（task20 补）。

        ## 为什么要在这里再发一次（`show_pet()` 里不是已经发过了吗）

        `visibility_changed` 原先**只**从 `hide_pet()` / `show_pet()` 发。
        但只要窗口的可见性从**别的路径**改变（Qt 自己恢复窗口、系统重建
        顶层窗口、将来多一条显示路径），那个信号就不会响 —— 于是"该不该
        轮询"的判断一直是旧的，KV 表现成"按什么都没反应"且**不会自愈**。
        用户 2026 实测分发版："只要窗口失焦 KV 就失效，点一下桌宠焦点回来
        才恢复"，正是这种"状态陈旧、没人来纠正"的症状。

        挂在这里之后，判据变成"窗口**实际**的可见性"，而不是"谁调了哪个
        方法"——多一条路径也不会漏。`sync()` 本身是幂等的，重复发无害。
        """
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """窗口真的藏起来了 -> **通知一次**（与 `showEvent` 同一条理由）。"""
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """左键按下：进入 **`Pressed`（候选拖动）**，此刻**不改任何状态**。

        **只有按在人物身上才起拖**由 `setMask` 负责——点击透明区时事件
        由系统级穿透到下面的窗口，本窗口根本收不到 `mousePressEvent`。
        所以这里**不需要**再判一次坐标（那会造出第二份判定真值，
        而且与遮罩的任何偏差都会变成"看得见的能点、能点的看不见"）。

        两种坐标**不可混用**（agent.md §4.9）：

        - 位移用 `globalPosition()`（屏幕坐标）；
        - 命中判定用 `position()`（窗口内坐标）——这里把它记进
          `DragState.grab_offset`，供诊断与将来的自窗口内判定使用。

        ⚠ **点一下不等于拖动**（task21 UX-03）：姿势、静置计时、指针三样都
        原样留着，等 `mouseMoveEvent` 越过 `DRAG_START_DISTANCE` 再交给
        `_begin_drag()`。
        """
        if event.button() == Qt.LeftButton:
            # 记下"按下这一刻是不是 manual 坐姿"：双击要用**按下前**的值，
            # 而 `_begin_drag()` 会把 manual 退出掉（见 mouseDoubleClickEvent）。
            self._manual_rest_at_press = self.manual_rest
            self._press(
                event.position().toPoint(),
                event.globalPosition().toPoint(),
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """拖动：位移一律用**全局坐标**算（窗口内坐标是给命中判定用的）。

        两段：`Pressed` 里先看"离按下点够不够远"，够了才转 `Dragging`；
        转的那一下**接着就用同一个事件算位置**，所以越过阈值几乎没有延迟
        （真正的跟手位移从第一个移动事件就全部算上）。
        """
        if not event.buttons() & Qt.LeftButton:
            super().mouseMoveEvent(event)
            return
        global_pos = event.globalPosition().toPoint()
        if self._press_pending:
            if not self._press_exceeded(global_pos):
                super().mouseMoveEvent(event)
                return
            self._begin_drag(self.drag_controller.state.press_global)
        if self.drag_controller.active:
            self._apply_drag(global_pos)
        super().mouseMoveEvent(event)

    def _press_exceeded(self, global_pos: QPoint) -> bool:
        """`Pressed` 态里"这一次移动够不够格算拖动"。

        判据是**离按下点的直线距离**（x、y 都算）：纯竖直拖动也要能起拖，
        否则"往上拖"会永远卡在候选态。
        """
        delta = QPoint(global_pos) - self.drag_controller.state.press_global
        return (
            abs(delta.x()) >= DRAG_START_DISTANCE
            or abs(delta.y()) >= DRAG_START_DISTANCE
        )

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """松手：真的拖动过才算一次完整收尾；**只点了一下**就安静收尾。

        单击（`Pressed` 里松手，从没越过阈值）走 `_cancel_press()`：
        不写配置、不发 `drag_finished`、不切动作、不碰静置计时——
        那一下**什么都没发生**，正是 task21 UX-03 要的结果。
        """
        if event.button() == Qt.LeftButton:
            if self.drag_controller.active:
                self._finish_drag(event.globalPosition().toPoint())
            elif self._press_pending:
                self._cancel_press()
        super().mouseReleaseEvent(event)

    def _cancel_press(self) -> None:
        """`Pressed` 态里松手 / 被打断：**什么都不留**（与 `abort_drag()` 同一种语义）。"""
        self._press_pending = False

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击宠物：**完整挥手一次 + 播 `waving`**（task10 §4"双击手势"）。

        **拖动途中一律忽略**（task10 §2 第 5 条 / task08 §2 第 6 条）：拖动是
        "按下 → 移动 → 松开"的连续操作，中途插入一个手势会把这次拖动切成两段。
        判据就是 `self.dragging`——**不看位移**：拖动一旦开始就接管动作控制权，
        这时候再挥手会与拖动逻辑抢动作，表现是一边走一边抽搐。

        不在拖动中且处于 `manual` 坐姿时**退出坐姿**（task07 §2 第 6 条）：
        用户双击的意思是"别一直坐着了"，于是站起来回 `auto` 模式。
        挥手会在那之后开始（`exit_manual_rest()` 切了 `idle`，随后被手势顶掉）。

        ## 手势怎么表达：`play_sequence` 的 `("loop", 1)` 片段

        "完整播放一次 `waving`，播完回 `idle`"用 `animation.play_sequence()`
        （等价 `("loop", 1)`）表达，而不是"手动数到末帧再切"：
        片段序列负责**何时结束**（`sequence_done`），调用方只管收尾。
        `waving` 是一次性动作（`PLAY_MODES`），片段里仍按它自己的方式走；
        片段只收窄帧区间——本手势用的是**整段**，所以连帧区间都不收窄。
        **不裁帧**：首帧是抬手（起势）、末帧是收手（收势），衔接 `idle` 更自然。

        ## 为什么最后要 `abort_drag()`

        双击的第 2 次按下会先来一个 `mousePressEvent`（Qt 的固定顺序是
        press → release → **dblclick**），于是窗口已经进入了 `Pressed`
        （task21 之前则是直接进了 `Dragging`：静置逻辑被挂起、姿势被改成 `idle`）。
        紧接着到来的 `dblclick` **没有**配对的 release（第 2 次按下的 release
        在 dblclick 之后才到），所以这一次"按下"会一直挂在那儿——
        表现是**双击之后宠物僵住**（`dragging` 恒为真 → 右键菜单永远不弹、
        静置逻辑永远被挂起）。

        task07 的自检当场抓到了这一条（双击后 `suspended` 变成 `True` 且不再恢复）。
        对策是双击时**明确中止**这次按下：`Pressed` 清掉、真的在拖动时把
        动作控制权还回去、指针还原。
        用户真要拖动时，第二次按下之后**移动**会重新进入拖动——
        双击的那两次点击本身没有位移，中止它不会丢任何东西。

        ⚠ 手势与 `abort_drag()` 的**先后顺序**：先中止拖动，再做手势。
        反了的话 `abort_drag()` 之后那次 press 会重新起拖、并把动作抢回 `idle`，
        挥手当场被打断（表现是"双击没反应"）。
        """
        if event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return

        # 拖动途中双击被忽略（task10 §2 第 5 条）：连 `super()` 都不必特殊处理，
        # 走默认路径即可——关键只是**不触发手势、不响**。
        if self.drag_controller.active:
            super().mouseDoubleClickEvent(event)
            self.abort_drag()
            return
        # `Pressed`（按了但没挪）时双击：**必须**把候选收掉再走手势。
        # task21 起"按下"不再接管任何状态，所以这里不需要也不应该
        # 走上面那条"拖动中忽略"的路径——单击/双击照常挥手。
        self._cancel_press()

        if self._manual_rest_at_press:
            # `manual` 坐姿中双击 → 站起来回 `auto`（task07 §2 第 6 条）。
            # ⚠ 看的是**按下时**记下的值：第二次按下之后若真的拖起来，
            # `_begin_drag()` 会把 `manual` 退出掉（拖动 = "别坐着了"）。
            # 此刻再问 `manual_rest` 就永远是假，这一条就失效了。
            self.exit_manual_rest()

        super().mouseDoubleClickEvent(event)
        # ⚠ **必须放在 `super()` 之后**：QWidget 的默认实现会把这次双击
        # 再翻成一次 `mousePressEvent`（Qt 的顺序是 press → release → dblclick，
        # 而我们正在处理的那次 press 之前、super 之后还可能再来一次）。
        # 放在前面中止的话，紧随其后的 press 会立刻把拖动又开起来。
        self.abort_drag()
        # 手势排在**中止之后**：`abort_drag()` 把动作控制权还给了静置逻辑，
        # 这时才轮到手势接管（见上面那条顺序说明）。
        self.wave_once()

    def wave_once(self) -> bool:
        """完整播放一次 `waving` 并播 `waving` 音效；返回手势有没有真的开始。

        素材里没有 `waving` 动作时**不挥手**（agent.md §2.5："缺 `waving` 就不挥手，
        按可恢复路径退化"）——但音效**照样播**：它是"双击这个交互"的声音，
        与素材有没有挥手动作无关（task10 §4 把 `waving` 定为"双击交互音"；
        逻辑名与动作同形是刻意的，两者各查各的表，不互相牵制）。
        """
        started = False
        if self.pet.has_action(WAVING_ACTION):
            # 片段序列：播完整段一圈 → `animation.sequence_done` → 回 `idle`。
            self._animation.play_sequence(WAVING_SEGMENT)
            self._action = self._animation.render_action
            self._shown_index = None
            self._shown_mirrored = None
            self.show_action_frame(self._animation.render_action, self._animation.index)
            # 片段必须先 `driver.start()`：`play_sequence` 之后定时器可能正停着
            # （上一次一次性动作播完就停了），不重启就永远不会推进。
            self._driver.start()
            # 手势标志**在开播之后**置真：`play_sequence()` 会中断旧序列，
            # 万一它触发了收尾，那时标志还该是假（免得收尾逻辑被上一次手势带走）。
            self._gesture_active = True
            started = True
        self._play_sound(SOUND_WAVING)
        return started

    def _on_sequence_done(self) -> None:
        """片段序列播完：**回 `idle`**（task10 §2 第 4 条"播完自动回 idle"）。

        ## 为什么必须自己记一个标志，而不是问 `in_sequence`

        `sequence_done` 在**任何**一次"播完"时都会发出来：普通的一次性动作
        （`play_action("waving")`）播到末帧也发它。而信号发出时
        `Animation._segments` **已经被清空了**（`_finish()` 先清再发），
        所以此刻问 `animation.in_sequence` **永远是假** ——
        拿它当判据会把"普通挥手播完"也当成"手势播完"，于是切回 `idle`
        让 idle 一直循环下去（task05 的真平台自检当场抓到：
        期望"停在末帧 29、定时器已停"，实际停在 41 且 `running=True`）。

        所以判据是 `_gesture_active`：**只有双击手势才收尾**。
        """
        if not self._gesture_active:
            return
        self._gesture_active = False
        if self.drag_controller.active:
            # 拖动中途播完：动作控制权在拖动逻辑手里，不许抢（§2.16）。
            return
        if self.pet.has_action(IDLE_ACTION):
            self.play_action(IDLE_ACTION)

    def abort_drag(self) -> bool:
        """立即中止当前拖动（不写配置、不发 `drag_finished`）；返回是否真的中止了。

        用途只有一个：**双击**。双击的第 2 次按下先来一个 `mousePressEvent`
        （Qt 的顺序是 press → release → dblclick），于是窗口已经被置成
        `Pressed`（task21 之前是直接 `Dragging`）；而这一次按下**不会**有配对的
        `mouseReleaseEvent` 走到 `_finish_drag()`（它的 release 在 dblclick 之后，
        且此时标志已经被这里清掉）。

        "中止"与"松手"是两件事，所以**故意不复用** `_finish_drag()`：

        - 松手要写配置、发 `drag_finished`（用户真的挪了位置）；
        - 中止什么都不留（那两次点击没有位移，位置本来就是原样）。

        动作控制权照常还回去，并**重新计时**（与松手一致）——
        否则静置逻辑会一直停摆，宠物再也不会张望。
        """
        # `Pressed`（按下但还没越过阈值）也要收掉：task21 起"双击的第 2 次按下"
        # 只可能停在候选态，那时没有任何状态被接管过，清掉标志就够了。
        self._press_pending = False
        if not self.drag_controller.active:
            return False
        self.drag_controller.active = False
        self.set_action_suspended(False)
        self.unsetCursor()
        return True


def _union_box(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """两个窗口内矩形 `(x, y, w, h)` 的**并集外接矩形**（task21 UX-06）。

    夹取要的是"这两块一起放得下"的那一块：并集之后，换姿势只会让夹取
    **更保守**，不会让上一块已经合规的画面因为换帧而越界。
    """
    x0 = min(first[0], second[0])
    y0 = min(first[1], second[1])
    x1 = max(first[0] + first[2], second[0] + second[2])
    y1 = max(first[1] + first[3], second[1] + second[3])
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _pick_action(pet: PetSpec, preferred: str) -> str:
    """挑一个**素材里真实存在**的动作来显示第一帧。

    素材里**有几个动作就是几个**（agent.md §2.5）：不补、不改名、不造别名。
    首选 ``preferred``；它不在就按 `action_order` 取第一个；
    连一个动作都没有时返回 ``preferred``（`load_frame_image` 会退化返回空图）。
    """
    if pet.has_action(preferred):
        return preferred
    if pet.action_order:
        return pet.action_order[0]
    return preferred
