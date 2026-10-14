"""拖动状态机：基准位置 + 鼠标偏移 + "上次方向 → 动作名"的**纯函数**。

## 三条硬规则（task08 §4 / agent.md §4.6）

1. **禁止回读窗口实时坐标**（`pos()` / `geometry()`）：窗口位置是**异步生效**的，
   回读会拿到旧值，表现就是"拖动只走一小截"。位移一律用
   `base_pos + (当前全局坐标 − 按下时的全局坐标)`。
2. **位移基准是按下那一刻的位置**，`update()` 的参数是**相对按下点的累计位移**
   （不是相邻两次移动的增量）。按增量累加会把窗口越拖越飞。
3. **不设速度阈值**：切跑动动作只看水平位移的**符号**。加阈值会让慢速拖动
   时动作来回闪（"11.0 fps 的跑动"那种观感问题不是这里能修的）。

## 为什么 `direction_action()` 是纯函数

"往哪边跑"只取决于 `dx` 的**符号**与上次的方向，没有任何状态可变。
把它写成纯函数，自检就能直接摆事实（`+1` / `-1` / `0` 三组输入），
不必造窗口、不必模拟鼠标。

## 纯竖直移动沿用上次方向

鼠标上下移动时 `dx == 0`，这时**不切换动作**（返回 `None` 表示"不用改"），
沿用上次的跑动方向。完全没有方向依据时（`last=None` 且 `dx == 0`）
以 `running_right` 起跑——与"状态机第一阶段是 idle"一样，是个明确的缺省，
不是猜的（agent.md §4.6 明写"以朝右起跑"）。

## 本模块**不依赖 Qt 窗口**

只用 `QPoint`（纯值对象）。拖动逻辑不碰窗口，所以自检能在 offscreen 下
把整条拖动时间线跑完（不依赖真鼠标）。
"""

from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, QPoint, Signal

#: 左右跑动的动作名（**真值是素材索引里的动作名**，2025 定案下划线写法）。
RUNNING_LEFT: Final[str] = "running_left"
RUNNING_RIGHT: Final[str] = "running_right"

#: 没有方向依据时的起跑方向（agent.md §4.6："以朝右起跑"）。
DEFAULT_DIRECTION: Final[str] = RUNNING_RIGHT


def direction_action(dx: int, last: str | None) -> str | None:
    """由水平位移 ``dx`` 决定该播哪个跑动动作。

    返回 ``None`` 表示"**方向没有依据，沿用上次**"（调用方什么都不用做）：

    - ``dx > 0`` → ``running_right``；
    - ``dx < 0`` → ``running_left``；
    - ``dx == 0``（纯竖直移动）→ ``last``；``last`` 也是 ``None`` 时
      返回 ``None``，让调用方按 `DEFAULT_DIRECTION` 起跑。

    **只看符号，不看大小**：任何速度阈值都会让慢速拖动时动作来回闪
    （agent.md §4.6 明写"不设速度阈值"）。
    """
    if dx > 0:
        return RUNNING_RIGHT
    if dx < 0:
        return RUNNING_LEFT
    return last


