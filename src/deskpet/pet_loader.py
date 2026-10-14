"""素材加载：两根扫描、`asset.json` 解析、**一次性列全校验**、统一解析"一个说法"。

本模块是**唯一碰素材磁盘的地方**。它把 `asset.json` 读成内存对象，
交给 `pet_spec` 变成数据对象，然后补上只能靠磁盘回答的问题（帧文件在不在）。

## 三条不许动摇的规则

1. **一次列全校验**（`迁移清单.md` §1）：缺索引 / 索引解析不了 / `cell` 非法 /
   无动作 / 缺帧文件一次性全部列出，**不是**"修一个报一个"。
2. **解析动作永不抛异常**：`resolve_pet()` 只返回结果与问题，绝不打断启动。
   真正"要报错"的场合（命令行显式指定了坏包、连默认形象都不可用）
   由调用方用 `load_pet()` 主动抛 `PetLoadError`。
3. **读一律 `utf-8-sig`**（容 BOM，agent.md §4.4）；本模块**不写任何文件**。

## 与 `pet_spec` 的分工

`pet_spec` 只处理"内存里的 JSON 对象"，所以它会为了错误类型反向 import 本模块
（`IssueKind` / `PetIssue` 定义在这里）。本模块在**模块级** import `pet_spec`，
不构成循环：反向那条 import 写在函数体里。
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Final

from . import nsrules, paths
from .errors import DeskPetError
from .pet_spec import (
    DEFAULT_PET_NAMESPACE,
    PetSpec,
    build_pet_spec,
)

logger = logging.getLogger(__name__)

#: 索引文件名（固定，`pet.json` / `frames.json` 已废弃，无向后兼容）。
INDEX_FILE_NAME: Final[str] = "asset.json"

#: 帧目录名（固定）。
FRAMES_DIR_NAME: Final[str] = "frames"

#: 来源标记。
SOURCE_USER: Final[str] = "user"
SOURCE_BUNDLED: Final[str] = "bundled"


class IssueKind(Enum):
    """一条问题的**种类**（诊断输出与自检断言都按它分类，不要比字面量）。"""

    NAMESPACE_INVALID = "namespace_invalid"
    NOT_A_DIRECTORY = "not_a_directory"
    INDEX_MISSING = "index_missing"
    INDEX_UNREADABLE = "index_unreadable"
    INDEX_INVALID_JSON = "index_invalid_json"
    INDEX_NOT_OBJECT = "index_not_object"
    CELL_INVALID = "cell_invalid"
    ACTIONS_MISSING = "actions_missing"
    ACTION_INVALID = "action_invalid"
    ACTION_NAME_INVALID = "action_name_invalid"
    FRAMES_MISSING = "frames_missing"
    FRAME_NAME_INVALID = "frame_name_invalid"
    FRAME_FILE_MISSING = "frame_file_missing"
    NO_USABLE_ACTION = "no_usable_action"
    # --- 以下几类**不是**致命问题，只出现在 warnings 里 ---
    FPS_INVALID = "fps_invalid"
    SOUND_INVALID = "sound_invalid"
    CELL_MISMATCH = "cell_mismatch"


@dataclass(frozen=True)
class PetIssue:
    """一条可读的问题 / 警告。

    `kind` 是机器判据、`detail` 是给人看的文字（诊断输出直接用 `str(issue)`）。
    `action` 为 `None` 表示这条问题不属于某个具体动作。
    """

    kind: IssueKind
    namespace: str
    action: str | None
    detail: str

    def __str__(self) -> str:
        if self.action:
            return f"[{self.kind.value}] {self.namespace}/{self.action}：{self.detail}"
        return f"[{self.kind.value}] {self.namespace}：{self.detail}"


class PetLoadError(DeskPetError):
    """素材包不可用。**带出完整问题列表**，不是只带第一个异常。

    `agent.md` §2.10 第一类（启动期致命）与"命令行显式指定坏包"都走这里。
    """

    def __init__(
        self,
        root: Path | str,
        issues: Iterable[PetIssue],
        warnings: Iterable[PetIssue] = (),
    ) -> None:
        self.root = Path(root)
        self.issues: tuple[PetIssue, ...] = tuple(issues)
        self.warnings: tuple[PetIssue, ...] = tuple(warnings)
        head = f"素材包不可用：{self.root}（{len(self.issues)} 个问题）"
        body = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"{head}\n{body}" if body else head)


@dataclass(frozen=True)
class PetCandidate:
    """**目录层面**扫出来的一个候选包（`--list-pets` 用它开始）。

    `display_name` / `sounds` 是"顺手读到的索引信息"：索引坏掉时退回命名空间与空字典，
    **不在这里报错**——完整结论由 `validate_pet()` 给。
    """

    namespace: str
    root: Path
    source: str
    display_name: str
    sounds: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class PetReport:
    """一个候选包的**完整校验结论**（`--list-pets` 的最终产物）。"""

    candidate: PetCandidate
    spec: PetSpec | None
    issues: tuple[PetIssue, ...]
    warnings: tuple[PetIssue, ...]

    @property
    def ok(self) -> bool:
        """这个包能不能用（**问题**为空才算能用；警告不影响）。"""
        return self.spec is not None and not self.issues

    @property
    def namespace(self) -> str:
        return self.candidate.namespace

    @property
    def display_name(self) -> str:
        return self.candidate.display_name

    @property
    def source(self) -> str:
        return self.candidate.source

    def sound_summary(self) -> str:
        """音效摘要（`--list-pets` 打印用）。没有声明任何音效 → `"无音效"`。

        候选多于一个时打全并标出个数：这份摘要的用处正是"一眼看出有几个备选"。
        """
        if not self.candidate.sounds:
            return "无音效"
        parts = []
        for name, filenames in self.candidate.sounds.items():
            if not filenames:
                parts.append(f"{name}→(未提供文件)")
            elif len(filenames) == 1:
                parts.append(f"{name}→{filenames[0]}")
            else:
                parts.append(f"{name}→{len(filenames)} 个（{'、'.join(filenames)}）")
        return "、".join(parts)

    def conclusion(self) -> str:
        """校验结论（一句话）。"""
        if self.ok:
            assert self.spec is not None  # ok 的定义保证了这一点
            return f"可用（{len(self.spec.actions)} 个动作）"
        if self.spec is None and not self.issues:
            return "不可用（原因未知）"
        return f"不可用（{len(self.issues)} 个问题）"


@dataclass(frozen=True)
class ResolutionResult:
    """`resolve_pet()` 的产物：`(规格 | None, 问题, 是否回退过)`。

    `fell_back=True` 表示"用户/配置说的那个包没用上，改用了默认形象"——
    调用方据此决定是"记一条警告继续跑"（配置来源）还是"如实报错"（命令行来源）。
    """

    spec: PetSpec | None
    issues: tuple[PetIssue, ...]
    fell_back: bool = False

    @property
    def ok(self) -> bool:
        return self.spec is not None


# ------------------------------------------------------------------ 目录扫描


def _directories(root: Path) -> list[Path]:
    """列出一个根下的子目录（根不存在 / 读不了 → 空列表 + 一条 debug）。

    **不是错误**：`data/pets/` 在用户导入素材前本来就不存在（§2.3）。
    """
    try:
        if not root.is_dir():
            logger.debug("素材根不存在，跳过：%s", root)
            return []
        return sorted(
            (entry for entry in root.iterdir() if entry.is_dir()),
            key=lambda item: nsrules.fold_for_compare(item.name),
        )
    except OSError as exc:
        logger.warning("素材根读不了，跳过：%s（%s）", root, exc)
        return []


def scan_pets() -> list[PetCandidate]:
    """扫描**两个根**（用户优先、名字排序去重），返回候选包列表。

    - 用户根 `<data>/pets/` 里的包 `source="user"`，自带根 `<resource>/assets/`
      里的包 `source="bundled"`（agent.md §1）；
    - **同名时用户优先**（§2.3）：自带的那份被整个丢弃，不出现在结果里；
    - 不合法命名空间的目录**直接跳过**（并记一条 debug，不算错误）；
    - 本函数只读索引里的"展示名 / 音效"这类轻量信息（读不出来就退回命名空间），
      **不检查帧文件**——完整结论请用 `list_reports()` / `validate_pet()`。
    """
    seen: dict[str, PetCandidate] = {}

    # 用户根排在前面：先放进 seen 的赢（同名自带包被忽略）。
    for root, source in (
        (paths.user_pets_dir(), SOURCE_USER),
        (paths.bundled_pets_dir(), SOURCE_BUNDLED),
    ):
        for directory in _directories(root):
            namespace = directory.name
            reason = nsrules.rejection_reason(namespace)
            if reason is not None:
                logger.debug("跳过命名非法的素材目录 %s：%s", directory, reason)
                continue
            key = nsrules.fold_for_compare(namespace)
            if key in seen:
                logger.debug(
                    "素材包 %s：用户目录的版本优先，忽略 %s", namespace, directory
                )
                continue
            display_name, sounds = _peek_index(directory, namespace)
            seen[key] = PetCandidate(
                namespace=namespace,
                root=directory,
                source=source,
                display_name=display_name,
                sounds=sounds,
            )

    return sorted(seen.values(), key=lambda item: nsrules.fold_for_compare(item.namespace))


def _peek_index(root: Path, namespace: str) -> tuple[str, dict[str, str]]:
    """顺手读一眼索引里的展示名与音效；**任何失败都静默退回**。

    故意不在这里报错：本函数服务于"列目录"，报错是 `validate_pet()` 的职责。
    """
    raw = _read_index(root, namespace)
    if raw is None:
        return namespace, {}

    raw_name = raw.get("name")
    display_name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else namespace

    from .pet_spec import parse_sounds

    sounds, _ = parse_sounds(raw.get("sounds"), namespace=namespace)
    return display_name, sounds


def _read_index(root: Path, namespace: str) -> dict[str, Any] | None:
    """读并解析 `asset.json`；**只返回"能用的对象"**，失败返回 `None`。

    这里**不记问题**（问题由调用方按上下文记），但会记 warning 日志，
    这样即使调用方忘了汇报，日志里也留得下线索。
    """
    index_path = root / INDEX_FILE_NAME
    try:
        # 人写的 JSON 一律 utf-8-sig（容 BOM，agent.md §4.4）。
        text = index_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        logger.warning("素材包 %s 缺少 %s", namespace, INDEX_FILE_NAME)
        return None
    except OSError as exc:
        logger.warning("素材包 %s 的 %s 读不出来：%s", namespace, INDEX_FILE_NAME, exc)
        return None
    except UnicodeError as exc:
        logger.warning("素材包 %s 的 %s 不是合法 UTF-8：%s", namespace, INDEX_FILE_NAME, exc)
        return None

    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.warning("素材包 %s 的 %s 不是合法 JSON：%s", namespace, INDEX_FILE_NAME, exc)
        return None

    if not isinstance(data, dict):
        logger.warning(
            "素材包 %s 的 %s 顶层必须是对象，实际是 %s",
            namespace,
            INDEX_FILE_NAME,
            type(data).__name__,
        )
        return None
    return data


# ------------------------------------------------------------------ 校验


def _index_issues(root: Path, namespace: str) -> tuple[dict[str, Any] | None, list[PetIssue]]:
    """把"读索引"这一步的失败翻译成 `PetIssue`（**逐种情况分开**，便于诊断）。"""
    index_path = root / INDEX_FILE_NAME
    if not index_path.is_file():
        return None, [
            PetIssue(IssueKind.INDEX_MISSING, namespace, None, f"缺少索引文件 {index_path}")
        ]

    try:
        text = index_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, [
            PetIssue(IssueKind.INDEX_UNREADABLE, namespace, None, f"索引读不出来：{exc}")
        ]
    except UnicodeError as exc:
        return None, [
            PetIssue(IssueKind.INDEX_UNREADABLE, namespace, None, f"索引不是合法 UTF-8：{exc}")
        ]

    try:
        data = json.loads(text)
    except ValueError as exc:
        return None, [
            PetIssue(IssueKind.INDEX_INVALID_JSON, namespace, None, f"索引不是合法 JSON：{exc}")
        ]

    if not isinstance(data, dict):
        return None, [
            PetIssue(
                IssueKind.INDEX_NOT_OBJECT,
                namespace,
                None,
                f"索引顶层必须是对象，实际是 {type(data).__name__}",
            )
        ]
    return data, []


def _frame_issues(
    root: Path, namespace: str, spec: PetSpec
) -> tuple[list[PetIssue], list[PetIssue]]:
    """逐个动作检查帧文件在不在。

    返回 `(问题, 警告)`。**同一个文件名只报一次**（把它挂在第一次引用它的动作上）：
    一个包里有 4 个动作共用一张图时，报 4 条完全相同的"缺帧"只会淹没真正的问题。

    另外顺手核对第一帧的实际像素尺寸与 `cell` 是否一致（**只是警告**）：
    `cell` 是"必须等于帧文件实际像素尺寸"的字段（agent.md §2.5），
    但对不上时画面本身照样能出，不值得让整个包不可用。
    """
    issues: list[PetIssue] = []
    warnings: list[PetIssue] = []
    reported: set[str] = set()
    frames_root = root / FRAMES_DIR_NAME

    for action_name in spec.action_order:
        action = spec.actions[action_name]
        for frame in action.frames:
            key = frame.as_posix()
            if key in reported:
                continue
            target = frames_root / frame
            if not target.is_file():
                reported.add(key)
                issues.append(
                    PetIssue(
                        IssueKind.FRAME_FILE_MISSING,
                        namespace,
                        action_name,
                        f"帧文件不存在：{target}",
                    )
                )

    if spec.action_order:
        first_name = spec.action_order[0]
        first = spec.actions[first_name]
        if first.frames:
            first_path = frames_root / first.frames[0]
            if first_path.is_file():
                actual = _image_size(first_path)
                if actual is not None and actual != spec.cell:
                    warnings.append(
                        PetIssue(
                            IssueKind.CELL_MISMATCH,
                            namespace,
                            first_name,
                            f"cell 声明 {spec.cell[0]}×{spec.cell[1]}，"
                            f"但 {first.frames[0]} 实际是 {actual[0]}×{actual[1]}",
                        )
                    )
    return issues, warnings


def _image_size(path: Path) -> tuple[int, int] | None:
    r"""只读 PNG 文件头拿尺寸——**不构造 QImage**（本模块不依赖 Qt）。

    取不到（不是 PNG / 文件截断）返回 `None`，由调用方当作"没法核对"。

    PNG 开头固定是：

    | 偏移 | 长度 | 内容 |
    | --- | --- | --- |
    | 0 | 8 | 签名 `\x89PNG\r\n\x1a\n` |
    | 8 | 4 | 第一个 chunk 的**长度**（IHDR 固定是 13） |
    | 12 | 4 | chunk 类型 `IHDR` |
    | 16 | 4 | 宽度（大端） |
    | 20 | 4 | 高度（大端） |

    > **别把宽度读在偏移 8**——那里是 chunk 长度，读出来是个荒唐的大数。
    > task03 实际踩到：报出 `13 × 1229472850`，那个 13 正是 IHDR 的长度字段。
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    if header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _analyze(root: Path, *, source: str | None = None) -> PetReport:
    """**校验的唯一实现**：`list_reports()` / `validate_pet()` / `load_pet()` 都走它。"""
    namespace = root.name
    resolved_source = source or _source_for(root)
    candidate = PetCandidate(
        namespace=namespace,
        root=root,
        source=resolved_source,
        display_name=namespace,
    )

    def _report(
        spec: PetSpec | None,
        issues: list[PetIssue],
        warnings: list[PetIssue],
        candidate_info: PetCandidate | None = None,
    ) -> PetReport:
        return PetReport(
            candidate=candidate_info or candidate,
            spec=spec,
            issues=tuple(issues),
            warnings=tuple(warnings),
        )

    # 0) 目录本身 + 命名空间白名单（后面所有推理都建立在"它是个合法目录"上）。
    if not root.is_dir():
        return _report(
            None,
            [PetIssue(IssueKind.NOT_A_DIRECTORY, namespace, None, f"不是一个目录：{root}")],
            [],
        )
    reason = nsrules.rejection_reason(namespace)
    if reason is not None:
        return _report(
            None,
            [PetIssue(IssueKind.NAMESPACE_INVALID, namespace, None, reason)],
            [],
        )

    # 1) 读索引。
    data, issues = _index_issues(root, namespace)
    if data is None:
        return _report(None, issues, [])

    # 2) 结构解析（cell / actions / fps / sounds）。
    spec, build_issues, warnings = build_pet_spec(root, data, source=resolved_source)
    issues.extend(build_issues)

    # 3) 帧文件在不在（只有结构过了才谈得上）。
    if spec is not None and not issues:
        frame_issues, frame_warnings = _frame_issues(root, namespace, spec)
        issues.extend(frame_issues)
        warnings.extend(frame_warnings)

    if issues:
        return _report(None, issues, warnings)

    # 校验通过 → 用索引里的展示名与音效刷新候选信息（`spec` 是权威来源）。
    assert spec is not None
    return _report(
        spec,
        [],
        warnings,
        PetCandidate(
            namespace=namespace,
            root=root,
            source=resolved_source,
            display_name=spec.display_name or namespace,
            sounds=dict(spec.sounds),
        ),
    )


