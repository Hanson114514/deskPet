"""占位图形（**临时模块**）。

task01 还没有素材加载层（属 task03），所以先用一块带 alpha 的圆角矩形顶着：
- 图形**四周是真正的透明**（alpha = 0），用来验证 ``WA_TranslucentBackground``
  与 ``setMask`` 的效果；
- 图形**比画布小**，于是"占位图形以外的区域点不到宠物窗口"这条验收有东西可测；
- 画在**设备像素**上并按 DPR 标注，高 DPI 屏下不糊。

task03 接入真实素材（``pet_loader`` + ``rendering``）之后，**本模块应当删除**。
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen

#: 占位画布边长（逻辑单位）。附录 C：画布 / cell = 250 × 250。
CANVAS_SIZE = 250

#: 图形相对画布的内缩（逻辑单位）——这就是四周那圈"真透明"的宽度。
INSET = 25

#: 圆角半径（逻辑单位）。用圆角是为了让"遮罩边缘"这条验收看得更清楚。
CORNER_RADIUS = 36

_BODY = QColor(56, 132, 222, 255)
_EDGE = QColor(255, 255, 255, 235)
_TEXT = QColor(255, 255, 255, 255)


def build_placeholder_image(
    size: int = CANVAS_SIZE,
    device_pixel_ratio: float = 1.0,
) -> QImage:
    """画一张带 alpha 的占位图：不透明圆角矩形 + 四周真透明。

    ``size`` 是**逻辑**边长；``device_pixel_ratio`` 用来决定实际画布像素数
    （``size * dpr``）。返回的 ``QImage`` 用 ``setDevicePixelRatio()`` 标注，
    而 **``QPainter`` 会按 paint device 的 DPR 缩放自己的坐标系**，
    所以下面的绘制**一律用逻辑单位**（不要再自己乘 ratio，乘了就是缩放两次：
    实测 250 的图会画成 313 逻辑单位、右下角被切掉）。
    """
    if size <= 0:
        raise ValueError(f"占位画布边长必须是正数，收到 {size!r}")
    ratio = float(device_pixel_ratio) if device_pixel_ratio else 1.0
    if ratio <= 0:
        ratio = 1.0

    device_size = max(1, round(size * ratio))
    image = QImage(device_size, device_size, QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.transparent)

    # 以下全部是**逻辑单位**。
    body = QRectF(
        INSET,
        INSET,
        max(1.0, size - 2.0 * INSET),
        max(1.0, size - 2.0 * INSET),
    )

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_BODY)
        painter.drawRoundedRect(body, CORNER_RADIUS, CORNER_RADIUS)

        # 一圈描边：让"被遮罩切掉的地方"与"画出来的地方"一眼能分辨。
        pen_width = 2.0
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_EDGE, pen_width))
        inner = body.adjusted(pen_width, pen_width, -pen_width, -pen_width)
        painter.drawRoundedRect(inner, CORNER_RADIUS, CORNER_RADIUS)

        # 居中文字：DPI 缩放糊不糊，看它最直观。
        font = QFont()
        font.setPointSizeF(15.0)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(_TEXT)
        painter.drawText(body, Qt.AlignCenter, "deskPet")
    finally:
        painter.end()

    return image
