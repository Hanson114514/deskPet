"""命中判定与窗口区域的**唯一入口**（agent.md §2.8 / 附录 D 的 D2）。

## 两件事分开

- **取哪一帧的图**是调用方（`pet_window`）的事；
- **怎么由这张图算出可点区域**是本模块的事。

`region_for_image()` 就是 task01 放在 `pet_window.py` 里的那份实现，
本 task 按 §2.8 的说法**搬到这里**（"不要写第二份"），`pet_window` 只保留转发。

## 坐标：窗口内的**逻辑**像素

`hit_test()` 收的 `pos` 是 `QMouseEvent.position()`（窗口内坐标），
**不是** `globalPosition()`（屏幕坐标）——两者不可混用（agent.md §2.8 / §4.9）。
镜像时按 `x' = width - 1 - x` 翻转（task08 §4 明写的公式）。

## 降级：算不出来就整帧可点，永不抛异常

"无遮罩 / 坏数据 / 帧号越界 / 动作不存在 → **整帧可点**"是硬规则
（task08 §4 的"必须保留的降级语义"，也是 agent.md §2.8 的降级约定）。
手写素材包少一个可选文件不该变成"拖不动"。

## 关于 `setMask` 与命中判定的关系

D2 定案是 `setMask(QRegion)`：区域外的点击由**系统级穿透**，窗口根本收不到，
所以"能收到 `mousePressEvent` 就说明按在人物身上"。既然如此，
`hit_test()` 的用处是**自检与诊断**（"这个点该不该起拖"要能在无窗口时断言），
以及将来若有人改成"自窗口内自己判"时的现成入口。
**窗口的事件处理器不重复调用它**——那会在遮罩之外再造一份判定真值。
"""

from typing import Final

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBitmap, QGuiApplication, QImage, QRegion

#: 遮罩的默认不透明阈值：走 Qt 自带的 `QImage.createAlphaMask()`。
#: 实测（用一张 `alpha = 0..255` 的单行图探出来）它的判据**恰好是 `alpha >= 128`**。
#: 真实素材的源 GIF 是 1 位 alpha（只有 0 / 255），这个阈值不影响结果（§4.12）。
#: 本模块**不自己数像素**——"按 alpha 判定"会失去真穿透（§4.9 写过理由）。
DEFAULT_ALPHA_THRESHOLD: Final[int] = 128

#: 开了边缘抗锯齿之后要用的**低阈值**（task15 §6 的"可点范围允许沿轮廓扩大"）。
#:
#: ## 为什么必须降（这是 task15 实测出来的一条硬事实）
#:
#: `createAlphaMask()` 是**二值**的（`alpha >= 128`）。而抗锯齿的本质就是
#: 产生**半透明**的过渡像素——实测那 2074 个新像素的 alpha 分布是
#: `1–32: 27%` / `33–64: 42%` / `65–96: 26%` / `97–127: 2%`，
#: **绝大多数落在 32~96 之间**。于是用 128 去筛，**只有 53 个能上屏**，
#: 其余 2021 个被系统级穿透直接裁掉 —— **抗锯齿等于白做，肉眼完全看不出**。
#: （这不是推理：用户先反馈"感觉没什么变化"，随后按上面的方式量出来的。）
#:
#: ## 为什么是 16
#:
#: 实测（`idle` 第 0 帧 / 第 6 级 / 500×500，先与真实现校准过）：
#:
#: | 阈值 | 能上屏的过渡像素 | 可点范围 |
#: | --- | --- | --- |
#: | 128（默认） | **53** | 基线 6.74% |
#: | 64 | 1123 | +0.40 个百分点 |
#: | 32 | 1546 | +0.56 |
#: | **16** | **2074（全部）** | **+0.81** |
#: | 8 / 4 / 2 / 1 | 2074（与 16 完全相同） | +0.81 |
#:
#: **16 是"全部过渡像素都上屏"的最小阈值**：再往下降结果一模一样，
#: 而代价固定为"可点范围沿轮廓外扩约 0.8 个百分点、外接矩形 157×226 → 159×228"。
#: 这个代价**用户明确接受**（task15 §1："允许可点范围沿轮廓适当扩大"；
#: 凹陷小结构不穿透是**预期行为**，不要求修复）。
ALIASED_ALPHA_THRESHOLD: Final[int] = 16


def can_build_region() -> bool:
    """现在能不能安全地造 `QBitmap` / `QRegion`。

    ## 为什么需要这个判据（task08 实测）

    `QBitmap.fromImage()` 在**没有 `QGuiApplication` 实例**时会**直接段错误**
    （Windows 上退出码 `0xC0000005`），**不是**抛异常——`try/except` 拦不住它。
    实测：同一段代码在有 `QApplication` 时正常给出区域，在没有时进程当场消失，
    连一行输出都留不下。

    所以"降级：算不出来就整帧可点，永不抛异常"（agent.md §2.8）这条约定
    必须**先问一句"现在能算吗"**，否则那句"永不抛异常"在无 app 的场景下
    变成了"直接崩掉"——比抛异常更糟。

    正常路径（`app.py` 建完 `QApplication` 才建窗口）永远为真，
    这个判据只在**自检 / 导入期**这类没有 app 的场景起作用。
    """
    return QGuiApplication.instance() is not None


