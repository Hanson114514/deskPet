"""设置窗口：**数据驱动**的五行设置 + 改完立即生效 + **绝不显示假成功**。

## 一切都从字段描述表来

`FIELDS` 是**单一真相源**：分组、行顺序、控件类型、量程、单位、提示文字
全部写在里面，界面代码只负责"照着渲染"（task11 §5 明写：**不许**在界面
代码里手写行顺序或控件类型）。加一行设置 = 在 `FIELDS` 里加一项。

| `FieldKind` | 用在 | 控件 |
| --- | --- | --- |
| `COMBO` | `角色` | 下拉 | 
| `CHECK` | `开机自启` | 勾选框 |
| `KEYSEQ` | `呼出快捷键` | `keycapture.KeyCaptureEdit` |
| `SLIDER` | `音量` / `久置时间` | 滑块 + 数值标签 |

**未实现的项不隐藏**：`implemented=False` → 控件 `setEnabled(False)` +
标题追加 `（暂未开放）`，位置照常占着（task11 §5：布局不许跳）。

## 三条"绝不显示假成功"的落点

1. **写失败回滚控件**：`_write_field()` 拿到 `ConfigStore.apply()` 的 `SetResult`，
   `ok` 为假就把控件值改回 `store.get(key)`（**磁盘上的真值**）。
2. **回滚不许再触发一次写入**：回滚会改控件 → 控件又会发 `valueChanged`
   → 又去写配置 → 又失败……的死循环。判据是 `_applying`（守卫标志）：
   **凡是程序自己动控件，一律置真**，槽函数见到它就返回。
   （agent.md §4.9 的"信号回路"就是这一条；task11 §4 明写"必须屏蔽信号/加守卫"。）
3. **状态栏先看 `error`**：渲染时 `error` 存在就显示失败原因，
   **不在拼字符串的时候决定**（那样迟早拼出一个"成功"的假象）。
"""

import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Final

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import keycapture, pet_loader
from .config import ConfigStore, SetResult, VALIDATION_ERROR
from .idle_state import normalize_idle_seconds
from .pet_spec import DEFAULT_PET_NAMESPACE
from .sounds import VOLUME_MAX, VOLUME_MIN

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 文字真值

#: 窗口标题（附录 B）。
TITLE: Final[str] = "桌宠设置"

#: 分组标题与**顺序**（附录 B）：常规 / 声音 / 行为。
GROUP_ORDER: Final[tuple[str, ...]] = ("常规", "声音", "行为")

#: 未实现的项在标题后面追加的说法（task11 §2：**不是**隐藏）。
NOT_READY_SUFFIX: Final[str] = "（暂未开放）"

#: 左下角逃生门按钮（附录 B）。本 task 只要求"能打开那个文件夹"。
TEXT_OPEN_STARTUP: Final[str] = "打开启动文件夹"

#: 状态栏前缀（附录 B）：`开机自启：`。本 task 这一行未实现，恒为 `未开启`。
STATUS_PREFIX: Final[str] = "开机自启："

#: 本 task 阶段那一行的状态文字（真值在 task14 的 `autostart`，这里不猜）。
STATUS_NOT_READY: Final[str] = "未开启"

#: 写失败时状态栏的说法（task11 §6 逐字要求）。
FAILED_PREFIX: Final[str] = "设置失败："

#: 窗口尺寸（task11 §4）：560 × 516 **逻辑单位**。
#: "够不够高"在 Qt 下不靠调这个数字解决，见 `_build_body()` 的 `QScrollArea`。
WINDOW_WIDTH: Final[int] = 560
WINDOW_HEIGHT: Final[int] = 516

#: 垂直位置：约屏幕高度的 1/3 处（task11 §2 第 1 条）。
VERTICAL_RATIO: Final[float] = 1.0 / 3.0

#: 角色下拉里"当前生效"的前缀与坏包的标记（附录 B）。
CURRENT_PREFIX: Final[str] = "● "
BROKEN_SUFFIX: Final[str] = "　⚠ 不可用"

#: 界面上的长文字（**取自 task11 §2 的表格，逐字**）。
HINT_PET: Final[str] = "换一个形象；切换需要重启桌宠（会自动问你要不要现在重启）"
HINT_AUTOSTART: Final[str] = "登录 Windows 后自动启动桌宠"
HINT_HOTKEY: Final[str] = "隐藏后按此键呼出；点右边按钮后按一次想要的组合"
HINT_VOLUME: Final[str] = "点击互动与坐下时的音效音量"
HINT_IDLE: Final[str] = "鼠标静止这么久后开始张望，再过一半时间坐下"

