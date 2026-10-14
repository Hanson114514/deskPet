"""配置：默认值表 + `Config` 数据对象 + `ConfigStore`（原子读写 / 校验 / `has()`）。

三条不许动摇的约定（agent.md §2.4 / 迁移清单 第二部分 §4）：

1. **读取永不致命**：文件不存在 / JSON 坏 / 顶层不是对象 / 字段类型不对
   → 逐项回退默认值 + 一条警告，**绝不让程序起不来**。
2. **写入原子**：临时文件 + `os.replace()`；失败不留半截文件、不留临时文件。
   （`QSaveFile` 被**明确否掉**：`config` 是"基础"层，不该依赖 Qt；
   见 agent.md §2.4 写死的那一句。）
3. **只保存已知键**：写回按 `DEFAULTS` 的键过滤（**`mirror` 也是已知键，别丢**）。

`has(key)` 存在的理由：**必须能区分"没设过"与"设成了默认值"**
（例如用户主动把档位选在默认的第 6 级）。用"读到的值 ≠ 默认值"代替是错的。
"""

import json
import logging
import os
import tempfile
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any, Final, Mapping

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

#: `mirror` 只允许这三个状态名（= 动作名，不另造一套状态词汇）。
#: 适用范围见 agent.md §0/§2.8：只有 待机 / 张望 / 休息 可镜像。
MIRROR_STATES: Final[tuple[str, ...]] = ("idle", "waiting", "sit")

#: 默认尺寸档位（第 6 级，附录 C）。
#: **task04 会改成"引用 `size.DEFAULT_LEVEL`"**——真值只许存在一处，
#: 在那之前这里就是那唯一一处，不要在别处再写一个 6。
DEFAULT_SIZE_LEVEL: int = 6


def _build_defaults() -> dict[str, Any]:
    """默认值表（**唯一真相源**）。键顺序即后续设置界面的行顺序。"""
    return {
        "pet": "",
        "hotkey": "Ctrl+Alt+Shift+P",
        "volume": 70,
        "idle_seconds": 20,
        "size_level": DEFAULT_SIZE_LEVEL,
        "position": "",
        "mirror": {},
    }


#: 配置默认值表。**不要在别处再写一份默认值。**
DEFAULTS: Final[dict[str, Any]] = _build_defaults()


# ------------------------------------------------------------------ 校验


def is_valid_type(value: Any, default: Any) -> bool:
    """按默认值的类型校验（agent.md §2.4 规则 4：`isinstance(value, type(default))`）。

    **`bool` 不算 `int`**：`volume: True` 必须被拒（`True` 在 Python 里是 `int` 的子类，
    直接 `isinstance` 会放行）。
    """
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(value, bool) and isinstance(default, int):
        return False
    return isinstance(value, type(default))


def normalize_mirror(value: Any) -> tuple[dict[str, bool] | None, str | None]:
    """把 `mirror` 的原始值过滤成"只含适用范围 + 值必须是 bool"的字典。

    返回 `(过滤结果, 警告原因)`：

    - 结果 `None` = **整体不可用**（根本不是对象）→ 回退默认空对象；
    - 结果可能是**空字典**：原对象存在、只是里面一项都不合规。
      这**不等于**"没设过"——调用方要照常采用结果，只把警告记下来
      （判据是"结果是不是 `None`"，不是"原因是否为空"）。
    """
    if not isinstance(value, dict):
        return None, f"mirror 必须是对象，实际是 {type(value).__name__}"

    kept: dict[str, bool] = {}
    dropped: list[str] = []
    for key, item in value.items():
        if not isinstance(key, str) or key not in MIRROR_STATES:
            dropped.append(f"{key!r}（不在适用范围 {'/'.join(MIRROR_STATES)} 内）")
            continue
        if not isinstance(item, bool):
            dropped.append(f"{key!r}（值必须是布尔，实际是 {type(item).__name__}）")
            continue
        kept[key] = item

    reason = f"mirror 丢弃了 {'; '.join(dropped)}" if dropped else None
    return kept, reason


