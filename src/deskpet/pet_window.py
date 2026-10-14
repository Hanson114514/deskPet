"""宠物主窗口：无边框 / 置顶 / 透明背景 / 可拖动。

task01 交付了**最小可运行骨架**（占位图形 + 拖 + 退出）；task03 起窗口显示
**真实素材的第一帧**，占位模块 `placeholder.py` 已随之删除。

动画（task05）、尺寸档位与缩放（task04）、控制栏（task09）都不在这里。
本 task 的窗口尺寸**就是素材的 `cell`**（不缩放、不选档位）。

**两条硬约定**（agent.md §2.8 / §4.6）：

1. **位置用自己记的基准算，绝不回读窗口实时坐标**。
   权威位置是 ``PetWindow._base_pos``，只有 ``move_to()`` 会写它。
   回读 ``self.pos()`` / ``self.geometry()`` 会拿到**异步生效的旧值**，
   表现就是"拖动只走一小截"。
2. **两种坐标不可混用**：位移看 ``event.globalPosition()``（屏幕坐标），
   命中判定看 ``event.position()``（窗口内坐标）。
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

from . import PROGRAM_NAME, pet_loader
from .pet_spec import PetSpec

#: 默认落点距屏幕右下角的留白（逻辑单位）。附录 C：默认落点边距 24 逻辑单位。
DEFAULT_MARGIN = 24

#: 没有素材时窗口退化的边长（逻辑单位）。正常路径用不到它 ——
#: 本 task 起"连默认素材包都不可用"是**启动期致命**（agent.md §2.10 第一类），
#: 不会带着一张空窗口跑起来。
FALLBACK_SIZE = 250

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
    """宠物主窗口（task03：显示真实素材的静态第一帧）。"""

    def __init__(self, pet: PetSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        #: 当前显示的素材包（task03 只支持"一个包的一个动作的第一帧"）。
        self.pet = pet
        #: 权威位置（左上角，屏幕逻辑坐标）。**只由 move_to() 写**。
        self._base_pos = QPoint(0, 0)
        #: 一次拖动的基准位置（按下那一刻的 _base_pos）。
        self._drag_base_pos = QPoint(0, 0)
        #: 按下时的**全局**坐标；None = 当前没有拖动。
        self._press_global: QPoint | None = None
        self._dragging = False
        self._quit_shortcuts: list[QShortcut] = []

        #: 当前显示的帧（带 alpha 的 QImage；任务05 起会被逐帧替换）。
        self._frame_image = QImage()
        #: 本 task 显示的动作名（回退之后的实际名字，可能不是 "idle"）。
        self._action = _pick_action(pet, DEFAULT_ACTION)

        self._setup_window()
        self.show_action_frame(self._action, 0)
        self._install_quit_shortcuts()

    # ------------------------------------------------------------ 装配

    def _device_pixel_ratio(self) -> float:
        """当前屏幕的 DPI 缩放因子（拿不到就当 1.0）。"""
        screen = self.screen() or QGuiApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen is not None else 1.0
        return float(ratio) if ratio else 1.0

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
        # 窗口尺寸就是素材的 cell（本 task 不缩放、不选档位）。
        self.setFixedSize(self.pet.cell[0], self.pet.cell[1])

    def _install_quit_shortcuts(self) -> None:
        """Esc 与 Ctrl+Q 退出。窗口拿到焦点时生效。"""
        for sequence in ("Esc", "Ctrl+Q"):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WindowShortcut)
            shortcut.activated.connect(self.close)
            self._quit_shortcuts.append(shortcut)

    # ------------------------------------------------------------ 显示

    @property
    def action(self) -> str:
        """当前显示的动作名（回退之后的实际值）。"""
        return self._action

    @property
    def frame_image(self) -> QImage:
        """当前显示的帧（只读；空图 = 没有图可画）。"""
        return self._frame_image

    def show_action_frame(self, action: str, index: int) -> bool:
        """显示 ``action`` 的第 ``index`` 帧，并按它的 alpha 重设窗口遮罩。

        返回**是否真的换上了新帧**。取不到图（动作不存在 / 帧号越界 /
        文件读不出来）时**保留原来的画面**并返回 ``False``——绝不把已经
        画好的东西擦掉，也绝不抛异常（agent.md §2.8 的降级约定）。
        """
        image = pet_loader.load_frame_image(self.pet, action, index)
        if image.isNull():
            return False

        self._frame_image = image
        self._action = action
        # 换帧之后**衍生数据必须失效重算**（agent.md §4.7）：遮罩是上一帧的形状
        # 就会出现"人物变了但可点区域没变"。
        self.apply_mask(image)
        self.update()
        return True

    # ------------------------------------------------------------ 位置

    @property
    def base_position(self) -> QPoint:
        """权威位置（只读）。调用方**不要**用 ``pos()`` 代替它。"""
        return QPoint(self._base_pos)

    def move_to(self, x: int, y: int) -> None:
        """把窗口移到屏幕逻辑坐标 ``(x, y)``，并更新权威位置。

        本 task **不做夹取**（越界位置不许写进权威位置那条约定属 task04 / task08）。
        """
        self._base_pos = QPoint(int(x), int(y))
        self.move(self._base_pos)

    def showing_at_default_position(self) -> None:
        """首次显示：贴屏幕右下角，右下各留 ``DEFAULT_MARGIN`` 逻辑单位。

        用**整块屏幕**的 ``geometry()``（而不是排除任务栏的 ``availableGeometry()``），
        因为附录 C 的"默认落点边距 24 逻辑单位"是从**屏幕边缘**量起的，
        旧实现（``dotPet``）用的也是整屏高度。
        """
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            self.move_to(0, 0)
            return

        area = screen.geometry()
        self.move_to(
            area.x() + area.width() - self.width() - DEFAULT_MARGIN,
            area.y() + area.height() - self.height() - DEFAULT_MARGIN,
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
            # 帧图未标注 DPR（见 pet_loader.load_frame_image），按原尺寸摆放。
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
