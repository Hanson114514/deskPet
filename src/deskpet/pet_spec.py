"""素材描述对象：`PetSpec` / `ActionSpec` + **帧率解析的唯一实现**。

本模块是**数据层**，只做两件事：

1. 把已经解析好的 JSON 对象（`asset.json` 的内存形式）**变成不可变的数据对象**；
2. 把"这个动作该按多少毫秒一帧显示"这件事**一次算清**，对外只暴露 `frame_ms`。

**不碰磁盘、不碰 Qt**：读文件是 `pet_loader` 的事，画图是视图层的事。
这样"帧率优先级"这条契约可以在自检里用普通字典直接测。

## 帧率优先级（agent.md §2.7，从高到低）

1. `asset.json` 的 `actions.<动作>.fps`；
2. **遗留兜底表**（过渡期兼容层，值取自旧 Tkinter 实现）；
3. `DEFAULT_FPS = 12.0`。

对外**只**暴露 `frame_ms = 1000.0 / fps`（浮点，不取整；取整会累积成肉眼可见的节奏偏移）。
`fps` 本身只在 `--list-actions` 这种诊断输出里露脸。

## 规范动作集（2025 用户定案，见 agent.md §2.5 / §5 的 D14）

`idle` / `running_left` / `running_right` / `waiting` / `rest` / `waving`。
用户明确要求**编程侧与素材侧统一**：`jumping` → `waving`（交互动作）、
`review` → `waiting`、`sit` → `rest`。素材里"有几个动作就是几个"，
`CANONICAL_ACTIONS` **不参与过滤、不用于补动作、不造别名**
（缺 `waving` 就不挥手，按可恢复路径退化）。
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Final

#: **默认形象的真值，全项目只允许存在这一处**（agent.md §2.4 / §4.1）。
#: 配置项 `pet` 的默认值是**空串**（"用户还没表态"），不许在配置里再写一次形象名。
#:
#: 2025 用户定案：`phoebe` 不再重做，默认形象改为 `weedy`。
DEFAULT_PET_NAMESPACE: Final[str] = "weedy"

#: 规范动作集（顺序即"展示 / 菜单"的推荐顺序）。见模块 docstring。
CANONICAL_ACTIONS: Final[tuple[str, ...]] = (
    "idle",
    "running_left",
    "running_right",
    "waiting",
    "rest",
    "waving",
)

#: **默认帧率**（第 3 层）。全项目只允许存在这一处。
DEFAULT_FPS: Final[float] = 12.0

#: **遗留兜底帧率表**（第 2 层，过渡期兼容层）。
#:
#: 语义是"旧动作名（连字符写法）在旧实现里的观感"，只允许存在这一处
#: （agent.md §4.1）。值原样取自 `迁移清单.md` 第一部分的遗留表。
#:
#: 注意：当前两个自带素材包**每个动作都写了 `fps: 30.0`**，所以本表
#: **一次都不会被触发**——它保留只为"用户把 fps 删掉时观感不变"。
#: 表里没有 `running_left` 这类下划线键：用户定案"就按素材文件名走"，
#: 素材用的是下划线，所以旧的连字符键不会再被查到（命中第 3 层）。
LEGACY_ACTION_FPS: Final[Mapping[str, float]] = {
    "idle": 8.0,
    "running-left": 11.0,
    "running-right": 11.0,
    "waiting": 8.0,
    "sit": 8.25,
    "jumping": 8.0,
}


class FrameRateSource(Enum):
    """一个动作的帧率**是从哪一层来的**（`--list-actions` 要打印它）。"""

    ASSET = "asset"
    LEGACY = "legacy"
    DEFAULT = "default"

    @property
    def label(self) -> str:
        """给人看的中文说法（诊断输出用；枚举值本身仍是英文短名，便于脚本比对）。"""
        return {
            FrameRateSource.ASSET: "asset.json",
            FrameRateSource.LEGACY: "遗留兜底",
            FrameRateSource.DEFAULT: "默认 12",
        }[self]


def normalize_fps(value: Any) -> float | None:
    """把 `asset.json` 里的原始 `fps` 值规整成一个**可用的正浮点数**。

    坏值一律返回 `None`，调用方按"这个动作没有写 fps"继续走下一层
    （agent.md §2.7：**绝不抛异常**）。以下都算坏值：

    - 不是数字（`"快"` / `None` / 列表……）；
    - **`bool`**（`True` 在 Python 里是 `int` 的子类，不挡就会变成 1 fps）；
    - `<= 0`（含 0 与负数，"零分之一帧"没有意义）；
    - `NaN` / `±inf`（`NaN` 与任何数比较都是假，漏过去会污染整条时间轴）。
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None

    fps = float(value)
    if not math.isfinite(fps):
        return None
    if fps <= 0:
        return None
    return fps


