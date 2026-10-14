"""尺寸控制栏：一条悬浮条（`1.00x（6/10）  [−] [+] [×]`）。

## 为什么是**独立顶层窗口**而不是画在宠物画布上

宠物窗口的透明区是**真的穿透**（`setMask`，agent.md §0）——把控制栏画进去，
它的背景区就会把点击漏到底下那个窗口，按钮按不动（task09 §5 明写"不做"）。
所以这里是一个 `Qt.Tool` 顶层窗口，跟宠物各活各的。

## 三个窗口标志一个都不能少

| 标志 | 少了会怎样 |
| --- | --- |
| `Qt.Tool` | 会出现在任务栏里，关掉宠物后它还在那儿 |
| `Qt.FramelessWindowHint` | 戴上标题栏，变成一条"小窗" |
| `Qt.WindowStaysOnTopHint` | 宠物是置顶的，控制栏会掉到宠物后面 |

⚠ 另加一条**必须显式设置**的：`Qt.WA_QuitOnClose`。Qt 不为 `Qt.Tool` 窗口
设这个属性，而 `Qt.Tool` **不计入**"最后一个主窗口"，于是关掉它之后
`app.exec()` 不返回（agent.md §4.9 第一条坑，task01 实测过）。

## "缩放时按钮不动"是怎么实现的

**不在本文件里**。本文件只保证一件事：**每次换档按钮的窗口内坐标完全不变**
（布局是固定的，文字由 `set_level()` 刷新，长度变化不会撑动布局）。

真正让"屏幕坐标"也不变的是**底部边距机制**（`app.py` + `PetWindow`）：
打开控制栏时把宠物整体上移 `RESERVED`，宠物**底边固定**，而控制栏贴的是
宠物的底边 → 切档时宠物向**左上**长大（右下角锚点不动，§2.12），
底边不动 → 控制栏一动不动。**用户连点 `+` 一路到 10 级不用追鼠标。**

## 定位：右边缘与宠物**右边缘**对齐（不是水平居中）

`place_relative_to()` 的规则是 task09 §4 的"定位 / 兜底位置"两条：

1. 优先宠物**下方**（`pet.bottom + GAP`）；
2. 下方放不下 → 宠物**上方**（仍锚定右侧）；
3. 上方也放不下 → 退化为**贴屏幕上下缘**。

三种情况**都保持右对齐**，这样切档时（宠物右边固定）栏不会横向跑。
"""

from typing import Final

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QGuiApplication, QShowEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import size

#: 控制栏高度（逻辑单位）。**真值只在这里一处**（task09 §4）。
BAR_HEIGHT: Final[int] = 40

#: 控制栏与宠物之间的间距（逻辑单位）。
GAP: Final[int] = 8

#: 控制栏需要向屏幕"预留"的总高度：栏高 + 间距 + 4。
#:
#: 那个 **+4** 是余量：任务是"贴屏幕底边"的，`QRegion` 遮罩的锯齿与
#: 任务栏上沿的观感都会让"刚好贴着"看起来像压住了（agent.md §4.10 记着
#: 缺省落点本来就会压在任务栏上沿约 24 逻辑单位）。留 4 个逻辑单位缓冲。
RESERVED: Final[int] = BAR_HEIGHT + GAP + 4

#: 档位文字的字号（逻辑单位，px）。比默认略大，40 高的栏里看着才不空。
LABEL_FONT_PX: Final[int] = 15

#: 三个按钮的固定宽度（逻辑单位）。
#:
#: **必须固定**：`−` / `×` 这类单字符按钮在各平台的默认尺寸不一致，
#: 让布局自己算会出现"换个字号栏就变宽"，而"缩放时按钮不动"这条验收
#: 要求栏的横向布局**完全稳定**。
BUTTON_WIDTH: Final[int] = 30

#: 按钮文字。`−` 是 U+2212（**减号**，不是 U+002D 的连字符）、
#: `×` 是 U+00D7（**乘号**，不是字母 x）——task09 §6 要求"不是方框"，
#: 这两个都在常用字体（Segoe UI / 微软雅黑）里有字形，实测正常。
#: 若将来换字符，**附录 B 与 agent.md 要同步**（task09 §6 最后一条）。
TEXT_LEVEL_DOWN: Final[str] = "\u2212"
TEXT_LEVEL_UP: Final[str] = "+"
TEXT_CLOSE: Final[str] = "\u00d7"