def _source_for(root: Path) -> str:
    """这个目录属于哪个根（用户优先）。判不出来时按自带包算。

    三个容易被忽略的点：

    - **两边都要 `resolve()`**：`<data>/pets` 与 `<resource>/assets` 常常是相对
      `program_dir()` 拼出来的，而调用方给的 `root` 可能是相对的，
      不解析就会"比了半天全是 False"（task03 实际踩到：用户包被当成自带包）。
    - 用 `is_relative_to()`，不要用 `in parents`：后者对**相等的路径**是 False，
      于是"根目录自己"永远判不出来。
    - 自带根排在后面比较，但**用户根要先判**：用户根常常就在自带根附近，
      顺序反了会把用户包算成自带包。
    """
    try:
        resolved = root.resolve()
    except OSError:  # pragma: no cover - 解析失败极罕见
        return SOURCE_BUNDLED

    for candidate_root, source in (
        (paths.user_pets_dir(), SOURCE_USER),
        (paths.bundled_pets_dir(), SOURCE_BUNDLED),
    ):
        try:
            candidate = candidate_root.resolve()
        except OSError:  # pragma: no cover
            continue
        if resolved == candidate or resolved.is_relative_to(candidate):
            return source
    return SOURCE_BUNDLED


def validate_pet(root: Path | str) -> list[PetIssue]:
    """**一次性列全**所有问题（缺索引 / 索引解析不了 / `cell` 非法 / 无动作 / 缺帧文件）。

    返回**问题列表**（可能为空），**不抛异常**。坏 `fps` / 坏 `sounds` 这类
    "只该退化、不该致命"的情况不进这个列表，它们只记警告（见 `list_reports()`）。
    """
    return list(_analyze(Path(root)).issues)