def normalize_value(key: str, value: Any) -> tuple[Any, str | None]:
    """按 `key` 的默认值类型校验并规范化。返回 `(值, 警告原因)`。

    **值 `is None` 表示"整体不可用"**，调用方回退该键的默认值；
    `mirror` 走额外过滤（过滤后可能是个空字典，那**照样采用**）。
    """
    default = DEFAULTS[key]
    if key == "mirror":
        kept, reason = normalize_mirror(value)
        if kept is None:
            return None, reason
        return kept, reason

    if not is_valid_type(value, default):
        return None, (
            f"{key} 类型不符：期望 {type(default).__name__}，"
            f"实际 {type(value).__name__}（{value!r}）"
        )
    return value, None


def validate_value(key: str, value: Any) -> tuple[bool, str | None]:
    """供 `set()` 用：值能不能写进配置。返回 `(是否通过, 原因)`。"""
    if key not in DEFAULTS:
        return False, f"{key} 不是已知配置键"
    if key == "mirror":
        kept, reason = normalize_mirror(value)
        if kept is None:
            return False, reason or "mirror 非法"
        return True, None

    default = DEFAULTS[key]
    if not is_valid_type(value, default):
        return False, (
            f"{key} 类型不符：期望 {type(default).__name__}，"
            f"实际 {type(value).__name__}（{value!r}）"
        )
    return True, None


# ------------------------------------------------------------------ 数据对象


@dataclass(frozen=True)
class Config:
    """一份**不可变**的配置快照（字段即 `DEFAULTS` 的键）。

    `frozen=True` 让"读到一半被谁改了"这种问题不可能发生；
    `mirror` 是可变对象，构造时**拷一份**，免得外部改原字典影响快照。
    """

    pet: str = ""
    hotkey: str = "Ctrl+Alt+Shift+P"
    volume: int = 70
    idle_seconds: int = 20
    size_level: int = DEFAULT_SIZE_LEVEL
    position: str = ""
    #: 用 `default_factory` 而不是可变默认值 —— 默认值必须与 `DEFAULTS["mirror"]`
    #: （空对象）一致，下面模块级那组断言会盯着这一点。
    mirror: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mirror", dict(self.mirror or {}))

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "Config":
        """按字段名从映射里取（缺的走字段默认值）。**只认已知键。**"""
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in values.items() if key in known})

    def to_dict(self) -> dict[str, Any]:
        """展开成普通字典（写盘与比对用）。`mirror` 单独拷一份。"""
        data = {field.name: getattr(self, field.name) for field in fields(self)}
        data["mirror"] = dict(self.mirror)
        return data


# ------------------------------------------------------------------ 读写


