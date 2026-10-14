"""全局光标监视：轮询 `QCursor.pos()`，判断"鼠标**是不是真的**动了"。

## 为什么必须轮询

鼠标在**宠物窗口外面**移动时，窗口收不到任何事件（`enterEvent` / `mouseMoveEvent`
都得先有窗口），而久置判定要按**全屏鼠标活动**算。全屏鼠标活动**没有信号可订阅**，
所以 `QCursor.pos()` 的轮询是必要的（agent.md §4.8）。

## 但"计时"不在这里

本模块**只负责一件事**：把"光标动了"这个事实变成信号。计时与阶段升级全部由
`idle_state.IdleStateMachine` 用 `QTimer` 表达——**绝不能**把计时写成
"每个动画帧减一次"：窗口隐藏后精灵停帧、帧回调不再触发，那样计时就永久停住了
（agent.md §4.8 点名的那条）。

## 两条判定规则

1. **位移平方 > `MOUSE_MOVE_EPSILON²`（2 px）才算动了**：光标静止时系统仍可能
   报出 1 px 级别的抖动，那不叫"用户动了鼠标"；
2. **第一次看到光标按"没动"处理**：程序刚启动时的那一次读数不是"移动"，
   否则启动瞬间就会被当成用户活动。
"""

import logging
from typing import Final

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QCursor

from .idle_state import MOUSE_MOVE_EPSILON_SQ

logger = logging.getLogger(__name__)

#: 轮询间隔（毫秒）。150 ms 对"人停下手"这件事足够灵敏，开销也可以忽略
#: （一次 `GetCursorPos` 是系统调用，跨进程/窗口都不需要）。
DEFAULT_INTERVAL_MS: Final[int] = 150


class CursorWatch(QObject):
    """独立 `QTimer` + 自己记的"上次位置"。

    **不依赖窗口的 `enterEvent` / `leaveEvent`**：那两个只在光标进出宠物窗口时才有，
    而这里要的是"整个屏幕上光标动没动"。

    位置**自己记**（`last_pos`），于是"两次读数之间有没有动"这件事与窗口毫无关系。
    """

    #: 光标真的动了（位移平方超过阈值）。**首帧不发**。
    moved = Signal()

    def __init__(
        self,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setInterval(max(1, int(interval_ms)))
        # 轮询不需要精确节拍（隔几次多睡几毫秒完全不影响"手停了多久"），
        # 但也不该用 CoarseTimer 那样被 15.6 ms 时钟节拍放大得太离谱——
        # 这里保留默认的 CoarseTimer：150 ms 的轮询对节拍不敏感（agent.md §4.9
        # 那条 PreciseTimer 的坑针对的是 33 ms 的动画间隔，不是这里）。
        self._timer.timeout.connect(self._on_timeout)
        #: 上一次看到的光标位置；`None` = **还没看过**（首帧按"没动"处理）。
        self._last_pos = None
        #: 统计（自检与诊断用，不影响行为）。
        self.polls = 0
        self.moves = 0

    # ------------------------------------------------------------ 只读

    @property
    def interval_ms(self) -> int:
        """轮询间隔（毫秒）。"""
        return self._timer.interval()

    @property
    def running(self) -> bool:
        """轮询是否在跑（退出时必须是 `False`）。"""
        return self._timer.isActive()

    @property
    def last_pos(self):
        """上一次看到的光标位置（`QPoint` 或 `None`）。"""
        return self._last_pos

    def poll_once(self) -> bool:
        """读一次光标并判定；返回**这一次是否算"动了"**。

        `QTimer` 的 tick 走这里，自检也可以直接调它（不必等真实时间）。
        **永不抛异常**：读不到光标（平台不给）时按"没动"处理，
        静置行为照常工作，只是永远不会自动张望。
        """
        try:
            position = QCursor.pos()
        except Exception:  # pragma: no cover - 平台异常，退化即可
            logger.debug("读光标位置失败，本次按没动处理", exc_info=True)
            return False

        self.polls += 1
        previous = self._last_pos
        self._last_pos = position

        if previous is None:
            # **首帧**：第一次看到光标不等于"光标动了"（§4.8）。
            return False

        dx = int(position.x()) - int(previous.x())
        dy = int(position.y()) - int(previous.y())
        if dx * dx + dy * dy <= MOUSE_MOVE_EPSILON_SQ:
            return False

        self.moves += 1
        self.moved.emit()
        return True

    # ------------------------------------------------------------ 起停

    def start(self) -> None:
        """开始轮询（**立刻先读一次**，把基准位置记下来）。"""
        if self._last_pos is None:
            self.poll_once()
        self._timer.start()

    def stop(self) -> None:
        """停止轮询（退出时必须调；停掉之后没有任何残留的定时器）。"""
        self._timer.stop()

    def reset(self) -> None:
        """忘掉上次位置：下一次读数重新按"首帧"处理（**不算动了**）。"""
        self._last_pos = None

    # ------------------------------------------------------------ 内部

    def _on_timeout(self) -> None:
        self.poll_once()