def list_reports() -> list[PetReport]:
    """扫描 + **逐个包完成完整校验**，返回每个包的结论（`--list-pets` 用它）。"""
    return [_analyze(candidate.root, source=candidate.source) for candidate in scan_pets()]


def load_pet(root: Path | str) -> PetSpec:
    """构建 `PetSpec`；有问题时抛 `PetLoadError`（**带完整问题列表**）。

    这是"调用方明确要求、失败就必须报错"的那条路径（agent.md §2.10 第二类）：
    命令行显式指定的包、以及连默认形象都不可用时的收尾。
    """
    report = _analyze(Path(root))
    if report.issues or report.spec is None:
        raise PetLoadError(Path(root), report.issues, report.warnings)
    return report.spec


# ------------------------------------------------------------------ 统一解析


def _looks_like_directory(spec: str) -> bool:
    """命令行说法是"目录"还是"命名空间"（agent.md §2.3 的唯一判据）。

    含路径分隔符（`/` 或 `\\`）**或本身就是已存在的目录** → 按目录解释。
    两种说法（`--pet` / `--sprite`）**共用这一条规则**。
    """
    if "/" in spec or "\\" in spec:
        return True
    try:
        return Path(spec).is_dir()
    except OSError:  # pragma: no cover - 极端路径（超长等）按命名空间处理
        return False