class ControlBar(QWidget):
    """尺寸控制栏（独立顶层窗口）。**幂等由调用方保证**（同一个实例反复 `show()`）。"""

    #: 用户点了 `+`（加一档）。**不夹取**：边界判定在调用方（`app._on_level_step`），
    #: 因为"到边界返回没有变化"这件事要连着"不写配置"一起判（task09 §4）。
    level_up = Signal()
    #: 用户点了 `−`（减一档）。
    level_down = Signal()
    #: 用户点了 `×`（关闭控制栏）。
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: 当前档位（`set_level()` 写；`level` 属性读）。
        self._level = size.DEFAULT_LEVEL
        #: 当前总数（同上；保存在这里是为了 `level_text` 不用调用方每次都给）。
        self._total = size.LEVEL_COUNT

        self._setup_window()
        self._build_layout()

    # ------------------------------------------------------------ 装配

    def _setup_window(self) -> None:
        """窗口标志与属性（见模块开头那张表）。"""
        self.setWindowFlags(
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        # ⚠ 必须显式打开：Qt 不为 `Qt.Tool` 设 `WA_QuitOnClose`，而 `Qt.Tool`
        # 不计入"最后一个主窗口" → 关掉之后 `app.exec()` 不返回（agent.md §4.9）。
        self.setAttribute(Qt.WA_QuitOnClose, True)
        self.setWindowTitle("调整大小")
        # 栏自己是不透明的深色底：它是"控件"不是"人物"，不需要透明背景。
        self.setFixedHeight(BAR_HEIGHT)

    def _build_layout(self) -> None:
        """`QHBoxLayout`：档位文字 + `−` + `+` + `×`（**不用绝对坐标摆放控件**）。

        窗口**自身的位置**才由代码算（`place_relative_to()` / `follow()`）——
        那是窗口管理，不是布局（task09 §4 布局约定）。
        """
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self.level_label = QLabel(size.level_text(self._level, self._total), self)
        self.level_label.setAlignment(Qt.AlignCenter)
        # 固定字号：让文字长度只随"档位/总数"变化，不随系统设置跳。
        font = self.level_label.font()
        font.setPixelSize(LABEL_FONT_PX)
        self.level_label.setFont(font)
        # 给文字一个最小宽度，免得第 1 级（`0.25x（1/10）`）比第 10 级窄
        # 而让按钮横向挪动一点点。
        self.level_label.setMinimumWidth(96)

        self.button_down = self._make_button(TEXT_LEVEL_DOWN, self.level_down.emit)
        self.button_up = self._make_button(TEXT_LEVEL_UP, self.level_up.emit)
        self.button_close = self._make_button(TEXT_CLOSE, self.closed.emit)

        layout.addWidget(self.level_label)
        layout.addWidget(self.button_down)
        layout.addWidget(self.button_up)
        layout.addWidget(self.button_close)

        # 布局定死后**不要**再让窗口自己改尺寸（`sizeHint` 随文字变就会挪按钮）。
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def _make_button(self, text: str, handler) -> QPushButton:
        """造一个固定宽度的按钮（宽度固定的理由见 `BUTTON_WIDTH`）。"""
        button = QPushButton(text, self)
        button.setFixedWidth(BUTTON_WIDTH)
        button.setFixedHeight(BAR_HEIGHT - 8)
        button.setFocusPolicy(Qt.NoFocus)  # 别抢宠物的焦点（Esc 退出要留给它）
        button.clicked.connect(handler)
        return button

    # ------------------------------------------------------------ 对外

    @property
    def level(self) -> int:
        """当前显示的档位（只读）。"""
        return self._level

    def set_level(self, level: int, total: int | None = None) -> None:
        """刷新文字与两个按钮的**禁用态**（边界置灰）。

        到第 1 级 → `−` `setEnabled(False)`；到第 10 级 → `+` `setEnabled(False)`
        （task09 §4"边界置灰"，用**禁用**而不是隐藏，位置才稳定）。

        本函数**不改窗口尺寸**：`setFixedSize()` 已经在布局期定死，
        文字长度变化不会挪动任何按钮——这正是"缩放中按钮不动"的前提。
        """
        self._level = size.clamp_level(level)
        self._total = int(total) if total is not None else size.LEVEL_COUNT
        self.level_label.setText(size.level_text(self._level, self._total))
        self.button_down.setEnabled(self._level > 1)
        self.button_up.setEnabled(self._level < self._total)

    def follow(self, dx: int, dy: int) -> None:
        """按**增量**平移自己的位置（由拖动信号驱动，task09 §4"跟随"）。

        **禁止重算锚点**：拖动到屏幕边缘时，"重新按宠物右边算一次"会因为
        夹取结果与理论位置不一致而**越跟越偏**；来回拖还会抖。
        增量平移的口径是"宠物动了多少，栏就动多少"，天然不会累积误差。
        """
        if dx == 0 and dy == 0:
            return
        self.move(self.x() + int(dx), self.y() + int(dy))

    def place_relative_to(self, pet_rect: QRect, screen_rect: QRect) -> None:
        """把栏摆到宠物**下方**（优先）/ **上方** / **贴屏幕缘**，**右边缘对齐**。

        `pet_rect` / `screen_rect` 都是**屏幕逻辑坐标**的矩形
        （`QRect(x, y, w, h)`），直接来自 `PetWindow.base_position` 与
        `QScreen.geometry()`——**不要**传窗口内坐标（agent.md §4.9 的"坐标两种"）。

        右对齐用 `pet_rect.right() - width()`：`QRect.right()` 是 `x + w - 1`，
        所以再加 1 才是"右边缘"。
        """
        width = self.width()
        height = self.height()
        left = pet_rect.right() + 1 - width
        # 右对齐后仍可能超出屏幕（宠物贴左/右缘时）→ 夹回屏幕内。
        left = max(screen_rect.left(), min(screen_rect.right() + 1 - width, left))

        below = pet_rect.bottom() + 1 + GAP
        above = pet_rect.top() - GAP - height

        if below + height <= screen_rect.bottom() + 1:
            top = below  # ① 宠物下方
        elif above >= screen_rect.top():
            top = above  # ② 宠物上方
        else:
            # ③ 上下都放不下（宠物比屏幕还高）：夹到屏幕内，
            # 优先贴上缘——任务是"贴屏幕上下缘"，贴哪个都行，选上缘更稳定。
            top = max(screen_rect.top(), min(screen_rect.bottom() + 1 - height, below))
        self.move(left, top)

    def screen_rect(self) -> QRect:
        """栏**当前所在屏幕**的矩形（逻辑单位）；拿不到 → 空矩形。

        多显示器时 `primaryScreen()` 会算错，所以按栏自己的位置问屏幕。
        本阶段只有一块屏，但口径现在就统一，免得将来散落好几处。
        """
        return screen_rect_for(self)

    # ------------------------------------------------------------ 事件

    def showEvent(self, event: QShowEvent) -> None:
        """显示时不做额外的事——留着这个覆写是为了说明**为什么不做**：

        首次 `show()` 之前 `self.screen()` 可能返回 `None`，但真实使用里
        栏总是跟在宠物之后显示，那时屏幕已经确定了。`app.py` 在 `show()`
        **之后**立刻调一次 `place_relative_to()`，位置就不依赖这里的时机。
        """
        super().showEvent(event)


def screen_rect_for(widget: QWidget) -> QRect:
    """`widget` 当前所在屏幕的矩形（逻辑单位）；拿不到 → 空矩形。

    放在本模块是为了让 `app.py` 与自检用**同一个**取屏幕的口径，
    不必各自写一遍 `screen() or primaryScreen()`（那种重复迟早分叉）。
    """
    screen = widget.screen() or QGuiApplication.primaryScreen()
    if screen is None:  # pragma: no cover
        return QRect(0, 0, 0, 0)
    return QRect(screen.geometry())


def pet_rect_of(window: QWidget, width: int, height: int) -> QRect:
    """宠物的屏幕矩形。

    ⚠ **宽度 / 高度必须由调用方传**（用 `PetWindow.pixel_size`），
    **不要回读 `window.geometry()`**：窗口坐标是异步生效的，切档刚发生
    时回读到的是旧尺寸（agent.md §4.6 同一条教训），控制栏就会按旧尺寸定位。
    """
    pos: QPoint = window.pos()
    return QRect(int(pos.x()), int(pos.y()), int(width), int(height))