def resolve_frame_rate(action: str, raw_fps: Any) -> tuple[float, FrameRateSource]:
    """按优先级解析出某动作的 `fps` 与**来源**（本函数的唯一职责）。

    `raw_fps` 传 `asset.json` 里读到的原值（**没写就传 `None`**）：
    坏值在这里被丢掉，于是自然落到第 2 / 3 层。
    """
    fps = normalize_fps(raw_fps)
    if fps is not None:
        return fps, FrameRateSource.ASSET

    legacy = LEGACY_ACTION_FPS.get(action)
    if legacy is not None:
        return float(legacy), FrameRateSource.LEGACY

    return DEFAULT_FPS, FrameRateSource.DEFAULT


def frame_ms_for(action: str, raw_fps: Any) -> tuple[float, float, FrameRateSource]:
    """`(fps, frame_ms, 来源)` 三件套。**每帧毫秒只在加载层算一次**（§2.7）。"""
    fps, source = resolve_frame_rate(action, raw_fps)
    return fps, 1000.0 / fps, source


@dataclass(frozen=True)
class ActionSpec:
    """一个动作：帧文件（**已按播放顺序排好**）+ 每帧显示时长 + 帧率来源。"""

    name: str
    frames: tuple[Path, ...]
    frame_ms: float
    fps_source: FrameRateSource
    #: 解析出来的 fps（`frame_ms` 的倒数）。只在诊断输出里用。
    fps: float

    @property
    def frame_count(self) -> int:
        """帧数（`--list-actions` 用）。"""
        return len(self.frames)


@dataclass(frozen=True)
class PetSpec:
    """一个素材包的**不可变**描述。构造成功后即视为"这个包可用"。"""

    namespace: str
    display_name: str
    description: str
    cell: tuple[int, int]
    actions: dict[str, ActionSpec]
    action_order: tuple[str, ...]
    #: 逻辑名 → 文件名。**只认索引里的声明**，没有兜底表（§2.5）。
    sounds: dict[str, str]
    root: Path
    #: `"user"` / `"bundled"`。
    source: str

    def action(self, name: str) -> ActionSpec | None:
        """按名字取动作；没有就是 `None`（**不抛异常、不给替身**）。"""
        return self.actions.get(name)

    def has_action(self, name: str) -> bool:
        """素材里到底有没有这个动作（"缺 `waving` 就不挥手"的判据）。"""
        return name in self.actions

    @property
    def sound_names(self) -> tuple[str, ...]:
        """已声明的音效逻辑名（保持索引里的顺序）。"""
        return tuple(self.sounds)

    def frame_path(self, action: str, index: int) -> Path | None:
        """第 `index` 帧的**绝对路径**；动作不存在或帧号越界 → `None`。

        帧文件名是相对 `root/frames` 的（§2.5），这里拼成绝对路径，
        免得调用方各自去算相对路径。
        """
        spec = self.actions.get(action)
        if spec is None:
            return None
        if index < 0 or index >= len(spec.frames):
            return None
        return self.root / "frames" / spec.frames[index]


# ------------------------------------------------------------------ 索引解析
#
# 下面这一组只处理"内存里的 JSON 对象"，**不读文件**：读盘（含 `utf-8-sig`）
# 是 `pet_loader` 的职责。放在 `pet_spec` 是为了让"一次列全校验"这条规则
# 和"数据结构"待在同一处，测试也不必造真实文件。


def parse_cell(value: Any) -> tuple[tuple[int, int] | None, str | None]:
    """`cell` 必须是**两个正数的数组**（不是对象）；布尔不算数。

    返回 `((宽, 高), None)` 或 `(None, 原因)`。
    """
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None, f"cell 必须是两个数的数组，实际是 {type(value).__name__}"
    if len(value) != 2:
        return None, f"cell 必须恰好有两个数，实际有 {len(value)} 个"

    numbers: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None, f"cell 的每一项都必须是整数，实际有 {type(item).__name__}（{item!r}）"
        if item <= 0:
            return None, f"cell 的每一项都必须是正数，实际有 {item!r}"
        numbers.append(item)
    return (numbers[0], numbers[1]), None


