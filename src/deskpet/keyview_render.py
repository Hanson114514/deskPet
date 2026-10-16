"""键盘形态的**分层合成器**：给定"哪些键按着" → 一张画布。

## 层序是固定的（拖不改）

`background`（数组顺序）→ `keys`（按下态）→ `hands`（数组顺序）→ `head`（数组顺序）。

⚠ **不把 `background` 的多个图层烤成一张合并图**：参考工程的层序是
`Main(人物) → Piano(键盘) → 键 → 手 → 头`（取自 prefab 的 `m_Children`，
那才是渲染顺序）。合并就等于把顺序定死，而且改一层要重烤一次（task17 §5）。

## 本模块只做"给定状态 → 图"

`compose(level, pressed)` 的输入是 `pressed: tuple[str, ...]`
（**已按下的键 id，按按下顺序，最早在前**）。

- **手的 `down` 用"最后按下的那个"**（`pressed[-1]`，与参考实现
  `pressed[^1]` 一致）；每只手只看**自己覆盖到的那些键**里的最后一个。
- **头**出不出头**只是 `pressed` 集合的纯函数**（`when_all_of`）——
  于是"全按出头"在 K1 就做完了，task18 只管"物理键 → pressed"。

"物理键 → pressed"是 task18 的事，**本模块不碰**。

## 缓存：按**状态签名**，不是按帧号

宠物形态的缓存键是 `(动作, 帧序号, 档位, 镜像)`；键盘形态没有"帧序号"这回事，
对应物是**状态签名**（`state_signature()`）：把 `pressed` 规范化成一个字符串。
于是同一组按键反复出现时只合成一次。

⚠ `pressed` 是**有序**的（"最后按下的那个"要用来选手），所以签名保留顺序；
同一组键的不同到达顺序会产生不同签名——**这是故意的**：顺序确实会改变出图
（手用哪张），合并它们就是错的。

## 缩放

**不另抄一份档位逻辑**：目标尺寸走 `size.pixel_size(keyview.cell, level)`
（与宠物形态同一个真值），与宠物形态一样用 `SmoothTransformation`。
`keyview.cell` 是**所有布局共用**的一份（切布局不改窗口尺寸，task17 §3.3）。
"""

from collections import OrderedDict
from typing import Final

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QImage, QPainter

from . import keyview_spec, size
from .keyview_spec import KeyViewSpec

#: 合成缓存的**兜底上限**（张）。真值由 `limit_for()` 按内存预算算出来，
#: 这个常量只在"预算算不出来"时兜底（与 `rendering.DEFAULT_CACHE_LIMIT` 同一种角色）。
DEFAULT_CACHE_LIMIT: Final[int] = 32

#: 合成缓存的内存预算（字节）。真值在 `rendering.MEMORY_BUDGET_BYTES`，
#: 这里**直接引用**它——两块缓存加起来就是"渲染层总共占多少内存"，
#: 各写一个数迟早会让总占用超出预期。
def _budget_bytes() -> int:
    """合成缓存的预算（= 渲染层预算）。延迟 import 避免循环依赖。"""
    from .rendering import MEMORY_BUDGET_BYTES

    return MEMORY_BUDGET_BYTES


def state_signature(pressed: object) -> str:
    """把按键状态规范化成一个**字符串签名**（缓存键的第二半）。

    规则两条，都为了"同一件事只有一个签名"：

    - 只收合法字符串项（坏项静默丢掉，不抛异常——它只是个缓存键）；
    - **保留顺序**（原因见模块开头），用 ``\x1f``（单元分隔符）连接：
      键 id 里不可能出现它，所以不会与"某个键名叫 A\x1fB"撞车。
    """
    if pressed is None:
        return ""
    if isinstance(pressed, str):
        return pressed
    try:
        items = [str(item) for item in pressed if isinstance(item, str) and item]
    except TypeError:
        return ""
    return "\x1f".join(items)


def pressed_tuple(pressed: object) -> tuple[str, ...]:
    """把外面对 `pressed` 的各种说法规整成 `tuple[str, ...]`。

    接受 `None` / 字符串 / 任意可迭代；**永远给得出一个元组**（不抛异常）。
    """
    if pressed is None:
        return ()
    if isinstance(pressed, str):
        return (pressed,) if pressed else ()
    try:
        return tuple(str(item) for item in pressed if isinstance(item, str) and item)
    except TypeError:
        return ()