#: 角色行的临时提示：自动重启属 task13（task11 §5 明确定了这条边界）。
PET_RESTART_HINT: Final[str] = "重启后生效"


class FieldKind(Enum):
    """控件的**种类**（界面按它造控件，不按 key 硬编码）。"""

    COMBO = "combo"
    CHECK = "check"
    KEYSEQ = "keyseq"
    SLIDER = "slider"


@dataclass(frozen=True)
class FieldSpec:
    """一行设置的描述（**界面的唯一真相源**）。

    | 列 | 语义 |
    | --- | --- |
    | `group` | 属于哪一组（必须是 `GROUP_ORDER` 里的一个） |
    | `key` | 配置键；**空串 = 这一行没有配置项**（`开机自启` 尚未实现） |
    | `title` | 行标题（未实现时界面会追加 `（暂未开放）`） |
    | `hint` | 标题下面那句说明 |
    | `kind` | 用什么控件 |
    | `minimum` / `maximum` | 滑块量程（其它控件不用） |
    | `unit` | 数值后面的单位；`音量` 是**空串**，`久置时间` 是 ` 秒` |
    | `implemented` | 本 task 接没接上；假 → 控件禁用 + 标题加后缀 |
    """

    group: str
    key: str
    title: str
    hint: str
    kind: FieldKind
    minimum: int = 0
    maximum: int = 0
    unit: str = ""
    implemented: bool = True


#: 五行设置，**顺序即界面顺序**（task11 §4）。
FIELDS: Final[tuple[FieldSpec, ...]] = (
    FieldSpec(
        group="常规",
        key="pet",
        title="角色",
        hint=HINT_PET,
        kind=FieldKind.COMBO,
    ),
    FieldSpec(
        group="常规",
        # **没有配置键**：开机自启是系统状态（task14 的 `autostart`），
        # 不是 `config.json` 里的一项。写一个假键进去才是真的会分叉。
        key="",
        title="开机自启",
        hint=HINT_AUTOSTART,
        kind=FieldKind.CHECK,
        implemented=False,
    ),
    FieldSpec(
        group="常规",
        key="hotkey",
        title="呼出快捷键",
        hint=HINT_HOTKEY,
        kind=FieldKind.KEYSEQ,
        implemented=False,
    ),
    FieldSpec(
        group="声音",
        key="volume",
        title="音量",
        hint=HINT_VOLUME,
        kind=FieldKind.SLIDER,
        minimum=VOLUME_MIN,
        maximum=VOLUME_MAX,
        unit="",
    ),
    FieldSpec(
        group="行为",
        key="idle_seconds",
        title="久置时间",
        hint=HINT_IDLE,
        kind=FieldKind.SLIDER,
        minimum=5,
        maximum=300,
        unit=" 秒",
    ),
)


# ------------------------------------------------------------------ 角色候选


@dataclass(frozen=True)
class PetChoice:
    """角色下拉里的一项。

    | 字段 | 语义 |
    | --- | --- |
    | `namespace` | 命名空间 |
    | `display_name` | 展示名（索引坏了就退回命名空间） |
    | `ok` | 这个包**能不能用**（坏包照样列出来，只是带标记） |
    | `issues` | 不能用时的原因（换角色失败要"提示详情"，task13 §4） |
    """

    namespace: str
    display_name: str
    ok: bool
    issues: tuple[str, ...] = ()

    def label(self, current: bool) -> str:
        """下拉里的文字：当前项带 `● `，坏包带 `　⚠ 不可用`（附录 B）。"""
        text = f"{self.display_name}（{self.namespace}）"
        if not self.ok:
            text += BROKEN_SUFFIX
        return f"{CURRENT_PREFIX}{text}" if current else text


