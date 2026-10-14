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

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QBitmap, QGuiApplication, QImage, QRegion

#: 判定用的不透明阈值说明：走 Qt 自带的 `QImage.createAlphaMask()`（约 `alpha >= 128`）。
#: 真实素材的源 GIF 是 1 位 alpha（只有 0 / 255），这个阈值不影响结果（§4.12）。
#: 本模块**不自己数像素**——"按 alpha 判定"会失去真穿透（§4.9 写过理由）。


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


def region_for_frame(frame_image: QImage | None, mirrored: bool = False) -> QRegion | None:
    """按 ``frame_image`` 的**不透明像素**生成窗口区域。

    返回 ``None`` 表示"**算不出来**"，调用方必须退化成**不设遮罩**（整帧可点）：

    - ``None`` / 空图 / 尺寸非正 → ``None``；
    - **没有 alpha 通道** = 整块不透明 → 返回整幅区域；
    - 不透明阈值取 Qt 自带的 ``QImage.createAlphaMask()``（约 ``alpha >= 128``）。

    ``mirrored=True``：图**已经翻过**时不要再翻一次——区域由"看到的这张图"
    算出来，于是"能点到的范围"与"看到的画面"永远一致（agent.md §2.14）。
    传进来的图**没翻**而要求镜像区域时，先翻再算。

    **带 `devicePixelRatio` 的图先折回逻辑尺寸**再生成区域：遮罩坐标是窗口内的
    逻辑像素，不折回的话区域会整体放大 ``ratio`` 倍（人物能点到的范围比看到的
    大一圈，高 DPI 下必现，task01 已有实现，别改回去）。

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

        alpha_mask = source.createAlphaMask()
        if alpha_mask.isNull():
            return None
        return QRegion(QBitmap.fromImage(alpha_mask))
    except Exception:
        # 降级路径不许再抛异常（"算不出来就整帧可点"）。
        return None


def hit_test(
    frame_image: QImage | None,
    pos: QPoint,
    rendered_size: tuple[int, int] | None = None,
    mirrored: bool = False,
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

        region = region_for_frame(image)
        if region is None or region.isEmpty():
            # 算不出来 → 整帧可点（降级，不是"判不中"）。
            return True
        return region.contains(QPoint(x, y))
    except Exception:
        return True


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
