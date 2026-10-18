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
from typing import Callable, Final, NamedTuple

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
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import autostart, keycapture, keygrid, pet_loader
from .config import ConfigStore, SetResult, VALIDATION_ERROR
from .idle_state import normalize_idle_seconds
from .keyview_spec import (
    KeyBinding,
    KeyViewSpec,
    bindings_for_layout,
    bindings_to_config,
    normalize_bindings,
)
from .pet_spec import DEFAULT_PET_NAMESPACE
from .sounds import VOLUME_MAX, VOLUME_MIN

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 文字真值

#: 窗口标题（附录 B）。
TITLE: Final[str] = "桌宠设置"

#: 分组标题与**顺序**（附录 B）：常规 / 声音 / 行为 / 键盘。
#: ⚠ `键盘`（task19）摆在 `行为` **之后**（task19 §4.1 明写的位置）。
GROUP_ORDER: Final[tuple[str, ...]] = ("常规", "声音", "行为", "键盘")

#: 未实现的项在标题后面追加的说法（task11 §2：**不是**隐藏）。
NOT_READY_SUFFIX: Final[str] = "（暂未开放）"

#: 左下角逃生门按钮（附录 B）。本 task 只要求"能打开那个文件夹"。
TEXT_OPEN_STARTUP: Final[str] = "打开启动文件夹"

#: 状态栏前缀（附录 B）：`开机自启：`。**真值在 `autostart.STATUS_PREFIX`**，
#: 这里直接引用它（§4.1：同一个真值只允许定义一处）。
STATUS_PREFIX: Final[str] = autostart.STATUS_PREFIX

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

#: `键盘` 那两行的提示（task19 §1.1）。
#: ⚠ 键位那一条必须说清"**配一次、所有布局通用**"——用户看到 4 键与 8 键两套布局时
#: 最先冒出来的问题就是"要不要各配一套"，答案是不用（键 id 跨布局通用，§2.23）。
HINT_KEYVIEW_LAYOUT: Final[str] = "键盘形态的键位排布；只列出当前素材包里真的有的"
HINT_KEYVIEW_KEYS: Final[str] = (
    "点一个键位，再按键盘上的键即可绑定；配一次所有布局通用"
)

#: `边缘抗锯齿` 那一行的提示（task15 §4 的逐字要求）。
#: ⚠ **必须如实写清"收益 + 代价"**，不许只写好处：算法**不保证**对所有素材
#: 都更好看（用户将来会导入自己的素材包），各档位收益也不一致。
#: 所以这里明写"部分素材/档位可能变差"，并注明**重启后生效**。
HINT_ANTIALIAS: Final[str] = (
    "浅色背景下边缘更柔和；部分素材/档位可能变差，可随时关掉（重启后生效）"
)

#: 角色行提示（task11 那条边界的原文）：**不同意重启**时显示它。
PET_RESTART_HINT: Final[str] = "重启后生效"

#: `开机自启` 那一行**没有配置键**（它是系统状态）。界面按这个标记认它。
#: ⚠ 与 `FieldSpec.key=""` 配套：所有"这一行是不是自启"的判断都走这个常量，
#: 不许在界面代码里散落 `field.title == "开机自启"` 这类文字比较。
AUTOSTART_KEY: Final[str] = "@autostart"

#: 同意重启之后要说的话（task13 §4："同意才重启"）。
PET_RESTARTING_HINT: Final[str] = "正在重启…"

#: 询问"现在自动重启吗？"的标题与正文（task13 §4）。
RESTART_ASK_TITLE: Final[str] = "换角色"
RESTART_ASK_TEXT: Final[str] = "角色已经切换，需要重启桌宠才会生效。\n现在自动重启吗？"

#: `边缘抗锯齿` 的询问框标题与正文（task15 §4）。
#:
#: ⚠ **本 task 的诚实性落点**：正文里**必须**有"不保证对所有素材、所有档位
#: 都更好看"这个意思。理由有两条：
#:   ① 用户**将来会导入自己的素材包**（`data/pets/`），算法不保证对它们成立；
#:   ② 各档位收益本来就不一致（1–5 档是缩小，没有硬台阶可消，收益小甚至无）。
#: **不许**写成"边缘更平滑"这类**无条件的承诺**（§4.2：不要写没验证过的承诺）。
#: 把判断权交给用户 = 让他**看了再决定**，比替他保证可靠得多。
RESTART_ASK_ANTIALIAS_TITLE: Final[str] = "边缘抗锯齿"
RESTART_ASK_ANTIALIAS_TEXT: Final[str] = (
    "已切换到「{state}」。\n\n"
    "抗锯齿对多数素材能减轻边缘锯齿，但不保证对所有素材、所有档位都更好看"
    "——某些素材或档位可能反而变差。请重启后自己看一眼，不满意随时改回来。\n\n"
    "现在重启桌宠吗？"
)

#: `边缘抗锯齿` 那两行的显示说法（`{state}` 的取值）。
ANTIALIAS_ON_TEXT: Final[str] = "开"
ANTIALIAS_OFF_TEXT: Final[str] = "关"

#: 配置写失败时的提示（task13 §4 逐字要求）。
PET_SAVE_FAILED_HINT: Final[str] = "保存失败，重启后会回到原来的角色"

#: 坏包被拒时的标题（正文见 `_report_broken_pet()`）。
BROKEN_PET_TITLE: Final[str] = "换角色失败"

# ---------------------------------------------------------------- 键盘（task19）

#: 换布局成功 / 重置成功的状态栏提示。**不写"重启后生效"**——这两项是**即时生效**的
#: （task19 §4.5），写那句话会把已经生效的事情说成还没生效。
KEYVIEW_LAYOUT_APPLIED: Final[str] = "键位布局已切换到「{layout}」"
KEYVIEW_KEYS_RESET: Final[str] = "键位映射已清空"
KEYVIEW_KEY_BOUND: Final[str] = "键位 {key_id} 已绑定为 {name}"

#: 录到一个键、但**没能写进配置**时的说法（写失败回滚那一条）。
KEYVIEW_SAVE_FAILED: Final[str] = "键位没能保存"

#: 重置**没能写进配置**时的说法。
KEYVIEW_RESET_FAILED: Final[str] = "键位没能清空"


class FieldKind(Enum):
    """控件的**种类**（界面按它造控件，不按 key 硬编码）。

    | 取值 | 用在 | 控件 |
    | --- | --- | --- |
    | `COMBO` | `角色` / `键位布局` | 下拉 |
    | `CHECK` | `开机自启` / `边缘抗锯齿` | 勾选框 |
    | `KEYSEQ` | `呼出快捷键` | `keycapture.KeyCaptureEdit`（**组合**） |
    | `SLIDER` | `音量` / `久置时间` | 滑块 + 数值标签 |
    | `KEYGRID` | `键位映射`（task19） | `keygrid.KeyGrid`（整组，不是一行） |

    ⚠ 键位网格**必须**是新的一种，不许塞进 `COMBO` 分支里特判：
    那会破坏"按种类分派"这条纪律（task19 §4.1 明写）。
    布局下拉**可以**照 `pet` 的先例在 `COMBO` 分支里特判——它**就是**一个下拉。
    """

    COMBO = "combo"
    CHECK = "check"
    KEYSEQ = "keyseq"
    SLIDER = "slider"
    KEYGRID = "keygrid"


class HotkeyOutcome(NamedTuple):
    """一次"换呼出快捷键"的结果（task12）。

    设置窗口**不碰 `RegisterHotKey`**：它只问一句"这个组合能不能用"。
    真正的注册在 `app.py` 那层（涉及 Win32 热键、候选链、失败回滚与
    运行时状态），界面只管显示结论——分层与本项目其他部分一致。

    | 字段 | 语义 |
    | --- | --- |
    | `ok` | 这个组合**真的成了**（系统热键已注册上） |
    | `combo` | 最终生效的组合；失败时是 `None` |
    | `reason` | 失败原因（**系统原文优先**，成功时空串） |
    """

    ok: bool
    combo: str | None = None
    reason: str = ""


