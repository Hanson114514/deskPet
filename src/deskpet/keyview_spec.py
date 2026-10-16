"""键盘形态（KV）的素材契约：**asset.json 的 keyview 段**解析 + 六个布局白名单。

本模块是**数据层**：只处理"内存里的 JSON 对象"，**不读磁盘、不碰 Qt**
（与 `pet_spec` 同一条分工）。图片到底在不在，由 `pet_loader` 的列全校验回答。

## 与 `sounds` 是同级可选段

`keyview` 段**缺失 / 为 `null`** 是**完全合法**的（= 这个包没有键盘形态），
与"没有 `sounds`"完全同义：**不报错、不警告**（task17 §3.1 / §5）。
段坏掉也**只影响键盘形态**——KV 不可用、菜单项灰掉，**绝不影响宠物形态的加载**。

## 键的身份：`r{行}k{序号}`，序号**从中间往两边增加**

8 列左→右的序号是 `7 5 3 1 2 4 6 8`。于是 4 键 / 6 键布局正好是 8 键布局的
**居中子集**（取前 N 个序号），与 ADOFAI 本身的键位规律一致
（默认 8 键 `A S D F J K L ;`，4 键取中间的 `D F J K`）。

好处有两条，都是结构性的：

1. **同一份键位坐标与素材被所有布局复用**，配置也只需要配一次；
2. **在结构上不可能**出现参考实现那张
   `Location = [0,1,2,3,4,5,6,7,12,13,9,8,10,11,14,15]` 式的置换表错位
   ——"第几列"与"键是谁"**解耦**了，谁也拿不到"槽位顺序"当身份用。

## 层序（**固定**，不许合并）

`background`（数组顺序）→ `keys`（按下态）→ `hands`（数组顺序）→ `head`（数组顺序）。

⚠ **绝不能把 `background` 的多个图层烤成一张合并图**：参考工程的层序是
`Main(人物) → Piano(键盘) → 键 → 手 → 头`（取自 prefab 的 `m_Children`，
**那才是渲染顺序**）。顺序搞反就是"人物盖住键盘"，而且改一层要重烤一次。
"""

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

#: **布局白名单**（六个固定布局）。真值只允许存在这一处。
#:
#: 不在白名单里的布局名 → **忽略该项 + 一条 WARNING**（task17 §3.1 的"忽略报错"，
#: 用户 2026 明确要求）：不致命、不弹窗、**不牵连其它布局**。
LAYOUTS: Final[tuple[str, ...]] = ("1*4", "1*6", "1*8", "2*4", "2*6", "2*8")

#: 布局名的形状：`{行数}*{每行键数}`。用来核对"行数 × 列数"与名字对不对得上。
LAYOUT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^(\d+)\*(\d+)$")

#: 键 id 的形状：`r{行}k{序号}`（行与序号都是 1 起算的正整数）。
KEY_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^r(\d+)k(\d+)$")

#: 键盘形态的图片目录名（与 `frames/`、`sounds/` 同级；清单里只写文件名）。
KEYVIEW_DIR_NAME: Final[str] = "keyview"

#: `background` 的必需分支名。⚠ **没有 `wink`**：眨眼本项目不做（用户 2026 决定），
#: 参考工程的 `Line_wink*.png` 用不上。
BACKGROUND_BRANCH: Final[str] = "normal"


def parse_key_id(key_id: object) -> tuple[int, int] | None:
    """把 `"r2k7"` 拆成 `(行, 序号)`；形状不对返回 `None`。

    **唯一**解析键身份的地方。别处不许再写一遍正则或用
    "k 后面那个数字"当列号——**序号是从中间往外排的，不是列号**。
    """
    if not isinstance(key_id, str):
        return None
    match = KEY_ID_PATTERN.match(key_id)
    if match is None:
        return None
    row, number = int(match.group(1)), int(match.group(2))
    if row < 1 or number < 1:
        return None
    return row, number


