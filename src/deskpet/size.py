"""尺寸档位：10 级**精确有理数**倍数 + 像素换算 + 屏幕自适应 + 夹取。

## 为什么用有理数而不是浮点

档位倍数写成 `(zoom, subsample)`（例如 2/3 = `(2, 3)`），像素尺寸一律走
`cell * zoom // subsample`——**全程整数运算**。若写成 0.333 / 0.666 这类浮点再乘，
第 2 级与第 4 级会算出同一个像素值（"两个档位塌缩成同一尺寸"），
而且 0.666×500 与 2×500/3 的结果在边界上差 1 像素。

**约束先行**（agent.md §4.1）：先钉死这 10 个精确有理数，再谈"选哪一级"；
反过来"先定 10 个浮点目标尺寸再挑最接近的组合"必然塌缩。

## 10 级为什么两两不同

`cell = 500×500` 时各级高度是 125 / 166 / 250 / 333 / 375 / 500 / 625 / 666 / 750 / 1000，
严格递增且互不相同，模块级断言守着这一点（换素材包时若某个 `cell` 会塌缩，
导入本模块就会当场 `RuntimeError`，而不是等到用户按 `+` 才发现少了一级）。

## 屏幕自适应按**高度**不按宽度

与 `迁移清单.md` §4 一致：让"宠物高度 ÷ 屏幕高度"最接近 `SCREEN_TARGET_RATIO`。
按宽度会在宽屏笔记本上选出偏小的档位（素材画布是正方形，宽度那边富余得多）。
"""

from dataclasses import dataclass
from typing import Final

#: 档位数量。
LEVEL_COUNT: Final[int] = 10

#: 默认档位（第 6 级，1.00×）。**配置默认值必须引用它**（agent.md §2.4 / §4.1）。
DEFAULT_LEVEL: Final[int] = 6

#: 每级的 `(zoom, subsample)`：**精确有理数倍数**，第 N 级（1 起）对应下标 N-1。
#:
#: | 级 | 分数 | 倍数 |
#: | --- | --- | --- |
#: | 1 | 1/4 | 0.25 |
#: | 2 | 1/3 | 0.333 |
#: | 3 | 1/2 | 0.50 |
#: | 4 | 2/3 | 0.666 |
#: | 5 | 3/4 | 0.75 |
#: | 6 | 1/1 | 1.00 |
#: | 7 | 5/4 | 1.25 |
#: | 8 | 4/3 | 1.333 |
#: | 9 | 3/2 | 1.50 |
#: | 10 | 2/1 | 2.00 |
LEVEL_RATIOS: Final[tuple[tuple[int, int], ...]] = (
    (1, 4),
    (1, 3),
    (1, 2),
    (2, 3),
    (3, 4),
    (1, 1),
    (5, 4),
    (4, 3),
    (3, 2),
    (2, 1),
)

#: 屏幕占比目标：宠物高度应约占屏幕高度的这个比例（按**高度**选档位）。
SCREEN_TARGET_RATIO: Final[float] = 0.24

#: 默认落点距屏幕边缘的逻辑单位（与 `pet_window.DEFAULT_MARGIN` 同一件事，
#: 这里再放一份是因为"排版几何"属本模块；两者由自检盯着一并核对）。
DEFAULT_MARGIN: Final[int] = 24

#: 标定基准：**本机 2560×1440 + DPI 1.0（逻辑高度 1440）→ 第 4 级（333 px）**。
#:
#: ## 为什么不是 task 卡里的 "1080p + DPI 1.25 → 第 6 级"
#:
#: 那一条与 `SCREEN_TARGET_RATIO = 0.24` **在数学上不可能同时成立**：
#: 第 6 级的像素高度是 500（cell 500 × 1.00），要让它是"最接近 0.24 的那个"，
#: 屏幕逻辑高度得在 2083 左右。1080 物理 → 第 3 级、1080/1.25=864 逻辑 → 第 2 级，
#: 两种口径都到不了第 6 级。与用户确认后**以 0.24 为准**，
#: 标定基准改用本机实测屏幕（2560×1440 @100% → 第 4 级），差异记在 agent.md。
#:
#: `screen_height` 写**物理**高度，断言里按 `dpi_scale` 折成逻辑高度
#: （Qt 的 `geometry()` 给的就是逻辑值，见 `level_for_screen()`）。
#: 断言只读这几个常量，**不许读当前真实屏幕**（agent.md §4.11）。
CALIBRATION: Final[dict[str, int | float]] = {
    "screen_width": 2560,
    "screen_height": 1440,
    "dpi_scale": 1.0,
    "cell": 500,
    "level": 4,
}