def region_for_frame(
    frame_image: QImage | None,
    mirrored: bool = False,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> QRegion | None:
    """按 ``frame_image`` 的**不透明像素**生成窗口区域。

    返回 ``None`` 表示"**算不出来**"，调用方必须退化成**不设遮罩**（整帧可点）：

    - ``None`` / 空图 / 尺寸非正 → ``None``；
    - **没有 alpha 通道** = 整块不透明 → 返回整幅区域；
    - 不透明阈值：``alpha_threshold`` 默认是 Qt ``createAlphaMask()`` 的
      ``128``；开了边缘抗锯齿的调用方应传 ``ALIASED_ALPHA_THRESHOLD``（``16``），
      否则抗锯齿产生的半透明像素会被**全部裁掉**（理由与实测数据见那个常量）。

    ``mirrored=True``：图**已经翻过**时不要再翻一次——区域由"看到的这张图"
    算出来，于是"能点到的范围"与"看到的画面"永远一致（agent.md §2.14）。
    传进来的图**没翻**而要求镜像区域时，先翻再算。

    **带 `devicePixelRatio` 的图先折回逻辑尺寸**再生成区域：遮罩坐标是窗口内的
    逻辑像素，不折回的话区域会整体放大 ``ratio`` 倍（人物能点到的范围比看到的
    大一圈，高 DPI 下必现，task01 已有实现，别改回去）。

    ## 低阈值是怎么实现的（**不自己数像素**）

    ⚠ §4.9 那条"不自己按 alpha 判定，否则失去真穿透"**仍然成立**。
    所以这里**不自己造 `QBitmap`**：做法是先把"不达标的像素"的 alpha **改成 0**、
    达标的改成 255，然后再交给 Qt 的 ``createAlphaMask()`` 去二值化。
    阈值由我们定，**"怎么算不透明"仍然是 Qt 说了算**——没有第二份实现。
    阈值等于 128（默认）时走的就是原来那条路径，行为**逐字节不变**。

    本函数**永不抛异常**（agent.md §2.8 的降级约定）；没有 `QGuiApplication`
    实例时返回 ``None``（**不是**崩掉，理由见 `can_build_region()`）。
    """
    if frame_image is None or frame_image.isNull():
        return None
    if not can_build_region():
        return None

    try:
        source = frame_image
        ratio = float(source.devicePixelRatio() or 1.0)
        if ratio > 0 and ratio != 1.0:
            source = source.scaled(
                max(1, round(source.width() / ratio)),
                max(1, round(source.height() / ratio)),
                Qt.IgnoreAspectRatio,
                Qt.FastTransformation,
            )

        width = source.width()
        height = source.height()
        if width <= 0 or height <= 0:
            return None

        if not source.hasAlphaChannel():
            return QRegion(0, 0, width, height)

        threshold = int(alpha_threshold)
        if threshold < DEFAULT_ALPHA_THRESHOLD:
            source = _clamp_alpha(source, threshold)
            if source is None:  # pragma: no cover - 只有平台异常才会走到
                return None

        alpha_mask = source.createAlphaMask()
        if alpha_mask.isNull():
            return None
        return QRegion(QBitmap.fromImage(alpha_mask))
    except Exception:
        # 降级路径不许再抛异常（"算不出来就整帧可点"）。
        return None


def _clamp_alpha(image: QImage, threshold: int) -> QImage | None:
    """把 alpha **按阈值硬化**成 0 / 255，交给 Qt 去二值化。

    这就是"降低 `createAlphaMask()` 阈值"的实现方式：先改 alpha，
    再让 Qt 用**它自己**的规则（`alpha >= 128`）筛一遍。
    于是"哪些像素算不透明"这件事**始终只有 Qt 一个真值**（§4.9）。

    用 numpy 做（渲染层已经因为抗锯齿引入了它，不是新依赖；
    纯 Python 逐像素在 1000×1000 上要几百毫秒，会拖慢换档）。
    """
    try:
        import numpy as np

        converted = image.convertToFormat(QImage.Format_ARGB32)
        if converted.isNull():  # pragma: no cover
            return None
        height = converted.height()
        width = converted.width()
        flat = np.frombuffer(
            bytes(converted.constBits()), dtype=np.uint8
        )
        expected = converted.bytesPerLine() * height
        if flat.size < expected:  # pragma: no cover
            return None
        # 必须按 `bytesPerLine()` 切行再截到 `width()`：Qt 的扫描行有 4 字节
        # 对齐填充，直接按 width*4 重塑会让图斜着错位（宽度非 4 倍数时必现）。
        pixels = flat[:expected].reshape(height, converted.bytesPerLine() // 4, 4)[
            :, :width, :
        ].copy()
        alpha = pixels[:, :, 3]
        pixels[:, :, 3] = np.where(alpha >= threshold, 255, 0).astype(np.uint8)
        buffer = np.ascontiguousarray(pixels).tobytes()
        # ⚠ `QImage(bytes, ...)` 不拷贝缓冲区：`buffer` 必须活到 `.copy()` 之后。
        rebuilt = QImage(buffer, width, height, QImage.Format_ARGB32).copy()
        if rebuilt.isNull():  # pragma: no cover
            return None
        return rebuilt
    except Exception:
        return None


def hit_test(
    frame_image: QImage | None,
    pos: QPoint,
    rendered_size: tuple[int, int] | None = None,
    mirrored: bool = False,
    alpha_threshold: int = DEFAULT_ALPHA_THRESHOLD,
) -> bool:
    """``pos``（**窗口内**逻辑坐标）是不是落在 ``frame_image`` 的不透明像素上。

    ``rendered_size`` 是这张图在窗口里占的尺寸（``(宽, 高)``）；给 ``None``
    时按图自己的尺寸算。缩放到该尺寸之后再做判定，因为**窗口里看到的是缩放后的图**，
    而 ``frame_image`` 可能还是原图（判定必须跟着"看到的画面"走）。

    ``mirrored=True`` 时按 ``x' = width - 1 - x`` 翻转横坐标再判定
    （task08 §4 的坐标约定）。注意这里翻转的是**查询点**，等价于把图翻过来——
    两种做法结果一致，翻查询点少一次全图拷贝。

    **降级**（永不抛异常，一律返回"可点"）：

    - 图是空图 / 尺寸非正 / 区域算不出来 → ``True``（整帧可点）；
    - ``pos`` 落在图的范围**之外** → ``False``（那本来就不是这张图的地盘）。
      只有"越出图外"这一条不按降级处理：区域为空时窗口整个可点，
      但窗口内、图外的那部分是**别的**判定对象，不该算作"按在人物身上"。
    """
    if frame_image is None or frame_image.isNull():
        return True

    try:
        size = rendered_size or (frame_image.width(), frame_image.height())
        width = int(size[0])
        height = int(size[1])
        if width <= 0 or height <= 0:
            return True

        x = int(pos.x())
        y = int(pos.y())
        if x < 0 or y < 0 or x >= width or y >= height:
            return False

        # 先缩到 `rendered_size`（窗口里看到的是缩放后的图），再按需翻转查询点。
        # ⚠ 缩放必须在**镜像分支之外**：「判定跟着看到的画面走」对两种镜像态都成立，
        # 只有镜像时才缩会让"没镜像 + 指定了 rendered_size"用原图判定（task08 自检抓到）。
        image = _scaled_for_test(frame_image, width, height)
        if mirrored:
            # 查询点镜像（等价于把图左右翻过来，见 docstring）。
            x = width - 1 - x

        region = region_for_frame(image, alpha_threshold=alpha_threshold)
        if region is None or region.isEmpty():
            # 算不出来 → 整帧可点（降级，不是"判不中"）。
            return True
        return region.contains(QPoint(x, y))
    except Exception:
        return True


def visible_bounds(region: QRegion | None) -> tuple[int, int, int, int] | None:
    """区域里**真正有像素**的那一块外接矩形 ``(x, y, w, h)``；算不出来 → ``None``。

    ## 它解决什么（task21 UX-06）

    窗口的尺寸是**透明画布**的尺寸（素材 `cell`，500×500 或 540×420），
    而人物只占其中一部分。按画布贴屏幕边缘时，人物与屏幕边之间还隔着
    一大圈透明像素——"贴边"看起来根本没贴到（用户 2026 实测反馈）。
    贴边要用**人物自己的边缘**，那正是本函数给的四元组。

    ## 为什么直接用区域的外接矩形，不另数像素

    区域本来就是"不透明像素的集合"（`region_for_frame()` 是唯一入口，
    §2.8 / §4.9："不自己按 alpha 判定"），外接矩形就是它的 `boundingRect()`。
    另写一份"扫 alpha 找边界"等于造第二个真值，迟早与遮罩对不上。

    空区域 / `None` → `None`（调用方按"整块画布"处理，即退化成旧行为）。
    """
    if region is None or region.isEmpty():
        return None
    rect = region.boundingRect()
    return (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))


def _scaled_for_test(image: QImage, width: int, height: int) -> QImage:
    """把图缩到 ``width × height`` 再做判定（已经同尺寸就原样返回）。

    缩放算法用 ``FastTransformation``：这里只关心"哪些像素不透明"，
    判定不该引入插值产生的新半透明像素（那是渲染层
    ``SmoothTransformation`` 的事）。
    """
    if (image.width(), image.height()) == (width, height):
        return image
    return image.scaled(
        width, height, Qt.IgnoreAspectRatio, Qt.FastTransformation
    )