class ConfigStore(QObject):
    """配置的**内存快照 + 原子落盘**。

    - `load()` / `reload()`：读盘，**永不抛异常**；
    - `get()` / `has()`：读内存快照；
    - `set()`：校验 → 改内存 → 原子落盘；写失败**只记日志并返回 `False`**，不抛；
      失败原因留在 `last_error(key)` 里，供"用户明确要求的写操作必须报错"那条约定取用
      （agent.md §2.10 第二类：界面要把系统给的原因**原样**带出来）。
    - `config_changed(key, value)`：改动后发出，供后续 task 的"改完立刻生效"。
    """

    #: 某个配置项的值已经变了（改完立刻生效的信号）。
    config_changed = Signal(str, object)

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        from . import paths

        self._path = Path(path) if path is not None else paths.config_path()
        #: 当前**有效**值（每个已知键都有）。
        self._values: dict[str, Any] = _build_defaults()
        #: 配置文件里**真的写过且通过了校验**的键。
        self._present: set[str] = set()
        #: 最近一次失败的原因（按"本次调用"记录，不按 key 累积）。
        self._last_errors: dict[str, str] = {}

    # -------------------------------------------------- 只读

    @property
    def path(self) -> Path:
        """本 store 对应的配置文件路径。"""
        return self._path

    def get(self, key: str) -> Any:
        """取当前值。未知键是**编程错误**，直接 `KeyError`（不静默返回 `None`）。"""
        if key not in DEFAULTS:
            raise KeyError(f"{key} 不是已知配置键")
        return self._values[key]

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def has(self, key: str) -> bool:
        """**配置文件里真的写过这一项吗**（有效且通过校验才算）。

        与 `get(key) != DEFAULTS[key]` 不是一回事：用户主动选默认档位时
        `has()` 为真、`get()` 等于默认值（agent.md §2.4 规则 5）。
        """
        return key in self._present

    def to_config(self) -> Config:
        """当前快照（不可变副本）。"""
        return Config.from_mapping(self._values)

    def to_dict(self) -> dict[str, Any]:
        """当前全部已知键（含默认值）的普通字典。"""
        data = dict(self._values)
        data["mirror"] = dict(self._values["mirror"])
        return data

    def snapshot(self) -> dict[str, Any]:
        """`to_dict()` 的别名（旧说法，保留给"读一份内存快照"的调用方）。"""
        return self.to_dict()

    def last_error(self, key: str | None = None) -> str | None:
        """最近一次 `set()` 失败的原因。

        `key=None` 返回"任意一项"的失败原因（供不需要区分键的调用方）。
        """
        if key is None:
            for reason in self._last_errors.values():
                if reason:
                    return reason
            return None
        return self._last_errors.get(key)

    # -------------------------------------------------- 读盘

    def load(self) -> Config:
        """从磁盘读配置。**永不抛异常。**

        每个键独立判定：缺的 / 类型不符的 / 被过滤掉的 → 回退默认值 +
        一条 `WARNING`，**其它键不受影响**。
        """
        raw = self._read_raw()
        values, present = self._merge(raw)
        self._values = values
        self._present = present
        self._last_errors.clear()
        return self.to_config()

    def reload(self) -> Config:
        """重新读盘（供"改完立即生效"）。与 `load()` 同一条路径，变化时发信号。"""
        before = dict(self._values)
        config = self.load()
        for key in DEFAULTS:
            after = self._values[key]
            if before.get(key) != after:
                self.config_changed.emit(key, after)
        return config

    def _read_raw(self) -> dict[str, Any]:
        """读原始 JSON 对象；任何失败都返回**空字典**（= 全部走默认值）。"""
        if not self._path.exists():
            logger.info("配置文件不存在，全部使用默认值：%s", self._path)
            return {}

        try:
            # 人写的 JSON 一律用 utf-8-sig 读（容 BOM，agent.md §4.4）。
            text = self._path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("配置文件读不出来，全部使用默认值：%s（%s）", self._path, exc)
            return {}

        try:
            data = json.loads(text)
        except (ValueError, UnicodeError) as exc:
            logger.warning("配置文件不是合法 JSON，全部使用默认值：%s（%s）", self._path, exc)
            return {}

        if not isinstance(data, dict):
            logger.warning(
                "配置文件顶层必须是对象，全部使用默认值：%s（实际 %s）",
                self._path,
                type(data).__name__,
            )
            return {}
        return data

    def _merge(self, raw: Mapping[str, Any]) -> tuple[dict[str, Any], set[str]]:
        """把原始映射并进默认值，返回 `(有效值, 真的写过的键)`。"""
        values = _build_defaults()
        present: set[str] = set()

        for key in DEFAULTS:
            if key not in raw:
                continue  # 缺字段 = 没设过，**不**记警告
            value, reason = normalize_value(key, raw[key])
            if reason is not None:
                logger.warning("配置项 %s 有不可用的部分：%s", key, reason)
            if value is None:
                # 只有"整体不可用"才回退默认值；`mirror` 过滤后为空**照样采用**。
                values[key] = DEFAULTS[key]
                continue
            values[key] = value
            present.add(key)

        unknown = [key for key in raw if key not in DEFAULTS]
        if unknown:
            # 未知键**不影响启动**，下次写回时被过滤掉（task02 §2.3）。
            logger.warning("配置文件里有未知键，将在下次写回时过滤：%s", ", ".join(map(str, unknown)))

        return values, present

    # -------------------------------------------------- 写盘

    def set(self, key: str, value: Any) -> bool:
        """校验 + 改内存 + **原子落盘**；写失败**只记日志**，返回 `False`，**不抛**。

        - 值类型不合法 → `False` + 警告（内存**不动**，避免"内存是脏值、磁盘是旧值"）；
        - `mirror` 不合法**整项拒收**；合法但含适用范围外的键 → 只保留合规部分；
        - 写入成功时该项会被记为"**真的写过**"（`has()` 变真），
          哪怕写的正好是它的默认值（用户主动选了默认档位，agent.md §2.4 规则 5）。
        """
        self._last_errors.pop(key, None)

        ok, reason = validate_value(key, value)
        if not ok:
            self._last_errors[key] = reason or "值不合法"
            logger.warning("拒绝写入配置项 %s：%s", key, self._last_errors[key])
            return False

        normalized, filter_reason = normalize_value(key, value)
        if normalized is None:  # pragma: no cover - validate_value 已经挡过一次
            message = filter_reason or f"{key} 的值不可用"
            self._last_errors[key] = message
            logger.warning("拒绝写入配置项 %s：%s", key, message)
            return False
        if filter_reason is not None:
            logger.warning("配置项 %s 写入前做了过滤：%s", key, filter_reason)

        if self._values.get(key) == normalized:
            # 值没变 → 不发信号、**不写盘**（拖动过程不逐帧写配置）。
            # 但"用户确实写过这一项"这件事必须记下来。
            self._present.add(key)
            return True

        self._values[key] = normalized
        if not self._write():
            reason = self._last_errors.get(key) or self.last_error() or "写配置失败"
            self._last_errors[key] = reason
            return False

        self._present.add(key)
        self.config_changed.emit(key, normalized)
        return True

    def write(self) -> bool:
        """按 `DEFAULTS` 的键过滤后原子落盘当前全部值（不校验、不改内存）。"""
        return self._write()

    def _write(self) -> bool:
        """原子写：临时文件 + `os.replace()`。

        顺序是"先把全部字节落到临时文件并 `fsync`，再替换"，
        所以任何一步失败都**不会留下半个 config.json**；
        失败路径自己负责删掉临时文件（agent.md §2.4 规则 2）。
        """
        payload = self.to_dict()
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)
        except (TypeError, ValueError) as exc:  # pragma: no cover - 值都校验过，理论到不了
            message = f"配置序列化失败：{exc}"
            self._last_errors["*"] = message
            logger.error("%s", message)
            return False

        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o644)
            os.replace(temp_path, self._path)
            temp_path = None  # 替换成功，已经没有临时文件要收拾
            return True
        except Exception as exc:
            # **不要**只捕 OSError：写盘要经过"序列化 / 目录 / 替换"好几步，
            # 任何一步的意外都会让"写配置"这条用户可见操作失败——
            # 那时必须返回 False 并把原因带出来，而不是把异常甩给调用方。
            message = f"写配置失败：{self._path}（{type(exc).__name__}: {exc}）"
            self._last_errors["*"] = message
            logger.error("%s", message)
            return False
        finally:
            if temp_path is not None:
                try:
                    os.unlink(temp_path)
                except OSError:
                    # "清理类"操作尽力而为；即使清理失败也不改变"写失败"这个结论。
                    logger.debug("临时文件清理失败：%s", temp_path)