def _find_by_namespace(namespace: str) -> PetCandidate | None:
    """在候选包里按命名空间找（Windows 文件名大小写不敏感 → 用折叠比较）。"""
    key = nsrules.fold_for_compare(namespace)
    for candidate in scan_pets():
        if nsrules.fold_for_compare(candidate.namespace) == key:
            return candidate
    return None


def _resolve_one(
    spec_text: str,
) -> tuple[PetSpec | None, list[PetIssue], str]:
    """解析**一个说法**，返回 `(规格 | None, 问题, 说法是"目录"还是"命名空间")`。

    **永不抛异常**：命令行坏说法与配置坏说法走的是同一条路，
    区别只在调用方拿到结果后怎么处置（报错退出 / 记警告继续）。
    """
    if _looks_like_directory(spec_text):
        root = Path(spec_text)
        report = _analyze(root)
        return report.spec, list(report.issues), "目录"

    reason = nsrules.rejection_reason(spec_text)
    if reason is not None:
        return (
            None,
            [
                PetIssue(
                    IssueKind.NAMESPACE_INVALID,
                    spec_text,
                    None,
                    f"素材包命名非法：{reason}",
                )
            ],
            "命名空间",
        )

    candidate = _find_by_namespace(spec_text)
    if candidate is None:
        available = [item.namespace for item in scan_pets()]
        listing = "、".join(available) if available else "（一个都没有）"
        return (
            None,
            [
                PetIssue(
                    IssueKind.NOT_A_DIRECTORY,
                    spec_text,
                    None,
                    f"找不到素材包 {spec_text!r}；已找到的包：{listing}",
                )
            ],
            "命名空间",
        )

    report = _analyze(candidate.root, source=candidate.source)
    return report.spec, list(report.issues), "命名空间"


