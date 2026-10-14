"""久置行为状态机：鼠标一停就张望，再等一会儿休息（**纯逻辑，不依赖 Qt 窗口**）。

## 三个阶段与升级规则

| 阶段 | 动作名 | 进入条件 |
| --- | --- | --- |
| `Stage.IDLE` | `idle` | 鼠标动过 / 刚启动 |
| `Stage.WAITING` | `waiting` | 静置满 `T` 秒（`T` = 配置 `idle_seconds`） |
| `Stage.REST` | `rest` | 在 `WAITING` 上再静置满 `S = max(REST_MIN_S, T × REST_RATIO)` 秒 |

**阶段名与动作名同形**（`idle` / `waiting` / `rest`），因为镜像记忆是按**动作名**存的
（`config.MIRROR_STATES`）——两套词汇会立刻分叉。

## `idle_seconds` 每次判定现读

构造时**不快照**：`idle_seconds_provider` 是个回调，每次要判定时才调一次，
于是"改 `config.json` → 不用重启，下一次判定就用新值"是天然成立的
（task07 §2 第 4 条）。值坏掉时由回调负责兜底（`app` 那边读配置 + 夹下限）。

## 两个例外：计时归零，但**不强行切动作**

1. **主动坐姿（`manual`）**：用户点菜单让它坐下，鼠标怎么动、等多久都不该把它拽起来；
2. **拖动中**（`set_suspended(True)`）：动作控制权在拖动逻辑手里，
   这里若切一下动作，画面会闪一下。

两条的共同点是"计时照常归零、动作交给别人"。**本 task 只做 ①**（② 的接线在 task08，
但接口在这里）。

## 计时基准是**单调时钟**

`on_tick()` 接受调用方给的时间（自检可以喂假时间），默认取 `time.monotonic()`。
不用 `time.time()`：系统时间被改（对时 / 用户改表）会让久置计时跳来跳去。
"""

import logging
import time
from enum import Enum
from typing import Callable, Final

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

#: 位移平方的阈值（px²）：**2 px**。与平方比较，省一次开方。
#: 1 px 的抖动（光标静止时系统仍可能报出 1 px 的位移）不算"动了"。
MOUSE_MOVE_EPSILON: Final[int] = 2
MOUSE_MOVE_EPSILON_SQ: Final[int] = MOUSE_MOVE_EPSILON * MOUSE_MOVE_EPSILON

#: 休息等待的**下限**（秒）。用户把久置时间设成 5 秒时，
#: `5 × 0.5 = 2.5` 太短，用这个下限顶住。
REST_MIN_S: Final[float] = 5.0

#: 休息等待 = 久置时间 × 这个比例。**用户只看到一个"久置时间"**，
#: 休息必须由它推导出来，不许再冒出一个用户没见过的配置项。
REST_RATIO: Final[float] = 0.5

#: `idle_seconds` 读不出来时的兜底（秒）。配置坏了不能让静置行为失效。
DEFAULT_IDLE_SECONDS: Final[float] = 20.0

#: `idle_seconds` 的**下限**（秒）：设成 0 / 负数都按 1 秒算。
MIN_IDLE_SECONDS: Final[float] = 1.0


class Stage(Enum):
    """姿势阶段。**取值与动作名同形**（便于按状态记忆镜像）。"""

    IDLE = "idle"
    WAITING = "waiting"
    REST = "rest"

    @property
    def action(self) -> str:
        """本阶段对应的动作名（与 `value` 相同，写出来是为了让调用处的意图清楚）。"""
        return self.value


