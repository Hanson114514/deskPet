"""摆放几何：屏幕内夹取、右下角锚点、缺省落点、位置字符串解析。

## 为什么单独成文件（task08 §3）

task04 把夹取与锚点写在了 `size.py`（那里同时住着档位换算）。拖动要让
**"夹取必须发生在写进权威位置之前"**（agent.md §4.5）这条规则在**每一处**
都成立，于是需要一个"位置从哪来 → 夹到哪去"的统一入口，
按职责放在这里（`size.py` 继续管"档位 ↔ 像素尺寸"）。

**判据是"有没有分叉"**：`size.clamp_position()` / `size.anchor_of()` 仍是唯一实现，
本模块**只转发不重写**——重写一份就会有两个夹取真值，那正是 §4.5 要防的。

## 位置的三种来源（task08 §4"位置决定顺序"）

`命令行 --position` > `上次保存的`（`has("position")`）> **缺省贴右下角**（边距 24）。

配置里的坏值（`""` / `"abc"` / `"1,2,3"`）→ **忽略 + 记一行日志**，不报错、不影响启动；
命令行给的坏值 → 当场报错退出（`argparse`，见 `app._position_arg`）。
两条规则别混，与"档位越界"的两套规则是同一种分法（agent.md §2.12）。
"""

import logging
from typing import Final

from . import size

logger = logging.getLogger(__name__)

#: `(x, y, w, h)` 屏幕矩形。
ScreenArea = tuple[int, int, int, int]

#: 位置来源的显示文字（**启动行必须打印**，与档位来源同一个道理）。
POSITION_SOURCE_CLI: Final[str] = "命令行指定"
POSITION_SOURCE_CONFIG: Final[str] = "沿用上次"
POSITION_SOURCE_DEFAULT: Final[str] = "默认落点"


def parse_position(text: object) -> tuple[int, int] | None:
    """把配置里的 ``"x,y"`` 解析成 ``(x, y)``；**读不出来就返回 `None`**。

    读配置这条路径**永不致命**（agent.md §2.4 规则 1）：空串、缺逗号、
    多一段、不是整数、带空格但段数是 2 且都能转整数（`" 1200 , 600 "` 是合法的，
    因为那是**同一件事的另一种写法**，不是坏数据）——都只记一行日志并让调用方
    走缺省落点。

    不复用 `str.split(",")` 的结果长度做判断：`"1,2,3"` 与 `"abc"` 都必须被拒，
    统一的判据是"恰好两段、每段都是整数"。
    """
    if not isinstance(text, str):
        logger.warning("position 不是字符串（%s），忽略该项", type(text).__name__)
        return None

    parts = [piece.strip() for piece in text.split(",")]
    if len(parts) != 2:
        logger.warning("position 格式不对（%r），应为 \"x,y\"，忽略该项", text)
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        logger.warning("position 里不是整数（%r），忽略该项", text)
        return None


def format_position(x: int, y: int) -> str:
    """`(x, y)` → 配置里的 `"x,y"`（**唯一的拼法**）。"""
    return f"{int(x)},{int(y)}"


def clamp_position(left: int, top: int, width: int, height: int, area: ScreenArea) -> tuple[int, int]:
    """把窗口左上角夹到屏幕矩形内（转发 `size.clamp_position`，**不重写**）。"""
    return size.clamp_position(left, top, width, height, area)


def anchor_of(left: int, top: int, width: int, height: int) -> size.Anchor:
    """右下角锚点（转发 `size.anchor_of`）。"""
    return size.anchor_of(left, top, width, height)


def default_position(width: int, height: int, area: ScreenArea) -> tuple[int, int]:
    """缺省落点：贴屏幕**右下角**，右下各留 `size.DEFAULT_MARGIN` 逻辑单位。

    用整块屏幕的 `geometry()`（不是排除任务栏的 `availableGeometry()`）——
    边距是从**屏幕边缘**量起的（agent.md §4.10，task09 再决定要不要改）。
    屏幕信息拿不到 → `(0, 0)`，与 task04 的行为一致。
    """
    if area[2] <= 0 or area[3] <= 0:
        return 0, 0
    return clamp_position(
        area[0] + area[2] - int(width) - size.DEFAULT_MARGIN,
        area[1] + area[3] - int(height) - size.DEFAULT_MARGIN,
        width,
        height,
        area,
    )
