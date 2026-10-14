"""右键菜单：**数据驱动**构建 + 稳定 id 契约 + 勾选/禁用态刷新。

## 三条硬约定

1. **每次弹出前重建**（`build_menu()`）：勾选态与文字都按**当前状态**现算，
   不搞"建一次、之后手工 setChecked"那种两处状态要对齐的写法。
2. **稳定 id 靠 `QAction.setData()`**，**不靠菜单文字**：文字随时可能被改
   （附录 B 是用户可见文字、将来也可能润色），而接线只看 id
   （task06 §6："把 `镜像翻转` 改成 `翻转`，接线仍然有效"）。
3. **宠物窗口是 parent**：菜单不设 parent 就只是一条顶层窗口，
   在"始终置顶"的宠物旁边会掉到别的窗口后面去（task06 §4 行为契约）。

## 未接线的项是**禁用**不是隐藏

`隐藏宠物`（task12）已接线；**本文件现在没有未接线的项了**
（`NOT_WIRED_YET` 是空元组）。它曾经是灰的，理由与将来任何一项一样：
**保持菜单位置稳定**，免得用户以后找不到（task06 §2 第 2 条）。
（`调整大小…` 由 task09 接上、`设置…` 由 task11 接上、`隐藏宠物` 由 task12 接上：
三者的"可用性"都是**装没装处理器**；`隐藏宠物` 还要求**真的有呼出快捷键**。）

## 文字的真值

菜单文字必须与**附录 B** 完全一致，因此它们是本文件里的常量（**只写一处**）：
`调整大小…` / `坐下休息` / `站起来` / `镜像翻转` / `设置…` / `隐藏宠物` / `退出`。
`坐下休息` 与 `站起来` 是**同一个菜单项**（id 恒为 `action`）的两种说法，
由当前是不是"手动休息"决定用哪个（task06 §4 文字切换）。
"""

from dataclasses import dataclass
from typing import Final

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

# ------------------------------------------------------------------ 文字（附录 B）

TEXT_RESIZE: Final[str] = "调整大小…"
TEXT_REST: Final[str] = "坐下休息"
TEXT_STAND_UP: Final[str] = "站起来"
TEXT_MIRROR: Final[str] = "镜像翻转"
TEXT_SETTINGS: Final[str] = "设置…"
TEXT_VISIBILITY: Final[str] = "隐藏宠物"
TEXT_QUIT: Final[str] = "退出"

# ------------------------------------------------------------------ id 契约

#: 稳定 id（`QAction.setData()` 的取值）。**接线只看这些**，不看文字。
ID_RESIZE: Final[str] = "resize"
ID_ACTION: Final[str] = "action"
ID_MIRROR: Final[str] = "mirror"
ID_SETTINGS: Final[str] = "settings"
ID_VISIBILITY: Final[str] = "visibility"
ID_QUIT: Final[str] = "quit"

#: 分隔线在 `MENU_ORDER` 里的占位（它不是一个可点的项，没有 QAction）。
SEPARATOR: Final[str] = "(separator)"

#: 菜单项顺序（含分隔线占位）。**顺序与文字都属对外契约**（task06 §2 第 1 条）。
MENU_ORDER: Final[tuple[str, ...]] = (
    ID_RESIZE,
    ID_ACTION,
    ID_MIRROR,
    ID_SETTINGS,
    ID_VISIBILITY,
    SEPARATOR,
    ID_QUIT,
)

#: 尚未接线的项（**显示为灰色不可点**，不是隐藏）。
#: task09 接上了 `调整大小…`、task11 接上了 `设置…`、task12 接上了 `隐藏宠物`
#: ——**现在是空的**，也就是说菜单里不再有"点了没反应"的项。
NOT_WIRED_YET: Final[tuple[str, ...]] = ()

#: 禁用 `隐藏宠物` 的原因（task12 §6 逐字，附录 B）。
#: ⚠ 禁用是**没有商量余地的**：一个可用的呼出快捷键都没有时，
#: 隐藏出去就再也叫不回来，那句话必须原样出现在提示里。
REASON_NO_HOTKEY: Final[str] = (
    "没有可用的呼出快捷键，隐藏后将无法呼出。请先在「设置」里指定一个快捷键。"
)