# ------------------------------------------------------------------ 模块级断言

# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。
# 防漂移：`Config` 的字段必须与默认值表**同名同序同默认值**，否则
# `Config.from_mapping()` 会静默丢掉配置项（"加了默认值表一行却忘了数据对象"
# 这种错最难查），而且 `Config()` 直接构造出来的值会和 `DEFAULTS` 分叉。
_CONFIG_FIELDS = tuple(item.name for item in fields(Config))
if _CONFIG_FIELDS != tuple(DEFAULTS):  # pragma: no cover - 只有代码被改歪了才会走到
    raise RuntimeError(
        f"Config 字段与 DEFAULTS 键不一致：{_CONFIG_FIELDS} != {tuple(DEFAULTS)}"
    )

for _field in fields(Config):  # pragma: no cover - 只有代码被改歪了才会走到
    if _field.default is not MISSING:
        _effective: Any = _field.default
    elif _field.default_factory is not MISSING:  # type: ignore[misc]
        _effective = _field.default_factory()  # type: ignore[misc]
    else:
        raise RuntimeError(f"Config.{_field.name} 没有默认值，破坏了'默认值只在一处'")
    if _effective != DEFAULTS[_field.name]:
        raise RuntimeError(
            f"Config.{_field.name} 的默认值 {_effective!r} 与 "
            f"DEFAULTS 的 {DEFAULTS[_field.name]!r} 分叉了"
        )
del _field
