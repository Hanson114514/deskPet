"""边缘抗锯齿：**纯算法**（输入 `QImage` → 输出 `QImage`）。

`agent.md` 附录 D 的 D3 决策是"`SmoothTransformation` 缩放、不额外抗锯齿"，
而 §0 / §2.8 / §4.9 / §4.12 **四处**都把"`setMask` 的锯齿"记成**已接受的取舍**。
本模块是 task15 加上的**可选**补充：默认关闭时一行都不会被调用，
默认行为与 task15 之前**逐字节相同**，那四条决策仍然成立。

## 为什么需要它

素材源 GIF 是 **1 位 alpha**（只有 0 / 255，§4.12），所以抽出的 PNG 边缘天生
是硬台阶。第 6 级（1.00×）走 `rendering._render()` 里的短路、零重采样，
台阶原样交给用户看；深色人物 + 黑描边在浅色明亮背景上是黑↔白极限对比，
台阶被放大得很显眼。加一圈**过渡像素**就是在解决这件事。

## ⚠ 头号技术坑：必须先膨胀 RGB 再模糊 alpha

透明像素的 RGB 通常是**垃圾值**（GIF 抽帧后留下的是底色或 0）。若只处理 alpha，
边缘新产生的半透明像素会在合成时与背景错误混合：

- 透明区 RGB 是**黑** → 边缘出现**黑边**；
- 透明区 RGB 是**白** → 边缘出现**白边**。

这就是"鬼影"，在浅色背景下**比原锯齿还难看**。所以顺序**不能反**：

1. 把 RGB 从最近的不透明像素**向外扩散**（dilate）到整圈透明区；
2. 才去模糊 alpha。

膨胀之后，半透明像素的颜色**一定取自"最近的不透明像素"**，
与背景混合出来的就是人物本来的颜色，两种背景下都不会发黑/发白。

## 为什么用 numpy（task15 实测后**用户拍板**引入）

同一素材（`weedy` / `idle` 第 0 帧 / 500×500）两种实现的实测对比：

| 实现 | 单帧耗时 |
| --- | --- |
| 纯 Python 逐像素 | **1885 ms** |
| numpy 向量化 | **49 ms** |

纯 Python 慢 **38 倍**，远超 33 ms 的帧预算，完全不可用；两者**结果一致**
（本体被破坏 0 个像素、新增 2074 个过渡像素，逐像素对得上）。
耗时只落在**缓存未命中**（换档 / 首次取帧）那一拍，日常播放走
`rendering.FrameRenderer` 的 LRU 缓存、本模块一次都不会被调用。

⚠ 这是本项目除 PySide6 外的**唯一**第三方依赖，且**已由用户明确同意**
（`requests.md` 的红线是"不要自动安装依赖，有需求要问"）。
它不需要 `pip install`：本机 Python 里已经有 numpy。

## 在 premultiplied 空间下操作

`pet_loader` 已经把图转成 `Format_ARGB32_Premultiplied`（`pet_loader.py` 第 757 行）。
处理时转成 `Format_ARGB32`（**非预乘**）：预乘格式下 `(r, g, b)` 已经乘过 alpha，
直接做加权平均得到的不是"颜色"，而是"颜色×alpha"，膨胀搬过来就是错的。
转回时交给 `convertToFormat()` 由 Qt 精确地做预乘（**不手写 `r * a // 255`**，
那会引入舍入误差）。

## 边界（硬规则，逐条对应 task15 §4）

| 规则 | 怎么做到 |
| --- | --- |
| **守住人物本体** | 原 `alpha == 255` 的像素：alpha 取 `max(原值, 模糊值)`，RGB 直接沿用原图 |
| **不改素材** | 全程在内存里做，不写任何文件 |
| **半径克制** | `DEFAULT_RADIUS = 1`：过渡带 1 像素。加宽会让细发丝、细装饰变糊，并明显加重遮罩复杂度 |
| **降级** | 空图 / 尺寸非正 / 无 alpha / 任何异常 → **原样返回输入**（宁可没抗锯齿，不能没画面） |

## 幂等性

不要求 `smooth_edges(smooth_edges(x)) == smooth_edges(x)`（task15 §4 明写）。
"同一张图只算一次"由 `rendering.FrameRenderer` 的 LRU 缓存保证。

## 性能

按需处理：调用方（渲染层）**取哪帧算哪帧**，绝不批量预处理全部帧
（`weedy_sightseer` 有 600 帧的动作，批量会卡）。启动时集中运算那条路
已在 task15 §5 评估并否决，**不要重新论证**。
"""

