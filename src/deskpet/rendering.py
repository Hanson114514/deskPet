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

## 上限：内存预算说了算（task16）

**淘汰策略仍是纯 LRU**（`OrderedDict`，按使用顺序淘汰头部）；
task16 改的只是"上限怎么定"。

task04 定的 `DEFAULT_CACHE_LIMIT = 64` 是一个**拍脑袋的常量**，它比
`weedy.idle` 的 **90 帧**还小 → 缓存被反复冲洗 → **每一帧都是未命中、
每一帧都在重新缩放**（task15 实测：连播两轮 `hits=0 / misses=90`，
与抗锯齿开关无关）。所以上限不能再是常量，必须与"当前动作有多少帧、
这一档一张多大"挂钩。

**上限 = `limit_for(action_frames, level)`**，规则两条（真值都在本文件）：

1. **内存预算是硬顶**：`MEMORY_BUDGET_BYTES` 折算成"这一档最多放几张"
   ——这是"内存必须有明确上限"那条验收的落点；
2. **再夹到 `[MIN_CACHE_LIMIT, MAX_CACHE_LIMIT]`**：下限保证小动作
   （`waving` 30 帧）与换档过渡也留得住几张，上限防"预算配错到天上去"。

⚠ **实测纠正了 task16 卡里的两个数字**（2026 量出来，以实现为准）：
卡里写"`weedy_sightseer` 有个 **600 帧**的动作"——**不存在**，
它 6 个动作最大 180 帧（`rest`）；真正最大的是 **`weedy.waiting` 240 帧**。
卡里的单张内存（第 6 级 0.44 MB）也偏小 **2.2 倍**，实测第 6 级
500×500 的 `QImage.sizeInBytes()` = **1 000 000 B（0.954 MB）**、
第 10 级 = **4 000 000 B（3.815 MB）**（都是 32 位 `ARGB32`）。
于是"按动作帧数全留"在 `waiting` 上会吃到 229 MB（第 6 级）/
916 MB（第 10 级）——**被预算挡住是设计意图**，不是 bug。

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

#: 缓存上限的**兜底默认值**（张）。
#:
#: ⚠ task16 起这个常量**不再是实际生效的上限**：真正生效的是
#: `FrameRenderer.limit`（由 `limit_for(action_frames, level)` 算出来）。
#: 留着它是因为 `FrameRenderer(limit=...)` 需要一个缺省参数，
#: 而这个值同时是 `MIN_CACHE_LIMIT` 的来源——**别在别处再写一个 64**。
#: task04 选它的理由是"够留当前档 + 前一档"，而 task15 实测证明
#: 它比 `idle` 的 90 帧还小，反而把缓存打成了"永远不命中"。
DEFAULT_CACHE_LIMIT: Final[int] = 64

#: 缓存占用的**内存预算**（字节）。**这是"内存必须有明确上限"的真值**。
#:
#: 160 MiB 是用户 2026 拍板的数（task16 §4 的 D1）。它管的是**已经渲染好的
#: `QPixmap` 的总量**，不含 Qt 内部的解码缓冲与源 PNG。
#:
#: ⚠ 折算时用**该档的满张大小**（`bytes_per_frame`），所以它是"最坏情况"的
#: 上限：实际占用只可能更小（空帧不进缓存，见 `frame()`）。
MEMORY_BUDGET_BYTES: Final[int] = 160 * 1024 * 1024

#: 上限的**上限**（张）。防的是"预算或帧数被配到荒唐的值"这一种失控；
#: 正常路径永远碰不到它（第 1 级的额度 2684 张，离它还有一截）。
MAX_CACHE_LIMIT: Final[int] = 4096

#: 每个像素占几个字节。**32 位 `ARGB32` = 4**（实测
#: `QImage.sizeInBytes() == 宽 × 高 × 4`，与 `depth() == 32` 一致）。
BYTES_PER_PIXEL: Final[int] = 4


def bytes_per_frame(pixel_size_: tuple[int, int]) -> int:
    """一张该尺寸的帧占多少字节（`宽 × 高 × 4`）。

    这是**上界估算**，也是 `MEMORY_BUDGET_BYTES` 的折算依据：缓存里存的是
    `QPixmap`，其内部格式与 `QImage` 同为 32 位，实测两者尺寸一致。

    尺寸非正 → 返回 1（避免后面除零，永远给得出一个能用的数字）。
    """
    width = max(0, int(pixel_size_[0]))
    height = max(0, int(pixel_size_[1]))
    return max(1, width * height * BYTES_PER_PIXEL)