def resolve_pet(
    spec: str | None,
    config_pet: str | None = None,
) -> ResolutionResult:
    """统一解析"该用哪个素材包"。**永不抛异常**。

    按 `agent.md` §1 的坏素材规则逐条实现：

    | 来源 | 坏包时的行为 |
    | --- | --- |
    | `spec`（命令行） | 返回问题、**不回退**（`fell_back=False`），由调用方如实报错退出 |
    | `config_pet`（配置） | **回退默认形象** + 问题一并返回，`fell_back=True`，不阻断启动 |
    | 默认形象也坏 | 返回 `None` + 问题（调用方据此抛错，绝不"假装能跑"） |

    `spec` 为 `None` 表示"命令行**没给这个参数**"→ 走配置；配置也没表态
    （空串，`agent.md` §2.4）→ 用默认形象。

    **`spec == ""` 与 `spec is None` 不是一回事**：前者是"命令行给了参数但是空值"，
    那是一个**非法的说法**，要如实报错（`--pet ""` 属于验收里明确要求报错的一档）；
    后者才是"没表态"。别把两者合并成"空就是没给"——那样 `--pet ""` 会静默
    显示默认形象，用户以为自己指定了什么东西。
    """
    issues: list[PetIssue] = []

    # 1) 命令行给过这个参数（`""` 也算给过）：坏就是坏，不许偷偷换形象。
    if spec is not None:
        text = spec if isinstance(spec, str) else str(spec)
        pet_spec, spec_issues, _ = _resolve_one(text.strip())
        issues.extend(spec_issues)
        if pet_spec is not None:
            return ResolutionResult(pet_spec, (), False)
        return ResolutionResult(None, tuple(issues), False)

    # 2) 配置里的 `pet`（空串 / 空白 = 用户还没表态）。
    if config_pet is not None and str(config_pet).strip():
        pet_spec, config_issues, _ = _resolve_one(str(config_pet).strip())
        if pet_spec is not None:
            return ResolutionResult(pet_spec, (), False)
        # 配置指向的包不可用 → 回退默认形象（**不写配置**，回写属 task11）。
        # 这里只**攒问题**、不自己写日志：调用方（`app._report_resolution_problems`）
        # 负责把问题逐条 + "已回退"这条结论一次性写出去。在两层各记一次
        # 会让同一件事在日志里出现两遍（task03 实际踩到）。
        issues.extend(config_issues)
        fallback = resolve_pet(None, None)
        return ResolutionResult(
            fallback.spec,
            tuple(issues) + tuple(fallback.issues),
            True,
        )

    # 3) 默认形象。**真值只在 `pet_spec.DEFAULT_PET_NAMESPACE` 一处**。
    default_spec, default_issues, _ = _resolve_one(DEFAULT_PET_NAMESPACE)
    if default_spec is not None:
        return ResolutionResult(default_spec, (), False)

    issues.extend(default_issues)
    if not issues:  # pragma: no cover - 只可能出现在"默认形象调用约定被破坏"时
        issues.append(
            PetIssue(
                IssueKind.NOT_A_DIRECTORY,
                DEFAULT_PET_NAMESPACE,
                None,
                "默认形象不可用，但没有拿到任何原因",
            )
        )
    return ResolutionResult(None, tuple(issues), False)