def build_action_spec(
    name: str,
    raw: Any,
    *,
    namespace: str = "",
) -> tuple[ActionSpec | None, list, list]:
    """把一个动作的原始值变成 `ActionSpec`。

    返回 `(规格 | None, 问题列表, 警告列表)`。**帧列表必须写全**（§2.5）：
    空列表 / 非字符串项都算问题，而不是"跳过就完事"。
    """
    # 延迟 import：`pet_loader` 依赖本模块，反向 import 会成环。
    from .pet_loader import IssueKind, PetIssue

    issues: list[PetIssue] = []
    warnings: list[PetIssue] = []

    if not isinstance(raw, Mapping):
        issues.append(
            PetIssue(
                IssueKind.ACTION_INVALID,
                namespace,
                name,
                f"动作 {name!r} 的值必须是对象，实际是 {type(raw).__name__}",
            )
        )
        return None, issues, warnings

    raw_frames = raw.get("frames")
    frame_names: list[str] = []
    if isinstance(raw_frames, (str, bytes)) or not isinstance(raw_frames, Sequence):
        issues.append(
            PetIssue(
                IssueKind.FRAMES_MISSING,
                namespace,
                name,
                f"动作 {name!r} 缺少 frames 数组",
            )
        )
    elif len(raw_frames) == 0:
        issues.append(
            PetIssue(
                IssueKind.FRAMES_MISSING,
                namespace,
                name,
                f"动作 {name!r} 的 frames 是空数组（至少要有一帧）",
            )
        )
    else:
        for position, entry in enumerate(raw_frames):
            if not isinstance(entry, str) or not entry.strip():
                issues.append(
                    PetIssue(
                        IssueKind.FRAME_NAME_INVALID,
                        namespace,
                        name,
                        f"动作 {name!r} 的第 {position} 项帧名必须是字符串，"
                        f"实际是 {type(entry).__name__}（{entry!r}）",
                    )
                )
                continue
            frame_names.append(entry)

    # 坏 `fps` 只是**警告**：它不该让整个包不可用，按"没写 fps"走下一层。
    raw_fps = raw.get("fps")
    fps, frame_ms, source = frame_ms_for(name, raw_fps)
    if "fps" in raw and normalize_fps(raw_fps) is None:
        warnings.append(
            PetIssue(
                IssueKind.FPS_INVALID,
                namespace,
                name,
                f"动作 {name!r} 的 fps 不可用（{raw_fps!r}），已按没有 fps 处理"
                f"（实际采用 {fps:g}，来源：{source.label}）",
            )
        )

    if frame_names and not issues:
        spec = ActionSpec(
            name=name,
            frames=tuple(Path(item) for item in frame_names),
            frame_ms=frame_ms,
            fps_source=source,
            fps=fps,
        )
        return spec, issues, warnings
    return None, issues, warnings


def parse_sounds(value: Any, *, namespace: str = "") -> tuple[dict[str, str], list]:
    """`sounds` 是"逻辑名 → 文件名或 `null`"，**只认声明**（§2.5）。

    - `sounds` 缺失 / 不是对象 → 空字典（**不算问题**：音效是可选功能）；
    - 值是 `null` → 记为 `""`（"声明了这个逻辑名、但没有对应文件"）；
    - 值是非空字符串 → 采用；
    - 其它（数字 / 对象 / 空串）→ 丢弃 + 一条警告（**不是**致命问题）。
    """
    from .pet_loader import IssueKind, PetIssue

    if not isinstance(value, Mapping):
        return {}, []

    warnings: list[PetIssue] = []
    kept: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            warnings.append(
                PetIssue(
                    IssueKind.SOUND_INVALID,
                    namespace,
                    None,
                    f"sounds 里有非字符串键 {key!r}，已忽略",
                )
            )
            continue
        if item is None:
            kept[key] = ""
            continue
        if isinstance(item, str) and item.strip():
            kept[key] = item
            continue
        warnings.append(
            PetIssue(
                IssueKind.SOUND_INVALID,
                namespace,
                None,
                f"sounds.{key} 的值必须是文件名或 null，实际是 "
                f"{type(item).__name__}（{item!r}），已忽略",
            )
        )
    return kept, warnings