@dataclass
class DragState:
    """一次拖动过程中要记住的三件事（**权威位置由调用方持有**）。

    - ``base_pos``：按下那一刻的**窗口左上角**（权威位置，不是回读来的）；
    - ``grab_offset``：鼠标在窗口内的偏移（`按下点 − 窗口左上角`）；
    - ``last_direction``：上次切到的跑动动作名，供纯竖直移动沿用。

    ``grab_offset`` 记下来是为了"**快速来回拖不丢偏移**"这条验收：
    即便窗口跟随时因为夹取没能移到理论位置，下一次也用同一个偏移重算，
    不会出现"宠物慢慢滑到指针下面"。
    """

    #: 按下那一刻的窗口左上角（屏幕逻辑坐标）。
    base_pos: QPoint = QPoint(0, 0)
    #: 鼠标在窗口内的偏移（`按下点 − 窗口左上角`，窗口内逻辑坐标）。
    grab_offset: QPoint = QPoint(0, 0)
    #: 按下时的**全局**坐标（屏幕逻辑坐标）。
    press_global: QPoint = QPoint(0, 0)
    #: 上次切到的跑动动作名。
    last_direction: str | None = None
    #: 最近一次 `update()` / `begin()` 算出的目标位置（`finish()` 的返回值来源）。
    last_target: QPoint = QPoint(0, 0)

    def begin(self, local_pos: QPoint, global_pos: QPoint, window_pos: QPoint) -> None:
        """开始拖动。``window_pos`` 是**调用方持有的权威位置**（不是回读的）。"""
        self.base_pos = QPoint(window_pos)
        self.press_global = QPoint(global_pos)
        self.grab_offset = QPoint(
            int(local_pos.x()) - int(window_pos.x()),
            int(local_pos.y()) - int(window_pos.y()),
        )
        self.last_target = QPoint(window_pos)

    def target_position(self, global_pos: QPoint) -> QPoint:
        """按"**按下时的基准位置 + 累计位移**"算出理论上的新左上角。

        **不回读窗口坐标**：位移 = `当前全局坐标 − 按下时的全局坐标`。
        """
        delta = QPoint(global_pos) - self.press_global
        return QPoint(
            self.base_pos.x() + delta.x(),
            self.base_pos.y() + delta.y(),
        )

    def update(self, global_pos: QPoint) -> tuple[QPoint, str | None]:
        """算出新位置与该播的动作。返回 ``(新位置, 动作名或 None)``。

        动作名 ``None`` = "方向没有依据，沿用上次"（**不是**"不用播动作"）：
        调用方自己决定要不要切回 `idle`——那属于"拖动是不是还在进行"的判断，
        不在本函数里。
        """
        delta_x = int(global_pos.x()) - self.press_global.x()
        action = direction_action(delta_x, self.last_direction)
        if action is not None:
            self.last_direction = action
        target = self.target_position(global_pos)
        self.last_target = QPoint(target)
        return target, action

    def finish(self) -> QPoint:
        """松手：返回**最终位置**（调用方负责夹取 + 写配置）。

        最终位置 = 最后一次 `update()` 的目标位置，也就是
        `base_pos + 累计位移`；"只点了一下没拖动"时就是 `base_pos` 本身
        ——那种情况**照样要发一次完整的位置校正通知**（agent.md §4.6：
        处理"拖到边缘被夹住、位置没变"）。**不在本函数里夹取**：
        夹取属"摆放策略"，必须发生在写进权威位置之前（agent.md §4.5）。
        """
        return QPoint(self.last_target)


class DragController(QObject):
    """拖动的**信号出口**：开始 / 结束（供控制栏跟随与静置逻辑挂起使用）。

    task08 §4 要求发出 ``drag_started`` / ``drag_finished(pos)`` 两个信号：
    task09 的控制栏用它做增量跟随，久置状态机用它挂起 / 恢复动作控制权。
    **本类只带信号与状态**,窗口那一侧的鼠标事件仍写在 `pet_window.py`
    （事件处理器不能搬走），两者靠 `PetWindow` 里的一个 `DragController` 实例连起来。
    """

    #: 开始拖动（按下并通过命中判定）。
    drag_started = Signal()
    #: 结束拖动，参数是**夹取后**的最终位置（屏幕逻辑坐标）。
    drag_finished = Signal(QPoint)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: 当前这一次拖动的状态（`begin()` 之前的值没有意义）。
        self.state = DragState()
        #: 按下之后、松手之前为真（"拖动途中双击被忽略"看的就是它）。
        self.active = False

    def begin(self, local_pos: QPoint, global_pos: QPoint, window_pos: QPoint) -> None:
        """按下：进入拖动并发 ``drag_started``。**调用方负责先过命中判定**。"""
        self.state.begin(local_pos, global_pos, window_pos)
        self.active = True
        self.drag_started.emit()

    def update(self, global_pos: QPoint) -> tuple[QPoint, str | None]:
        """移动：返回 `(新位置, 该播的动作或 None)`。"""
        return self.state.update(global_pos)

    def finish(self, final_pos: QPoint) -> None:
        """松手：退出拖动并发 ``drag_finished``（参数是**夹取后**的位置）。"""
        self.state.last_target = QPoint(final_pos)
        self.active = False
        self.drag_finished.emit(QPoint(final_pos))