# ------------------------------------------------------------------ 帧数据


def load_frame_image(spec: PetSpec, action: str, index: int):
    """读第 `index` 帧，返回**带 alpha 的 `QImage`**。

    **这里没有任何颜色判定**（agent.md §2.8 / 迁移清单 D1）：素材本身就是
    透明背景 PNG，透明度的**唯一来源是 alpha 通道**，所以加载层只做
    "读 PNG → 统一成便于绘制的格式"。旧实现的色键（洋红那个 RGB 值）、
    色键吸附容差、RGB 烘焙**全部作废**（因此全仓库搜不到它们）。

    失败（动作不存在 / 帧号越界 / 文件读不出来）→ 返回**空 `QImage`**
    （`.isNull()` 为真），**永不抛异常**：调用方按"没有图可画"退化。

    关于 DPI：**不给读出来的图标注 `devicePixelRatio`**。窗口尺寸就用素材的
    `cell`（本 task 不缩放、不选档位），两者都是"逻辑单位"，标了反而会缩放两次
    （agent.md §4.9 的那条坑）。
    """
    from PySide6.QtGui import QImage

    if spec is None or not action:
        return QImage()

    path = spec.frame_path(action, index)
    if path is None:
        logger.warning(
            "取帧越界：素材包 %s 的动作 %r 没有第 %d 帧（共 %d 帧）",
            spec.namespace,
            action,
            index,
            len(spec.actions[action].frames) if action in spec.actions else 0,
        )
        return QImage()

    image = QImage(str(path))
    if image.isNull():
        logger.warning("帧文件读不出来：%s", path)
        return QImage()

    # `Format_ARGB32_Premultiplied` 是 QPainter 合成最快的格式（agent.md §2.8）。
    if image.format() != QImage.Format_ARGB32_Premultiplied:
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    return image
