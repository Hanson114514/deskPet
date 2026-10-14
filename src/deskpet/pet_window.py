"""宠物主窗口：无边框 / 置顶 / 透明背景 / 可拖动 / **按档位缩放**。

task01 交付了**最小可运行骨架**（占位图形 + 拖 + 退出）；task03 起窗口显示
**真实素材的第一帧**，占位模块 `placeholder.py` 已随之删除；
task04 起窗口尺寸由**尺寸档位**决定（10 级、屏幕自适应、右下角锚点）。

动画（task05）、控制栏（task09）都不在这里。

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
"""


from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QBitmap,
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

from . import PROGRAM_NAME, size
from .pet_spec import PetSpec
from .rendering import FrameRenderer

#: 默认落点距屏幕右下角的留白（逻辑单位）。**真值在 `size.DEFAULT_MARGIN`**，
#: 这里只做引用（同一个约定不要在两处各写一个 24）。
DEFAULT_MARGIN = size.DEFAULT_MARGIN

#: 本 task 显示的动作（动画播放属 task05，这里只取第一帧）。
DEFAULT_ACTION = "idle"


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


class PetWindow(QWidget):
    """宠物主窗口（task04：按尺寸档位显示缩放后的静态第一帧）。"""

    def __init__(
        self,
        pet: PetSpec,
        level: int | None = None,
        parent: QWidget | None = None,
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

        #: 当前显示的帧（**已缩放**，窗口尺寸就是它的尺寸）。
        self._frame_image = QImage()
        #: 本 task 显示的动作名（回退之后的实际名字，可能不是 "idle"）。
        self._action = _pick_action(pet, DEFAULT_ACTION)
        #: 当前帧序号（task05 会逐帧推进；本 task 一直是 0）。
        self._frame_index = 0

        #: 渲染器（帧 → 目标尺寸 + 缓存）。缓存键含档位（task06 还要加镜像）。
        self._renderer = FrameRenderer(pet, self._device_pixel_ratio())
        #: 当前档位。`None` 表示还没定（构造时要么给 level、要么自适应）。
        self._level = 0

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

        pixmap = self._renderer.frame(self._action, self._frame_index, self._level)
        if not pixmap.isNull():
            # 缓存里的 QPixmap 是**共享**的，转成 QImage 时 Qt 会做一次深拷贝，
            # 于是 `_frame_image` 与缓存互不影响（后续要画/要算遮罩）。
            self._frame_image = pixmap.toImage()

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
        """当前显示的动作名（回退之后的实际值）。"""
        return self._action

    @property
    def frame_image(self) -> QImage:
        """当前显示的帧（**已缩放**；只读，空图 = 没有图可画）。"""
        return self._frame_image

    def show_action_frame(self, action: str, index: int) -> bool:
        """显示 ``action`` 的第 ``index`` 帧（按当前档位缩放），并重设窗口遮罩。

        返回**是否真的换上了新帧**。取不到图（动作不存在 / 帧号越界 /
        文件读不出来）时**保留原来的画面**并返回 ``False``——绝不把已经
        画好的东西擦掉，也绝不抛异常（agent.md §2.8 的降级约定）。

        **不改窗口尺寸**：尺寸只由档位决定（`set_size_level()`）。
        帧的实际像素尺寸与 `cell` 不一致时（只是个警告，§2.5），
        渲染层按**真实图**的尺寸缩放，所以窗口尺寸仍以 `cell` 的档位换算为准。
        """
        pixmap = self._renderer.frame(action, index, self._level)
        if pixmap.isNull():
            return False

        self._frame_index = int(index)
        self._action = action
        self._frame_image = pixmap.toImage()
        # 换帧之后**衍生数据必须失效重算**（agent.md §4.7）：遮罩是上一帧的形状
        # 就会出现"人物变了但可点区域没变"。
        self.apply_mask(self._frame_image)
        self.update()
        return True

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