def pet_choices(current: str = "") -> list[PetChoice]:
    """列出**所有发现的包**（坏的也列，task11 §2 第 10 条）。

    排序：**当前生效的那个排最前面**（用户打开窗口第一眼要看到自己在用谁），
    其余按 `list_reports()` 的既有顺序（用户包优先、名字折叠排序）。

    `current` 指"配置里真正生效的命名空间"（调用方给），不是"界面选中项"。
    """
    reports = pet_loader.list_reports()
    choices = [
        PetChoice(
            namespace=report.namespace,
            display_name=report.display_name,
            ok=report.ok,
            issues=tuple(str(issue) for issue in report.issues),
        )
        for report in reports
    ]

    # 配置指向的包**不在发现列表里**（目录被删了）→ 补一项，避免下拉
    # "没有当前项"导致 setCurrentIndex(-1) 让用户以为程序没在跑任何形象。
    if current and not any(choice.namespace == current for choice in choices):
        choices.append(
            PetChoice(
                namespace=current,
                display_name=current,
                ok=False,
                issues=("这个素材包现在找不到了（目录被删或改名）",),
            )
        )

    wanted = _fold(current)
    choices.sort(key=lambda choice: (0 if _fold(choice.namespace) == wanted else 1,))
    return choices


def _fold(text: str) -> str:
    """比较用的折叠形式（Windows 文件名大小写不敏感）。"""
    from . import nsrules

    return nsrules.fold_for_compare(str(text or ""))


# ------------------------------------------------------------------ 窗口