def middle_out_numbers(columns: int) -> list[int]:
    """左→右每一列的序号，**中间往两边增加**（全项目唯一实现）。

    8 列 → `[7, 5, 3, 1, 2, 4, 6, 8]`；4 列 → `[3, 1, 2, 4]`；6 列 → `[5, 3, 1, 2, 4, 6]`。

    于是"取前 N 个序号"正好是**居中**的 N 个键（4 键 ⊂ 6 键 ⊂ 8 键），
    这也是"同一份键位坐标跨布局复用"能成立的依据。

    `columns <= 0` → 空列表（不抛异常：它只是个排布助手）。
    """
    count = int(columns)
    if count <= 0:
        return []
    center = (count - 1) / 2
    out: list[int] = []
    for column in range(count):
        base = int(abs(column - center) - 0.5) * 2 + 1
        out.append(base + (1 if column > center else 0))
    return out


def layout_shape(name: str) -> tuple[int, int] | None:
    """布局名 → `(行数, 每行键数)`；名字不是 `A*B` 形状 → `None`。"""
    match = LAYOUT_PATTERN.match(name) if isinstance(name, str) else None
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


# ------------------------------------------------------------------ 数据对象


@dataclass(frozen=True)
class KeySpec:
    """一个键位：**要么**"小图 + 声明坐标"，**要么**"整画布 overlay"。"""

    id: str
    #: `down` 是**按下态**的文件名。**未按下态画在 `background` 里**，所以没有 `up`。
    down: str = ""
    #: `x` / `y` 是**画布内左上角**（只有"小图 + 坐标"写法才有）。
    x: int = 0
    y: int = 0
    #: `overlay` 是"与 `cell` 同规格的整画布透明底图"，位置画在图里 → **零坐标**。
    overlay: str = ""

    @property
    def uses_overlay(self) -> bool:
        """这个键用的是 `overlay` 写法吗（两种写法渲染方式不同，别混）。"""
        return bool(self.overlay)


@dataclass(frozen=True)
class HandSpec:
    """一只手：松开态一张图 + "按了哪个键用哪张"的按下态表。

    `down` 是 **键 id → 文件名**；**只有 `up` 没有 `down` 合法**（就是松开时那张）。
    """

    up: str = ""
    down: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class HeadSpec:
    """一个头：`when_all_of` 里的键**全部**被按下时才画。

    可以有多个头（例如 2P 每人一个）；多个都满足 → **按数组顺序全部画**。
    """

    image: str
    when_all_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class LayoutSpec:
    """一个布局：`rows` 是**数组的数组**，外层行、内层**键 id**。

    **行内的书写顺序 = 画面左→右**（设置界面就照这个顺序摆按钮，与画面一一对应）。
    """

    name: str
    rows: tuple[tuple[str, ...], ...]

    @property
    def key_ids(self) -> tuple[str, ...]:
        """这个布局用到的全部键 id（按画面左→右、先上后下）。"""
        return tuple(key for row in self.rows for key in row)

    @property
    def key_count(self) -> int:
        """这个布局一共几个键。"""
        return sum(len(row) for row in self.rows)


@dataclass(frozen=True)
class KeyViewSpec:
    """一个素材包的键盘形态描述（构造成功即视为"这个包的 KV 可用"）。

    ⚠ `cell` 是**所有布局共用**的一份（于是切布局不改窗口尺寸 →
    锚点与控制栏零风险，task17 §3.3）。
    """

    cell: tuple[int, int]
    #: `normal` 分支的图层文件名，**先画的在前**（底层在前）。
    background: tuple[str, ...]
    keys: Mapping[str, KeySpec]
    layouts: Mapping[str, LayoutSpec]
    #: 书写顺序即图层顺序。
    hands: tuple[HandSpec, ...] = ()
    heads: tuple[HeadSpec, ...] = ()
    root: Path = Path()
    namespace: str = ""

    # ------------------------------------------------------------ 查询

    @property
    def layout_names(self) -> tuple[str, ...]:
        """可用布局名（**保持清单里的书写顺序**，于是"第一个布局"是确定的）。"""
        return tuple(self.layouts)

    def layout(self, name: str) -> LayoutSpec | None:
        """按名字取布局；没有就是 `None`（**不抛异常、不给替身**）。"""
        return self.layouts.get(name)

    def has_key(self, key_id: str) -> bool:
        """这个键 id 在全局键位表里吗。"""
        return key_id in self.keys

    def key(self, key_id: str) -> KeySpec | None:
        """按 id 取键位；没有就是 `None`。"""
        return self.keys.get(key_id)

    def image_path(self, filename: str) -> Path | None:
        """图片文件名 → **绝对路径**（空文件名 → `None`）。

        与 `PetSpec.frame_path()` 同一个模式：拼路径这件事只在这里做，
        免得调用方各自去算相对路径。
        """
        if not filename:
            return None
        return self.root / KEYVIEW_DIR_NAME / filename

    def hand_down_image(self, hand: HandSpec, key_id: str) -> str:
        """这只手在 `key_id` 按下时该用哪张图；**没配就退回 `up`**。

        "只有 `up` 没有 `down`"与"这张 `down` 没覆盖到这个键"是同一件事的
        两种来源，处理方式一样：用松开时那张（§3.3）。
        """
        return hand.down.get(key_id) or hand.up