def limit_for(level: int, cell: tuple[int, int]) -> int:
    """这一档合成缓存该留几张（= 内存预算 ÷ 单张大小）。

    与 `rendering.limit_for()` 用**同一个折算**，不另写一份：
    键盘形态一张就是整画布，没有"按动作帧数"这一维。
    """
    from .rendering import bytes_per_frame

    per_frame = bytes_per_frame(size.pixel_size(cell, level))
    return max(1, min(4096, _budget_bytes() // per_frame))


class KeyViewRenderer:
    """把"哪些键按着"合成为一张**已按档位缩放**的整画布图，并缓存。

    缓存键 = `(状态签名, 档位)`。⚠ **不含镜像**：键盘形态不做镜像
    （镜像只作用于 `idle` / `waiting` / `rest`，见 agent.md §0/§2.8）。
    """

    def __init__(
        self,
        spec: KeyViewSpec,
        level: int | None = None,
        limit: int | None = None,
    ) -> None:
        self.spec = spec
        #: 显式上限（自检钉边界用）；`None` = 按内存预算自动算（产品路径）。
        self._explicit_limit: int | None = None if limit is None else max(1, int(limit))
        self._limit_cache: dict[int, int] = {}
        #: `(状态签名, 档位) -> QImage`；尾部最新，LRU 淘汰头部。
        self._cache: "OrderedDict[tuple[str, int], QImage]" = OrderedDict()
        #: 统计（自检看"有没有真的走缓存"）。
        self.hits = 0
        self.misses = 0
        self._last_level: int = size.clamp_level(level or size.DEFAULT_LEVEL)

    # ------------------------------------------------------------ 对外

    def image(self, level: int, pressed: object = ()) -> QImage:
        """取 `level` 档、`pressed` 状态下的整画布图。

        `pressed` 是**已按下的键 id，按按下顺序**（最早在前）。

        **永不抛异常**：算不出来（图读不出来）时返回**空 `QImage`**，
        调用方按"没有图可画"退化（agent.md §2.8 的降级约定）。
        空结果**不进缓存**——图片是外部输入，用户补上文件后下次就有机会恢复。
        """
        level = size.clamp_level(level)
        self._last_level = level
        state = pressed_tuple(pressed)
        key = self.cache_key(state, level)

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            return cached

        self.misses += 1
        image = self._compose(state, level)
        if image.isNull():
            return image

        self._cache[key] = image
        self._evict(level)
        return image

    def cache_key(self, pressed: object, level: int) -> tuple[str, int]:
        """缓存键。**拼键的唯一入口**（与 `FrameRenderer.cache_key()` 同一个角色）。"""
        return (state_signature(pressed), size.clamp_level(level))

    def cached_count(self) -> int:
        """当前缓存里有几张图。"""
        return len(self._cache)

    @property
    def limit(self) -> int:
        """**当前生效**的上限（张）——上限是怎么定的唯一对外说法。"""
        return self.limit_for_level(self._last_level)

    def limit_for_level(self, level: int) -> int:
        """指定档位的上限（张）。"""
        if self._explicit_limit is not None:
            return self._explicit_limit
        level = size.clamp_level(level)
        cached = self._limit_cache.get(level)
        if cached is None:
            cached = limit_for(level, self.spec.cell)
            self._limit_cache[level] = cached
        return cached

    def clear(self) -> None:
        """清空缓存（换素材包 / 换 DPI 环境时调用）。"""
        self._cache.clear()

    # ------------------------------------------------------------ 合成

    def load(self, filename: str) -> QImage:
        """读一张 KV 图片 → 带 alpha 的 `QImage`；读不出来 → 空图。

        **统一成 `Format_ARGB32_Premultiplied`**（QPainter 合成最快的格式，
        与 `pet_loader.load_frame_image` 同一条约定）。
        **不做任何颜色判定**：透明度的唯一来源是 alpha 通道（§2.8）。
        """
        path = self.spec.image_path(filename)
        if path is None:
            return QImage()
        image = QImage(str(path))
        if image.isNull():
            return QImage()
        if image.format() != QImage.Format_ARGB32_Premultiplied:
            image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        return image

    def layers(self, pressed: object = ()) -> list[tuple[QImage, int, int]]:
        """按**层序**列出这一状态要画的全部图层 `(图, x, y)`。

        这是"合成结果长什么样"的**唯一说法**：`compose()` 只负责把它们
        `drawImage` 到画布上。自检里那条"逐像素与独立路径相同"的对照
        就是拿这个列表手工画一遍——**两条路径共用同一个层序定义**，
        不共用就证明不了"层序对"。

        图读不出来的图层**直接跳过**（缺一张不该让整幅画不出来）；
        整幅一张都读不出来时返回空列表，调用方按"没有图可画"退化。
        """
        state = pressed_tuple(pressed)
        items: list[tuple[QImage, int, int]] = []

        # 1) background：整画布，数组顺序即绘制顺序（底层在前）。
        for filename in self.spec.background:
            items.append((self.load(filename), 0, 0))

        # 2) keys：**按下态**。未按下态画在 background 里，这里不碰。
        for key_id in state:
            spec = self.spec.key(key_id)
            if spec is None:
                # 键 id 不在表里（换过素材包）→ 跳过，不抛异常。
                continue
            if spec.uses_overlay:
                # overlay：整画布透明底图，**位置画在图里**（零坐标）。
                items.append((self.load(spec.overlay), 0, 0))
            else:
                items.append((self.load(spec.down), spec.x, spec.y))

        # 3) hands：每只手用"**最后按下的那个**自己覆盖到的键"那张 down。
        for hand in self.spec.hands:
            chosen = ""
            for key_id in state:
                if key_id in hand.down:
                    chosen = key_id
            filename = hand.down.get(chosen) if chosen else hand.up
            items.append((self.load(filename or hand.up), 0, 0))

        # 4) head：`when_all_of` 里的键**全部**被按下才画；多个都满足按顺序全画。
        state_set = set(state)
        for head in self.spec.heads:
            if head.when_all_of and set(head.when_all_of) <= state_set:
                items.append((self.load(head.image), 0, 0))

        return [item for item in items if not item[0].isNull()]

    def compose(self, level: int, pressed: object = ()) -> QImage:
        """**已按档位缩放**的最终图（缓存里存的就是它）。"""
        level = size.clamp_level(level)
        state = pressed_tuple(pressed)
        target = size.pixel_size(self.spec.cell, level)
        return self._render(state, level, target)

    # ------------------------------------------------------------ 内部

    def _compose(self, state: tuple[str, ...], level: int) -> QImage:
        """`compose()` 的内部形状（缓存路径调它）。"""
        target = size.pixel_size(self.spec.cell, level)
        return self._render(state, level, target)

    def _render(
        self, state: tuple[str, ...], level: int, target: tuple[int, int]
    ) -> QImage:
        """真做合成的地方：**先按 `cell` 合成整画布，再整体缩到该档像素尺寸**。

        ## 为什么是"先合成再缩放"而不是"每张图各自缩"

        每张图各自缩放的**取整误差会累积**（键帽是 39~61 px 的小图，
        缩到 0.25 倍后各自取整会让小图的位置偏移 1~2 px），
        而整画布缩放在数学上就是"看到的那张画布等比变小"，
        与"画面里所有东西一起缩放"的直觉一致，也与宠物的处理方式一致。

        缩放用 `SmoothTransformation`（D3 定案，与宠物形态同一个算法）。
        """
        items = self.layers(state)
        if not items:
            return QImage()

        canvas = QImage(self.spec.cell[0], self.spec.cell[1], QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        try:
            for image, x, y in items:
                painter.drawImage(x, y, image)
        finally:
            painter.end()

        if (canvas.width(), canvas.height()) == target:
            # 已经是目标尺寸（第 6 级 / 1.00×）就别走一趟缩放（同 rendering 的做法）。
            return canvas
        return canvas.scaled(
            target[0],
            target[1],
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )

    def _evict(self, level: int) -> None:
        """超出该档上限时丢掉最久没用过的（纯 LRU，与 `rendering` 同一个策略）。"""
        limit = self.limit_for_level(level)
        while len(self._cache) > limit:
            self._cache.popitem(last=False)


def compose_manually(
    renderer: KeyViewRenderer, pressed: object, level: int
) -> QImage:
    """**独立路径**的合成：照着 `layers()` 的层序手工 `drawImage` 到同一画布。

    存在的理由只有一个：验收要求"合成结果与独立路径**逐像素相同**"。
    如果对照也走产品的 `_render()`，那就是"自己跟自己比"，
    证明不了"不串位、层序对"（agent.md §4.23 的教训：没有对照的断言证明不了什么）。

    ⚠ 它与产品路径**共用** `layers()` 的层序定义，但**不共用**画布创建、
    缩放与缓存——这正是"层序对、位置对"要被独立验证的那一段。

    ## 画布格式必须与产品路径一致（否则对照会"差 534 个像素"）

    第一版这里用了 `Format_ARGB32`（直通 alpha），而产品路径用
    `Format_ARGB32_Premultiplied`。实拍素材的 `figure.png` / `piano.png`
    有大量**半透明**像素（抽样 9880 / 1620 个），两种格式逐次 `drawImage`
    的**取整方式不同**，于是同一份层序会给出 534 个像素的差异——
    那是格式差异，**不是层序错**。用同一个格式，这条对照才真的在验层序。
    """
    spec = renderer.spec
    target = size.pixel_size(spec.cell, level)
    canvas = QImage(
        spec.cell[0], spec.cell[1], QImage.Format_ARGB32_Premultiplied
    )
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    try:
        for image, x, y in renderer.layers(pressed):
            painter.drawImage(x, y, image)
    finally:
        painter.end()
    if (canvas.width(), canvas.height()) == target:
        return canvas
    return canvas.scaled(
        target[0], target[1], Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )


def key_rect(spec: KeyViewSpec, key_id: str, level: int) -> QRect | None:
    """一个键位在**当前档位**下占的矩形（画布坐标）；查不到 → `None`。

    给自检用：**逐键回放对照**要断言"按下的那个键确实出现在它该在的位置"。
    `overlay` 写法的键没有坐标，整画布就是它的矩形。
    """
    key = spec.key(key_id)
    if key is None:
        return None
    if key.uses_overlay:
        width, height = size.pixel_size(spec.cell, level)
        return QRect(0, 0, width, height)
    image = QImage(str(spec.image_path(key.down) or ""))
    if image.isNull():
        return None
    zoom_w, zoom_h = size.pixel_size(
        (key.x + image.width(), key.y + image.height()), level
    )
    origin = size.pixel_size((key.x, key.y), level)
    return QRect(origin[0], origin[1], max(1, zoom_w - origin[0]), max(1, zoom_h - origin[1]))
