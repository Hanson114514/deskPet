"""渲染层：帧 → 目标尺寸的可绘制对象 + 缓存。

## 三件事，只有三件

1. **取缩放后的帧**：`FrameRenderer.frame(action, index, level, mirrored=False)`；
2. **缓存**：同一 `(动作, 帧序号, 档位, 镜像)` 只缩放一次（放大**只在换档 / 首次取帧时**做，
   绝不每帧做）；
3. **上限**：缓存有明确上限，来回切档不会把内存吃满。

缩放算法取 **`SmoothTransformation`**（附录 D 的 D3 二选一里的前者）。
理由：素材是 1 位 alpha 的硬边图，放大时最近邻会把台阶等比放大得很显眼；
`SmoothTransformation` 在放大时做双线性/双三次插值，边缘的台阶被**柔化**，
而这份柔化正好和"`setMask` 的锯齿是已接受代价"（agent.md §0）不冲突。
代价是**边缘会出现半透明像素**，于是 `setMask` 的区域可能比"纯 0/255"时略小一圈——
可点范围与视觉范围仍然一致（都由同一张缩放图算出来），符合 §2.8 的约定。

## 缓存键：四个维度，一个都不能少（task06 已补齐）

键是 `(动作, 帧序号, 档位, **镜像**)`。镜像这一维是 task06 补上的：
漏掉它的表现是"翻转后拿到未翻转的缓存图"（agent.md §4.7 / 迁移清单 §3.20
点名的**必然踩点**）——第一次翻转看起来对（缓存还没这张），第二次开始错。
`cache_key()` 是本文件里**唯一**拼键的地方。

## 镜像在哪一层做

**渲染层**，用 `QImage.flipped(Qt.Horizontal)`：不生成新素材、不改 `asset.json`、
不动原 PNG（task06 §4 渲染契约）。镜像**不影响锚点与档位**——
水平翻转折点对称，"右下角锚点不动"仍然成立。

## 边缘抗锯齿是可选的第二步（task15）

`FrameRenderer(..., antialias=True)` 时，**缩放之后**会再走一趟
`antialias.smooth_edges()` 加一圈过渡像素。默认 `False`，此时本文件的行为
与 task15 之前**完全一致**（那条"边缘台阶是已接受的取舍"的决策仍然成立）。

调用点**在"缩放 or 短路"的 if/else 之外**，所以**第 6 级那条短路路径
（零重采样）也会经过抗锯齿**——它是台阶最硬、收益最大的一档。
理由与位置见 `_render()` 里的注释（那段是硬要求，改动前先读 task15 §4）。

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

from . import antialias, pet_loader, size
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

    def __init__(
        self,
        spec: PetSpec,
        dpi_scale: float = 1.0,
        limit: int = DEFAULT_CACHE_LIMIT,
        antialias: bool = False,
    ) -> None:
        self.spec = spec
        self.dpi_scale = float(dpi_scale) if dpi_scale else 1.0
        self._limit = max(1, int(limit))
        #: 边缘抗锯齿开关（task15）。**默认 `False`**，守回归基线：
        #: 默认行为与 task15 之前**逐字节相同**（§0 / §2.8 / §4.9 / §4.12 那四条
        #: "锯齿是已接受的取舍"仍然成立）。
        #:
        #: ⚠ **不进缓存键**：生效方式是**重启生效**（用户拍板），
        #: 于是开关在**一个进程的生命周期内恒定**，同一个键永远只对应一种渲染结果。
        #: 少一个维度就少一处会分叉的地方（`cache_key()` 保持 4 维不动）。
        self.antialias = bool(antialias)
        #: `键 -> QPixmap`；`OrderedDict` 的**尾部是最新**（LRU 淘汰头部）。
        self._cache: "OrderedDict[tuple[str, int, int, bool], QPixmap]" = OrderedDict()
        #: 统计信息（自检看"有没有真的走缓存"用，不影响行为）。
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------ 对外

    def frame(self, action: str, index: int, level: int, mirrored: bool = False) -> QPixmap:
        """取 `action` 第 `index` 帧在 `level` 档下**已经缩放好**的图。

        `mirrored=True` 时返回**水平翻转后**的图（在渲染层做，见模块开头）。

        取不到（动作不存在 / 帧号越界 / 文件坏）→ 返回**空 `QPixmap`**
        （`.isNull()` 为真），**永不抛异常**：调用方按"没有图可画"退化
        （agent.md §2.8 的降级约定）。空结果**不进缓存**——帧文件是外部输入，
        下次再问一次才有机会在用户补上文件后恢复。
        """
        key = self.cache_key(action, index, level, mirrored)

        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            self.hits += 1
            return cached

        self.misses += 1
        pixmap = self._render(action, index, level, mirrored)
        if pixmap.isNull():
            return pixmap

        self._cache[key] = pixmap
        self._evict()
        return pixmap

    def cache_key(
        self, action: str, index: int, level: int, mirrored: bool = False
    ) -> tuple[str, int, int, bool]:
        """缓存键。**拼键的唯一入口**（含镜像维度，见模块开头）。

        档位先 `clamp_level`：越界档位与夹取后的档位**共用同一份缓存**，
        免得"配置里写 99"造出一堆永远不会再被问到的键。
        """
        return (action, int(index), size.clamp_level(level), bool(mirrored))

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

    def _render(self, action: str, index: int, level: int, mirrored: bool = False) -> QPixmap:
        """真正做缩放的地方：读原图 → **按需镜像** → 缩到该档像素尺寸 → `QPixmap`。

        **镜像在缩放之前做**：先翻再缩与先缩再翻在 `IgnoreAspectRatio` +
        等比档位下结果等价，但先翻少一次全尺寸的拷贝（源图就是原尺寸）。
        翻转本身用 `QImage.flipped(Qt.Horizontal)`，**不产生新素材文件**。
        （`QImage.mirrored()` 在当前 PySide6 里已标记弃用，别再改回去。）
        """
        image = pet_loader.load_frame_image(self.spec, action, index)
        if image.isNull():
            return QPixmap()

        if mirrored:
            image = image.flipped(Qt.Horizontal)

        target = size.pixel_size(image_size(image), level)
        if (image.width(), image.height()) == target:
            # 已经是目标尺寸（第 6 级 / 1.00×）就别走一趟缩放：
            # `SmoothTransformation` 对同尺寸也会拷一份，纯属浪费。
            processed = image
        else:
            processed = image.scaled(
                target[0],
                target[1],
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation,  # D3 的最终选择，理由见模块开头
            )

        # ---------------------------------------------------------- 边缘抗锯齿（task15）
        #
        # ⚠⚠ **调用点必须在上面那个 if/else 之"外"**（task15 §4 的硬要求）。
        #
        # 写成"两个分支各自处理"必然漏掉某一档：第 6 级走的是**短路分支**
        # （零重采样、原图直接交出），把它写在 `else` 里就等于"6 档没有抗锯齿"
        # ——而 6 档恰恰是台阶最硬、收益最大的那一档。
        # 反过来写在 `if` 里则会漏掉其余 9 档。**只有放在分支之外，
        # 全部 10 个档位才都流经同一个出口**（自检里用"每档结果都 != 关闭时"
        # 盯着这个结构）。
        #
        # **位置在"缩放之后"**，理由有两条：
        #   ① 放大档（7–10）本来就是 `SmoothTransformation` 产生的过渡像素，
        #      放在缩放**之前**会让它们**被柔化两次**，细节损失比 6 档大得多；
        #   ② 放在之后，过渡带宽度才与"最终看到的那张图"的像素一一对应
        #      （放在之前，放大后过渡带会被一起放大成好几像素宽）。
        if self.antialias:
            processed = antialias.smooth_edges(processed)

        pixmap = QPixmap.fromImage(processed)
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
