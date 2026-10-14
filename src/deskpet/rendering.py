"""渲染层：帧 → 目标尺寸的可绘制对象 + 缓存。

## 三件事，只有三件

1. **取缩放后的帧**：`FrameRenderer.frame(action, index, level)`；
2. **缓存**：同一 `(动作, 帧序号, 档位)` 只缩放一次（放大**只在换档 / 首次取帧时**做，
   绝不每帧做）；
3. **上限**：缓存有明确上限，来回切档不会把内存吃满。

缩放算法取 **`SmoothTransformation`**（附录 D 的 D3 二选一里的前者）。
理由：素材是 1 位 alpha 的硬边图，放大时最近邻会把台阶等比放大得很显眼；
`SmoothTransformation` 在放大时做双线性/双三次插值，边缘的台阶被**柔化**，
而这份柔化正好和"`setMask` 的锯齿是已接受代价"（agent.md §0）不冲突。
代价是**边缘会出现半透明像素**，于是 `setMask` 的区域可能比"纯 0/255"时略小一圈——
可点范围与视觉范围仍然一致（都由同一张缩放图算出来），符合 §2.8 的约定。

## 缓存键：本 task 只有三维，task06 必须补上镜像

键是 `(动作, 帧序号, 档位)`。**task06 加镜像时务必把镜像并进这个键**，
否则"翻转后拿到未翻转的缓存图"（agent.md §4.7 那条坑）会立刻复现。
`_cache_key()` 是本文件里**唯一**拼键的地方，改动只在这一处。

## LRU 而不是"按档位保留"

用 `OrderedDict` 做 LRU，上限 `DEFAULT_CACHE_LIMIT` 张。
天然满足"当前档位 + 前一档"的保留需求（刚用过的档位在最前面），
又不至于像"每档全留"那样在 10 个档位 × 大帧数下失控。

> **缓存里存的是 `QPixmap`**，而 `QPixmap` **只能在 GUI 线程碰**（agent.md §2.6）。
> 本阶段没有工作线程，所以这不是问题；将来若真的引线程解码，
> 跨线程要传 `QImage`、`QPixmap` 只在 GUI 线程造。
"""

from collections import OrderedDict
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from . import pet_loader, size
from .pet_spec import PetSpec

#: 缓存上限（张）。切档时"当前档 + 前一档"的帧都还在，来回切感觉不到重算；
#: 10 档 × 上百帧的极端来回也不会把内存吃满（每张最大也就 level 10 的 1000×1000）。
DEFAULT_CACHE_LIMIT: Final[int] = 64


class FrameRenderer:
    """把 `PetSpec` 的帧缩放成当前档位的可绘制对象，并缓存。

    `dpi_scale` 记录"这份渲染属于哪个 DPI 环境"：本类**不**给 `QPixmap` 标
    `devicePixelRatio`（窗口尺寸与内容都是逻辑单位，标了会缩放两次，agent.md §4.9），
    留着它是为了**缓存自检**能看出"换屏幕后缓存该整体作废"这件事该由谁负责——
    本阶段由调用方重建 `FrameRenderer` 解决。
    """

    def __init__(self, spec: PetSpec, dpi_scale: float = 1.0, limit: int = DEFAULT_CACHE_LIMIT) -> None:
        self.spec = spec
        self.dpi_scale = float(dpi_scale) if dpi_scale else 1.0
        self._limit = max(1, int(limit))
        #: `键 -> QPixmap`；`OrderedDict` 的**尾部是最新**（LRU 淘汰头部）。
        self._cache: "OrderedDict[tuple[str, int, int], QPixmap]" = OrderedDict()
        #: 统计信息（自检看"有没有真的走缓存"用，不影响行为）。
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------ 对外

    def frame(self, action: str, index: int, level: int) -> QPixmap:
        """取 `action` 第 `index` 帧在 `level` 档下**已经缩放好**的图。

        取不到（动作不存在 / 帧号越界 / 文件坏）→ 返回**空 `QPixmap`**
        （`.isNull()` 为真），**永不抛异常**：调用方按"没有图可画"退化
        （agent.md §2.8 的降级约定）。空结果**不进缓存**——帧文件是外部输入，
        下次再问一次才有机会在用户补上文件后恢复。
        """
        key = self.cache_key(action, index, level)

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            return cached

        self.misses += 1
        pixmap = self._render(action, index, level)
        if pixmap.isNull():
            return pixmap

        self._cache[key] = pixmap
        self._evict()
        return pixmap

    def cache_key(self, action: str, index: int, level: int) -> tuple[str, int, int]:
        """缓存键。**拼键的唯一入口**——task06 加镜像只改这一处。

        档位先 `clamp_level`：越界档位与夹取后的档位**共用同一份缓存**，
        免得"配置里写 99"造出一堆永远不会再被问到的键。
        """
        return (action, int(index), size.clamp_level(level))

    def cached_count(self) -> int:
        """当前缓存里有几张图。"""
        return len(self._cache)

    def clear(self) -> None:
        """清空缓存（换素材包 / 换 DPI 环境时调用）。"""
        self._cache.clear()

    # ------------------------------------------------------------ 内部

    def _evict(self) -> None:
        """超出上限时丢掉**最久没用过**的那些。"""
        while len(self._cache) > self._limit:
            self._cache.popitem(last=False)

    def _render(self, action: str, index: int, level: int) -> QPixmap:
        """真正做缩放的地方：读原图 → 缩到该档像素尺寸 → `QPixmap`。"""
        image = pet_loader.load_frame_image(self.spec, action, index)
        if image.isNull():
            return QPixmap()

        target = size.pixel_size(image_size(image), level)
        if (image.width(), image.height()) == target:
            # 已经是目标尺寸（第 6 级 / 1.00×）就别走一趟缩放：
            # `SmoothTransformation` 对同尺寸也会拷一份，纯属浪费。
            scaled = image
        else:
            scaled = image.scaled(
                target[0],
                target[1],
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,  # D3 的最终选择，理由见模块开头
            )

        pixmap = QPixmap.fromImage(scaled)
        if pixmap.isNull():  # pragma: no cover - 只有显存/平台异常才会走到
            return pixmap
        return pixmap


def image_size(image: QImage) -> tuple[int, int]:
    """读一张 `QImage` 的像素尺寸（给"按图算目标尺寸"用）。

    单独抽出来是为了让 `size.pixel_size()` 的输入**永远是"帧文件实际尺寸"**，
    而不是 `spec.cell`——`cell` 与实际帧尺寸不一致时只是警告（agent.md §2.5），
    此时应当以**真实图**为准缩放，否则会拉伸变形。
    """
    return (image.width(), image.height())