# ------------------------------------------------------------------ 档位换算


def clamp_level(level: int) -> int:
    """把档位夹到 `1..LEVEL_COUNT`。

    这是**内部/容错**路径的规则（配置里写歪的值、程序内部算出的值）。
    **命令行**越界是另一条规则：必须当场报错退出，**不许**悄悄夹取——
    调用方（`app.py`）要在解析阶段就拒掉，不要依赖本函数兜底。
    """
    try:
        value = int(level)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    return max(1, min(LEVEL_COUNT, value))


def is_valid_level(level: object) -> bool:
    """`level` 是不是一个**合法的档位**（1..10 的整数，`bool` 不算）。"""
    if isinstance(level, bool) or not isinstance(level, int):
        return False
    return 1 <= level <= LEVEL_COUNT


def ratio_of(level: int) -> float:
    """该级的倍数（**展示用**）。

    像素计算**不要**用它，要走 `pixel_size()` 的精确有理数（`LEVEL_RATIOS`）。
    """
    zoom, subsample = LEVEL_RATIOS[clamp_level(level) - 1]
    return zoom / subsample


def ratio_text(level: int) -> str:
    """该级倍数的展示文字（`0.25` … `2.00`，两位小数，给 `--list-sizes` 用）。"""
    return f"{ratio_of(level):.2f}"


def level_text(level: int, total: int = LEVEL_COUNT) -> str:
    """控制栏上的档位文字：`{倍数:.2f}x（{档位}/{总数}）`，例如 `1.00x（6/10）`。

    **格式的真值只在这里一处**（task09 §4 行为契约）：控制栏、自检、将来的
    设置窗口都调它，谁都不许再拼一遍——否则"字数变了、栏宽跟着变"这种
    问题会分散到好几处去改。

    括号用**全角**（`（` / `）`），与 task09 §2 的示例逐字符一致。

    档位与总数都夹到合法范围：这是个**展示函数**，越界输入不该让它抛异常
    （`level=0` 会打出 `0.25x（0/10）` 那种读不通的东西）。
    """
    return f"{ratio_of(level):.2f}x（{clamp_level(level)}/{int(total)}）"


