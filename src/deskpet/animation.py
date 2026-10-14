"""动画：帧推进 + 调度意图（loop / pingpong / oneshot）+ 定格 + 片段序列。

本模块是**纯逻辑**：只有一个 `QObject`（为了 `sequence_done` 信号），
**不碰定时器、不碰窗口、不碰 `QPixmap`**。推进节奏由调用方（`pet_window.AnimationDriver`）
按 `advance()` 的返回值排下一次——于是"哪一帧、下一帧隔多久"这件事能用普通字典在
自检里直接测，不必起窗口、不必等真实时间。

## 播放方式表是**唯一真相源**（task05 §4）

| 动作 | 播放方式 | 结束行为 |
| --- | --- | --- |
| `idle` | 单向循环 | 永不到头 |
| `running_left` / `running_right` | 往返 | 永不到头 |
| `waiting` | 往返 | 永不到头 |
| `rest` | 单向循环 | 永不到头（**不往返**：剪成刚好一圈才连续） |
| `waving` | 一次性 | 播到**末帧即停** |

**表里没有的动作**（例如 `weedy_sightseer` 的第 7 个动作 `温蒂-倾听-基建-Special-x1`）
按 `oneshot` 处理：素材里"有几个动作就是几个"（agent.md §2.5），
本表决定不了的一律当一次性演出，播完就停在末帧。

> `rest` 为什么**不能**用往返：素材是从 GIF 循环剪出来的，末帧接首帧本身连续；
> 往返会让它倒着躺回去。`waving` 为什么是 oneshot：挥手有起手和收手，来回播像抽搐。

## 帧率**只消费、不解析**

`frame_ms` 来自 task03 的 `pet_spec.ActionSpec.frame_ms`（`1000.0 / fps`，浮点不取整）。
本模块**不重复解析、不重复告警**——坏 `fps` 的警告已经在加载层记过一次了
（重复一次会让"一次列全"变成"一次列两遍"）。

## 定格（hold）的表达：`advance()` 返回 `None`

定格 = "不再推进、**也不排下一次定时器**"。调用方拿 `None` 就 `stop()` 定时器，
而不是"定时器照转、每帧什么都不干"——后者既费电又掩盖了 bug。

## 片段序列（`play_sequence`）

片段是 `(动作, 帧区间或 None, 结束方式)`，结束方式 `"auto"` 或 `("loop", N)`：

- `"auto"`：随动作默认行为（一次性动作播到末帧即止；循环动作**永不结束**，
  会一直播下去直到有人 `set_action()`）；
- `("loop", N)`：N 是**完整圈数**。**一圈 = 回到起点帧且方向朝前**，
  往返播放的一圈包含**两个折返点**——按"到达末端"计次会把圈数算成两倍（本模块曾按
  折返点计次并因此多为空循环，是自检里的断言把它挡下来的）。

片段**不改变动作本质**：`play_sequence` **不**写 `_mode`（`Animation.mode` 始终反映
"当前动作默认该怎么播"），片段只临时收窄**帧区间**并接管**何时结束**。
于是同一个动作在片段里的推进方向与它默认的方向一致，不会出现"片段里 idle 倒着放"。
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from PySide6.QtCore import QObject, Signal

from .errors import DeskPetError
from .pet_spec import PetSpec

logger = logging.getLogger(__name__)

#: `dt` 上限（毫秒）。休眠唤醒后定时器会报出一个巨大的间隔，
#: 不夹住就会"睡着前跑 1 帧、醒来直接跳十几帧"（task05 §4 调度）。
MAX_DT_MS: Final[float] = 100.0


class PlayMode(Enum):
    """播放方式（`Animation.mode` 的取值）。"""

    LOOP = "loop"
    PINGPONG = "pingpong"
    ONESHOT = "oneshot"


#: **播放方式表**（唯一真相源，task05 §4）。改这张表就是改行为本身。
PLAY_MODES: Final[dict[str, PlayMode]] = {
    "idle": PlayMode.LOOP,
    "running_left": PlayMode.PINGPONG,
    "running_right": PlayMode.PINGPONG,
    "waiting": PlayMode.PINGPONG,
    "rest": PlayMode.LOOP,
    "waving": PlayMode.ONESHOT,
}

#: 表里没有的动作按哪种方式播（见模块 docstring）。
FALLBACK_MODE: Final[PlayMode] = PlayMode.ONESHOT

#: 正向 / 反向。往返播放靠它折返。
_FORWARD: Final[int] = 1
_BACKWARD: Final[int] = -1


class UnknownActionError(DeskPetError):
    """素材里没有这个动作。**带出可用动作列表**（`app.py` 据它报错）。

    与 `pet_loader.PetLoadError` 一样，属于 agent.md §2.10 第二类里"用户明确要求、
    失败就必须说清楚"的那种：静默换成 `idle` 会让用户以为自己指定成功
    （task05 §2 第 4 条明确要求"不是静默换成 idle"）。
    """

    def __init__(self, namespace: str, action: str, available: Sequence[str]) -> None:
        self.namespace = namespace
        self.action = action
        self.available: tuple[str, ...] = tuple(available)
        listing = "、".join(self.available) or "（一个都没有）"
        super().__init__(
            f"素材包 {namespace} 里没有动作 {action!r}；可用动作：{listing}"
        )


class FrameIndexError(DeskPetError):
    """帧号越界。**报错而不是画空白**（task05 §6："`hold(999)` 报错"）。"""

    def __init__(self, action: str, index: int, frame_count: int) -> None:
        self.action = action
        self.index = index
        self.frame_count = frame_count
        super().__init__(
            f"动作 {action!r} 没有第 {index} 帧（合法范围 0..{frame_count - 1}）"
        )


def mode_for(action: str) -> PlayMode:
    """查播放方式表；表里没有 → `FALLBACK_MODE`（**不抛异常**，见模块 docstring）。"""
    return PLAY_MODES.get(action, FALLBACK_MODE)


@dataclass(frozen=True)
class Segment:
    """片段序列里的一段：动作 + 帧区间 + 结束方式。

    | 字段 | 语义 |
    | --- | --- |
    | `action` | 素材里真实存在的动作名 |
    | `start` / `end` | 帧区间 `[start, end]`；`None` 表示该端取到端点（0 / 末帧） |
    | `loop` | `None` = `"auto"`；`N` = 播完 N 个**完整圈**后结束 |

    `loop` 用的是 `Optional[int]` 而不是"字符串或元组"那种联合类型：
    `("loop", N)` 只是**写法**，落成数据就是 `loop=N`，判断起来不必再拆元组。
    """

    action: str
    start: int | None = None
    end: int | None = None
    loop: int | None = None

    @classmethod
    def coerce(cls, raw: object) -> "Segment":
        """把 `(动作, 区间, 结束方式)` 元组或现成的 `Segment` 变成 `Segment`。

        非法形状 → `ValueError`（**这是编程错误，不是用户输入**，不该继续跑）。
        """
        if isinstance(raw, Segment):
            return raw
        if not isinstance(raw, tuple) or not raw:
            raise ValueError(f"片段必须是 (动作, 区间, 结束方式) 元组，实际是 {raw!r}")

        action = raw[0]
        if not isinstance(action, str) or not action:
            raise ValueError(f"片段的动作名必须是非空字符串，实际是 {action!r}")

        start: int | None = None
        end: int | None = None
        span = raw[1] if len(raw) > 1 else None
        if span is not None:
            if not isinstance(span, tuple) or len(span) != 2:
                raise ValueError(f"片段的帧区间必须是 (起, 止) 二元组，实际是 {span!r}")
            start, end = span
            for value in (start, end):
                if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                    raise ValueError(f"片段的帧号必须是整数或 None，实际是 {value!r}")

        loop: int | None = None
        finish = raw[2] if len(raw) > 2 else "auto"
        if finish == "auto" or finish is None:
            loop = None
        elif isinstance(finish, tuple) and len(finish) == 2 and finish[0] == "loop":
            count = finish[1]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError(f"圈数必须是 >= 1 的整数，实际是 {count!r}")
            loop = count
        else:
            raise ValueError(f"片段的结束方式只能是 'auto' 或 ('loop', N)，实际是 {finish!r}")

        return cls(action=action, start=start, end=end, loop=loop)


class Animation(QObject):
    """帧推进器：**只回答"现在是第几帧、下一帧隔多久"**，不管怎么显示。

    用法（调用方）：

        anim = Animation(spec, "waving")
        anim.frame_ms           # 当前动作的每帧毫秒
        anim.index              # 当前帧号
        next_ms = anim.advance()  # None = 定格，**不要**再排定时器

    `spec` 里的动作**一个都不认识时**（`spec.actions` 为空）也不抛异常：
    此时 `index` 恒为 0，`advance()` 恒返回 `None`（没有帧可推进）。
    """

    #: 片段序列播完。**带数据的信号优先传结构化对象**（agent.md §2.2），
    #: 这里没有负载——"播完了"本身就是全部信息。
    sequence_done = Signal()

    def __init__(
        self,
        spec: PetSpec,
        action: str = "idle",
        fps_override: float | None = None,
    ) -> None:
        super().__init__()
        self._spec = spec
        self._fps_override = _normalize_override(fps_override)

        #: 内部动作名（可能与"素材里真实存在的动作"不同——动作一个都没有时的兜底）。
        self._action = action
        #: 实际交给渲染层的动作名（素材里真实存在的那个）。
        self._render_action = action
        #: 当前动作的每帧毫秒（task03 解析结果，或 `fps_override`）。
        self._frame_ms = _fallback_frame_ms(spec, action)
        #: 当前动作的默认播放方式。
        self._mode = mode_for(action)
        #: 当前帧区间的两端（默认整段）。
        self._first = 0
        self._last = 0
        self._index = 0
        self._direction = _FORWARD
        #: 上一步之前的帧号（判断"这一步到底有没有换帧"用，见 `advance()`）。
        self._frame_before_step = 0
        self._held = False
        self._finished = False
        #: 已完成的圈数（**只有片段序列在数**）。
        self._laps = 0
        #: 当前片段 / 片段队列 + 它是第几段。
        self._segments: list[Segment] = []
        self._segment_cursor = 0
        #: 真正的"换动作"实现（下面会换掉，构造期先记下原本那个）。
        self._apply_action = self._set_action_direct

        self._select_action(action)

    # ------------------------------------------------------------ 只读状态

    @property
    def spec(self) -> PetSpec:
        """当前素材包。"""
        return self._spec

    @property
    def action(self) -> str:
        """当前动作名（**逻辑动作名**，可能是素材里并不存在的兜底名）。"""
        return self._action

    @property
    def render_action(self) -> str:
        """该拿去取帧的动作名（素材里真实存在的那个，逻辑名不存在时的兜底）。"""
        return self._render_action

    @property
    def frame_ms(self) -> float:
        """当前动作的每帧毫秒（**来自 task03 的解析结果**，本模块不重复解析）。"""
        return self._frame_ms

    @property
    def mode(self) -> PlayMode:
        """**对外**当前在按哪种方式播（片段序列期间可能被收窄，见 `_effective_mode`）。

        动作的默认方式本身不因片段而改变，`animation.mode_for(anim.action)`
        拿到的始终是它的默认值。
        """
        return self._effective_mode

    @property
    def _effective_mode(self) -> PlayMode:
        """实际推进用的方式。

        - **不在片段里**：就是动作的默认方式；
        - **在片段里且这一段是 `("loop", N)`**：一律按 `LOOP` 在区间内来回——
          一圈的定义才唯一（"回到起点帧且方向朝前"，往返的一圈含两个折返点）；
        - **在片段里且这一段是 `"auto"`**：仍按**动作自己的方式**走，
          于是"一次性动作播到末帧即止、循环动作永不结束"照旧成立，
          而循环动作那一圈也照旧由 `_count_lap()` 交给 `"auto"` 的"永不结束"处理。
        """
        if not self._segments:
            return self._mode
        segment = self._segments[self._segment_cursor]
        if segment.loop is None:
            return self._mode
        return PlayMode.LOOP

    @property
    def index(self) -> int:
        """当前帧序号。"""
        return self._index

    @property
    def frame_count(self) -> int:
        """当前动作的总帧数（动作不存在 → 0）。"""
        action = self._spec.action(self._render_action)
        return action.frame_count if action is not None else 0

    @property
    def held(self) -> bool:
        """是否处于定格。"""
        return self._held

    @property
    def finished(self) -> bool:
        """一次性动作 / 片段序列是否已经播完（播完 = `advance()` 恒返回 `None`）。"""
        return self._finished

    @property
    def in_sequence(self) -> bool:
        """是否正在播片段序列。"""
        return bool(self._segments)

    @property
    def direction(self) -> int:
        """当前推进方向（`1` 往前 / `-1` 往回）；自检与诊断用。"""
        return self._direction

    @property
    def laps(self) -> int:
        """片段序列里当前这一段已完成的**完整圈数**。"""
        return self._laps

    # ------------------------------------------------------------ 推进

    def advance(self, dt_ms: float = 0.0) -> float | None:
        """推进一帧；返回**下一次换帧需要的毫秒数**，**定格时返回 `None`**。

        `dt_ms` 是"距离上一次推进过去了多少毫秒"（首帧传 0）。
        **本阶段不用它做时间累积**：调度本身就是"算完一帧再重排"，
        抖动会自然累积而不会被放大（agent.md §4.9 第一条）。
        它存在的理由是**夹在 `MAX_DT_MS`**——休眠唤醒后一次巨大的间隔
        不该变成"一次性跳十几帧"（task05 §6 最后一条）。

        返回 `None` 的三种情况：**定格** / **一次性动作已停在末帧** /
        **片段序列已播完**（此时会发出 `sequence_done`）。
        三种情况的共同点是"**不要再排下一次**"。

        > **退化重试也算一步**：`advance()` 返回 `frame_ms` 就说明"这一刻确实
        > 换了一帧"。若那一步只走到"原地不动"（单帧动作 / 帧区间只有一帧），
        > 就再走一步直到真的换了帧——否则窗口会每 33 ms 白重绘一次同一张图，
        > CPU 白白偏高（自检里有一条"静止的单帧动作不得每帧重绘"盯着它）。
        """
        if self._held or self._finished or self.frame_count <= 0:
            return None

        for _ in range(self._steps_for(dt_ms)):
            if self._held or self._finished:
                return None
            self._step()
            if self._finished:
                return None
            if self._index == self._start_index() and self._direction == _FORWARD:
                # "回到起点帧且方向朝前" = **一整圈**（往返的一圈含两个折返点）。
                break

        # 这一步没换帧（区间里只有一帧）→ 再走一步，别把"没变化"报成一次换帧。
        if self._index == self._frame_before_step:
            return self._advance_until_changed()

        return self._frame_ms

    def _advance_until_changed(self) -> float | None:
        """继续推进直到真的换了一帧（或定格 / 播完）。**上限防死循环**。"""
        for _ in range(self.frame_count + 1):
            if self._held or self._finished:
                return None
            was = self._index
            self._step()
            if self._finished:
                return None
            if self._index != was:
                break
        return None if self._finished or self._held else self._frame_ms

    def _step(self) -> None:
        """推进一步，并按需折返 / 结束 / 结算这一段的圈数。

        **推进方式用 `_effective_mode`**：片段序列期间一律按 `LOOP` 在区间内来回，
        一圈的定义才唯一（"回到起点帧且方向朝前"）。
        """
        count = self.frame_count
        if count <= 0:  # pragma: no cover - advance() 已经挡在前面
            return
        self._frame_before_step = self._index
        mode = self._effective_mode

        if count == 1:
            # 单帧动作：没有"下一帧"可言。往返与循环**原地不动**（不是结束，
            # 调用方照常按 frame_ms 重排——节奏对，只是画面不变）；
            # 一次性动作（且不在片段里）则该收尾了。
            self._direction = _FORWARD
            if mode is PlayMode.ONESHOT:
                self._finish()
            else:
                self._count_lap()
            return

        self._index += self._direction

        if self._index > self._last:
            # 越过末端。
            if mode is PlayMode.ONESHOT:
                self._index = self._last
                self._direction = _FORWARD
                self._finish()
                return
            if mode is PlayMode.PINGPONG:
                # 折返：**同一帧原地停一拍再往回**，否则端点帧的停留时间只有别人的一半。
                self._index = self._last - 1
                self._direction = _BACKWARD
                return
            # 单向循环：末帧接首帧。
            self._index = self._first
            self._count_lap()
            return

        if self._index < self._first:
            # 从首帧往回退：往返回头（同样原地停一拍）。
            self._index = self._first + 1
            self._direction = _FORWARD
            return

        if mode is PlayMode.PINGPONG and self._index == self._first:
            self._direction = _FORWARD
            self._count_lap()

    def _finish(self) -> None:
        """本段结束。还有下一段就切过去，否则收尾并报 `sequence_done`。"""
        if self._segment_cursor + 1 >= len(self._segments):
            self._segments = []
            self._finished = True
            self.sequence_done.emit()
            return

        self._segment_cursor += 1
        segment = self._segments[self._segment_cursor]
        self._apply_action(segment.action)
        self._apply_span(segment.start, segment.end)
        self._laps = 0

    def _count_lap(self) -> None:
        """完成一整圈：片段序列按圈数收尾，普通播放（非片段）什么都不做。"""
        if not self._segments:
            return
        self._laps += 1
        segment = self._segments[self._segment_cursor]
        if segment.loop is not None and self._laps >= segment.loop:
            self._finish()

    def _steps_for(self, dt_ms: float) -> int:
        """这一次 `advance()` 最多走几帧。

        **正常情况恒为 1**：调用方每排一次定时器只换一帧，真实经过的时间由
        `QTimer` 承担，"每帧加一"这种假设被彻底避免（agent.md §4.9）。
        只有"上一次回调晚到"（休眠唤醒 / 系统卡顿）时，才按 `dt_ms` 补齐，
        且上限受 `MAX_DT_MS` 约束——所以最多补 `MAX_DT_MS / frame_ms` 帧。
        """
        if not dt_ms or dt_ms <= 1.5 * self._frame_ms:
            return 1
        budget = min(float(dt_ms), MAX_DT_MS)
        return max(1, int(budget // self._frame_ms))

    # ------------------------------------------------------------ 换动作

    def set_action(self, action: str) -> None:
        """切换动作；**动作不存在 → 抛 `UnknownActionError`（列出可用动作）**。

        切换会**中断正在播的片段序列**（task05 §4），并清掉定格与"已播完"状态。
        """
        self._segments = []
        self._segment_cursor = 0
        self._laps = 0
        self._select_action(action)

    def _select_action(self, action: str) -> None:
        """真正换动作：解析帧数、区间、帧率、方式与起始帧。"""
        spec = self._spec.action(action)
        if spec is None:
            available = tuple(self._spec.action_order)
            if not available:
                # 一个动作都没有的包：`app` 那边已经按启动期致命处理过了，
                # 这里退化成一个"没有帧"的空动作，不抛异常（§2.10 第三类）。
                self._action = action
                self._render_action = action
                self._frame_ms = _fallback_frame_ms(self._spec, action)
                self._mode = mode_for(action)
                self._first = self._last = self._index = 0
                self._direction = _FORWARD
                self._held = False
                self._finished = True
                return
            raise UnknownActionError(self._spec.namespace, action, available)

        self._action = action
        self._render_action = action
        self._frame_ms = self._fps_override or spec.frame_ms
        self._mode = mode_for(action)
        self._first = 0
        self._last = spec.frame_count - 1
        # 一次性动作**从末帧倒着播**：素材里挥手是"手抬起→收手"，翘着的手接呼吸动作
        # 更接近"刚做完这个动作"的观感（旧实现同此，属观感约定，不是技术约束）。
        self._index = self._last if self._mode is PlayMode.ONESHOT else self._first
        self._direction = _BACKWARD if self._mode is PlayMode.ONESHOT else _FORWARD
        self._held = False
        self._finished = False

    #: `_apply_action` 的默认实现（构造期记在 `_apply_action` 上）。
    #: 存在它是因为 `Animation.__init__` 里"真正的实现"是实例属性——
    #: 这一点会被自检盯着，别改成类属性与方法同名。
    def _set_action_direct(self, action: str) -> None:
        spec = self._spec.action(action)
        if spec is None:
            raise UnknownActionError(
                self._spec.namespace, action, tuple(self._spec.action_order)
            )
        self._action = action
        self._render_action = action
        self._frame_ms = self._fps_override or spec.frame_ms
        self._mode = mode_for(action)
        self._held = False
        self._finished = False

    # ------------------------------------------------------------ 定格

    def set_fps_override(self, fps: float | None) -> None:
        """设置/清除调试用的帧率覆盖；**不写回配置**（task05 §4）。

        非正数 / 非数字一律当作"清除"（`_normalize_override`），**永不抛异常**。
        """
        self._fps_override = _normalize_override(fps)
        action = self._spec.action(self._render_action)
        if action is not None:
            self._frame_ms = self._fps_override or action.frame_ms

    def hold(self, index: int) -> None:
        """定格在第 `index` 帧（**越界报错**，不是画空白）。

        定格之后 `advance()` 恒返回 `None` → 调用方**不再排下一次定时器**，
        画面完全不动（task05 §6 倒数第 4 条）。
        """
        count = self.frame_count
        if index < 0 or index >= count:
            raise FrameIndexError(self._action, index, count)
        self._index = int(index)
        self._held = True

    def release_hold(self) -> None:
        """解除定格（方向与"已播完"状态都不动，恢复时接着往下走）。"""
        self._held = False

    # ------------------------------------------------------------ 片段序列

    def play_sequence(self, segments: Sequence[object]) -> None:
        """播放一段片段序列；`segments` 可以是 `Segment` 或 `(动作, 区间, 结束方式)` 元组。

        序列期间 `set_action()` / 再调一次 `play_sequence()` 都会**中断**当前序列
        （task05 §4："切换动作中断正在播的片段序列"）。
        空序列 = 什么都不做（**不是**"播完就发信号"：没有片段可播是编程错误，
        静默发一个 `sequence_done` 只会让调用方以为播过一遍）。
        """
        parsed = [Segment.coerce(item) for item in segments]
        if not parsed:
            logger.warning("play_sequence 收到空序列，已忽略")
            return

        for segment in parsed:
            if self._spec.action(segment.action) is None:
                raise UnknownActionError(
                    self._spec.namespace, segment.action, tuple(self._spec.action_order)
                )

        self._segments = parsed
        self._segment_cursor = 0
        self._laps = 0
        first = parsed[0]
        self._select_action(first.action)
        self._apply_span(first.start, first.end)

    def _apply_span(self, start: int | None, end: int | None) -> None:
        """设置帧区间并**夹住**（越界不能画出空白，task05 §4）。

        一次性动作在片段里**从区间起点正着播**：片段是显式请求
        "播这一段"，此时"倒着放"不是任何人的意图。非一次性动作照旧从起点开始。
        """
        count = self.frame_count
        if count <= 0:  # pragma: no cover - play_sequence 已经验过动作存在
            return
        first = 0 if start is None else int(start)
        last = count - 1 if end is None else int(end)
        first = min(max(first, 0), count - 1)
        last = min(max(last, 0), count - 1)
        if last < first:
            logger.warning(
                "片段区间 (%s, %s) 在动作 %r（共 %d 帧）里是反的，已夹成 (%d, %d)",
                start,
                end,
                self._action,
                count,
                min(first, last),
                max(first, last),
            )
            first, last = min(first, last), max(first, last)

        self._first, self._last = first, last
        self._index = first
        self._direction = _FORWARD

    def _start_index(self) -> int:
        """"起点帧"——判断一整圈用的那个帧号。"""
        return self._first


# ------------------------------------------------------------------ 小工具


def _normalize_override(value: float | None) -> float | None:
    """调试用的 `fps_override`：非正数 / `bool` / 非数字一律当作没给（**永不抛**）。"""
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    fps = float(value)
    if fps <= 0 or fps != fps or fps in (float("inf"), float("-inf")):
        return None
    return fps


def _fallback_frame_ms(spec: PetSpec, action: str) -> float:
    """动作不存在时"每帧多少毫秒"的兜底：同名字动作 → 任意动作 → 12 fps。

    只用于"素材里连动作都没有"的退化路径，正常播放一律用 `ActionSpec.frame_ms`。
    """
    named = spec.action(action)
    if named is not None:
        return named.frame_ms
    if spec.action_order:
        first = spec.actions[spec.action_order[0]]
        return first.frame_ms
    from .pet_spec import DEFAULT_FPS

    return 1000.0 / DEFAULT_FPS


# ------------------------------------------------------------------ 防漂移断言
#
# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。

if set(PLAY_MODES) != {
    "idle",
    "running_left",
    "running_right",
    "waiting",
    "rest",
    "waving",
}:  # pragma: no cover
    raise RuntimeError(f"播放方式表的动作集合被改动了：{sorted(PLAY_MODES)}")

# 表里**不许**再出现已被用户废弃的旧名（`jumping` / `sit` / `review`）。
_STALE_MODES = [name for name in ("jumping", "sit", "review") if name in PLAY_MODES]
if _STALE_MODES:  # pragma: no cover
    raise RuntimeError(f"播放方式表里出现了已废弃的动作名：{_STALE_MODES}")

if PLAY_MODES["rest"] is not PlayMode.LOOP:  # pragma: no cover
    raise RuntimeError("rest 必须是单向循环（往返会让躺姿倒着回去，task05 §4）")

if PLAY_MODES["waving"] is not PlayMode.ONESHOT:  # pragma: no cover
    raise RuntimeError("waving 必须是一次性（挥手有起手收手，来回播像抽搐）")

if MAX_DT_MS <= 0:  # pragma: no cover
    raise RuntimeError(f"MAX_DT_MS 必须是正数，实际是 {MAX_DT_MS}")