from typing import Final

import numpy as np
from PySide6.QtGui import QImage

#: 过渡带宽度（像素）。**建议 1，先别贪大**（task15 §4 硬规则第 5 条）：
#: 加宽会让细节（细发丝、细装饰）变糊，且会明显加重遮罩复杂度。
DEFAULT_RADIUS: Final[int] = 1


def smooth_edges(image: QImage, radius: int = DEFAULT_RADIUS) -> QImage:
    """给 ``image`` 的边缘加一圈过渡像素，返回**新图**。

    **本函数永不抛异常**：任何异常都**原样返回输入**（降级 = 没抗锯齿，
    不是没画面；与 §2.8 的降级口径一致）。

    输入应当是带 alpha 的图（`pet_loader` 给的是 `Format_ARGB32_Premultiplied`）。
    下列情况**直接返回输入本身**（连拷贝都不做）：

    - 空图 / 宽或高非正；
    - **没有 alpha 通道**（那本来就没有"边缘"可言）；
    - ``radius <= 0``。

    返回值是**新构造的 `QImage`**，与输入不共享像素（调用方可以放心缓存）。
    """
    try:
        if image is None or image.isNull():
            return image
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return image
        if not image.hasAlphaChannel():
            return image
        if int(radius) <= 0:
            return image

        # 非预乘格式：权重和是 2 的幂，全程只有加法与乘法，不需要除法。
        source = _to_numpy(image)
        if source is None:  # pragma: no cover - 只有平台异常才会走到
            return image

        rgb = _dilate_rgb(source, int(radius) + 1)
        blurred = _blur_alpha(source[:, :, 3], int(radius))
        return _compose(image, rgb, blurred)
    except Exception:
        # 降级路径**不许再抛异常**（§2.8：可恢复路径永不抛异常）。
        return image


# ------------------------------------------------------------------ 内部步骤