@dataclass(frozen=True)
class MenuState:
    """构建菜单时需要的**当前状态**（调用方每次弹出前现算一份）。

    | 字段 | 语义 |
    | --- | --- |
    | `action` | 当前动作名（决定 `镜像翻转` 是否可用） |
    | `manual_rest` | 当前是不是"手动坐下休息"（决定 `坐下休息` / `站起来`） |
    | `mirror_on` | 当前动作的镜像是不是开着（决定勾选态） |
    | `can_mirror` | 当前动作在不在镜像适用范围内 |
    | `can_rest` | 素材里有没有 `rest`（缺动作时那一项点了也没用，灰掉） |
    | `resize_available` / `settings_available` / `visibility_available` | 那三项接上了没有（各自看"装没装处理器"） |
    | `visibility_reason` | `隐藏宠物` 不可用时的原因（挂到提示条上）；可用时是空串 |

    `mirror_on` 与 `can_mirror` **分开**：适用范围外"没有勾选"与"勾选了但无效"
    是两件事，后者会让用户以为点了没反应。

    `visibility_reason` **只在不可用时才有内容**：可用时挂一句"为什么不能藏"
    只会让用户以为出了什么事。灰色项本身不给提示条（Qt 不给禁用项发
    `helpEvent`），所以这句话由调用方在别处显示（见 `pet_window` 的用法）。
    """

    action: str
    manual_rest: bool = False
    mirror_on: bool = False
    can_mirror: bool = False
    can_rest: bool = True
    resize_available: bool = False
    settings_available: bool = False
    visibility_available: bool = False
    visibility_reason: str = ""

    def action_text(self) -> str:
        """`action` 项当前该显示的文字。"""
        return TEXT_STAND_UP if self.manual_rest else TEXT_REST


def action_id(action: QAction | None) -> str:
    """取一个 `QAction` 的**稳定 id**（`setData()` 承载）。

    没有 id（不是我们造的项 / 忘了设）→ 返回空串。
    **不要**改成"按文字反查"：文字是可以改的，id 不是（task06 §4）。
    """
    if action is None:
        return ""
    data = action.data()
    return data if isinstance(data, str) else ""


def build_menu(parent: QWidget, state: MenuState) -> QMenu:
    """按 `state` 现造一个菜单（**每次弹出前调用一次**）。

    勾选态 / 禁用态都在这里定死，返回的菜单**不保留任何跨次弹出要维护的状态**：
    下次弹出再调一遍本函数即可（"每次弹出前重建"）。
    """
    menu = QMenu(parent)

    for item_id in MENU_ORDER:
        if item_id == SEPARATOR:
            menu.addSeparator()
            continue
        action = menu.addAction(_text_for(item_id, state))
        action.setData(item_id)
        _apply_state(action, item_id, state)

    return menu


def _text_for(item_id: str, state: MenuState) -> str:
    """菜单项文字（`坐下休息` / `站起来` 是同一个 id 的两种说法）。"""
    if item_id == ID_RESIZE:
        return TEXT_RESIZE
    if item_id == ID_ACTION:
        return state.action_text()
    if item_id == ID_MIRROR:
        return TEXT_MIRROR
    if item_id == ID_SETTINGS:
        return TEXT_SETTINGS
    if item_id == ID_VISIBILITY:
        return TEXT_VISIBILITY
    if item_id == ID_QUIT:
        return TEXT_QUIT
    return item_id  # pragma: no cover - MENU_ORDER 是常量，到不了


def _apply_state(action: QAction, item_id: str, state: MenuState) -> None:
    """勾选态与可用性（**唯一**决定"灰不灰、勾不勾"的地方）。"""
    if item_id == ID_MIRROR:
        # 适用范围外 → 灰色不可点（task06 §2 第 8 条）。
        action.setEnabled(state.can_mirror)
        action.setCheckable(True)
        action.setChecked(bool(state.mirror_on) and state.can_mirror)
        return

    if item_id == ID_ACTION:
        # 素材里连 rest 都没有时，这一项点了也没用 → 灰掉
        # （"有几个动作就是几个"，缺动作按可恢复路径退化，agent.md §2.5）。
        action.setEnabled(state.can_rest or state.manual_rest)
        return

    if item_id == ID_QUIT:
        return  # 退出永远是通的

    # 其余三项由"接上了没有"决定（本 task 恒为 False → 灰色不可点）。
    action.setEnabled(_availability(item_id, state))


def _availability(item_id: str, state: MenuState) -> bool:
    """`调整大小…` / `设置…` / `隐藏宠物` 各自接上了没有。

    三项都是**装没装处理器**（task09 / task11 / task12）。
    ⚠ `隐藏宠物` 还多一个条件：**必须有可用的呼出快捷键**——没有热键时
    藏起来就叫不回来，所以那种情况下即使处理器装了也要灰掉
    （`visibility_available` 在 `pet_window.menu_state()` 里已经把两件事与在一起）。
    """
    if item_id == ID_RESIZE:
        return state.resize_available
    if item_id == ID_SETTINGS:
        return state.settings_available
    if item_id == ID_VISIBILITY:
        return state.visibility_available
    return False  # pragma: no cover - 调用方只会传上面这三个