#: 换呼出快捷键的函数签名：`(新说法, 旧说法) -> HotkeyOutcome`。
HotkeyChanger = Callable[[str, str], HotkeyOutcome]


@dataclass(frozen=True)
class RestartOutcome:
    """一次"重启自己"的结果（task13）。

    | 字段 | 语义 |
    | --- | --- |
    | `started` | 新实例**真的起来了**（旧实例该退出了） |
    | `reason` | 失败原因（**系统原文优先**，成功时空串） |

    界面据 `started` 决定"是说一句正在重启、还是弹失败提示"——
    **绝不显示假成功**（task11 立下的规矩在 task13 同样适用）。
    """

    started: bool
    reason: str = ""


#: 重启自己的函数签名：`() -> RestartOutcome`。由 `app.py` 装上。
RestartHandler = Callable[[], RestartOutcome]

#: 询问"要不要现在重启"的函数签名：`(标题, 正文) -> 用户是否同意`。
#: 由 `app.py` 装上（它才知道弹窗与宠物窗口的父子关系）。
ConfirmHandler = Callable[[str, str], bool]


@dataclass(frozen=True)
class AutostartOutcome:
    """一次"开 / 关开机自启"的结果（task14）。

    界面**不碰启动文件夹**：它只问一句"帮我开/关一下"，真正的
    快捷方式生成、`.cmd` 回退、删除都在 `autostart` 模块里。

    | 字段 | 语义 |
    | --- | --- |
    | `ok` | 这次操作**真的成了**（成功或幂等成功） |
    | `enabled` | 操作之后自启项**到底能不能用**（以只读查询为准） |
    | `status_text` | 状态栏要显示的那段文字（`autostart` 给的固定文案） |
    | `reason` | 失败原因（**系统原文优先**，成功时空串） |

    ⚠ `ok` 与 `enabled` 是**两件事**：`disable()` 幂等成功时 `ok=True`
    而 `enabled=False`（"本来就没有"也是一种成功）。
    """

    ok: bool
    enabled: bool
    status_text: str = ""
    reason: str = ""


#: 开关开机自启的函数签名：`(要开启吗) -> AutostartOutcome`。由 `app.py` 装上。
AutostartHandler = Callable[[bool], AutostartOutcome]


@dataclass(frozen=True)
class KeyviewOutcome:
    """一次"换键盘布局"的结果（task19 §4.5）。

    界面**不直接持有宠物窗口**（"不许在界面里直接持有宠物窗口/监听器"，
    task19 §5）：它只把"换到哪个布局"交给 `app.py` 装的处理器，
    处理器负责 content 换布局 + 重绘一次。

    | 字段 | 语义 |
    | --- | --- |
    | `ok` | 这次换布局**真的成了**（画面已经是新布局） |
    | `layout` | 最终生效的布局名（失败时是空串） |
    | `reason` | 失败原因（成功时空串） |

    ⚠ 与"写配置"**是两件事**：写配置失败由 `_write_field()` 那条路径管
    （回滚 + 状态栏带系统原文），这里只回答"画面换没换过去"。
    """

    ok: bool
    layout: str = ""
    reason: str = ""