class SettingsWindow(QDialog):
    """设置窗口（模态；**同一时间只允许一个**，由调用方保证——见 `app.py`）。

    | 信号 | 含义 |
    | --- | --- |
    | `settings_changed(key, value)` | 用户改了一项设置（且**真的写进去了**） |

    ⚠ 信号只在写入**成功**时发出：失败时控件会被回滚，那一刻的"值"根本不是
    用户想要的那个，发出去只会让下游按错误的值行动（task11 §4）。
    """

    settings_changed = Signal(str, object)

    def __init__(
        self,
        store: ConfigStore,
        current_pet: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._current_pet = str(current_pet or "")
        #: **守卫标志**：程序自己在动控件（初值填充 / 失败回滚）时为真，
        #: 槽函数见到就返回。**没有它，回滚会再触发一次写入**（死循环）。
        self._applying = False
        #: 角色下拉里的候选（列表与下拉项一一对应，按下标取）。
        self._choices: list[PetChoice] = []
        #: 每一行造出来的控件（key → widget），刷新与回滚都按 key 找。
        self._widgets: dict[str, QWidget] = {}
        #: 每行滑块的数值标签（`_build_slider` 里填）。
        self._value_labels: dict[str, QLabel] = {}
        #: 最近一次失败的原因；**空串 = 没有失败**。状态栏渲染时先看它。
        self._last_failure: str = ""

        self.setWindowTitle(TITLE)
        self.setModal(True)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_WIDTH, WINDOW_HEIGHT)

        self._build_body()
        self._load_from_config()
        self.center_on_screen()

    # ------------------------------------------------------------ 装配

    def _build_body(self) -> None:
        """三组五行 + 状态栏 + 左下角逃生门。

        下半部分（状态栏 / 按钮行）**固定**，上半部分（五行）装在
        `QScrollArea` 里——窗口被拉小时滚动条出现，**按钮行仍点得到**
        （task11 §4：不要靠"把窗口调高"来解决截断）。
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        for group in GROUP_ORDER:
            body_layout.addWidget(self._build_group(group))
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 状态栏（附录 B 的前缀 + 失败原因优先）。
        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        outer.addWidget(self._status_label)

        # 底部按钮行：逃生门在**左下角**（task11 §2 第 8 条），关闭在右边。
        buttons = QDialogButtonBox()
        self._startup_button = QPushButton(TEXT_OPEN_STARTUP)
        self._startup_button.clicked.connect(self.open_startup_folder)
        buttons.addButton(self._startup_button, QDialogButtonBox.ActionRole)

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.close)
        buttons.addButton(close_button, QDialogButtonBox.RejectRole)
        outer.addWidget(buttons)

    def _build_group(self, group: str) -> QGroupBox:
        """一组：`QGroupBox` + 组内 `QFormLayout`（task11 §4，不用绝对坐标）。"""
        box = QGroupBox(group)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)

        for field in FIELDS:
            if field.group != group:
                continue
            form.addRow(self._build_row_label(field), self._build_control(field))
        return box

    def _build_row_label(self, field: FieldSpec) -> QWidget:
        """行标题 + 提示文字（未实现的项在标题后追加 `（暂未开放）`）。"""
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        title = field.title if field.implemented else f"{field.title}{NOT_READY_SUFFIX}"
        title_label = QLabel(title)
        layout.addWidget(title_label)

        hint = QLabel(field.hint)
        hint.setWordWrap(True)
        hint.setEnabled(False)  # 提示用更浅的颜色，不去抢注意力
        layout.addWidget(hint)
        return container

    def _build_control(self, field: FieldSpec) -> QWidget:
        """按 `FieldKind` 造控件（**唯一的按种类分派处**）。"""
        if field.kind is FieldKind.COMBO:
            widget: QWidget = QComboBox()
            widget.currentIndexChanged.connect(lambda _index: self._on_combo(field))
        elif field.kind is FieldKind.CHECK:
            from PySide6.QtWidgets import QCheckBox

            widget = QCheckBox()
            widget.toggled.connect(lambda checked: self._on_check(field, checked))
        elif field.kind is FieldKind.KEYSEQ:
            widget = keycapture.KeyCaptureEdit()
            widget.captured.connect(lambda text: self._on_keyseq(field, text))
        else:
            widget = self._build_slider(field)

        if not field.implemented:
            # **禁用而不是隐藏**（task11 §5）：位置照常占着，布局不跳。
            widget.setEnabled(False)
        if field.key:
            self._widgets[field.key] = widget
        return widget

    def _build_slider(self, field: FieldSpec) -> QWidget:
        """滑块 + 数值标签（`量程 + 单位`由描述表给）。"""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(field.minimum, field.maximum)
        slider.setSingleStep(1)
        # `valueChanged` 在拖拽期间连续触发，正是"拖一下立刻听到音量变化"
        # 要的"改完立即生效"（task11 §2 第 3 条）。**一个槽干两件事**：
        # 刷新数值标签 + 写配置（写成两个 connect 会让"谁先谁后"变得要紧）。
        slider.valueChanged.connect(lambda value: self._on_slider(field, value))
        layout.addWidget(slider, 1)

        value_label = QLabel()
        value_label.setMinimumWidth(56)
        layout.addWidget(value_label)

        self._value_labels[field.key] = value_label
        return container

    # ------------------------------------------------------------ 读配置

    def _load_from_config(self) -> None:
        """把配置里的当前值摆到控件上（**只读**：打开窗口不写盘，§3.13）。

        整个过程包在 `_applying` 里：填充控件会触发 `valueChanged`，
        没有守卫的话"打开设置窗口"就等于"把当前值重新写一遍配置"。
        """
        self._applying = True
        try:
            self._fill_pet_combo()
            for field in FIELDS:
                if not field.key or field.key == "pet":
                    continue
                widget = self._widgets.get(field.key)
                if widget is None:
                    continue
                value = self._store.get(field.key)
                if field.kind is FieldKind.SLIDER:
                    slider = self._find_slider(widget)
                    if slider is not None:
                        slider.setValue(int(value))
                        self._update_value_label(field, int(value))
                elif field.kind is FieldKind.KEYSEQ:
                    widget.set_shortcut_text(str(value))  # type: ignore[attr-defined]
        finally:
            self._applying = False
        self.refresh_status()

    def reload_from_config(self, current_pet: str | None = None) -> None:
        """重新读一遍配置（**重复点 `设置…` 时用**：窗口可能停在旧值上）。

        `current_pet` 是调用方刚查出来的"配置里真正生效的角色"：角色可能
        在别处被改过（task13 会把坏包回写默认形象），界面得跟着走。
        传 `None` 表示"不改这个认知"，沿用打开时的那个。
        """
        if current_pet is not None:
            self._current_pet = str(current_pet)
        # 重开一次不该继续显示上一次的失败（那是**上一次操作**的结论）。
        self._last_failure = ""
        self._load_from_config()

    def _fill_pet_combo(self) -> None:
        """填角色下拉：**所有**发现的包都列出来（坏的也列）。"""
        combo = self._widgets.get("pet")
        if not isinstance(combo, QComboBox):
            return
        self._choices = pet_choices(self._current_pet)
        combo.clear()
        for choice in self._choices:
            current = _fold(choice.namespace) == _fold(self._current_pet)
            combo.addItem(choice.label(current))
        index = next(
            (
                position
                for position, choice in enumerate(self._choices)
                if _fold(choice.namespace) == _fold(self._current_pet)
            ),
            0,
        )
        combo.setCurrentIndex(index)
        self._sync_pet_tooltip()

    # ------------------------------------------------------------ 状态栏

    def refresh_status(self) -> None:
        """刷新状态栏：**`error` 存在时失败原因优先于状态本身**（task11 §4）。

        所以这里是"先看有没有失败、再看普通状态"两段，不是把两段拼起来。
        """
        if self._last_failure:
            self._status_label.setText(f"{FAILED_PREFIX}{self._last_failure}")
            return
        self._status_label.setText(f"{STATUS_PREFIX}{STATUS_NOT_READY}")

    def _set_failure(self, result: SetResult) -> None:
        """记一次失败并刷新状态栏（**只在真的失败时调用**）。"""
        self._last_failure = result.error or "写配置失败"
        self.refresh_status()

    def _clear_failure(self) -> None:
        if self._last_failure:
            self._last_failure = ""
            self.refresh_status()

    # ------------------------------------------------------------ 写入

    def _write_field(self, field: FieldSpec, value: object) -> bool:
        """写一项配置；失败则**回滚控件** + 状态栏带出系统原文。

        返回"到底写进去了没有"——调用方据此决定要不要发 `settings_changed`
        与显示"重启后生效"这类提示（**失败时一个字都不许说成功**）。
        """
        if not field.key:
            return False
        result = self._store.apply(field.key, value)
        if result.ok:
            self._clear_failure()
            self.settings_changed.emit(field.key, value)
            logger.info("设置：%s → %r", field.key, value)
            return True

        logger.error("设置：写 %s 失败：%s", field.key, result.error)
        self._rollback(field)
        self._set_failure(result)
        return False

    def _rollback(self, field: FieldSpec) -> None:
        """把控件恢复到**配置里的真值**（不是"改之前那个值"）。

        用配置当真值来源的理由：界面可能落后于配置（例如另一条路径改过它），
        "回滚到打开窗口时的快照"会把两者永久拉开（task11 §4"回滚到真实状态"）。
        """
        widget = self._widgets.get(field.key)
        if widget is None:
            return
        value = self._store.get(field.key)
        self._applying = True
        try:
            if field.kind is FieldKind.SLIDER:
                slider = self._find_slider(widget)
                if slider is not None:
                    slider.setValue(int(value))
                    self._update_value_label(field, int(value))
            elif field.kind is FieldKind.KEYSEQ:
                widget.set_shortcut_text(str(value))  # type: ignore[attr-defined]
            elif field.kind is FieldKind.CHECK:
                widget.setChecked(bool(value))  # type: ignore[attr-defined]
        finally:
            self._applying = False

    # ------------------------------------------------------------ 槽

    def _on_slider(self, field: FieldSpec, value: int) -> None:
        """滑块动了：立刻写配置（**改完立即生效**）。

        `valueChanged` 在拖拽期间连续触发，于是每一格都会走一次
        `ConfigStore.set()` → 一次原子写盘。这是"拖一下立刻听到变化"的代价，
        可以接受（§4.6 那条"不逐帧写"说的是**拖动窗口**那条路径）。
        """
        if self._applying:
            return
        self._update_value_label(field, value)
        self._write_field(field, int(value))

    def _on_check(self, field: FieldSpec, checked: bool) -> None:
        """勾选框动了（本 task 该控件是禁用的，接上后走这里）。"""
        if self._applying:
            return
        self._write_field(field, bool(checked))

    def _on_keyseq(self, field: FieldSpec, text: str) -> None:
        """录到一个快捷键（本 task 该控件是禁用的，task12 接上后走这里）。"""
        if self._applying:
            return
        self._write_field(field, str(text))

    def _on_combo(self, field: FieldSpec) -> None:
        """角色下拉换了：写 `pet`，坏包则退回默认形象 + 提示详情。"""
        if self._applying:
            return
        combo = self._widgets.get(field.key)
        if not isinstance(combo, QComboBox):
            return
        index = combo.currentIndex()
        if not 0 <= index < len(self._choices):
            return
        choice = self._choices[index]
        self._select_pet(field, choice)

    def _select_pet(self, field: FieldSpec, choice: PetChoice) -> None:
        """选中一个角色。

        ## 两条路径（task11 §6 / §5 的边界）

        | 情形 | 本 task 的行为 | 留给 task13 的 |
        | --- | --- | --- |
        | **可用**包 | 写配置 + 提示 `重启后生效` | 询问"现在自动重启吗？" |
        | **坏包** | 不改配置（见下）+ 提示详情 + 界面回退到真实角色 | 把配置改回默认形象的完整流程 |

        **坏包为什么不改配置**：`agent.md` §2.5 写死了"动作/素材有几个就是几个，
        不许硬塞替身"，而角色这一项的真值只有"配置里那个字符串"。把一个**已知
        不可用**的命名空间写进去，等于让下次启动必然走一遍"回退默认形象"的
        警告路径——那是**故意的破坏**，不是用户的选择。配置默认值是空串
        （"用户还没表态"），所以"继续用默认形象"本来就不需要写任何东西。
        """
        if choice.ok:
            if self._write_field(field, choice.namespace):
                self._set_status_text(
                    f"已切换到「{choice.display_name}」（{choice.namespace}）；"
                    f"{PET_RESTART_HINT}"
                )
                self._current_pet = choice.namespace
                self._refresh_pet_combo_current()
            return

        self._report_broken_pet(choice)

    def _report_broken_pet(self, choice: PetChoice) -> None:
        """坏包：把详情说出来，并把界面**退回真实状态**。"""
        detail = "\n".join(f"· {issue}" for issue in choice.issues) or "· （没有拿到原因）"
        message = (
            f"你的选择没被接受，「{choice.display_name}」（{choice.namespace}）不可用，"
            f"已回到默认形象 {DEFAULT_PET_NAMESPACE}。\n"
            f"「{choice.namespace}」的问题：\n{detail}"
        )
        logger.warning("设置：角色 %s 不可用，已回到默认形象", choice.namespace)
        self._last_failure = message
        self.refresh_status()
        # 界面必须与"真实生效的角色"一致：配置没动过，所以当前仍是原值。
        self._applying = True
        try:
            self._fill_pet_combo()
        finally:
            self._applying = False

    def _refresh_pet_combo_current(self) -> None:
        """写成功后把 `● ` 挪到新的当前项上。"""
        self._applying = True
        try:
            self._fill_pet_combo()
        finally:
            self._applying = False

    def _set_status_text(self, text: str) -> None:
        """显示一句**成功**提示（失败提示走 `_set_failure`）。"""
        self._last_failure = ""
        self._status_label.setText(text)

    # ------------------------------------------------------------ 逃生门

    def open_startup_folder(self) -> None:
        """打开 Windows 的「启动」文件夹（`shell:startup`）。

        本 task **只要求"能打开"**：真正的路径解析与自启项读写是 task14 的
        `autostart` 模块（`shell:startup` 的解析真值在那边）。这里直接用
        shell 的说法让系统自己打开，**不自己拼 `%APPDATA%`**——否则本项目
        会出现第二份"启动文件夹在哪"的推导，而那正是 task14 要定死的东西。

        打不开**只记日志**：这是个逃生门，失败不该阻断设置窗口
        （agent.md §2.10 第三类）。
        """
        opened = QDesktopServices.openUrl(QUrl("shell:startup"))
        if not opened:
            logger.warning("打开启动文件夹失败（shell:startup）")

    # ------------------------------------------------------------ 几何

    def center_on_screen(self) -> None:
        """水平居中、垂直约 1/3 处（task11 §2 第 1 条）。

        用 `availableGeometry()`（排除任务栏）：设置窗口是个普通对话框，
        压到任务栏下面会让"关闭"按钮点不到。垂直位置取"屏幕高度的 1/3 处"
        作为窗口**中心**的落点，再夹进屏幕——一个 516 高的窗口若从 1/3 处
        往下长，在小屏上会顶到屏幕外，夹取保证它永远完整可见。
        """
        screen = self.screen()
        if screen is None:  # pragma: no cover - 拿不到屏幕时不硬摆
            return
        area = screen.availableGeometry()
        width = self.width()
        height = self.height()
        left = area.x() + (area.width() - width) // 2
        top = area.y() + int(area.height() * VERTICAL_RATIO) - height // 2
        top = max(area.y(), min(area.y() + area.height() - height, top))
        self.move(max(area.x(), left), top)

    # ------------------------------------------------------------ 内部工具

    def _find_slider(self, container: QWidget) -> QSlider | None:
        return container.findChild(QSlider)

    def _update_value_label(self, field: FieldSpec, value: int) -> None:
        label = self._value_labels.get(field.key)
        if label is not None:
            label.setText(f"{int(value)}{field.unit}")

    def _sync_pet_tooltip(self) -> None:
        """把当前角色的完整命名空间放进提示条（按钮宽度放不下长名字）。"""
        combo = self._widgets.get("pet")
        if isinstance(combo, QComboBox):
            combo.setToolTip(f"当前：{self._current_pet or '（默认形象）'}")