def limit_for(action_frames: int, level: int, cell: tuple[int, int]) -> int:
    """算这一档该留几张（task16 的**唯一**上限算法）。

    ## 规则

    1. 预算折算：`MEMORY_BUDGET_BYTES // bytes_per_frame(pixel_size(cell, level))`
       —— 该档最多放得下几张。**这就是上限本身**（`MAX_CACHE_LIMIT` 只防
       "预算被配到天上去"这种失控，正常路径碰不到）。

    ## 为什么不直接"上限 = 动作帧数"（task16 §4 的方案 A 原样）

    实测：`weedy.waiting` 有 **240 帧**，第 10 级一张 **3.815 MB** →
    全留是 **916 MB**；第 6 级也有 **229 MB**。用户拍板"按 160 MB 硬预算卡住"，
    于是**能全命中的动作就全命中**（`idle` 90 帧 × 0.954 MB = 86 MB < 160 MB ✓）、
    **放不下的动作按预算截断**（`waiting` 第 6 级只留 167 张）。

    `action_frames` **不参与**计算，只用来给调用方与日志一个说法：
    上限与"当前动作有多少帧"无关，只与"这一档一张多大"有关——
    这样换动作**不需要**通知渲染层（少一条状态，见 agent.md §A.2 的同一条理由）。
    """
    per_frame = bytes_per_frame(size.pixel_size(cell, level))
    #: 这一档的额度（张）。**它就是上限**——不要再给它加"至少留 N 张"的下限：
    #: 第 10 级一张 3.815 MB，额度只有 41 张；托底到 64 张就是 244 MB，
    #: 比 160 MB 的预算还大 53%（task16 实测踩到的第一条，别改回去）。
    limit = MEMORY_BUDGET_BYTES // per_frame
    return max(1, min(MAX_CACHE_LIMIT, limit))


def budget_for(action_frames: int, level: int, cell: tuple[int, int]) -> int:
    """`limit_for()` 对应的**字节数**（自检断言"条目数 × 单张 ≤ 预算"用）。

    单独提供一个函数是为了让那条断言与上限算法**共用同一个折算**，
    而不是在自检里重算一遍（重算就等于第二份真值，迟早分叉）。
    """
    return limit_for(action_frames, level, cell) * bytes_per_frame(size.pixel_size(cell, level))


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
        limit: int | None = None,
        antialias: bool = False,
    ) -> None:
        self.spec = spec
        self.dpi_scale = float(dpi_scale) if dpi_scale else 1.0
        #: 上限（张）。`None` = **按内存预算自动算**（task16 的默认行为，
        #: 真实产品路径走这条）；显式给数 = 调用方自己说了算（自检里钉边界用）。
        #:
        #: ⚠ 自动模式下上限是**跟着档位走**的（第 1 级一张 0.06 MB 能放几千张、
        #: 第 10 级一张 3.8 MB 只放得下 42 张），所以不是构造期算一次就完事——
        #: 每次 `frame()` 都会按**这次请求的档位**重新问一次 `_limit_for_level()`。
        self._explicit_limit: int | None = None if limit is None else max(1, int(limit))
        #: 上一次算出来的上限（只为了让 `limit` 属性不必重复算；**不是真值**）。
        self._limit_cache: dict[int, int] = {}
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
        #: 最近一次被请求的（动作, 档位）。**只用于诊断与 `limit` 属性**：
        #: 上限的自动算法要问"现在这一档一张多大"，而档位不在构造期定死
        #: （窗口会切档）。这两个值**不参与缓存键**，也不会让行为分叉。
        self._last_action: str = ""
        self._last_level: int = size.DEFAULT_LEVEL

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
        self._last_action = action
        self._last_level = size.clamp_level(level)

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
        self._evict(level)
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

    @property
    def limit(self) -> int:
        """**当前生效**的缓存上限（张）。

        自动模式下跟着**最近一次请求的档位**走——这是唯一一处"上限是多少"
        的对外说法，自检与诊断都读它，别再去猜 `DEFAULT_CACHE_LIMIT`
        （那个常量 task16 起只是兜底参数了）。
        """
        return self._limit_for_level(self._last_level)

    def limit_for_level(self, level: int) -> int:
        """**指定档位**的上限（张）。换档过渡的账要按"两边档位"分别算时用它。"""
        return self._limit_for_level(level)

    def clear(self) -> None:
        """清空缓存（换素材包 / 换 DPI 环境时调用）。"""
        self._cache.clear()

    # ------------------------------------------------------------ 内部

    def _limit_for_level(self, level: int) -> int:
        """这一档的上限。显式给了 `limit` 就用它（自检钉边界用），否则按预算算。"""
        if self._explicit_limit is not None:
            return self._explicit_limit
        level = size.clamp_level(level)
        cached = self._limit_cache.get(level)
        if cached is None:
            cached = limit_for(self._current_action_frames(), level, self.spec.cell)
            self._limit_cache[level] = cached
        return cached

    def _current_action_frames(self) -> int:
        """当前动作有多少帧（**只进日志与诊断**，不参与上限计算）。

        见 `limit_for()` 的说明：上限只与"这一档一张多大"有关，
        与动作帧数无关——所以换动作**不需要**通知渲染层。
        """
        action = self.spec.action(self._last_action)
        return action.frame_count if action is not None else 0

    def _evict(self, level: int) -> None:
        """超出**该档**上限时丢掉**最久没用过**的那些（纯 LRU，task16 未改策略）。

        ## 为什么上限按"刚写进来的那一档"算

        缓存的键是**跨档位共存**的（键里含档位）。但每张图的**大小**由档位决定，
        差两个数量级（第 1 级 0.06 MB / 第 10 级 3.8 MB）。若按"最省的那一档"
        定上限，来回切到高档就会把内存吃穿；按"最贵的那一档"定上限，
        正常用低档时又白白浪费额度。

        取"这次请求的档位"是最贴近真实占用的一侧：出现高档条目时按高档收紧，
        只有低档条目时按低档放宽——**内存永远不超过预算**，而缓存尽可能留住。
        """
        limit = self._limit_for_level(level)
        while len(self._cache) > limit:
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