# ------------------------------------------------------------------ 解析


class _Collector:
    """一次列全的收件箱：**问题**与**警告**分开攒。

    "一次列全，不是报第一个"是本项目的硬规矩（`pet_loader` 同款）。
    """

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.issues: list[Any] = []
        self.warnings: list[Any] = []

    def issue(self, kind: str, detail: str, action: str | None = None) -> None:
        self.issues.append(self._make(kind, detail, action))

    def warn(self, kind: str, detail: str, action: str | None = None) -> None:
        self.warnings.append(self._make(kind, detail, action))

    def _make(self, kind: str, detail: str, action: str | None) -> Any:
        # 延迟 import：`pet_loader` 在模块级 import 本模块，反向 import 会成环
        # （`pet_spec` 用的是同一条分工，见那儿的说明）。
        from .pet_loader import IssueKind, PetIssue

        return PetIssue(IssueKind(kind), self.namespace, action, detail)


def _non_empty_str(value: Any) -> str:
    """非空字符串才留下（空白串按"没写"处理）。其它类型一律返回空串。"""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _file_list(value: Any) -> tuple[list[str], int]:
    """把"文件名数组"过滤成非空字符串，返回 `(留下的, 丢掉的个数)`。

    坏项**只丢那一项**（与 `parse_sounds` 同一条规矩）：一个写错的文件名
    不该让同一组里其它能用的图一起失效。**单个字符串写法已废弃**。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return [], 0
    items = [_non_empty_str(item) for item in value]
    kept = [item for item in items if item]
    return kept, len(items) - len(kept)


def parse_keyview(
    value: Any,
    *,
    namespace: str = "",
    root: Path | None = None,
) -> tuple[KeyViewSpec | None, list, list]:
    """把 `asset.json` 的 `keyview` 段变成 `KeyViewSpec`。

    返回 `(规格 | None, 问题列表, 警告列表)`。**绝不抛异常。**

    规格为 `None` 的含义分两种，调用方按"有没有问题"区分：

    - **段缺失 / `null`** → `(None, [], [])`：这个包没有键盘形态，
      **不报错也不警告**（与"没有 `sounds`"完全同义）；
    - **段坏掉** → `(None, [问题...], [警告...])`：KV 不可用，
      但**只影响键盘形态**（宠物形态照常）。

    ## 可用性判据是**按布局判**的

    某布局缺图 → **只是该布局**不可用；**全部**布局都不可用 → KV 形态不可用。
    这一层只判"结构"，"图在不在"由 `pet_loader` 补上（它才碰磁盘）。
    """
    from .pet_loader import IssueKind

    if value is None:
        return None, [], []
    if not namespace and root is not None:
        namespace = root.name
    collector = _Collector(namespace)

    if not isinstance(value, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_ROOT_INVALID.value,
            f"keyview 必须是对象，实际是 {type(value).__name__}",
        )
        return None, collector.issues, collector.warnings

    # ---------------------------------------------------------- cell（必需）
    from .pet_spec import parse_cell

    cell, cell_reason = parse_cell(value.get("cell"))
    if cell is None:
        collector.issue(
            IssueKind.KEYVIEW_ROOT_INVALID.value,
            f"keyview.cell 不可用：{cell_reason or 'cell 非法'}",
        )

    # ---------------------------------------------------------- background（必需）
    background: tuple[str, ...] = ()
    raw_background = value.get("background")
    if not isinstance(raw_background, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_ROOT_INVALID.value,
            f"keyview.background 必须是对象，实际是 {type(raw_background).__name__}",
        )
    else:
        names, dropped = _file_list(raw_background.get(BACKGROUND_BRANCH))
        if dropped:
            collector.warn(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.background.{BACKGROUND_BRANCH} 里有 {dropped} 项"
                "不是非空文件名，已忽略",
            )
        # ⚠ 顶层出现 `wink` 一律忽略（本项目不做眨眼），连提都不提，
        # 免得用户以为"写上了就能用"。真写了也只是被无视，不是错误。
        if not names:
            collector.issue(
                IssueKind.KEYVIEW_ROOT_INVALID.value,
                f"keyview.background.{BACKGROUND_BRANCH} 至少要有图层文件名"
                "（数组、先画的在前）",
            )
        background = tuple(names)

    # ---------------------------------------------------------- keys（必需）
    keys: dict[str, KeySpec] = {}
    raw_keys = value.get("keys")
    if not isinstance(raw_keys, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_ROOT_INVALID.value,
            f"keyview.keys 必须是对象，实际是 {type(raw_keys).__name__}",
        )
    elif not raw_keys:
        collector.issue(IssueKind.KEYVIEW_ROOT_INVALID.value, "keyview.keys 是空对象")
    else:
        for raw_id, raw_key in raw_keys.items():
            key_id = _non_empty_str(raw_id)
            if not key_id:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.keys 里有非字符串键 {raw_id!r}",
                )
                continue
            if parse_key_id(key_id) is None:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.keys 的键 {key_id!r} 不符合 r{{行}}k{{序号}} 形状",
                )
                continue
            if key_id in keys:
                # JSON 对象里理论上不会有重复键，但**人写的文件**与二次加工的
                # 工具都可能造出来（task17 §6 明列了这一条）。
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.keys 里 {key_id!r} 重复出现",
                )
                continue
            spec = _parse_one_key(key_id, raw_key, collector)
            if spec is not None:
                keys[key_id] = spec
        if not keys:
            collector.issue(
                IssueKind.KEYVIEW_ROOT_INVALID.value,
                "keyview.keys 里没有任何一个合规的键位",
            )

    # ---------------------------------------------------------- hands（可选）
    hands: list[HandSpec] = []
    raw_hands = value.get("hands")
    if raw_hands is not None:
        if isinstance(raw_hands, (str, bytes)) or not isinstance(raw_hands, Sequence):
            collector.warn(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.hands 必须是数组，实际是 {type(raw_hands).__name__}，已忽略",
            )
        else:
            for index, raw_hand in enumerate(raw_hands):
                hand = _parse_one_hand(index, raw_hand, collector)
                if hand is not None:
                    hands.append(hand)

    # ---------------------------------------------------------- head（可选）
    heads: list[HeadSpec] = []
    raw_head = value.get("head")
    if raw_head is not None:
        if isinstance(raw_head, (str, bytes)) or not isinstance(raw_head, Sequence):
            collector.warn(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.head 必须是数组，实际是 {type(raw_head).__name__}，已忽略",
            )
        else:
            for index, raw_one in enumerate(raw_head):
                spec = _parse_one_head(index, raw_one, collector)
                if spec is not None:
                    heads.append(spec)

    # ---------------------------------------------------------- layouts（必需）
    layouts: dict[str, LayoutSpec] = {}
    raw_layouts = value.get("layouts")
    if not isinstance(raw_layouts, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_ROOT_INVALID.value,
            f"keyview.layouts 必须是对象，实际是 {type(raw_layouts).__name__}",
        )
    elif not raw_layouts:
        collector.issue(IssueKind.KEYVIEW_ROOT_INVALID.value, "keyview.layouts 是空对象")
    else:
        for raw_name, raw_layout in raw_layouts.items():
            name = _non_empty_str(raw_name)
            if name not in LAYOUTS:
                # ⚠ **忽略该项 + 一条 WARNING**，不致命、不弹窗、
                # **不牵连其它布局**（用户 2026 明确要求，task17 §3.1 / §5）。
                collector.warn(
                    IssueKind.KEYVIEW_LAYOUT_UNKNOWN.value,
                    f"keyview.layouts 里的 {raw_name!r} 不在白名单 "
                    f"{'/'.join(LAYOUTS)} 内，已忽略该项（其它布局不受影响）",
                )
                continue
            spec = _parse_one_layout(name, raw_layout, keys, collector)
            if spec is not None:
                layouts[name] = spec
        if not layouts:
            # 全部布局都不可用 → KV 形态不可用。**这里不抛、不升级成宠物形态失败**。
            collector.issue(
                IssueKind.KEYVIEW_NO_LAYOUT.value,
                "keyview.layouts 里没有任何一个可用布局（KV 形态不可用）",
            )

    # ---------------------------------------------------------- 交叉引用
    _check_hands_reference(hands, keys, collector)
    _check_heads_reference(heads, keys, collector)

    if collector.issues:
        return None, collector.issues, collector.warnings

    spec = KeyViewSpec(
        cell=cell or (0, 0),
        background=background,
        keys=keys,
        layouts=layouts,
        hands=tuple(hands),
        heads=tuple(heads),
        root=Path(root) if root is not None else Path(),
        namespace=namespace,
    )
    return spec, collector.issues, collector.warnings


def _parse_one_key(key_id: str, raw: Any, collector: "_Collector") -> KeySpec | None:
    """一个键位：`down` 必需；`x/y` 与 `overlay` 二选一（同时出现用 overlay + 警告）。"""
    from .pet_loader import IssueKind

    if not isinstance(raw, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.keys.{key_id} 必须是对象，实际是 {type(raw).__name__}",
        )
        return None

    down = _non_empty_str(raw.get("down"))
    if not down:
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.keys.{key_id} 缺少 down（按下态的文件名，必需）",
        )

    overlay = _non_empty_str(raw.get("overlay"))
    has_xy = "x" in raw or "y" in raw
    if overlay and has_xy:
        # **用 overlay + 一条警告**（task17 §3.3 明写）。
        collector.warn(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.keys.{key_id} 同时写了 overlay 与 x/y，"
            "已按 overlay 处理（坐标被忽略）",
        )
        has_xy = False

    x = y = 0
    if not overlay:
        if not has_xy:
            collector.issue(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.keys.{key_id} 必须给出 x/y 或 overlay（二选一）",
            )
            return None
        coords: list[int] = []
        bad = False
        for axis in ("x", "y"):
            item = raw.get(axis)
            if isinstance(item, bool) or not isinstance(item, int):
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.keys.{key_id}.{axis} 必须是整数，实际是 "
                    f"{type(item).__name__}（{item!r}）",
                )
                bad = True
                continue
            coords.append(item)
        if bad or len(coords) != 2:
            return None
        x, y = coords

    if not down:
        return None
    return KeySpec(id=key_id, down=down, x=x, y=y, overlay=overlay)


def _parse_one_hand(index: int, raw: Any, collector: "_Collector") -> HandSpec | None:
    """一只手：`up` 必需（松开时那张）；`down` 可选（键 id → 图）。"""
    from .pet_loader import IssueKind

    if not isinstance(raw, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.hands[{index}] 必须是对象，实际是 {type(raw).__name__}",
        )
        return None

    up = _non_empty_str(raw.get("up"))
    if not up:
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.hands[{index}] 缺少 up（松开时那张图，必需）",
        )
        return None

    down: dict[str, str] = {}
    raw_down = raw.get("down")
    if raw_down is not None:
        if not isinstance(raw_down, Mapping):
            collector.warn(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.hands[{index}].down 必须是对象（键 id → 图），"
                f"实际是 {type(raw_down).__name__}，已忽略",
            )
        else:
            for raw_id, raw_file in raw_down.items():
                key_id = _non_empty_str(raw_id)
                filename = _non_empty_str(raw_file)
                if not key_id or parse_key_id(key_id) is None:
                    collector.warn(
                        IssueKind.KEYVIEW_INVALID.value,
                        f"keyview.hands[{index}].down 里的键 {raw_id!r} 不合法，已忽略",
                    )
                    continue
                if not filename:
                    # 空串 = "这个键没有专门的按下图"，与"没写"同义，不是错误。
                    continue
                down[key_id] = filename

    # **只有 `up` 没有 `down` 合法**（就是松开时那张）——不警告。
    return HandSpec(up=up, down=down)


def _parse_one_head(index: int, raw: Any, collector: "_Collector") -> HeadSpec | None:
    """一个头：`image` 必需；`when_all_of` 是键 id 数组。"""
    from .pet_loader import IssueKind

    if not isinstance(raw, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.head[{index}] 必须是对象，实际是 {type(raw).__name__}",
        )
        return None

    image = _non_empty_str(raw.get("image"))
    if not image:
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value, f"keyview.head[{index}] 缺少 image"
        )
        return None

    raw_ids = raw.get("when_all_of")
    ids: list[str] = []
    if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Sequence):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.head[{index}].when_all_of 必须是键 id 的数组，"
            f"实际是 {type(raw_ids).__name__}",
        )
    else:
        for item in raw_ids:
            key_id = _non_empty_str(item)
            if not key_id or parse_key_id(key_id) is None:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.head[{index}].when_all_of 里的 {item!r} 不是合法键 id",
                )
                continue
            ids.append(key_id)
        if not ids:
            collector.issue(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.head[{index}].when_all_of 是空的（那就永远不画）",
            )
    return HeadSpec(image=image, when_all_of=tuple(ids))


def _parse_one_layout(
    name: str,
    raw: Any,
    keys: Mapping[str, KeySpec],
    collector: "_Collector",
) -> LayoutSpec | None:
    """一个布局：`rows` 必需；行数 × 列数与布局名必须对得上；引用的键必须存在。"""
    from .pet_loader import IssueKind

    shape = layout_shape(name)
    if not isinstance(raw, Mapping):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.layouts.{name} 必须是对象，实际是 {type(raw).__name__}",
        )
        return None

    raw_rows = raw.get("rows")
    if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, Sequence):
        collector.issue(
            IssueKind.KEYVIEW_INVALID.value,
            f"keyview.layouts.{name}.rows 必须是「数组的数组」，"
            f"实际是 {type(raw_rows).__name__}",
        )
        return None

    rows: list[tuple[str, ...]] = []
    ok = True
    for row_index, raw_row in enumerate(raw_rows):
        if isinstance(raw_row, (str, bytes)) or not isinstance(raw_row, Sequence):
            collector.issue(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.layouts.{name}.rows[{row_index}] 必须是键 id 的数组，"
                f"实际是 {type(raw_row).__name__}",
            )
            ok = False
            continue
        row: list[str] = []
        for item in raw_row:
            key_id = _non_empty_str(item)
            if not key_id:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.layouts.{name}.rows[{row_index}] 里有非字符串项 {item!r}",
                )
                ok = False
                continue
            if key_id not in keys:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.layouts.{name}.rows[{row_index}] 引用了 keys 里"
                    f"不存在的键 {key_id!r}",
                )
                ok = False
                continue
            row.append(key_id)
        rows.append(tuple(row))

    if not ok or not rows:
        return None

    # ---- 形状核对：行数 × 列数与布局名必须对得上（2*8 = 2 行 × 8 键）
    if shape is not None:
        expected_rows, expected_columns = shape
        if len(rows) != expected_rows:
            collector.issue(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.layouts.{name} 声明 {expected_rows} 行，实际有 {len(rows)} 行",
            )
            return None
        wrong = [index + 1 for index, row in enumerate(rows) if len(row) != expected_columns]
        if wrong:
            collector.issue(
                IssueKind.KEYVIEW_INVALID.value,
                f"keyview.layouts.{name} 每行应有 {expected_columns} 个键，"
                f"实际第 {wrong} 行不是",
            )
            return None

        # ---- 编号校验：行内键 id 的**行号与序号**必须与布局名对得上。
        # 序号**不是**中间往外排时记一条**警告**（不是错误）：结构仍然可用，
        # 但"4 键 ⊂ 6 键 ⊂ 8 键"这条跨布局复用的好处会失效（task17 §6 明写）。
        parsed = [parse_key_id(key_id) for key_id in (k for row in rows for k in row)]
        if all(item is not None for item in parsed):
            pairs = [item for item in parsed if item is not None]
            expected_rows_seq = [
                row_index + 1 for row_index in range(len(rows)) for _ in rows[row_index]
            ]
            rows_ok = [row for row, _number in pairs] == expected_rows_seq
            numbers = [number for _row, number in pairs[:expected_columns]]
            expected_numbers = middle_out_numbers(expected_columns)
            if not rows_ok or numbers != expected_numbers:
                collector.warn(
                    IssueKind.KEYVIEW_KEY_NUMBERING.value,
                    f"keyview.layouts.{name} 的键编号不是「行号从 1 起、序号中间往外排」"
                    f"（期望序号 {expected_numbers}，实际 {numbers}）：结构仍可用，"
                    "但跨布局复用坐标与配置的好处会失效",
                )

    return LayoutSpec(name=name, rows=tuple(rows))


def _check_hands_reference(
    hands: Sequence[HandSpec], keys: Mapping[str, KeySpec], collector: "_Collector"
) -> None:
    """`hands.down` 指向 `keys` 里不存在的键 → **问题**（一次列全）。"""
    from .pet_loader import IssueKind

    for index, hand in enumerate(hands):
        for key_id in hand.down:
            if key_id not in keys:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.hands[{index}].down 引用了 keys 里不存在的键 {key_id!r}",
                )


def _check_heads_reference(
    heads: Sequence[HeadSpec], keys: Mapping[str, KeySpec], collector: "_Collector"
) -> None:
    """`head.when_all_of` 指向 `keys` 里不存在的键 → **问题**（一次列全）。"""
    from .pet_loader import IssueKind

    for index, head in enumerate(heads):
        for key_id in head.when_all_of:
            if key_id not in keys:
                collector.issue(
                    IssueKind.KEYVIEW_INVALID.value,
                    f"keyview.head[{index}].when_all_of 引用了 keys 里不存在的键 {key_id!r}",
                )


# ------------------------------------------------------------------ 绑定规范化


@dataclass(frozen=True)
class KeyBinding:
    """一个键位上的绑定：**录到的那一个物理键**。

    | 字段 | 语义 |
    | --- | --- |
    | `vk` | 虚拟键码（`QKeyEvent.nativeVirtualKey()` 给什么就是什么） |
    | `name` | 可读名字（`J` / `Space` / `Num5`），只用于显示 |

    ⚠ **没有"布局"这一维**：键 id 在所有布局里指同一个键，所以**配一次、所有布局通用**
    （agent.md §2.23）。带布局前缀就会变成"每个布局各配一套"——那是明令不要做的。
    """

    vk: int
    name: str = ""


#: 配置里 `keyview_keys` 的值的合法形状：`{"vk": 74, "name": "J"}`。
#: 别处不许再手写一遍这两个字段名。
BINDING_VK_FIELD: Final[str] = "vk"
BINDING_NAME_FIELD: Final[str] = "name"


def normalize_bindings(
    raw: object, spec: KeyViewSpec | None = None
) -> dict[str, KeyBinding]:
    """把配置里的 `keyview_keys` 整理成"**当前素材包真的有的键位** → 绑定"。

    ## 三条约定（task19 §4.6 / 决策 1、2）

    1. **不存在的键 id 直接丢掉**（换过素材包、键位被删了），只记一条日志；
    2. ⚠ **绝不写回配置**——这是**读**操作（§2.10"读操作只读"）。被丢掉的那些
       留在文件里不动，用户把素材包换回来时它们是活的；
    3. **同一个物理键绑到多个键位是合法的**（用户 2026 决策 2），这里一个都不解绑。

    坏形状静默丢弃（配置是"读取永不致命"的那一类）：
    `vk` 不是正整数 / 值不是对象 / 键 id 不是非空字符串。

    `spec=None` 表示"这个包没有键盘形态" → 全部丢掉（只留日志）。
    """
    out: dict[str, KeyBinding] = {}
    if not isinstance(raw, Mapping):
        return out

    known = spec.keys if spec is not None else {}
    unknown: list[str] = []
    for raw_id, raw_entry in raw.items():
        key_id = raw_id.strip() if isinstance(raw_id, str) else ""
        if not key_id or not isinstance(raw_entry, Mapping):
            continue
        vk = raw_entry.get(BINDING_VK_FIELD)
        if isinstance(vk, bool) or not isinstance(vk, int) or vk <= 0:
            continue
        if key_id not in known:
            unknown.append(key_id)
            continue
        name = raw_entry.get(BINDING_NAME_FIELD)
        out[key_id] = KeyBinding(
            vk=int(vk), name=name.strip() if isinstance(name, str) else ""
        )

    if unknown:
        # ⚠ **只记日志，一个字节都不写回**（§2.10 读操作只读）。
        logger.info(
            "键位映射：配置里有 %d 个键位在当前素材包里不存在，已忽略（配置不改）：%s",
            len(unknown),
            "、".join(unknown),
        )
    return out


def bindings_for_layout(
    bindings: Mapping[str, KeyBinding], layout: LayoutSpec | None
) -> dict[str, KeyBinding]:
    """只留**这个布局真的用到**的键位（网格按它填初值）。

    "配了但当前布局没有这个键"不是错误：键 id 跨布局通用，
    换个布局它就会重新出现（真实例子里 4 键布局根本没有 8 键布局的那些键）。
    """
    if layout is None:
        return {}
    wanted = set(layout.key_ids)
    return {key_id: binding for key_id, binding in bindings.items() if key_id in wanted}


def bindings_to_config(bindings: Mapping[str, KeyBinding]) -> dict[str, dict]:
    """绑定 → 写进 `keyview_keys` 的普通对象（**写配置的唯一拼法**）。

    ⚠ 就是 `{"键 id": {"vk": …, "name": …}}` 的形状，**不加布局前缀**。
    键顺序取传入顺序（dict 保持插入序），于是写出来的 JSON 顺序稳定、可读。
    """
    return {
        key_id: {BINDING_VK_FIELD: int(binding.vk), BINDING_NAME_FIELD: binding.name}
        for key_id, binding in bindings.items()
    }


# ------------------------------------------------------------------ 模块级断言

# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。
if len(set(LAYOUTS)) != len(LAYOUTS):  # pragma: no cover
    raise RuntimeError(f"LAYOUTS 里有重复项：{LAYOUTS}")

# 六个布局名**必须**都能被 layout_shape() 解析出来（否则形状核对永远跳过）。
for _name in LAYOUTS:  # pragma: no cover - 只有常量被改歪了才走到
    if layout_shape(_name) is None:
        raise RuntimeError(f"LAYOUTS 里的 {_name!r} 不是 A*B 形状")

# 编号规则的自检：8 列必须是 7 5 3 1 2 4 6 8（§3.2 逐字写死的那一串）。
if middle_out_numbers(8) != [7, 5, 3, 1, 2, 4, 6, 8]:  # pragma: no cover
    raise RuntimeError(f"中间往外编号算错了：{middle_out_numbers(8)}")
if middle_out_numbers(4) != [3, 1, 2, 4]:  # pragma: no cover
    raise RuntimeError(f"4 键编号算错了：{middle_out_numbers(4)}")
# "4 键 ⊂ 6 键 ⊂ 8 键"：取前 N 个序号必须是同一批键（居中子集）。
if not set(middle_out_numbers(4)) < set(middle_out_numbers(6)) < set(middle_out_numbers(8)):  # pragma: no cover
    raise RuntimeError("4/6/8 键不是居中子集关系，'跨布局复用'这条前提被破坏了")
del _name