def pixel_size(cell: tuple[int, int], level: int) -> tuple[int, int]:
    """帧实际尺寸 `cell` × 该级倍数，**向下取整**。

    全程整数：`cell * zoom // subsample`。负数 / 零的 `cell` 归 1（永远给得出
    一个能用的窗口尺寸，`setFixedSize(0, …)` 会让窗口消失）。
    """
    zoom, subsample = LEVEL_RATIOS[clamp_level(level) - 1]
    return (
        max(1, int(cell[0]) * zoom // subsample),
        max(1, int(cell[1]) * zoom // subsample),
    )


def level_for_screen(screen_height: int, cell: tuple[int, int]) -> int:
    """选出让"宠物高度 ÷ 屏幕高度"最接近 `SCREEN_TARGET_RATIO` 的档位。

    - 按**高度**比较（不看宽度，理由见模块开头）；
    - 平局取**更小**的档位（`<` 而非 `<=`：先到者赢，保守优先，也保证单调性）；
    - `screen_height` 非正 → 退回 `DEFAULT_LEVEL`（拿不到屏幕信息时不要瞎选）。

    **单调性**：档位像素高度随级数严格递增，而目标值是固定的，
    所以"屏幕越高 → 目标像素高度越大 → 选出的档位不得变小"这条自然成立。

    ## 两个高度用同一套单位（这条最容易搞错）

    传进来的 `screen_height` 必须是**逻辑高度**（`QScreen.geometry().height()`），
    而 `cell` 与目标都是**逻辑单位**：像素尺寸 = `cell × 倍数` 也是逻辑单位，
    直接与逻辑屏幕高度比即可，**不要**再乘一遍 `device_pixel_ratio`
    （乘了就是缩放两次，agent.md §4.9 同一条坑）。

    Qt 的 `geometry()` 返回的**本来就是逻辑值**：1080p + DPI 1.25 的机器上报
    1080/1.25 = **864**，不是 1080。这正是标定基准 `CALIBRATION` 要同时写
    `screen_height` 与 `dpi_scale` 的原因——**"1080p"说的是物理高度**。
    """
    try:
        height = int(screen_height)
    except (TypeError, ValueError):
        return DEFAULT_LEVEL
    if height <= 0:
        return DEFAULT_LEVEL

    target = height * SCREEN_TARGET_RATIO
    best_level = 1
    best_distance = abs(pixel_size(cell, 1)[1] - target)
    for level in range(2, LEVEL_COUNT + 1):
        distance = abs(pixel_size(cell, level)[1] - target)
        if distance < best_distance:
            best_distance = distance
            best_level = level
    return best_level


def logical_screen_height(screen_height: int, dpi_scale: float) -> int:
    """物理屏幕高度 + DPI 缩放 → **逻辑高度**（`level_for_screen` 要的那个单位）。

    Qt 的 `QScreen.geometry()` 已经是逻辑值，正常路径**用不到**本函数；
    它的用处是① 把 `CALIBRATION` 的物理基准换算成逻辑基准做断言，
    ② 未来若有地方只拿得到物理分辨率时有个唯一入口，不必各自除一遍。

    `dpi_scale` 非正 → 按 1.0（除零保护）。
    """
    try:
        height = int(screen_height)
        scale = float(dpi_scale)
    except (TypeError, ValueError):
        return int(CALIBRATION["screen_height"])
    if height <= 0:
        return height
    if scale <= 0:
        scale = 1.0
    return max(1, round(height / scale))


def size_for_screen(
    screen_height: int,
    cell: tuple[int, int],
    level: int | None = None,
) -> tuple[int, tuple[int, int]]:
    """`(档位, 像素尺寸)`：`level=None` 表示"按屏幕自适应"。

    给"决定用哪一级"的调用方一个不再分叉的入口，免得每处各写一遍
    `level if level is not None else level_for_screen(...)`。
    """
    resolved = level_for_screen(screen_height, cell) if level is None else clamp_level(level)
    return resolved, pixel_size(cell, resolved)


# ------------------------------------------------------------------ 锚点与夹取


@dataclass(frozen=True)
class Anchor:
    """**右下角锚点**：窗口内容区的右下角在屏幕上的坐标（逻辑单位）。

    "切档时右下角不动"这件事靠它表达：换档前记下锚点，换档后按同一个锚点
    反推左上角。**不要**用"窗口中心"或"左上角"当锚点——那两个都会让脚底漂移。
    """

    x: int
    y: int

    def top_left_for(self, width: int, height: int) -> tuple[int, int]:
        """反推"内容区尺寸为 `width × height`"时的左上角坐标。"""
        return self.x - int(width), self.y - int(height)


def anchor_of(left: int, top: int, width: int, height: int) -> Anchor:
    """由左上角 + 内容尺寸算出右下角锚点。"""
    return Anchor(x=int(left) + int(width), y=int(top) + int(height))


def clamp_position(
    left: int,
    top: int,
    width: int,
    height: int,
    area: tuple[int, int, int, int],
) -> tuple[int, int]:
    """把窗口左上角夹到 `area` 表示的屏幕矩形内，返回夹取后的 `(left, top)`。

    `area` 是 `(x, y, width, height)`（就是 `QScreen.geometry()` 的四元组，
    本模块**不依赖 Qt**，所以用普通元组传）。

    **顺序硬约束**："夹取必须发生在存进权威位置之前"（agent.md §4.5）——
    否则越界值会被写进配置并跨次启动带过去，换过显示器后桌宠就跑到屏幕外了。

    窗口比屏幕还大时不硬塞到负数：贴左上角，宁可超出右下也不让左上角跑到屏幕外
    （这样人物头部始终在屏幕里，用户总能看到它）。
    """
    area_x, area_y, area_w, area_h = (int(value) for value in area)
    left = int(left)
    top = int(top)

    if area_w <= 0 or area_h <= 0:  # pragma: no cover - 拿不到屏幕信息时不猜
        return left, top

    max_left = area_x + area_w - int(width)
    max_top = area_y + area_h - int(height)
    if max_left < area_x:
        max_left = area_x
    if max_top < area_y:
        max_top = area_y
    return max(area_x, min(max_left, left)), max(area_y, min(max_top, top))


# ------------------------------------------------------------------ 模块级断言

# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。
# 防漂移：档位表本身的形状错了，后面所有推理都建立在错的前提上。
if len(LEVEL_RATIOS) != LEVEL_COUNT:  # pragma: no cover - 只有代码被改歪了才会走到
    raise RuntimeError(
        f"LEVEL_RATIOS 有 {len(LEVEL_RATIOS)} 项，与 LEVEL_COUNT={LEVEL_COUNT} 不符"
    )
if not 1 <= DEFAULT_LEVEL <= LEVEL_COUNT:  # pragma: no cover
    raise RuntimeError(f"DEFAULT_LEVEL={DEFAULT_LEVEL} 越界")
for _zoom, _subsample in LEVEL_RATIOS:  # pragma: no cover
    if _zoom <= 0 or _subsample <= 0:
        raise RuntimeError(f"LEVEL_RATIOS 里有非正的项：({_zoom}, {_subsample})")

# **"10 级两两不同且严格递增"**这条硬规则，用标定基准的 cell 当场验一遍。
_GENERIC_CELL: Final[tuple[int, int]] = (int(CALIBRATION["cell"]), int(CALIBRATION["cell"]))
_HEIGHTS = [pixel_size(_GENERIC_CELL, _level)[1] for _level in range(1, LEVEL_COUNT + 1)]
if len(set(_HEIGHTS)) != LEVEL_COUNT or _HEIGHTS != sorted(_HEIGHTS):  # pragma: no cover
    raise RuntimeError(f"{_GENERIC_CELL[1]}×{_GENERIC_CELL[1]} 下档位会塌缩或乱序：{_HEIGHTS}")

# 标定基准。**只读 `CALIBRATION`，不读当前屏幕。**
# 先把**物理**高度折成**逻辑**高度，再选档位（Qt 的 geometry() 给的是逻辑值）。
_CALIBRATION_CELL: Final[tuple[int, int]] = (
    int(CALIBRATION["cell"]),
    int(CALIBRATION["cell"]),
)
_CALIBRATION_HEIGHT = logical_screen_height(
    int(CALIBRATION["screen_height"]), float(CALIBRATION["dpi_scale"])
)
_CALIBRATED = level_for_screen(_CALIBRATION_HEIGHT, _CALIBRATION_CELL)
if _CALIBRATED != CALIBRATION["level"]:  # pragma: no cover
    raise RuntimeError(
        f"标定基准被破坏了：{CALIBRATION['screen_height']}p + DPI {CALIBRATION['dpi_scale']} "
        f"（逻辑高度 {_CALIBRATION_HEIGHT}）应选出第 {CALIBRATION['level']} 级，"
        f"实际算出第 {_CALIBRATED} 级"
    )
del _zoom, _subsample, _GENERIC_CELL, _HEIGHTS
del _CALIBRATION_CELL, _CALIBRATION_HEIGHT, _CALIBRATED