def _to_numpy(image: QImage) -> "np.ndarray | None":
    """`QImage` → `(高, 宽, 4)` 的 `uint8` 数组，通道顺序 **BGRA**（Qt 的内存布局）。

    先转 `Format_ARGB32`（**非预乘**）：后面要做的是"按分量加权平均"，
    预乘格式下颜色分量已经乘过 alpha，平均出来的是"颜色×alpha"而不是颜色。

    ⚠ 必须按 `bytesPerLine()` 切行再截到 `width()`：Qt 的扫描行有**4 字节对齐**
    填充，直接按 `width * 4` 重塑会让图**斜着错位**（宽度不是 4 的倍数时必现）。
    """
    converted = image.convertToFormat(QImage.Format_ARGB32)
    if converted.isNull():  # pragma: no cover
        return None
    height = converted.height()
    width = converted.width()
    buffer = converted.constBits()
    flat = np.frombuffer(bytes(buffer), dtype=np.uint8)
    expected = converted.bytesPerLine() * height
    if flat.size < expected:  # pragma: no cover - 平台异常
        return None
    return flat[:expected].reshape(height, converted.bytesPerLine() // 4, 4)[:, :width, :]


def _dilate_rgb(source: "np.ndarray", rings: int = 2) -> "np.ndarray":
    """把 RGB 从**不透明像素**向外扩散 `rings` 环，返回 `(高, 宽, 3)` 的 `uint8`。

    ## 为什么只扩散 RGB、不动 alpha

    alpha 是"这个像素盖住多少"，它要留给 `_blur_alpha()` 去模糊；
    这里要解决的是"透明区的 RGB 是垃圾值"这件事。两者分开做，
    膨胀的结果只在**合成阶段**与 alpha 配对使用。

    ## 取"最近的不透明邻居"，不做加权平均（task15 实测后的选择）

    第一版做的是"邻居加权平均"（权重 = 邻居 alpha × 距离权重）。实测：

    | 写法 | 1000×1000 耗时 | 边缘色是否等于某个邻居 |
    | --- | --- | --- |
    | 加权平均（三维掩码乘法） | **146 ms** | 会**混**出邻居都没有的颜色 |
    | 加权平均（布尔索引累加） | 97 ms | 同上 |
    | **最近邻优先**（本实现） | **30 ms** | **是**，严格等于某个邻居 |

    "最近邻优先"不但**快 5 倍**，而且正好就是验收要求的语义
    （task15 §6：*"边缘像素颜色取自最近的不透明像素"*）。
    加权平均会把两个不同颜色的邻居混成一个中间色，在凹陷拐角处
    反而**造出素材里不存在的颜色**——那既不必要，也不好证明。

    ## 顺序：由近到远逐环扩散

    距离 1 的四个正交邻居先填，距离 2 的四个对角邻居再补空位
    （`~filled`）。于是每个新像素拿到的是**曼哈顿距离最近**的那个邻居，
    同等距离时取"先遍历到的那个"（顺序固定，所以结果**可复现**）。

    ## ⚠ 为什么"一遍 8 邻域"不够（task15 实测）

    一开始这里只跑一遍 8 邻域。实测（`idle` 第 0 帧 / 500×500）发现
    **2074 个边缘新像素里没有一个的颜色等于相邻的不透明像素**，
    而且它们到最近不透明像素的距离分布是 `{1: 906, 2: 124, 3: 1044}`——
    **有一多半在距离 2~3**。

    原因：`_blur_alpha()` 把 alpha 从"任何 alpha>0 的像素"往外抹一圈，
    而**放大档位下源图本来就有半透明像素**（`SmoothTransformation` 产生的）。
    于是 alpha 的过渡带会**沿着这些半透明像素继续外推**，
    比"一遍膨胀"走得更远；走不到的地方就留着透明区的垃圾颜色 → 鬼影。

    对策：**膨胀的环数必须 ≥ alpha 能扩散的环数**。这里按
    `radius + 1` 环迭代扩散（`radius` 是 alpha 模糊半径，多留一环做保险），
    每环把上一环已填的区域当作新的"不透明"来源。
    便宜：每环只是一次布尔索引赋值，实测仍在 30 ms 量级。

    已经是不透明的像素**不动**：它自己就是颜色的来源。
    """
    height, width = source.shape[0], source.shape[1]
    opaque = source[:, :, 3] > 0

    colour = source[:, :, :3].astype(np.uint16)
    result = np.array(colour)
    filled = np.array(opaque)

    #: 正交（距离近）在前，对角在后；顺序固定 → 结果可复现。
    steps = ((0, -1), (0, 1), (-1, 0), (1, 0),
             (-1, -1), (-1, 1), (1, -1), (1, 1))
    for _ in range(rings):
        # 每一环都从"当前已经填好的区域"再往外扩一格。
        # 边界补 0（透明）：图外的邻居自然没有投票权。
        padded_colour = np.pad(result, ((1, 1), (1, 1), (0, 0)))
        padded_filled = np.pad(filled, 1)
        for dy, dx in steps:
            neighbour = padded_colour[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width, :]
            mask = padded_filled[1 + dy : 1 + dy + height, 1 + dx : 1 + dx + width]
            take = mask & ~filled
            result[take] = neighbour[take]
            filled |= mask

    return result.astype(np.uint8)


def _blur_alpha(alpha: "np.ndarray", radius: int) -> "np.ndarray":
    """按 `radius` 的方形窗口**加权平均**模糊 alpha（权重同 `_NEIGHBOURS` 的 3×3）。

    半径 1 时就是那个 3×3：中心 4、正交 2、对角 1，权重和 16。
    `radius > 1` 时按同样的"正交 2 / 对角 1 / 其余 1"规则铺开——
    本模块默认只用半径 1（见 `DEFAULT_RADIUS`），这条路是给将来留的口子。

    累加同样用 **uint16**（理由见 `_dilate_rgb`）：半径 1 时最大
    `255 × 16 = 4080`，装得下。半径越大权重和越大，所以按需选更宽的整数类型。
    """
    height, width = alpha.shape
    padded = np.pad(alpha.astype(np.uint16), radius)
    # 半径 1 → 权重和 16；半径 r → (3 + 4(r-1))²... 直接按累加上限挑类型，
    # 免得半径调大之后**静默溢出**（uint16 溢出是回绕，画面会出现反色噪点）。
    max_total = 255 * int(sum(
        4 if (dx == 0 and dy == 0) else (2 if (dx == 0 or dy == 0) else 1)
        for dy in range(-radius, radius + 1)
        for dx in range(-radius, radius + 1)
    ))
    dtype = np.uint16 if max_total <= 65535 else np.uint32

    total = np.zeros((height, width), dtype)
    weight_sum = 0.0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx == 0 and dy == 0:
                weight = 4
            elif dx == 0 or dy == 0:
                weight = 2
            else:
                weight = 1
            total += (
                padded[radius + dy : radius + dy + height, radius + dx : radius + dx + width]
                * weight
            )
            weight_sum += weight
    if weight_sum == 0:  # pragma: no cover - 尺寸非正时才会走到
        return alpha.astype(np.float32)
    return total.astype(np.float32) / weight_sum


def _compose(
    original: QImage,
    rgb: "np.ndarray",
    blurred: "np.ndarray",
) -> QImage:
    """把"膨胀后的 RGB"与"模糊后的 alpha"拼成结果，并转回预乘格式。

    ⚠ **不能只取膨胀后的 RGB 就完事**：那张图里人物内部像素的 RGB 是对的，
    但它整张图都带着 alpha；模糊结果里 alpha 是对的、RGB 却无从谈起。
    两张拼起来才是"颜色取自最近的不透明像素 + 边缘带过渡 alpha"。

    ## 两条硬规则在这里落地

    | 规则 | 做法 |
    | --- | --- |
    | **守住人物本体** | 原 alpha = 255 的像素：RGB 直接沿用原图；alpha 取 `max(原值, 模糊值)`，**只增不减** |
    | **颜色取自最近的不透明像素** | 非本体像素的颜色一律取 `rgb`（膨胀结果），**绝不取透明区的原 RGB** |

    alpha 取 `max` 是关键的一条：模糊会把边缘的 255 往下拉，
    若直接采用模糊值，人物会被**整体削薄一圈**——那就不是抗锯齿，
    而是"把整张图糊了"（task15 §4 硬规则第 1 条）。
    """
    height, width = rgb.shape[0], rgb.shape[1]
    source = _to_numpy(original)
    if source is None:  # pragma: no cover
        return original

    original_alpha = source[:, :, 3].astype(np.float32)
    new_alpha = np.maximum(original_alpha, blurred)

    # 本体：颜色**一个比特都不许动**；其余：膨胀来的颜色（= 最近的不透明邻居）。
    body = original_alpha >= 255.0
    out = np.empty((height, width, 4), np.uint8)
    out[:, :, 0:3] = np.where(body[:, :, None], source[:, :, :3], rgb)
    # 四舍五入而不是截断：截断会让整圈 alpha 系统性偏低（边缘发虚）。
    out[:, :, 3] = np.clip(np.rint(new_alpha), 0, 255).astype(np.uint8)

    # BGRA（Qt 内存布局）→ 交回 Qt，由它精确地做预乘（`Format_ARGB32_Premultiplied`）。
    #
    # ⚠ **`QImage(bytes, ...)` 不拷贝缓冲区**（task15 实测踩到，最难查的一个坑）：
    # 构造函数只是**引用**那块内存。写成 `QImage(np.ascontiguousarray(out).tobytes(), ...)`
    # 时，那个 `bytes` 临时对象在语句结束后立刻被回收，`QImage` 就指向了已释放的内存
    # ——RGB 变成 0、alpha 却还在（那部分内存恰好没被覆写）。
    # 表现是"**膨胀算对了，但输出全是黑的**"，而且**时好时坏**：
    # 实测同一帧里 3852 个像素的 RGB 凭空变成 0，而 `_dilate_rgb` 明明返回了正确的颜色。
    # 对策：**把缓冲区绑在一个活到函数结束的局部变量上**（`buffer`），
    # 并且**立刻 `copy()`** 出一份由 Qt 自己持有的图再返回。
    buffer = np.ascontiguousarray(out).tobytes()
    result = QImage(buffer, width, height, QImage.Format_ARGB32).copy()
    if result.isNull():  # pragma: no cover
        return original
    return result.convertToFormat(QImage.Format_ARGB32_Premultiplied)