def normalize_idle_seconds(raw: object) -> float:
    """把配置里的 `idle_seconds` 归一成"能用的秒数"。**永不抛异常。**

    - 非数字 / `bool` / NaN / ±inf → `DEFAULT_IDLE_SECONDS`（20 秒）；
    - 数值 → 夹到下限 `MIN_IDLE_SECONDS`（1 秒）。设成 0 / -10 都按 1 秒算——
      **下限夹取**而不是回退默认值：用户写 0 的意思显然是"很快就张望"。

    `bool` 必须单独挡掉：`True` 在 Python 里是 `int` 的子类，不挡就会变成 1 秒。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_IDLE_SECONDS
    value = float(raw)
    # NaN 与自身不相等；±inf 超过任何合理量程。两者都按"读不出来"处理。
    if value != value or value in (float("inf"), float("-inf")):
        return DEFAULT_IDLE_SECONDS
    return max(MIN_IDLE_SECONDS, value)


def rest_wait_seconds(idle_seconds: float) -> float:
    """休息等待 `S = max(REST_MIN_S, T × REST_RATIO)`（**唯一实现**）。"""
    return max(REST_MIN_S, float(idle_seconds) * REST_RATIO)


class IdleStateMachine(QObject):
    """久置计时 + 阶段升级 + 手动休息（`manual`）与挂起。

    时间由外部喂进来（`on_tick(now)` / `on_mouse_moved(now)`），本类**自己不排定时器**：
    定时器属于"怎么驱动"，阶段判定属于"逻辑"，两者分开本类才能在自检里
    用纯假时间跑完整条时间线（不必真的等 20 秒）。
    """

    #: 阶段变化（`old` / `new` 都是 `Stage`）。上层据此切动作。
    stage_changed = Signal(object, object)
    #: **只归零计时、不切动作**时发出（与 `stage_changed` 分开，task07 §4）。
    #: 适用场景就是"两个例外"：主动坐姿与拖动中。
    timer_reset = Signal()

    def __init__(
        self,
        idle_seconds_provider: Callable[[], object],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._idle_seconds_provider = idle_seconds_provider
        self._stage = Stage.IDLE
        self._manual = False
        self._suspended = False
        self._next_at: float | None = None
        self._clock = time.monotonic
        #: 时间基准的起点。**构造期的"当前时刻"是 `None`**：真正的值在第一次
        #: 调用时由 `_now()` 取（外部喂假的单调时刻时，这里不会被真实时钟污染）。
        self._last_call: float | None = None

    # ------------------------------------------------------------ 只读

    @property
    def stage(self) -> Stage:
        """当前阶段。"""
        return self._stage

    @property
    def manual(self) -> bool:
        """是否处于主动坐姿（右键菜单点出来的那次休息）。"""
        return self._manual

    @property
    def suspended(self) -> bool:
        """动作控制权是否被外部占用（拖动中；task08 会打开它）。"""
        return self._suspended

    def idle_seconds(self) -> float:
        """**现读**一次配置里的久置时间（秒），坏值走兜底与下限。"""
        raw = self._idle_seconds_provider()
        return normalize_idle_seconds(raw)

    def rest_seconds(self) -> float:
        """当前久置时间对应的休息等待（秒）。"""
        return rest_wait_seconds(self.idle_seconds())

    @property
    def next_change_at(self) -> float | None:
        """下一次阶段升级的单调时刻；`None` = 当前不会自动升级（`manual` / 挂起）。"""
        return self._next_at

    def remaining_seconds(self, now: float | None = None) -> float | None:
        """距下一次升级还有多少秒；没有下一次则为 `None`（诊断与自检用）。"""
        if self._next_at is None:
            return None
        return max(0.0, self._next_at - (self._clock() if now is None else now))

    # ------------------------------------------------------------ 输入

    def on_mouse_moved(self, now: float | None = None) -> None:
        """鼠标动了：**计时归零**，并从 `WAITING` / `REST` 站起来回 `IDLE`。

        - 已经在 `IDLE`：只归零（发 `timer_reset`），不发 `stage_changed`
          ——"只归零、不切动作"就是这个信号存在的理由；
        - `manual` / 挂起中：**照常归零**，但**不切动作**（两个例外，
          归零是为了"退出 `manual` 时从这一刻重新等"，不是为了让这里切动作）。
        """
        current = self._now(now)
        self._rearm(current)
        if self._manual or self._suspended:
            self.timer_reset.emit()
            return
        if self._stage is Stage.IDLE:
            self.timer_reset.emit()
            return
        self._set_stage(Stage.IDLE, current)

    def on_tick(self, now: float | None = None) -> None:
        """计时推进：到点就升级阶段（`IDLE → WAITING → REST`）。

        **时间由外部给**（`app` 用 `QTimer` 每 `TICK_MS` 喂一次单调时钟）。
        到点时按绝对时刻重排下一个期限，所以"每 150 ms 问一次"不会累积漂移。
        """
        current = self._now(now)
        if self._manual or self._suspended or self._next_at is None:
            return
        if current < self._next_at:
            return

        if self._stage is Stage.IDLE:
            self._set_stage(Stage.WAITING, current)
        elif self._stage is Stage.WAITING:
            self._set_stage(Stage.REST, current)
        else:
            # 已经在 REST：没有更后面的阶段，只是把期限推到"很远"
            # （实际到不了这里——`_set_stage` 在 REST 上已经把 `_next_at` 置空）。
            self._next_at = None

    def enter_manual(self, now: float | None = None) -> bool:
        """进入主动坐姿：切到 `REST` 并**停掉计时**，返回阶段是否真的变了。

        已经是 `REST`（例如久置自动休息中）再点菜单 → 阶段不变（返回 `False`），
        但 `manual` 这个开关照样打开：用户的意思是"我要它一直坐着"。
        """
        current = self._now(now)
        self._manual = True
        self._next_at = None
        if self._stage is Stage.REST:
            return False
        self._set_stage(Stage.REST, current)
        return True

    def exit_manual(self, now: float | None = None) -> bool:
        """退出主动坐姿：站起来回 `IDLE` 并**重新计时**。

        返回阶段是否真的变了（本函数幂等：`manual` 为假时什么都不做）。
        """
        if not self._manual:
            return False
        current = self._now(now)
        self._manual = False
        self._rearm(current)
        if self._stage is Stage.IDLE:
            return False
        self._set_stage(Stage.IDLE, current)
        return True

    def set_suspended(self, suspended: bool, now: float | None = None) -> None:
        """挂起 / 恢复：**只暂停自动升级，不切动作**（例外的第 ② 条，task08 接线点）。

        挂起 = 拖动开始（动作控制权交给拖动逻辑）；恢复 = 拖动结束——
        恢复时**计时从零开始重新等**，否则刚松手就立刻张望，
        用户会觉得"它好像一直在等着我松手"。
        """
        wanted = bool(suspended)
        if wanted == self._suspended:
            return
        self._suspended = wanted
        current = self._now(now)
        if wanted:
            self._next_at = None
            self.timer_reset.emit()
        else:
            self._rearm(current)
            self.timer_reset.emit()

    # ------------------------------------------------------------ 内部

    def _now(self, now: float | None) -> float:
        """本次调用的时间基准。

        `now=None` 时读单调时钟，并保证**不比上一次调用更早**：
        同一个 tick 里 `on_mouse_moved()` 之后紧跟着一次 `on_tick()`
        会读到相同（甚至理论上略小）的时刻，让它退回上一次的值更安全。
        """
        current = self._clock() if now is None else float(now)
        if self._last_call is not None and current < self._last_call:
            current = self._last_call
        self._last_call = current
        # 第一次被问到时刻 = 久置计时的起点：把第一个期限钉下来。
        # 没有这一步，刚构造出来的状态机 `_next_at` 是 `None`，
        # `on_tick()` 会一直"没有下一次"而**永远不升级**。
        if self._next_at is None and not self._manual and not self._suspended:
            if self._stage is Stage.IDLE:
                self._next_at = current + self.idle_seconds()
        return current

    def _rearm(self, current: float) -> None:
        """按**当前阶段**重排下一个期限（`manual` / 挂起时为"没有下一次"）。"""
        if self._manual or self._suspended:
            self._next_at = None
            return
        if self._stage is Stage.IDLE:
            self._next_at = current + self.idle_seconds()
        elif self._stage is Stage.WAITING:
            self._next_at = current + self.rest_seconds()
        else:
            self._next_at = None

    def _set_stage(self, stage: Stage, current: float) -> None:
        """改阶段 + 重排期限 + 发信号（**唯一**改 `_stage` 的地方）。"""
        previous = self._stage
        self._stage = stage
        self._rearm(current)
        logger.debug("久置阶段：%s → %s", previous.value, stage.value)
        self.stage_changed.emit(previous, stage)