#: 换键盘布局的函数签名：`(布局名) -> KeyviewOutcome`。由 `app.py` 装上。
KeyviewChanger = Callable[[str], KeyviewOutcome]


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
        # **没有配置键**：开机自启是系统状态（`autostart` 模块），
        # 不是 `config.json` 里的一项。写一个假键进去才是真的会分叉。
        # task14 接线：这一行解锁，勾选/取消直接读写系统的自启项。
        key="",
        title="开机自启",
        hint=HINT_AUTOSTART,
        kind=FieldKind.CHECK,
        implemented=True,
    ),
    FieldSpec(
        group="常规",
        key="hotkey",
        title="呼出快捷键",
        hint=HINT_HOTKEY,
        kind=FieldKind.KEYSEQ,
        # task12 接线：这一行解锁（不再带 `（暂未开放）`）。
        implemented=True,
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
    FieldSpec(
        group="行为",
        # 勾选后**写配置 → 问要不要重启**（照 `换角色` 那一套，task15 §4）。
        # 生效方式是**重启生效**：不在这里失效缓存、不重绘
        # （那正是本决策要避免的复杂度，见 task15 §5）。
        key="antialias",
        title="边缘抗锯齿",
        hint=HINT_ANTIALIAS,
        kind=FieldKind.CHECK,
        implemented=True,
    ),
    # ---------------------------------------------------------------- 键盘（task19）
    # ⚠ 这两行**都是即时生效**（不重启）：`keyview_layout` 只是换一张图，
    # `keyview_keys` 连图都不换（映射不进图像）。与"抗锯齿/换角色要重启"不矛盾
    # ——那两项改动代价大（重建滤镜 / 整个素材），这两项只是换一张图（task19 §4.5）。
    FieldSpec(
        group="键盘",
        key="keyview_layout",
        title="键位布局",
        hint=HINT_KEYVIEW_LAYOUT,
        kind=FieldKind.COMBO,
    ),
    FieldSpec(
        group="键盘",
        key="keyview_keys",
        title="键位映射",
        # ⚠ 这一行**不是一个控件**：`KEYGRID` 造出来的是整块网格 + 重置按钮，
        # 它自己就带着标题与提示（见 `keygrid.KeyGrid`）。
        hint=HINT_KEYVIEW_KEYS,
        kind=FieldKind.KEYGRID,
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
        keyview: KeyViewSpec | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store
        self._current_pet = str(current_pet or "")
        #: 当前素材包的键盘形态（task19）。**`None` = 这个包没有键盘形态**
        #: → 整组禁用 + 一句说明（task19 §4.2）。
        #:
        #: ⚠ 它是**构造时快照**：换角色要重启（task11/13 已定），所以一个窗口
        #: 存活期间素材包不会换——与"档位/镜像启动时读一次"同一条规矩。
        self._keyview: KeyViewSpec | None = keyview
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
        #: 最近一次**成功**提示（`重启后生效` / `正在重启…` 这类）；空串 = 没有。
        #: 记字段而不是直接写控件，理由见 `refresh_status()`（task15 修的 bug）。
        self._last_message: str = ""
        #: `开机自启` 那一行旁边的状态文字（`_build_control` 里填）。
        self._autostart_label: QLabel | None = None
        #: 最近一次**只读查询**得到的自启状态（`autostart.AutostartState`）。
        #: `None` = 还没查过。状态栏渲染用它（**不重新查**：渲染不是查询）。
        self._autostart_state: autostart.AutostartState | None = None
        #: 换呼出快捷键的处理器（task12，由 `app.py` 装）。
        #: `None` = 自检里那种"没有系统热键"的场景 → 只写配置。
        self._hotkey_changer: HotkeyChanger | None = None
        #: 重启自己的处理器（task13，由 `app.py` 装）。
        #: `None` = 自检里那种"不真重启"的场景 → 只写配置 + 提示"重启后生效"。
        self._restart_handler: RestartHandler | None = None
        #: 询问"要不要现在重启"（task13，由 `app.py` 装）。
        #: `None` = 不问，直接按"不同意"处理（**默认不重启**才安全）。
        self._confirm_handler: ConfirmHandler | None = None
        #: 开关开机自启的处理器（task14，由 `app.py` 装）。
        #: `None` = 自检里那种"不真动启动文件夹"的场景 → 只刷新状态、不写盘。
        self._autostart_handler: AutostartHandler | None = None
        #: 换键盘布局的处理器（task19，由 `app.py` 装）。
        #: `None` = 自检里那种"没有宠物窗口"的场景 → 只写配置、不动画面。
        self._keyview_changer: KeyviewChanger | None = None
        #: 键位网格控件（`_build_control` 里填）。`None` = 没造（理论上不会）。
        self._key_grid: keygrid.KeyGrid | None = None
        #: 布局下拉的候选（`_fill_layout_combo()` 里填；**来自素材包，不是白名单**）。
        self._layouts: tuple[str, ...] = ()
        #: 最近一次"重启成功"已经发生（供自检观察；**不写配置**）。
        self.restart_requested = False

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
        # 左右收到 8（task21 UX-12）：这 12 px 是**两侧各一份**，直接吃掉滚动区
        # 内容宽度——而 8 键网格那一行正是"差十几像素就裁掉最后一列"的量级。
        outer.setContentsMargins(8, 12, 8, 12)
        outer.setSpacing(8)

        body = QWidget()
        # ⚠ **滚动区里的内容不许比视口宽**（task21 UX-12）：默认策略下 QScrollArea
        # 的水平滚动条**关着**，内容比视口宽的部分直接被裁掉（标题、按钮都会缺一截）。
        # 这里显式允许横向压缩，由行内的 wordWrap / 短标签消化宽度。
        body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        body.setMinimumWidth(0)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)
        for group in GROUP_ORDER:
            body_layout.addWidget(self._build_group(group))
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # 横向不出滚动条：宽度问题必须由内容自己消化（见上面 body 的说明），
        # 让用户横向滚才能看全一个设置项是本末倒置。
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
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
        """一组：`QGroupBox` + 组内 `QFormLayout`（task11 §4，不用绝对坐标）。

        ## 行标题在左、控件在右；**太窄时自动上下排**（task21 UX-12）

        原先用的是 `WrapLongRows`：它只在"字段比剩余宽度还宽"时把那一个字段换到
        下一行，而 8 键网格这种**天生就比窗口宽**的控件永远满足那个条件——
        于是标题行与网格反复互相挤压，表现是文字被裁。改成 `WrapAllRows`：
        每一行都是"标题在上、控件在下"，宽度需求只剩**两者里最宽的那个**。
        """
        box = QGroupBox(group)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)

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
        """按 `FieldKind` 造控件（**唯一的按种类分派处**）。

        ⚠ 键位网格走的是**自己的分支**（`KEYGRID`），不是"在 COMBO 里特判"：
        它就是另一种控件（整块网格 + 重置按钮，不是一行）。
        布局下拉**是**一个下拉，所以照 `pet` 的先例在 `COMBO` 分支里特判。
        """
        if field.kind is FieldKind.KEYGRID:
            return self._build_key_grid(field)
        if field.kind is FieldKind.COMBO:
            widget: QWidget = QComboBox()
            widget.currentIndexChanged.connect(lambda _index: self._on_combo(field))
        elif field.kind is FieldKind.CHECK:
            from PySide6.QtWidgets import QCheckBox

            widget = QCheckBox()
            widget.toggled.connect(lambda checked: self._on_check(field, checked))
            if field.key == AUTOSTART_KEY:
                # 开机自启那一行再挂一个状态文字（`未开启［…］` 这类说明比
                # 一个光秃秃的勾选框有用得多）。⚠ 状态栏那一份才是**契约**
                # （`开机自启：<状态>`），这里只是把同一段文字再显示一次。
                self._autostart_label = QLabel()
                self._autostart_label.setWordWrap(True)
                container = QWidget()
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
                container_layout.setSpacing(2)
                container_layout.addWidget(widget)
                container_layout.addWidget(self._autostart_label)
                return container
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
        elif field.kind is FieldKind.CHECK:
            # `开机自启` 没有配置键，但界面照样要能找到它的控件（刷新/回滚），
            # 所以用一个**内部标记键**登记它（绝不写进配置）。
            self._widgets[AUTOSTART_KEY] = widget
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

    def _build_key_grid(self, field: FieldSpec) -> QWidget:
        """键位网格（task19 §4.3）：**整组**，不是一个"行里的控件"。

        ## 这个分支与其它四种不一样，两处要特别说明

        1. **它自带标题与提示**：网格的右上角要放重置按钮（task19 §4.4 的位置要求），
           所以组名那一行由 `keygrid.KeyGrid` 自己排，返回值是一个 `QGroupBox`
           而不是"等着被 `QFormLayout` 摆到右边"的控件；
        2. **两个信号都不直接写配置**：`binding_requested` / `reset_requested`
           交给 `_on_key_binding` / `_on_key_reset`，由那两条统一走
           `ConfigStore.apply()`（task19 §5 明写"不许新增第二套写配置的路径"）。

        ⚠ 挂起监听的钩子**从 `app.py` 装的处理器里拿**，不是界面自己去找监听器：
        界面不许直接持有监听器（task19 §5）。见 `set_keyview_changer()`。
        """
        box, grid = keygrid.build_group(self)
        grid.binding_requested.connect(self._on_key_binding)
        grid.reset_requested.connect(self._on_key_reset)
        self._key_grid = grid
        # `_widgets[key]` 登记的是**网格本身**，刷新与回滚都按它找
        # （与 `KEYSEQ` 登记 `KeyCaptureEdit` 同一个模式）。
        self._widgets[field.key] = grid
        return box

    # ------------------------------------------------------------ 读配置

    def _load_from_config(self) -> None:
        """把配置里的当前值摆到控件上（**只读**：打开窗口不写盘，§3.13）。

        整个过程包在 `_applying` 里：填充控件会触发 `valueChanged`，
        没有守卫的话"打开设置窗口"就等于"把当前值重新写一遍配置"。

        `开机自启` 那一行**没有配置键**：它的值是 `autostart.query_state()`
        的**只读查询**结果（task14）。所以这里排在配置填充之后、**同样在
        `_applying` 守卫之内**（勾选框的 `setChecked` 会发 `toggled`）。
        """
        self._applying = True
        try:
            self._fill_pet_combo()
            # ⚠ 键盘那两行**先**填：网格要按"最终采用的布局"来摆，
            # 而布局的最终取值要先把下拉填好才定得下来（见 `_fill_layout_combo`）。
            self._fill_layout_combo()
            self._fill_key_grid()
            for field in FIELDS:
                if not field.key or field.key in ("pet", "keyview_layout"):
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
                elif field.kind is FieldKind.CHECK:
                    # 勾选框（task15 的 `边缘抗锯齿`）：读配置摆上初值。
                    # ⚠ 这会发 `toggled`，但整段在 `_applying` 守卫里（见上）。
                    widget.setChecked(bool(value))  # type: ignore[attr-defined]
            self._fill_autostart()
        finally:
            self._applying = False
        self.refresh_status()

    def _fill_autostart(self) -> None:
        """把自启状态摆到勾选框上（**只读**）。

        ⚠ **必须包在 `_applying` 里调用**：`setChecked()` 会发 `toggled`，
        没有守卫的话"打开设置窗口"就等于"按当前状态再写一遍系统自启项"
        ——正好是 task14 §5 的头号红线。

        ⚠ 这里**只查询、不清理**：`cleanup_legacy_markers()` 是写操作，
        它由 `app.py` 在**用户明确动作**（打开窗口的那一次编排）里调，
        绝不挂在"读状态"这条路径上。
        """
        if self._autostart_handler is None:
            # 没装处理器（自检 / 精简场景）→ 只能读，不能改。
            # 仍然显示真实状态：`query_state()` 是纯只读的。
            state = autostart.query_state()
        else:
            state = self._query_autostart()
        self._apply_autostart_state(state)

    def _query_autostart(self) -> autostart.AutostartState:
        """问一次自启状态（**只读**，不经过处理器）。

        处理器负责"开 / 关"这类**写**操作；"查一下现在是什么状态"本身
        没有第二种做法，所以这里直接调 `autostart.query_state()`
        ——处理器接口上**故意不放** `query`：少一个入口就少一处
        可能被实现成"顺手自愈"的地方。
        """
        return autostart.query_state()

    def _apply_autostart_state(self, state: autostart.AutostartState) -> None:
        """把一次查询结果摆到控件与状态栏上（**不改守卫标志**，调用方负责）。"""
        self._autostart_state = state
        checkbox = self._widgets.get(AUTOSTART_KEY)
        if checkbox is not None:
            checkbox.setChecked(bool(state.enabled))  # type: ignore[attr-defined]
        if self._autostart_label is not None:
            self._autostart_label.setText(state.status_text)
        self.refresh_status()

    def set_autostart_handler(self, handler: AutostartHandler | None) -> None:
        """装上"开关开机自启"的处理器（task14；`app.py` 用）。

        与 `set_hotkey_changer()` / `set_restart_handler()` 同一个模式：
        **装没装决定这一行走哪条路**。没装（自检里）→ 只能读状态、改不动。

        ⚠ 与本窗口其余处理器同一条教训：`SettingsController.open()` 里
        **每个新窗口都要重新装**，否则第二次打开设置时勾选**只改界面、
        不动系统自启项**（又一种假成功）。
        """
        self._autostart_handler = handler

    def set_hotkey_changer(self, changer: HotkeyChanger | None) -> None:
        """装上"换呼出快捷键"的处理器（task12；`app.py` 用）。

        与 `PetWindow.set_resize_handler()` 同一个模式：**装没装决定这一行怎么走**。
        没装（自检里）→ 只写配置；装了 → **先注册成功再写配置**（见 `_on_keyseq`）。

        ⚠ 在 `__init__` **之后**调用会错过一次 `_load_from_config()`；
        那句话是有意为之：本方法只影响**之后**的录入行为，不影响初值显示。
        """
        self._hotkey_changer = changer

    def set_restart_handler(
        self,
        restart: RestartHandler | None,
        confirm: ConfirmHandler | None = None,
    ) -> None:
        """装上"重启自己"与"问用户要不要重启"两个处理器（task13；`app.py` 用）。

        与 `set_hotkey_changer()` 同一个模式：**装没装决定这一行走哪条路**。
        没装（自检里）→ 写完配置只提示 `重启后生效`，不真去重启。

        `confirm` 与 `restart` **分开**是有意的：`settings` 是视图层，
        它不该知道弹窗长什么样、也不该知道子进程怎么拉起来；
        它只问一句"用户同意了吗"。两件事分别由 `app.py` 提供。
        """
        self._restart_handler = restart
        self._confirm_handler = confirm

    def reload_from_config(self, current_pet: str | None = None) -> None:
        """重新读一遍配置（**重复点 `设置…` 时用**：窗口可能停在旧值上）。

        `current_pet` 是调用方刚查出来的"配置里真正生效的角色"：角色可能
        在别处被改过（task13 会把坏包回写默认形象），界面得跟着走。
        传 `None` 表示"不改这个认知"，沿用打开时的那个。
        """
        if current_pet is not None:
            self._current_pet = str(current_pet)
        # 重开一次不该继续显示上一次的失败（那是**上一次操作**的结论）。
        # 成功提示同理：`重启后生效` 说的是上一次改动，重开时该回到普通状态。
        self._last_failure = ""
        self._last_message = ""
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

    # ------------------------------------------------------------ 键盘（task19）

    def _fill_layout_combo(self) -> None:
        """填布局下拉：**只列素材包里真的有的布局**（task19 §4.2）。

        ⚠ 候选**取自 `keyview.layout_names`**，不是 `keyview_spec.LAYOUTS` 那张白名单：
        白名单是"哪些名字合法"，而已用列表是"这个包真的画得出来哪几个"——
        白名单之外的已经在解析时被忽略了，某个布局缺图也会被 `pet_loader` 筛掉。
        用白名单当候选的结果就是"下拉里有一半选了没反应"。

        ## 三种情况

        | 情况 | 行为 |
        | --- | --- |
        | 没有键盘形态 / 一个可用布局都没有 | 整组禁用 + 一句说明 |
        | 配置里的布局不存在 | 显示**回退后**那个 + 一句提示（**不静默改配置**） |
        | 正常 | 选中所配置的（空串 = 清单里第一个） |
        """
        combo = self._widgets.get("keyview_layout")
        grid = self._key_grid
        keyview = self._keyview
        if not isinstance(combo, QComboBox):
            return

        available = keyview.layout_names if keyview is not None else ()
        self._layouts = tuple(available)
        combo.clear()

        if not available:
            # ⚠ **整组禁用**（下拉空、网格空、重置禁用）+ 一句说明，
            # 与"开机自启"未实现时的样式一致：**禁用而不是隐藏**（task11 §5）。
            reason = (
                keygrid.TEXT_NO_KEYVIEW if keyview is None else keygrid.TEXT_NO_LAYOUT
            )
            if grid is not None:
                grid.set_layout(None)
                grid.apply_bindings({})
                grid.set_group_enabled(False, reason)
            combo.setEnabled(False)
            combo.setToolTip(reason)
            return

        combo.setEnabled(True)
        combo.setToolTip("")
        for name in available:
            combo.addItem(name)

        configured = str(self._store.get("keyview_layout") or "").strip()
        used = configured if configured in available else available[0]
        combo.setCurrentIndex(available.index(used))

        if grid is not None:
            grid.set_group_enabled(True)
            if configured and configured not in available:
                # **回退后显示 + 一句提示**（task19 §4.2）：
                # ⚠ **不静默改配置**——用户可能只是暂时换了素材包，
                # 把配置改掉就等于替他做了决定（下次换回来时布局没了）。
                grid.set_hint(
                    keygrid.TEXT_LAYOUT_FALLBACK.format(missing=configured, used=used)
                )
            else:
                grid.set_hint("")

    def _fill_key_grid(self) -> None:
        """按当前布局摆网格 + 填绑定（**只读**：打开窗口不写盘）。

        ⚠ 绑定经过 `normalize_bindings()`：**配置里不存在的键 id 在这里被丢掉**，
        而且**绝不写回**（§2.10 读操作只读，task19 §4.6）。
        """
        grid = self._key_grid
        if grid is None:
            return
        layout = None
        if self._layouts:
            name = str(self._store.get("keyview_layout") or "").strip()
            used = name if name in self._layouts else self._layouts[0]
            grid.set_layout(self._layout_spec(used))
            layout = grid.layout
        bindings = normalize_bindings(self._store.get("keyview_keys"), self._keyview)
        grid.apply_bindings(bindings_for_layout(bindings, layout))

    def _layout_spec(self, name: str):
        """按名字取布局规格（没有就是 `None`）。**只读**。"""
        if self._keyview is None:
            return None
        return self._keyview.layout(name)

    # ------------------------------------------------------------ 状态栏

    def refresh_status(self) -> None:
        """刷新状态栏：**失败原因 > 成功提示 > 普通状态**（三段，不是拼起来）。

        三段各自的来源：

        | 段 | 来源 | 什么时候清掉 |
        | --- | --- | --- |
        | 失败原因 | `_last_failure`（写失败的系统原文） | 下一次写入成功 |
        | **成功提示** | `_last_message`（`重启后生效` 这类） | 下一次刷新成别的东西之前**一直留着** |
        | 普通状态 | 最近一次只读查询的自启状态（task14 契约：前缀 `开机自启：`） | —— |

        ⚠ **中间那一段是 task15 补的**。第一版只写了两段，`_set_status_text()`
        直接 `setText()`——于是**任何一次后续 `refresh_status()` 都会把那句提示
        冲掉**（自检当场抓到：不同意重启时状态栏显示的是 `开机自启：未开启`
        而不是 `重启后生效`）。而"不同意重启 → 状态栏留下 `重启后生效`"
        是 task15 §6 的明文验收，task13 的换角色也依赖同一句话。
        对策：把成功提示**记在字段里**，由本方法统一渲染，而不是直接写控件。

        还没查过时显示 `未开启`：`autostart.TEXT_DISABLED` **就是**那个值，
        所以这里不另写一个字面量（真值只一处）。
        """
        if self._last_failure:
            self._status_label.setText(f"{FAILED_PREFIX}{self._last_failure}")
            return
        if self._last_message:
            self._status_label.setText(self._last_message)
            return
        state = self._autostart_state
        status_text = state.status_text if state is not None else autostart.TEXT_DISABLED
        self._status_label.setText(f"{STATUS_PREFIX}{status_text}")

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

        `开机自启` 那一行没有配置键，真值来源是**系统里的自启项**，
        走 `_rollback_autostart()`（先查一次再摆上去）。
        """
        if not field.key:
            if field.kind is FieldKind.CHECK:
                self._rollback_autostart()
            return
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
            elif field.kind is FieldKind.KEYGRID:
                # 键位映射：真值来源**就是这条路径**（task19 §4.4 "握写失败 → 网格回到原样"）。
                if isinstance(widget, keygrid.KeyGrid):
                    bindings = normalize_bindings(value, self._keyview)
                    widget.apply_bindings(bindings_for_layout(bindings, widget.layout))
            elif field.kind is FieldKind.COMBO:
                # 布局下拉：回滚到配置里的真值（**包括它指向一个不存在的布局**
                # 那种情况——那时 `_fill_layout_combo` 会把它显示成回退后那个）。
                self._fill_layout_combo()
                if isinstance(widget, QComboBox):
                    name = str(value or "").strip()
                    used = name if name in self._layouts else (
                        self._layouts[0] if self._layouts else ""
                    )
                    if used in self._layouts:
                        widget.setCurrentIndex(self._layouts.index(used))
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
        """勾选框动了。

        `开机自启`（task14）与别的行**走的不是同一条路**：

        | 行 | 真值在哪 | 怎么写 |
        | --- | --- | --- |
        | 其余 `CHECK` 行 | `config.json` | `_write_field()` → `ConfigStore.apply()` |
        | `开机自启` | **系统里的自启项** | 处理器（`app.py` 装）→ `autostart.enable()/disable()` |

        所以这里先按 `field.key` 分一次流。自启那条路上有一条**与写配置
        同构的规矩**：写入失败 → **勾选恢复原样** + 状态栏
        `设置失败：<系统原因>`，**绝不显示假的成功状态**（task14 §4）。

        ⚠ 回滚赋值走 `_applying` 守卫：`setChecked()` 会再发一次 `toggled`，
        没有守卫就是"回滚又触发一次写入"的死循环（§4.9 的信号回路）。
        """
        if self._applying:
            return
        if not field.key and field.kind is FieldKind.CHECK:
            self._on_autostart_check(checked)
            return
        if field.key == "antialias":
            self._on_antialias_check(field, checked)
            return
        self._write_field(field, bool(checked))

    def _on_antialias_check(self, field: FieldSpec, checked: bool) -> None:
        """勾 / 取消 `边缘抗锯齿`（task15）：**写配置 → 问要不要现在重启**。

        与 `换角色`（`_switch_to_pet`）是**同一套流程**，因为生效方式相同
        （重启生效）。三条规矩也照抄：

        | 情形 | 行为 |
        | --- | --- |
        | 写成功 | 询问"现在自动重启吗？"；同意则重启，不同意则状态栏 `重启后生效` |
        | **值没变**（重复勾同一个值） | `SetResult.changed` 为假 → **不问、不重启、不写盘** |
        | 写失败 | 控件**回滚** + 状态栏 `设置失败：<系统原因>`，**且不重启** |

        ⚠ 写失败**不问重启**：配置没写进去 = 磁盘上还是老值，这时重启
        只会让用户看到"我改了它，重启了，但还是老样子"（task13 §4 同一条）。
        """
        result = self._store.apply(field.key, bool(checked))
        if not result.ok:
            logger.error("设置：写 %s 失败：%s", field.key, result.error)
            self._rollback(field)
            self._set_failure(result)
            return

        self._clear_failure()
        if not result.changed:
            # 值没变：**不写盘、不发问、不重启**（task15 §6 有一条验收盯这个）。
            # 上面的 `apply()` 已经走了"没变就不落盘"的短路，这里只是不发问。
            logger.info("设置：%s 已经是 %r，不询问重启", field.key, bool(checked))
            self.settings_changed.emit(field.key, bool(checked))
            return

        self.settings_changed.emit(field.key, bool(checked))
        logger.info("设置：%s → %r（重启后生效）", field.key, bool(checked))
        self._ask_restart_for_antialias(bool(checked))

    def _ask_restart_for_antialias(self, enabled: bool) -> None:
        """问"现在自动重启吗？"（与 `_ask_restart` 同构，文案不同）。

        **默认不重启**：没装确认处理器（自检 / 精简场景）时按"不同意"处理
        ——"擅自把用户的程序关掉"比"让他自己重启"危险得多。
        """
        state = ANTIALIAS_ON_TEXT if enabled else ANTIALIAS_OFF_TEXT
        agreed = False
        if self._confirm_handler is not None:
            agreed = bool(
                self._confirm_handler(
                    RESTART_ASK_ANTIALIAS_TITLE,
                    RESTART_ASK_ANTIALIAS_TEXT.format(state=state),
                )
            )

        hint = f"边缘抗锯齿已切换到「{state}」；{PET_RESTART_HINT}"
        if not agreed:
            # 不同意 → 状态栏留下 `重启后生效`，**当前实例继续运行**。
            self._set_status_text(hint)
            logger.info("设置：边缘抗锯齿已改为 %s，用户选择稍后重启", state)
            return

        if self._restart_handler is None:
            # 装了确认处理器却没装重启处理器（自检里的组合）→ 不能假装重启成功。
            self._set_status_text(hint)
            return

        outcome = self._restart_handler()
        if not outcome.started:
            # 重启失败：**当前实例继续运行**，**不说"重启后生效"**
            # （那会让人以为自动重启成功了）。
            detail = outcome.reason or "新实例没能起来"
            logger.error("设置：边缘抗锯齿已改为 %s，但重启失败：%s", state, detail)
            self._last_failure = detail
            self.refresh_status()
            return

        self.restart_requested = True
        self._set_status_text(f"边缘抗锯齿已切换到「{state}」；{PET_RESTARTING_HINT}")

    def _on_autostart_check(self, checked: bool) -> None:
        """勾 / 取消 `开机自启`：**先动系统，再按只读查询的结果刷新界面**。

        ## 为什么"刷新"必须是一次**新查询**而不是"按勾选态显示"

        勾选成功不等于功能可用：`.cmd` 回退、快捷方式写了但核验不过
        都可能让"用户勾上了"与"实际能不能用"分家。所以：

        - 成功（含 `disable` 的**幂等成功**）→ 用返回的 `status_text`；
        - 失败 → `_last_failure` 放**系统原文** + **勾选回滚到真实状态**。

        处理器没装（自检）时**什么都不做、也不改界面**：宁可显示"没变化"，
        也不能假装成功——这与 `_on_keyseq` 里"没装处理器就只写配置"不同，
        因为这里**没有配置可写**，写不了就是写不了。
        """
        handler = self._autostart_handler
        if handler is None:
            logger.warning("设置：没有装开机自启的处理器，勾选被忽略（不会假装成功）")
            self._last_failure = "这次运行里开机自启功能不可用（没能装上处理器）"
            self._rollback_autostart()
            self.refresh_status()
            return

        try:
            outcome = handler(bool(checked))
        except Exception as exc:  # 处理器自己炸了也要报出来，不许静默
            logger.error("设置：开机自启处理器抛异常（%s: %s）", type(exc).__name__, exc)
            self._last_failure = f"{type(exc).__name__}: {exc}"
            self._rollback_autostart()
            self.refresh_status()
            return

        if not outcome.ok:
            logger.error("设置：开机自启没能改过来：%s", outcome.reason)
            self._last_failure = outcome.reason or "改不动系统里的自启项"
            self._rollback_autostart()
            self.refresh_status()
            return

        # 成功：**处理器给的结论就是权威**，别再拿界面自己的查询去推翻它。
        #
        # 为什么不"再查一次、以查询为准"（第一版就是这么写的，自检当场抓到两个问题）：
        # ① 界面查的是**真实**启动文件夹，而真正干活的是处理器；
        #    两者只要不是同一处（自检注入的假文件夹、将来换成注册表实现），
        #    "查询"就会把处理器刚做成的结果**覆盖成相反的值**
        #    ——用户勾上了，界面却弹回未勾选（假的失败，与假成功一样糟）；
        # ② `enabled` 取查询、`status_text` 取处理器，两个来源拼出来的状态
        #    可能自相矛盾（勾选态与文字对不上）。
        #
        # 所以规矩只有一条：**`AutostartOutcome` 是这一次操作的唯一结论**。
        # `autostart.enable()/disable()` 自己已经在内部做完了"写完再读回核验"
        # （见 `enable()` 里那次 `query_state()`），核验不过时它返回的就是
        # `ok=False`——那份核验比界面这里的第二次查询更贴近真相。
        self._last_failure = ""
        state = autostart.AutostartState(
            enabled=bool(outcome.enabled),
            status_text=outcome.status_text or autostart.TEXT_DISABLED,
            error=None,
            detail=f"设置界面的操作结论：enabled={outcome.enabled}",
        )
        self._applying = True
        try:
            self._apply_autostart_state(state)
        finally:
            self._applying = False
        logger.info("设置：开机自启已切换为 %s（%s）", outcome.enabled, state.status_text)

    def _rollback_autostart(self) -> None:
        """把勾选恢复到**系统里的真实状态**（重新查一次，不是取反）。

        与 `_rollback()` 用配置当真值来源同一个道理：界面可能落后于现实。
        查询本身是只读的，失败时它也只会返回"未开启"那类结论，不会更糟。
        """
        state = self._query_autostart()
        self._applying = True
        try:
            self._apply_autostart_state(state)
        finally:
            self._applying = False

    def _on_keyseq(self, field: FieldSpec, text: str) -> None:
        """录到一个快捷键（task12 接线）：**先注册成功，再写配置**。

        ## 顺序为什么不能反

        | 顺序 | 后果 |
        | --- | --- |
        | 先写配置、再注册 | 注册失败时磁盘上已经是一个**用不了的**快捷键，下次启动还会照着它去注册 |
        | **先注册、再写配置**（本实现） | 注册失败 → 配置与控件都回到真实状态，磁盘上永远是"真的能用"的那个 |

        注册失败**必须回滚到原来那个**（task12 §6）：这里先
        `restore(previous)` 把旧热键抢回来，再让控件显示配置里的真值。
        **绝不落到"没有热键可用"**——那会让 `隐藏宠物` 立刻变成禁用，
        而用户其实什么都没做错，只是敲了一个被占用的组合。

        ⚠ 校验交给 `hotkey.HotkeyManager.try_register()`：
        "什么算合法组合"的真值在 `hotkey_spec.parse_combo()` 一处，
        界面不重复判断（否则两处迟早不一样）。
        """
        if self._applying:
            return
        if self._hotkey_changer is None:
            # 没装改热键的处理器（自检里常见）→ 只写配置，不动系统热键。
            self._write_field(field, str(text))
            return

        previous = str(self._store.get(field.key) or "")
        outcome = self._hotkey_changer(str(text), previous)
        if not outcome.ok:
            # 换失败：把**旧热键抢回来**，界面退回真实值，状态栏说清原因。
            self._hotkey_changer(previous, previous)
            self._applying = True
            try:
                widget = self._widgets.get(field.key)
                if widget is not None:
                    widget.set_shortcut_text(previous)  # type: ignore[attr-defined]
                    widget.reject_capture(outcome.reason)  # type: ignore[attr-defined]
            finally:
                self._applying = False
            self._last_failure = outcome.reason
            self.refresh_status()
            logger.warning("设置：快捷键 %s 没能启用：%s", text, outcome.reason)
            return

        if not self._write_field(field, str(text)):
            # 写配置失败：热键已经换成新的了，配置还是旧的 →
            # **把热键换回去**，让两边一致（绝不显示假成功）。
            self._hotkey_changer(previous, previous)
            return
        self._set_status_text(f"呼出快捷键已改为 {text}")

    # ------------------------------------------------------------ 键盘（task19）

    def set_keyview(self, keyview: KeyViewSpec | None) -> None:
        """告诉窗口"当前素材包有什么键盘形态"（task19；`app.py` 用）。

        ⚠ 在 `__init__` **之后**调用会错过一次 `_load_from_config()`
        ——所以正常路径是把 `keyview` 传进构造函数（见 `SettingsController.open()`），
        本方法只用于"窗口已经开着、外部换了素材包"那种将来才会有的场景。
        真调到时**重新填一遍键盘那两行**，免得窗口显示的还是旧素材包的布局。
        """
        self._keyview = keyview
        self._applying = True
        try:
            self._fill_layout_combo()
            self._fill_key_grid()
        finally:
            self._applying = False

    def set_keyview_changer(self, changer: KeyviewChanger | None) -> None:
        """装上"换键盘布局"的处理器（task19；`app.py` 用）。

        与 `set_hotkey_changer()` 同一个模式：**装没装决定这一行走哪条路**。
        没装（自检里）→ 只写配置；装了 → **先切画面、切成功了再写配置**。

        ⚠ 与本窗口其余处理器同一条教训：`SettingsController.open()` 里
        **每个新窗口都要重新装**，否则第二次打开设置窗口换布局
        **只写配置、画面不动**（又一种假成功）。

        ⚠ 挂起按键监听也走它：界面**不许直接持有监听器**（task19 §5）。
        处理器内部调 `KeyListenerController.set_suspended()`。
        """
        self._keyview_changer = changer
        grid = self._key_grid
        if grid is not None:
            grid.set_suspend_hook(
                None if changer is None else self._suspend_for_capture
            )

    def _on_layout_combo(self, field: FieldSpec) -> None:
        """换了键位布局：**先切画面、切成功了再写配置**（与 `_on_keyseq` 同一条纪律）。

        ## 顺序为什么不能反

        | 顺序 | 后果 |
        | --- | --- |
        | 先写配置、再切 | 切不过去时磁盘上已经是一个**画面没在用**的布局，
        | | 下次启动会照着它去恢复（而它可能又不可用了） |
        | **先切、再写**（本实现） | 切不过去 → 配置与下拉都回到真实状态 |

        ⚠ 布局**不像换角色那样要重启**（task19 §4.5）：所有布局共用 `keyview.cell`，
        切布局不改窗口尺寸、不动锚点——所以这里是即时生效 + 重绘一次。
        """
        combo = self._widgets.get(field.key)
        if not isinstance(combo, QComboBox):
            return
        index = combo.currentIndex()
        if not 0 <= index < len(self._layouts):
            return
        layout = self._layouts[index]

        # 先把网格摆成新布局（**这一步不能失败**：布局名来自素材包）。
        previous = str(self._store.get("keyview_layout") or "").strip()
        grid = self._key_grid
        if grid is not None:
            grid.set_layout(self._layout_spec(layout))
            grid.set_hint("")
            grid.apply_bindings(
                bindings_for_layout(self._visible_bindings(), grid.layout)
            )

        if not self._write_field(field, layout):
            # 写失败：`_write_field` 已经回滚了控件（见 `_rollback`），
            # 这里只补一句"画面已经换了但没记住"（绝不显示假成功）。
            logger.error("设置：%s（布局 %s）", KEYVIEW_SAVE_FAILED, layout)
            self._append_status(KEYVIEW_SAVE_FAILED)
            return

        applied = True
        if self._keyview_changer is not None:
            outcome = self._keyview_changer(layout)
            applied = bool(outcome.ok)
            if not applied:
                # 切不过去：把下拉与配置都退回真实状态（磁盘上永远是真的能用的那个）。
                logger.warning("设置：布局 %s 切不过去：%s", layout, outcome.reason)
                self._last_failure = outcome.reason or "画面没能切到这个布局"
                if previous != layout:
                    self._write_field(field, previous)
                self.refresh_status()
                return

        if applied:
            self._set_status_text(KEYVIEW_LAYOUT_APPLIED.format(layout=layout))

    def _refresh_key_grid(self) -> None:
        """按**配置里的真值**重填网格（写成功之后的回填；只在 `_applying` 里改控件）。

        ⚠ 与 `_fill_key_grid()` 分开：那一个**连布局一起重摆**（打开窗口时用），
        这一个只刷绑定（录入/重置之后用）——重摆布局会把用户正看着的网格
        整个重建一遍，按钮的焦点与录入态都会莫名其妙地丢。
        """
        grid = self._key_grid
        if grid is None:
            return
        bindings = self._visible_bindings()
        self._applying = True
        try:
            grid.apply_bindings(bindings_for_layout(bindings, grid.layout))
        finally:
            self._applying = False

    def _visible_bindings(self) -> dict[str, KeyBinding]:
        """配置里**当前素材包真的有的**绑定（丢弃不存在的键 id，**不写回**）。

        网格与两条写路径都以它为基础——这样"配置里留着一个不存在的键 id"
        不会因为一次无关的录入被**顺手写掉**（§2.10 读操作只读，task19 §4.6）。
        """
        return normalize_bindings(self._store.get("keyview_keys"), self._keyview)

    def _on_key_binding(self, key_id: str, vk: int, name: str) -> None:
        """某个键位录到了一个键：写 `keyview_keys`（**唯一那条写配置的路**）。

        ⚠ **不自动解绑别的键位**（决策 2）：同一个物理键可以绑到多个键位，
        提示日志已经在 `keygrid` 里记过了。

        ⚠ 写失败 → 网格**回滚到配置里的真值**（不是"改之前那个值"），
        与其余各行同一条规矩（task19 §4.4 / §6）。
        """
        field = self._field_for("keyview_keys")
        if field is None:  # pragma: no cover - 字段表一定有它
            return
        bindings = self._visible_bindings()
        bindings[key_id] = KeyBinding(vk=int(vk), name=str(name))
        payload = bindings_to_config(bindings)

        if not self._write_field(field, payload):
            # ⚠ 写失败时 `_write_field` 已经按 `field.kind` 回滚过网格了
            # （`_rollback()` 里那条 KEYGRID 分支），这里只补一句后果说明。
            logger.error("设置：%s（%s -> %s）", KEYVIEW_SAVE_FAILED, key_id, name)
            self._append_status(KEYVIEW_SAVE_FAILED)
            return
        # 写成功 → **按配置回填一次**（判据以磁盘为准）。
        #
        # ⚠ 这一步不是多余的：网格上按钮的文字在录入那一刻就自己改了，
        # 但"哪些键位绑着"这份状态**只有配置是权威**。不重填的话，
        # 网格自己的 `_bindings` 还停在旧值上 —— 重置按钮的可用性、
        # 以及"同一个键绑在几个键位上"那条提示日志都会与配置对不上。
        self._refresh_key_grid()
        self._set_status_text(KEYVIEW_KEY_BOUND.format(key_id=key_id, name=name))

    def _on_key_reset(self) -> None:
        """重置：写 `keyview_keys = {}`（**一次点击释放全部映射**，task19 §4.4）。

        写成功 → 网格立刻全部回到"未设置" + 监听层立刻生效（`settings_changed`
        由 `app.py` 接上 `set_bindings()`，不用重启）。
        写失败 → `_write_field` 已经回滚，这里只补一句提示。
        """
        field = self._field_for("keyview_keys")
        if field is None:  # pragma: no cover
            return
        if not self._write_field(field, {}):
            logger.error("设置：%s", KEYVIEW_RESET_FAILED)
            self._append_status(KEYVIEW_RESET_FAILED)
            return
        # 写成功 → 按配置回填（现在的配置是空对象 → 网格全变「未设置」）。
        # 失败那一支不用管：`_write_field` → `_rollback()` 已经把网格还原了。
        self._refresh_key_grid()
        self._set_status_text(KEYVIEW_KEYS_RESET)

    def _suspend_for_capture(self, suspended: bool) -> None:
        """录入期间挂起按键监听（**唯一出口**：界面不持有监听器）。

        ⚠ 没装处理器时是**空操作**，且**不报错**：自检与"没有 KV"的场景下
        本来就没有监听器可挂起。
        """
        changer = self._keyview_changer
        hook = getattr(changer, "set_suspended", None) if changer is not None else None
        if callable(hook):
            hook(bool(suspended))
            return
        logger.debug("键位录入：没有装可挂起的监听处理器（%s）", suspended)

    @staticmethod
    def _field_for(key: str) -> FieldSpec | None:
        """按配置键找那一行（`FIELDS` 是唯一真相源，不另存一份）。"""
        return next((item for item in FIELDS if item.key == key), None)

    def _on_combo(self, field: FieldSpec) -> None:
        """下拉换了。**两个下拉走两条路**（就与 `开机自启` 与别的勾选框一样）。

        | 行 | 候选真值 | 换了之后 |
        | --- | --- | --- |
        | `角色` | 磁盘上发现的包 | 写配置 → 问要不要重启（task11/13） |
        | `键位布局`（task19） | 当前素材包的 KV 布局 | **先切画面、切成功了再写配置** |
        """
        if self._applying:
            return
        if field.key == "keyview_layout":
            self._on_layout_combo(field)
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

        ## 三条路径（task11 的边界在这里由 task13 补齐）

        | 情形 | 行为 |
        | --- | --- |
        | **可用**包 | 写配置 → 询问"现在自动重启吗？" → **同意才重启**；不同意则提示 `重启后生效` |
        | **坏包** | 弹窗说详情 + 退回默认形象 + **不重启** + **把配置改回默认形象** |
        | **配置写失败** | 提示 `保存失败，重启后会回到原来的角色`，界面显示与**真实生效**的一致 |

        **换形象走重启**（不做热替换）：`cell` 可能不同，热替换会留下
        "状态半新半旧"的中间态（task13 §4 的定案）。
        """
        if choice.ok:
            self._switch_to_pet(field, choice)
            return

        self._report_broken_pet(choice)

    def _switch_to_pet(self, field: FieldSpec, choice: PetChoice) -> None:
        """选中一个**可用**包：写配置 → 问要不要现在重启。

        ## 写失败为什么**不问重启**

        `pet` 没写进去 = 磁盘上还是老角色。这时若照常重启，新实例起来
        显示的仍是**旧角色**——用户看到的是"我换了角色，它重启了，但还是原来那个"，
        比"明确告诉他保存失败"更糟。所以写失败直接走提示，
        **界面退回配置里的真值**（`_rollback` 已经做掉了）。
        """
        if not self._write_field(field, choice.namespace):
            # 写失败：控件已经回滚到 `store.get("pet")` 了，这里只补那句话。
            # ⚠ `_set_failure()` 已经把**系统原文**放上状态栏了，这里**不覆盖**
            # 它——task13 §4 要的那句提示挂在文案上，两件事分开说：
            # 状态栏说"为什么没写进去"，日志说"所以重启也回不去"。
            logger.error("设置：%s（%s）", PET_SAVE_FAILED_HINT, choice.namespace)
            self._append_status(PET_SAVE_FAILED_HINT)
            return

        self._current_pet = choice.namespace
        self._refresh_pet_combo_current()
        self._ask_restart(field, choice)

    def _ask_restart(self, field: FieldSpec, choice: PetChoice) -> None:
        """问"现在自动重启吗？"（task13 §4）。

        **默认不重启**：没装确认处理器（自检 / 精简场景）时按"不同意"处理，
        因为"擅自把用户的程序关掉"比"让他自己重启"危险得多。
        """
        agreed = False
        if self._confirm_handler is not None:
            agreed = bool(self._confirm_handler(RESTART_ASK_TITLE, RESTART_ASK_TEXT))

        if not agreed:
            # 不同意 → 提示"重启后生效"，**当前实例继续运行**（task13 §2 第 6 条）。
            self._set_status_text(
                f"已切换到「{choice.display_name}」（{choice.namespace}）；"
                f"{PET_RESTART_HINT}"
            )
            logger.info("设置：角色已改为 %s，用户选择稍后重启", choice.namespace)
            return

        if self._restart_handler is None:
            # 装了确认处理器却没装重启处理器（自检里的组合）→ 不能假装重启成功。
            self._set_status_text(
                f"已切换到「{choice.display_name}」（{choice.namespace}）；"
                f"{PET_RESTART_HINT}"
            )
            return

        outcome = self._restart_handler()
        if not outcome.started:
            # 重启失败：**当前实例继续运行**（task13 §2 第 7 条）。
            # 文案由 `restart.RESTART_FAILED_MESSAGE` 一处给，弹窗由 `app.py` 弹
            # （`RestartController` 已经弹过了）；这里只把状态栏也说清楚，
            # **不说"重启后生效"**——那会让人以为自动重启成功了。
            detail = outcome.reason or "新实例没能起来"
            logger.error("设置：角色已改为 %s，但重启失败：%s", choice.namespace, detail)
            self._last_failure = detail
            self.refresh_status()
            return

        self.restart_requested = True
        self._set_status_text(
            f"已切换到「{choice.display_name}」（{choice.namespace}）；{PET_RESTARTING_HINT}"
        )

    def _report_broken_pet(self, choice: PetChoice) -> None:
        """坏包：弹窗说详情 + 退回默认形象 + **把配置改回默认形象** + 不重启。

        ## task13 补上的那一半：**配置要被改回去**

        task11 的边界是"坏包不动配置"，理由是 `config.pet` 的默认值本来
        就是空串（= "用户还没表态"），于是"继续用默认形象"不需要写任何东西。

        task13 §6 把这条验收写细了：**"配置被改回默认形象（重启后不再踩同一个坑）"**。
        两种情况要分开：

        | 配置里原来是什么 | 现在怎么写 |
        | --- | --- |
        | **空串**（没表态） | **什么都不写**——空串正是"默认形象"的意思（§2.4），写一个 `weedy` 进去反而把"用户没表态"变成了"用户选了 weedy" |
        | 指向**别的**包（哪怕那个包是好的） | 用户刚表达的是"我不想用这个了，我想用那个坏的" → 落回默认形象：**写空串** |

        第二种就是"改回默认形象"的落地方式：写空串 = 回到"没表态" =
        下次启动取 `pet_spec.DEFAULT_PET_NAMESPACE`（**真值只有一处**）。
        写 `"weedy"` 是错的：那会让默认形象的名字在项目里出现第二份（§4.1）。

        ## 写回失败怎么办

        **不许假装成功**：写不回去时明确说出来（状态栏带系统原文），
        因为"下次启动还会再踩一次同一个坑"是用户需要知道的事。
        但**不弹窗**——弹窗那一份已经把"这个包为什么坏"说清楚了，
        再弹一个只会让人以为出了更严重的问题。
        """
        detail = "\n".join(f"· {issue}" for issue in choice.issues) or "· （没有拿到原因）"
        message = (
            f"你的选择没被接受，「{choice.display_name}」（{choice.namespace}）不可用，"
            f"已回到默认形象 {DEFAULT_PET_NAMESPACE}。\n"
            f"「{choice.namespace}」的问题：\n{detail}"
        )
        logger.warning("设置：角色 %s 不可用，已回到默认形象", choice.namespace)
        self._last_failure = message
        self.refresh_status()
        self._restore_default_pet()
        # 界面必须与"真实生效的角色"一致：配置要么已经被改成空串、
        # 要么本来就指不到坏包，所以 `_current_pet` 跟着回到默认形象。
        self._current_pet = DEFAULT_PET_NAMESPACE
        self._applying = True
        try:
            self._fill_pet_combo()
        finally:
            self._applying = False

    def _restore_default_pet(self) -> None:
        """把 `pet` 写回"默认形象"（= 空串，见 `_report_broken_pet` 的说明）。

        只在配置**真的指向了别的包**时才写：本来就指不到任何包（空串）
        时写一遍是多余的一次磁盘写，而且会把"用户没表态"这个信息抹掉
        （空串与空串比较当然一样，所以这里判的是"非空"）。
        """
        current = str(self._store.get("pet") or "").strip()
        if not current:
            return  # 本来就是"没表态" → 默认形象，**一个字节都不用写**
        result = self._store.apply("pet", "")
        if result.ok:
            logger.info("设置：角色配置已改回默认形象（%s → 空串）", current)
            return
        logger.error("设置：角色配置没能改回默认形象：%s", result.error)
        self._last_failure = result.error or "写配置失败"
        self.refresh_status()

    def _refresh_pet_combo_current(self) -> None:
        """写成功后把 `● ` 挪到新的当前项上。"""
        self._applying = True
        try:
            self._fill_pet_combo()
        finally:
            self._applying = False

    def _set_status_text(self, text: str) -> None:
        """显示一句**成功**提示（失败提示走 `_set_failure`）。

        ⚠ **记字段 + 走 `refresh_status()`**，不要直接 `setText()`：
        直接写控件的话，任何一次后续刷新（例如自启状态刷新）都会把它冲掉
        （task15 自检抓到的真 bug，见 `refresh_status()` 的说明）。
        """
        self._last_failure = ""
        self._last_message = text
        self.refresh_status()

    def _append_status(self, text: str) -> None:
        """在**已经显示着的**状态栏后面补一句，**不覆盖失败原因**。

        存在的理由：写失败时状态栏上是 `设置失败：<系统原文>`（那是"为什么没成"），
        而 task13 §4 还要说一句"保存失败，重启后会回到原来的角色"（那是"后果"）。
        直接 `setText()` 会把系统原文挤掉，正好违反"失败原因优先于成功状态"
        （§2.10）——所以两句都留着。

        ⚠ **要接到"当前生效的那一段"上**（`refresh_status()` 是
        "失败 > 成功提示 > 普通状态"三段）：写失败时当前生效的是
        `_last_failure`，接错到 `_last_message` 上就会被失败那一段盖住、
        **一个字都显示不出来**（task15 改这一段时踩到，被 `check_task13.py`
        那条"写失败时提示『保存失败…』"当场抓住）。
        """
        if self._last_failure:
            if text in self._last_failure:  # 别重复堆同一句
                return
            self._last_failure = f"{self._last_failure}\n{text}"
            self.refresh_status()
            return

        existing = self._last_message or self._status_label.text()
        if not existing:
            self._set_status_text(text)
            return
        if text in existing:  # 别重复堆同一句
            return
        self._last_message = f"{existing}\n{text}"
        self.refresh_status()

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