def build_pet_spec(
    root: Path,
    data: Mapping[str, Any],
    *,
    source: str = "bundled",
) -> tuple[PetSpec | None, list, list]:
    """把 `asset.json` 的内存对象变成 `PetSpec`。

    返回 `(规格 | None, 问题列表, 警告列表)`。**不读磁盘**，
    所以"帧文件到底在不在"由 `pet_loader.validate_pet()` 负责。

    状态是"全有或全无"的：只要有**一个问题**（问题 ≠ 警告），
    就返回 `None`——半个可用的包比明确的失败更难查。
    """
    from .pet_loader import IssueKind, PetIssue

    namespace = root.name
    issues: list[PetIssue] = []
    warnings: list[PetIssue] = []

    # `name`：缺失 / 空白 → 退回命名空间（**不显示"未命名"**，§2.5）。
    raw_name = data.get("name")
    display_name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not display_name:
        display_name = namespace

    # `description`：非字符串 → 空串。
    raw_description = data.get("description")
    description = raw_description.strip() if isinstance(raw_description, str) else ""

    # `cell`：必需，两个正整数的数组。
    cell, cell_reason = parse_cell(data.get("cell"))
    if cell is None:
        issues.append(PetIssue(IssueKind.CELL_INVALID, namespace, None, cell_reason or "cell 非法"))

    # `actions`：必需，且至少要有一个能解析出来的动作。
    raw_actions = data.get("actions")
    actions: dict[str, ActionSpec] = {}
    action_order: list[str] = []
    if not isinstance(raw_actions, Mapping):
        issues.append(
            PetIssue(
                IssueKind.ACTIONS_MISSING,
                namespace,
                None,
                f"actions 必须是对象，实际是 {type(raw_actions).__name__}",
            )
        )
    elif len(raw_actions) == 0:
        issues.append(PetIssue(IssueKind.ACTIONS_MISSING, namespace, None, "actions 是空对象"))
    else:
        for action_name, action_raw in raw_actions.items():
            if not isinstance(action_name, str) or not action_name:
                issues.append(
                    PetIssue(
                        IssueKind.ACTION_NAME_INVALID,
                        namespace,
                        None,
                        f"动作名必须是非空字符串，实际是 {action_name!r}",
                    )
                )
                continue
            action_spec, action_issues, action_warnings = build_action_spec(
                action_name, action_raw, namespace=namespace
            )
            issues.extend(action_issues)
            warnings.extend(action_warnings)
            if action_spec is not None:
                actions[action_name] = action_spec
                action_order.append(action_name)
            else:
                # 动作级问题已经记进 issues；这里只补一条"它被跳过了"，
                # 免得调用方以为"这个动作还在、只是没帧"。
                issues.append(
                    PetIssue(
                        IssueKind.ACTION_INVALID,
                        namespace,
                        action_name,
                        f"动作 {action_name!r} 因上面的问题被跳过（未加入可用动作）",
                    )
                )
        if not actions:
            issues.append(
                PetIssue(
                    IssueKind.NO_USABLE_ACTION,
                    namespace,
                    None,
                    "没有任何动作能被解析出来",
                )
            )

    # `sounds`：可选；非法项只警告。
    sounds, sound_warnings = parse_sounds(data.get("sounds"), namespace=namespace)
    warnings.extend(sound_warnings)

    if issues:
        return None, issues, warnings

    return (
        PetSpec(
            namespace=namespace,
            display_name=display_name,
            description=description,
            cell=cell or (0, 0),
            actions=actions,
            action_order=tuple(action_order),
            sounds=sounds,
            root=root,
            source=source,
        ),
        issues,
        warnings,
    )


# ------------------------------------------------------------------ 防漂移断言
#
# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。

if len(set(CANONICAL_ACTIONS)) != len(CANONICAL_ACTIONS):  # pragma: no cover
    raise RuntimeError(f"CANONICAL_ACTIONS 里有重复项：{CANONICAL_ACTIONS}")

if not CANONICAL_ACTIONS or DEFAULT_PET_NAMESPACE not in ("weedy",):  # pragma: no cover
    raise RuntimeError("默认形象常量被改成了意料之外的值，请同步 agent.md §2.4 / §5")

# 规范集里**不许**再出现已被用户废弃的旧名（`jumping` / `sit` / `review`）：
# 留着它们会让"编程侧与素材侧统一"这条定案悄悄失效。
_OBSOLETE_ACTION_NAMES = ("jumping", "sit", "review")
_STALE = [name for name in _OBSOLETE_ACTION_NAMES if name in CANONICAL_ACTIONS]
if _STALE:  # pragma: no cover
    raise RuntimeError(f"规范动作集里出现了已废弃的动作名：{_STALE}")

# 遗留表**只允许**存在这几个键（多一个键就意味着有人又加了一份兜底）。
if set(LEGACY_ACTION_FPS) != {"idle", "running-left", "running-right", "waiting", "sit", "jumping"}:
    raise RuntimeError(f"遗留兜底表被改动了：{sorted(LEGACY_ACTION_FPS)}")

if DEFAULT_FPS <= 0:  # pragma: no cover
    raise RuntimeError(f"DEFAULT_FPS 必须是正数，实际是 {DEFAULT_FPS}")
