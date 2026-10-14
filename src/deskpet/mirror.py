"""镜像状态表：按状态分别记忆"是否左右翻转"，并写回 `config.mirror`。

## 为什么要有单独一层

镜像的**状态**（哪个状态翻着）与**怎么翻转**（渲染层怎么画）是两件事：

- 本模块只管**状态**：`is_on()` / `can_mirror()` / `toggle()`；
- 真正把图翻过来是 `rendering.FrameRenderer` 的事（`mirrored=True`）；
- 翻转之后衍生数据必须失效重算（agent.md §4.7）：帧缓存键含镜像维度，
  `QRegion` 也要按镜像后的图重算——这两件事由 `PetWindow` 在收到
  `mirror_changed` 时做。

## 适用范围的真值只有一处

`MIRRORABLE` **直接引用 `config.MIRROR_STATES`**（agent.md §4.1"默认值与真值：分散就会分叉"），
不在这里另抄一份 `("idle", "waiting", "rest")`。写回配置时的键过滤也由 `config` 负责。

## 记忆是"按状态"的

`{"idle": true, "rest": false}`：切到 `rest` 时读 `rest` 的那一项，
切回 `idle` 仍沿用 `idle` 上一次的选择（task06 §2 第 5/6 条）。
未记录过的状态 = **不翻转**（默认空对象，不写死三个 `false`）。

## 写失败必须能回滚（agent.md §2.10 第二类）

`toggle()` 在写盘失败时返回 `False` **且不改内存**——界面据此把勾选回滚，
**绝不显示假成功**（task06 §6 倒数第 4 条）。信号只在内存真的变了之后才发，
所以"写失败"这条路径上不会有人误以为翻转成功了。
"""

import logging
from typing import Final

from PySide6.QtCore import QObject, Signal

from . import config

logger = logging.getLogger(__name__)

#: 可镜像的状态（= 动作名）。**真值在 `config.MIRROR_STATES`**，这里只做引用。
MIRRORABLE: Final[tuple[str, ...]] = config.MIRROR_STATES

#: `config.mirror` 这个键的名字（只写一处，免得字符串散在代码里拼错）。
MIRROR_KEY: Final[str] = "mirror"


def can_mirror(action: str) -> bool:
    """这个动作在不在镜像的适用范围内（决定菜单项是否禁用）。

    纯函数：与"当前翻着没有"无关，只看**适用范围**。
    适用范围外的动作（`running_left` / `waving` / 用户包里的自定义动作）
    一律返回 `False`（task06 §2 第 8 条）。
    """
    return action in MIRRORABLE


class MirrorState(QObject):
    """"状态名 → 是否翻转"的映射，读写都经 `ConfigStore`。

    内存里的映射与配置可能不一致的唯一时刻是"写失败"：
    此时**内存不动**（保持与磁盘一致的旧值），并返回 `False`。
    """

    #: 某个状态的镜像翻转变了（agent.md §2.2：`<名词>_<过去分词>`）。
    #: 渲染层据此**重取帧 + 重算遮罩**（换镜像 = 衍生数据失效，§4.7）。
    mirror_changed = Signal(str, bool)

    def __init__(self, store: "config.ConfigStore", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store
        #: 内存快照：**从配置读一次**（配置坏掉时 `ConfigStore` 已经回退成空对象）。
        #: 不直接引用 `store.get()` 返回的那个字典，免得外部改到配置内部状态。
        self._on: dict[str, bool] = self._read_from_store()

    # ------------------------------------------------------------ 只读

    def _read_from_store(self) -> dict[str, bool]:
        """从配置里读一份"合规的"镜像表。

        配置层（`config.normalize_mirror`）已经把适用范围外的键与非布尔值过滤掉了，
        这里**再挡一次**是防御性的：即便有人绕过配置层直接塞了个脏字典，
        本模块的对外语义（"适用范围外恒为不翻转"）也不会被破坏。
        """
        raw = self._store.get(MIRROR_KEY)
        if not isinstance(raw, dict):
            # 配置整体坏掉 → 退化为"永远不翻转"，**不阻断启动**（§2.10 第三类）。
            logger.warning("mirror 配置不是对象，本次一律不翻转")
            return {}
        return {
            key: bool(value)
            for key, value in raw.items()
            if key in MIRRORABLE and isinstance(value, bool)
        }

    def is_on(self, action: str) -> bool:
        """这个状态**当前**是否翻转。非适用范围一律 `False`。"""
        if not can_mirror(action):
            return False
        return bool(self._on.get(action, False))

    def can_mirror(self, action: str) -> bool:
        """`can_mirror()` 的实例方法形式（菜单构建时手里只有实例）。"""
        return can_mirror(action)

    def snapshot(self) -> dict[str, bool]:
        """当前镜像表的副本（自检与诊断用；**只含适用范围内的键**）。"""
        return dict(self._on)

    # ------------------------------------------------------------ 写

    def toggle(self, action: str) -> bool:
        """翻转 ``action`` 的镜像并写回配置。

        返回**是否真的翻转成功**：

        - 适用范围外 → `False`（**不写盘**、不发信号）；
        - 写盘失败 → `False` 且**内存回滚**到翻转前的值——界面据此撤销勾选，
          不显示假成功（agent.md §2.10 第二类）；
        - 成功 → `True`，并发 `mirror_changed(action, on)`。

        写回时总是把**完整的**镜像表交给 `ConfigStore.set()`：配置层会按
        `MIRROR_STATES` 过滤键，所以"适用范围外的状态名不会累积"这条约定
        由配置层兜住，本模块不必自己再过滤一遍。
        """
        if not can_mirror(action):
            logger.warning("动作 %s 不在镜像适用范围内，忽略翻转请求", action)
            return False

        previous = self.is_on(action)
        wanted = not previous
        table = dict(self._on)
        table[action] = wanted

        if not self._store.set(MIRROR_KEY, table):
            # 写失败：**内存不动**（`self._on` 根本没被改），勾选由界面回滚。
            reason = self._store.last_error(MIRROR_KEY) or "写配置失败"
            logger.error("镜像翻转没能写进配置：%s", reason)
            return False

        self._on = table
        self.mirror_changed.emit(action, wanted)
        return True

    def reload(self) -> None:
        """重新从配置读一遍（供"改完立即生效"；本 task 没有调用方）。"""
        self._on = self._read_from_store()
