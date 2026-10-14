"""宠物主窗口：无边框 / 置顶 / 透明背景 / 可拖动 / 按档位缩放 / **按素材帧率播放动画**。

task01 交付了**最小可运行骨架**（占位图形 + 拖 + 退出）；task03 起窗口显示
**真实素材的第一帧**，占位模块 `placeholder.py` 已随之删除；
task04 起窗口尺寸由**尺寸档位**决定（10 级、屏幕自适应、右下角锚点）；
task05 起窗口**持续播放动画**——帧推进由 `animation.Animation` 决定，
本文件只负责"什么时候问下一帧、把哪一帧画出来"；
task06 起**右键菜单**（换动作 / 休息 / 镜像翻转）也接在这里：
菜单本身在 `context_menu.py` 里现造，本文件只负责"什么时候弹、弹完做什么"。

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


from PySide6.QtCore import QElapsedTimer, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBitmap,
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

from typing import Final

from . import PROGRAM_NAME, size
from . import context_menu
from .animation import MAX_DT_MS, Animation
from .context_menu import MenuState
from .idle_state import IdleStateMachine
from .mirror import MirrorState
from .pet_spec import PetSpec
from .rendering import FrameRenderer

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


def region_for_image(image: QImage | None) -> QRegion | None:
    """按 ``image`` 的**不透明像素**生成一个窗口区域。

    **区域生成的唯一入口**（agent.md §2.8：把"取哪一帧"与"怎么生成"分开）。
    task08 接上"取哪一帧 + 镜像"时，把本函数搬到 `hit_test.py` 即可，
    **不要写第二份**。

    返回 ``None`` 表示"**算不出来**"，调用方必须退化成**不设遮罩**（整帧可点）：

    - ``None`` / 空图 / 尺寸非正 → ``None``；
    - **没有 alpha 通道** = 整块不透明 → 返回整幅区域；
    - 不透明阈值取 Qt 自带的 ``QImage.createAlphaMask()``（约 ``alpha >= 128``）。
      真实素材的源 GIF 是 1 位 alpha（只有 0 / 255），所以这个阈值不影响判定。

    本函数**永不抛异常**（agent.md §2.8 的降级约定）。
    """
    if image is None or image.isNull():
        return None

    try:
        source = image
        ratio = float(source.devicePixelRatio() or 1.0)
        if ratio > 0 and ratio != 1.0:
            # 遮罩坐标是**窗口内的逻辑像素**，带 DPR 的图先折回逻辑尺寸，
            # 否则区域会整体放大 ratio 倍（人物能点到的范围比看到的大一圈）。
            source = source.scaled(
                max(1, round(source.width() / ratio)),
                max(1, round(source.height() / ratio)),
                Qt.IgnoreAspectRatio,
                Qt.FastTransformation,
            )

        width = source.width()
        height = source.height()
        if width <= 0 or height <= 0:
            return None

        if not source.hasAlphaChannel():
            return QRegion(0, 0, width, height)

        alpha_mask = source.createAlphaMask()
        if alpha_mask.isNull():
            return None
        return QRegion(QBitmap.fromImage(alpha_mask))
    except Exception:
        # 降级路径不许再抛异常（"算不出来就整帧可点"）。
        return None


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


class PetWindow(QWidget):
    """宠物主窗口（task05：按素材帧率持续播放当前动作）。"""

    def __init__(
        self,
        pet: PetSpec,
        level: int | None = None,
        parent: QWidget | None = None,
        mirror: MirrorState | None = None,
        idle_state: IdleStateMachine | None = None,
    ) -> None:
        super().__init__(parent)

        #: 当前显示的素材包。
        self.pet = pet
        #: 权威位置（左上角，屏幕逻辑坐标）。**只由 move_to() 写**。
        self._base_pos = QPoint(0, 0)
        #: 一次拖动的基准位置（按下那一刻的 _base_pos）。
        self._drag_base_pos = QPoint(0, 0)
        #: 按下时的**全局**坐标；None = 当前没有拖动。
        self._press_global: QPoint | None = None
        self._dragging = False
        self._quit_shortcuts: list[QShortcut] = []

        #: 镜像状态表（task06）。没给就自己造一个"永远不翻转"的
        #: ——`MirrorState` 需要一个 `ConfigStore`，自检里常常没有配置，
        #: 所以这里允许 `None` 并退化（`_mirror` 为 `None` 时恒不翻转）。
        self._mirror = mirror

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
        self._renderer = FrameRenderer(pet, self._device_pixel_ratio())
        #: 当前档位。`None` 表示还没定（构造时要么给 level、要么自适应）。
        self._level = 0

        #: 帧推进器（纯逻辑）+ 驱动它的定时器（本文件唯一的调度点）。
        self._animation = Animation(pet, self._action)
        self._driver = AnimationDriver(self._animation, self)
        self._driver.frame_due.connect(self._on_frame_due)
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
        """当前档位下的内容像素尺寸（= 窗口尺寸）。"""
        return size.pixel_size(self.pet.cell, self._level)

    @property
    def size_level(self) -> int:
        """`level` 的别名（说"档位"时两种叫法都有人用，统一指同一个值）。"""
        return self._level

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
        pixmap = self._renderer.frame(
            self._animation.render_action, self._animation.index, self._level, mirrored
        )
        if not pixmap.isNull():
            # 缓存里的 QPixmap 是**共享**的，转成 QImage 时 Qt 会做一次深拷贝，
            # 于是 `_frame_image` 与缓存互不影响（后续要画/要算遮罩）。
            self._frame_image = pixmap.toImage()
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

    def _move_clamped(self, left: int, top: int) -> None:
        """夹到屏幕内再写权威位置（越界值不进 `_base_pos`，agent.md §4.5）。"""
        width, height = self.pixel_size
        x, y = size.clamp_position(left, top, width, height, self._screen_area())
        self.move_to(x, y)

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

    def _repaint_current(self) -> None:
        """按**当前动作 / 当前帧 / 当前档位 / 当前镜像**重取帧并重设遮罩。

        换镜像后必须走这一条：`show_action_frame` 会重新问渲染层要图
        （键含镜像，所以拿到的确实是翻过的那张），并顺带重算遮罩。
        """
        self.show_action_frame(self._animation.render_action, self._animation.index)

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
        mirrored = self.mirrored
        pixmap = self._renderer.frame(action, index, self._level, mirrored)
        if pixmap.isNull():
            return False

        self._frame_index = int(index)
        self._action = action
        self._frame_image = pixmap.toImage()
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

    def move_to(self, x: int, y: int) -> None:
        """把窗口移到屏幕逻辑坐标 ``(x, y)``，并更新权威位置。

        **本函数不夹取**：它接受调用方算好的最终位置（`_move_clamped()` 已经夹过）。
        夹取属"摆放策略"而不是"搬运"——放在这里会让"拖到边缘被夹住"与
        "换档后重算锚点"两种意图混在一起，说不清是谁夹的。
        """
        self._base_pos = QPoint(int(x), int(y))
        self.move(self._base_pos)

    def showing_at_default_position(self) -> None:
        """首次显示：贴屏幕右下角，右下各留 ``DEFAULT_MARGIN`` 逻辑单位。

        用**整块屏幕**的 ``geometry()``（而不是排除任务栏的 ``availableGeometry()``），
        因为"边距 24 逻辑单位"是从**屏幕边缘**量起的，旧实现（``dotPet``）用的也是整屏高度。
        后果：底部有任务栏时宠物会压在任务栏上沿约 24 逻辑单位（agent.md §4.10）；
        task09 的控制栏预留会把它再抬起来，**到那时再决定要不要改**。
        """
        area = self._screen_area()
        if area[2] <= 0 or area[3] <= 0:  # 拿不到屏幕信息
            self.move_to(0, 0)
            return

        width, height = self.pixel_size
        self._move_clamped(
            area[0] + area[2] - width - DEFAULT_MARGIN,
            area[1] + area[3] - height - DEFAULT_MARGIN,
        )

    def drag_by(self, dx: int, dy: int) -> None:
        """按**按下时记下的基准位置 + 位移**摆放窗口。

        ``dx`` / ``dy`` 是相对**按下点**的累计位移
        （``当前全局坐标 - 按下时的全局坐标``），不是相邻两次移动的增量。
        基准位置是自己记的，因此**不回读窗口实时坐标**。
        """
        self.move_to(
            self._drag_base_pos.x() + int(dx),
            self._drag_base_pos.y() + int(dy),
        )

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
        """
        action = str(getattr(new, "value", new))
        if not self.pet.has_action(action):
            return
        self.play_action(action)

    def exit_manual_rest(self) -> bool:
        """退出 `manual`（主动坐姿）并站起来；返回是否真的退出了。

        退出方式有三条（task07 §4）：**再次点菜单 / 双击宠物 / 拖动**。
        task08 接线拖动，本类先把入口暴露出来。
        """
        if self._idle is None:
            if not self._manual_rest:
                return False
            self._manual_rest = False
            if self.pet.has_action(REST_STANDUP_ACTION):
                self.play_action(REST_STANDUP_ACTION)
            return True
        return self._idle.exit_manual()

    def set_action_suspended(self, suspended: bool) -> None:
        """把**动作控制权**交给外部（拖动中）或收回。

        例外的第 ② 条（task07 §4）：挂起期间计时**归零但不切动作**，
        由拖动逻辑按方向播跑动。**本 task 只暴露接口**，接线在 task08。
        """
        if self._idle is not None:
            self._idle.set_suspended(suspended)

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
        """
        action = self._animation.render_action
        can_mirror = self._mirror is not None and self._mirror.can_mirror(action)
        return MenuState(
            action=action,
            manual_rest=self.manual_rest,
            mirror_on=can_mirror and self.mirrored,
            can_mirror=can_mirror,
            can_rest=self.can_rest(),
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
        if self._dragging:
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
        if item_id == context_menu.ID_MIRROR:
            self.toggle_mirror()
        elif item_id == context_menu.ID_ACTION:
            self.toggle_rest()
        elif item_id == context_menu.ID_QUIT:
            self.close()
        # resize / settings / visibility 本 task 一律是禁用的，
        # 能点到它们说明禁用没生效——那种情况什么都不做，绝不"顺手实现"。

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
        """
        region = region_for_image(image)
        if region is None or region.isEmpty():
            self.clearMask()
            return False
        self.setMask(region)
        return True

    # ------------------------------------------------------------ 事件

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        try:
            # `_frame_image` 是**按当前档位缩放后**的图，尺寸与窗口一致，按 (0, 0) 摆放。
            # 它未标注 DPR（见 pet_loader.load_frame_image / rendering 的说明），
            # 所以这里也**不要**自己乘 devicePixelRatio —— 那是缩放两次（agent.md §4.9）。
            painter.drawImage(0, 0, self._frame_image)
        finally:
            painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            # 能收到这个事件，就说明按下点落在遮罩区域内 —— 也就是"按在人物身上"。
            # 位移一律用全局坐标（窗口内坐标是给命中判定用的，两者不可混用）。
            self._press_global = event.globalPosition().toPoint()
            self._drag_base_pos = QPoint(self._base_pos)
            self._dragging = True
            self.setCursor(Qt.SizeAllCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._dragging
            and self._press_global is not None
            and event.buttons() & Qt.LeftButton
        ):
            delta = event.globalPosition().toPoint() - self._press_global
            self.drag_by(delta.x(), delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._press_global = None
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """双击宠物：**退出 `manual` 坐姿**（task07 §2 第 6 条）。

        本 task **不做**"双击挥手"那个手势（属 task10），只做这一件事：
        用户双击的意思是"别一直坐着了"，于是站起来回 `auto` 模式。
        不在 `manual` 里时**什么都不做**——不抢 task10 的手势。
        """
        if event.button() == Qt.LeftButton and self.manual_rest:
            self.exit_manual_rest()
        super().mouseDoubleClickEvent(event)


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
