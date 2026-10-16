"""键盘形态的**按键监听**：把"物理键"变成 `pressed: tuple[str, ...]`。

## 这一段在整个链路里的位置

K1（`keyview_render`）做的是**纯函数**："给定 `pressed` -> 出一张 540x420 的合成图"。
"`pressed` 从哪来"就是本模块——**全局按键监听**（不抢焦点、不需要管理员权限）。

```
物理键（VK）  ->  [本模块]  ->  pressed: tuple[str, ...]  ->  keyview_render.compose()
```

## 三条硬约定（task18 4.2）

| 项 | 约定 |
| --- | --- |
| **键态源可注入** | `KeyStateSource = Callable[[int], bool]`。产品路径给 `win32.get_async_key_state`，
  自检注入一个字典驱动的假源——**这是本卡唯一的自检入口**（`SendInput` 造合成按键在本机被拒，
  见 task18 1.2 第 3 条） |
| **只轮询真正被绑定的 VK** | 先去重：一个 VK 映射到多个键 id 也只问**一次** |
| **`pressed` 有序** | 按按下先后（最早的在前）。手的 `down` 要用"最后按下的那个"，
  所以顺序**不是**装饰 |

## 同一次轮询里按下的多个键，"真实先后"取不到

轮询的粒度是 `POLL_INTERVAL_MS`（8 ms）。同一个窗口内同时按下的几个键，
操作系统**没有**留下相对顺序（`GetAsyncKeyState` 只回答"现在按着吗"）——
只能按**绑定声明的顺序**给一个确定值。

**如实记一笔，不假装能分辨**：这个顺序是**确定且可重复**的（同一份绑定下每次一样），
所以自检可以逐项断言；但它**不是**物理先后。

## 停得干净

`set_enabled(False)` / `set_suspended(True)` 任一个成立 -> **停掉定时器 + `pressed` 清空**，
于是"切回宠物形态 / 窗口隐藏"时轮询次数**不再增长**（CPU 0），
而且回来时不会带着一个**陈旧的按下**（task18 8 第 3 条：切走再切回时按着的键当作没按）。
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from . import win32

logger = logging.getLogger(__name__)

#: 键态源：给一个 VK，回答"现在按着吗"。**可注入**——这是本卡唯一的自检入口。
KeyStateSource = Callable[[int], bool]

#: 轮询间隔（毫秒）。实测 16 键一轮 **28.9 us**（task18 1.2 第 2 条），
#: 8 ms 一轮约 **0.36% 单核**——够跟手，代价可接受。
#:
#: 放宽到 16 ms 能再省一半，代价是**高速连打时可能漏掉"按下又抬起"**
#: （task18 8 第 1 条：这是待确认项，默认取 8 ms）。
POLL_INTERVAL_MS: Final[int] = 8

#: 绑定表：`VK -> (键 id, ...)`。一个 VK 映射到多个 id 是**合法且常见**的
#: （用户 2026 决策 5），所以值是元组不是单个 id。
Bindings = Mapping[int, Sequence[str]]


def normalize_bindings(bindings: Bindings | None) -> dict[int, tuple[str, ...]]:
    """把绑定表规整成 `VK -> (键 id, ...)`，并**按 id 去重、保持声明顺序**。

    坏项（VK 不是整数 / 键 id 不是非空字符串）**静默丢弃**：绑定表的来源是配置，
    而配置是"读取永不致命"的那一类（agent.md 2.4 规则 1）。
    空 id 的槽位是"**置空**"（用户没配这个键）——它根本不是绑定。

    去重保留**第一次**出现的顺序：同一个 VK 映射到多个 id 时，
    这几个 id 的先后就是"同一窗口内同时按下"时给的那个确定顺序（见模块开头）。
    """
    out: dict[int, tuple[str, ...]] = {}
    if not bindings:
        return out
    try:
        items = bindings.items()
    except AttributeError:
        return out
    for raw_vk, raw_ids in items:
        if isinstance(raw_vk, bool) or not isinstance(raw_vk, int):
            continue
        if isinstance(raw_ids, str):
            candidates: Sequence[object] = (raw_ids,)
        else:
            try:
                candidates = tuple(raw_ids)
            except TypeError:
                continue
        ordered: list[str] = []
        for raw_id in candidates:
            key_id = raw_id.strip() if isinstance(raw_id, str) else ""
            if key_id and key_id not in ordered:
                ordered.append(key_id)
        if ordered:
            out[int(raw_vk)] = tuple(ordered)
    return out


class KeyListener(QObject):
    """定时轮询键态 -> 算出 `pressed` -> 只在**真的变了**时发信号。

    | 信号 | 含义 |
    | --- | --- |
    | `keys_changed(tuple[str, ...])` | 已按下的键 id 变了（**序列相同就不发**） |

    ## 为什么是定时器而不是线程

    参考实现是一条**不 sleep 的 while 忙等线程**——那正是本项目不要的做法
    （task18 5 明写禁止）。所有 Qt 对象都住在 GUI 线程（agent.md 2.6），
    一次轮询才 28.9 us，`QTimer` 足够。
    """

    #: 已按下的键 id 变了。**只在真的变了时发**（序列相同就不发）——
    #: 下游的合并复绘靠它计数（"信号节流"那条验收直接盯这个）。
    keys_changed = Signal(tuple)

    def __init__(
        self,
        key_state: KeyStateSource | None = None,
        bindings: Bindings | None = None,
        interval_ms: int = POLL_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        #: 键态源。`None` -> 用产品路径（`win32.get_async_key_state`）。
        #: 默认值写成 `None` 而不是直接引那个函数：
        #: 后者是**模块级绑定**，自检 `monkeypatch` 时不会生效（4.20 同一条教训）。
        self._key_state: KeyStateSource = key_state or win32.get_async_key_state
        #: `VK -> (键 id, ...)`。改了绑定**立刻生效、不用重绘**（映射不进图像）。
        self._bindings: dict[int, tuple[str, ...]] = normalize_bindings(bindings)
        #: 上一轮每个被绑定 VK 的按下态（算"新按下"与"抬起"用）。
        self._down: set[int] = set()
        #: **当前按下的键 id，按按下先后**（最早的在前）。
        self._pressed: tuple[str, ...] = ()
        #: `enabled` 与 `suspended` **都要成立**才轮询（两个开关管两件事）。
        self._enabled = False
        self._suspended = False
        #: 轮询次数（自检与诊断用：**"停得干净"的判据就是它不再增长**）。
        self.polls = 0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(max(1, int(interval_ms)))
        self._timer.timeout.connect(self.poll)

    # ------------------------------------------------------------ 只读

    @property
    def pressed(self) -> tuple[str, ...]:
        """当前按下的键 id（**按按下先后**，最早的在前）。**只读**。"""
        return self._pressed

    @property
    def bindings(self) -> dict[int, tuple[str, ...]]:
        """当前绑定表的一份拷贝（改它不会生效）。"""
        return dict(self._bindings)

    @property
    def polled_vks(self) -> tuple[int, ...]:
        """这一轮真正会去问的 VK（**去重且有序**，诊断与自检用）。"""
        return tuple(self._bindings)

    @property
    def interval_ms(self) -> int:
        """轮询间隔（毫秒）。**真值是构造时那一个**，运行期不改。"""
        return self._timer.interval()

    @property
    def running(self) -> bool:
        """定时器还在不在转（"还在轮询吗"只由 `isActive()` 表达，2.13）。"""
        return self._timer.isActive()

    @property
    def enabled(self) -> bool:
        """外部（形态 / 可见性）让不让轮询。"""
        return self._enabled

    @property
    def suspended(self) -> bool:
        """是不是被**临时**挂起了（task19 录入时要挂起）。"""
        return self._suspended

    # ------------------------------------------------------------ 装配

    def set_bindings(self, bindings: Bindings | None) -> None:
        """换一套绑定。**立刻生效、不用重绘**（映射不进图像）。

        绑定变了之后 `pressed` 必须**重算**：

        - 已经不在任何绑定里的键 id 要**马上消失**（否则画面上留着一个没人按的键）；
        - 各键 id 的**相对顺序**保持（它们本来就是从按下顺序来的）。

        重算后**照常发信号**（序列真的变了才发）——所以这条路径下
        "改绑定 -> 画面跟上"是一次正常的合并复绘，不需要另开通道。
        """
        self._bindings = normalize_bindings(bindings)
        # 上一轮的 `_down` 里可能有**已解绑**的 VK：留着会让"抬起"永远算不出来。
        self._down &= set(self._bindings)
        self._recompute()

    def set_enabled(self, enabled: bool) -> None:
        """让不让轮询（**形态 / 可见性这条线**）。

        关掉时 `pressed` **清空**：回来时不会带着"陈旧的按下"
        （task18 8 第 3 条：切走再切回时按着的键当作没按）。
        """
        self._enabled = bool(enabled)
        self._sync()

    def set_suspended(self, suspended: bool) -> None:
        """**临时**挂起（task19 录入时要挂起）。

        与 `set_enabled` 分开是因为"谁让它停的"是两件事，
        恢复时各还各的（一个关着就还是停）。效果一样：停表 + 清空 `pressed`。
        """
        self._suspended = bool(suspended)
        self._sync()

    def stop(self) -> None:
        """退出收尾：停表 + 清空（与其它驱动一样，`app.py` 的 finally 里调）。"""
        self._timer.stop()
        self._pressed = ()
        self._down = set()

    # ------------------------------------------------------------ 轮询

    def poll(self) -> None:
        """跑一轮：问每个被绑定的 VK -> 算 `pressed` -> 变了才发信号。

        **每个 VK 一轮只问一次**（先去重）：一个 VK 映射到多个 id
        也只问一次，多出来的那几个 id 是"同一件事的多个说法"。
        """
        self.polls += 1
        down: set[int] = set()
        for vk in self._bindings:
            try:
                if self._key_state(vk):
                    down.add(vk)
            except Exception as exc:  # pragma: no cover - 键态源不该抛，但绝不冒这个险
                # 键态源出问题 = 退化到"这个键没按"，**绝不中断轮询**
                # （agent.md 2.10 第三类：可恢复 -> 退化）。
                logger.warning(
                    "读按键状态失败（vk=%d）：%s: %s", vk, type(exc).__name__, exc
                )
                continue
        self._advance(down)

    def _advance(self, down: set[int]) -> None:
        """与本轮的 `down` 对比，更新 `pressed` 并**在真的变了时**发信号。

        | 情况 | 怎么做 |
        | --- | --- |
        | **新按下**的 VK | 把它的键 id **按绑定声明顺序**追加到队尾 |
        | **抬起**的 VK | 把它的键 id 从序列里**移除** |
        | 两者都没变 | **什么都不做、不发信号**（"信号节流"那条验收盯它） |

        ⚠ **同一个 8 ms 窗口内**同时按下的多个键，"真实先后"是**取不到**的
        （见模块开头）——只能按绑定声明顺序给一个确定值。
        """
        added = [vk for vk in self._bindings if vk in down and vk not in self._down]
        removed = [vk for vk in self._down if vk not in down]
        self._down = set(down)

        if not added and not removed:
            return  # 状态没变：**不发信号**（自检数次数的那一条）

        dropped: set[str] = set()
        for vk in removed:
            dropped.update(self._bindings.get(vk, ()))

        kept = [key_id for key_id in self._pressed if key_id not in dropped]
        for vk in added:
            kept.extend(self._bindings.get(vk, ()))
        self._emit(tuple(kept))

    def _recompute(self) -> None:
        """按当前绑定表把 `pressed` 过滤一遍（**不改 `_down`**）。"""
        allowed = {key_id for ids in self._bindings.values() for key_id in ids}
        kept = tuple(key_id for key_id in self._pressed if key_id in allowed)
        self._emit(kept)

    def _emit(self, pressed: tuple[str, ...]) -> None:
        """**改 `pressed` 并广播**的唯一出口（判据"真的变了"也在这里）。"""
        if pressed == self._pressed:
            return
        self._pressed = pressed
        logger.debug("按键状态：%s", "、".join(pressed) if pressed else "（没有键按下）")
        self.keys_changed.emit(self._pressed)

    def _sync(self) -> None:
        """按 `enabled` 与 `suspended` 决定要不要轮询（**唯一改定时器状态的地方**）。

        ⚠ 停下来时**必须清空 `pressed` 与 `_down`**：

        - `pressed` 留着 -> 回来时画面上亮着一个**没人按的键**；
        - `_down` 留着 -> 回来那一次会把它当成"一直是按着的"，于是**永远不抬起**。
        """
        if self._enabled and not self._suspended:
            if not self._timer.isActive():
                self._timer.start()
            return
        self._timer.stop()
        self._down = set()
        self._emit(())


def bindings_from_config(raw: object) -> dict[int, tuple[str, ...]]:
    """配置里的 `keyview_keys` -> 轮询用的绑定表。

    配置的形状是 **键 id -> `{"vk": 74, "name": "J"}`**（agent.md 2.23），
    本函数把它**翻过来**成 `VK -> (键 id, ...)`：

    - **没配的键**（不在表里 / `vk` 缺失 / `vk` 非法）-> **不进绑定** -> 永远不亮
      （用户 2026 的"置空"）；
    - **同一个 VK 出现在多个键 id 上** -> 一个 VK 对多个 id，天然支持
      （用户 2026 决策 5）；
    - 顺序取配置里键的出现顺序（dict 保持插入序），于是"同一窗口内多键"
      给出来的次序**确定且可重复**。

    ⚠ **配置坏掉不报错、不警告**：这是"读取永不致命"的那一类，
    坏项静默丢弃即可（配置坏了的表现就是"那个键不亮"）。
    """
    out: dict[int, list[str]] = {}
    if not isinstance(raw, Mapping):
        return {}
    for raw_id, raw_entry in raw.items():
        key_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not key_id or not isinstance(raw_entry, Mapping):
            continue
        vk = raw_entry.get("vk")
        if isinstance(vk, bool) or not isinstance(vk, int) or vk <= 0:
            continue
        out.setdefault(int(vk), []).append(key_id)
    return {vk: tuple(ids) for vk, ids in out.items()}

