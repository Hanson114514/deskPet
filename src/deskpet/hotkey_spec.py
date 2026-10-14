"""快捷键的**说法**：`Ctrl+Alt+Shift+P` ↔ (修饰键掩码, 主键)。

## 这里只处理"文字"，不碰系统

注册（`RegisterHotKey`）在 `hotkey.HotkeyManager` 里；本模块只回答
"这个说法合法吗""它的掩码是多少""这两个掩码怎么写回文字"。

## 两套修饰键掩码**数值不同**（task12 §4 的表格，agent.md §4.9）

| 用途 | Shift | Ctrl | Alt | Win |
| --- | --- | --- | --- | --- |
| **Qt 事件的状态位** | `0x0001` | `0x0004` | `0x0008` | — |
| **系统热键注册** | `0x0004` | `0x0002` | `0x0001` | `0x0008` |

所以 `Ctrl+Alt+Shift` 在**系统热键**里是 `0x7`（2|1|4），**不是** `0xF`；
而 `0x0004` 在**事件状态位**里是 Control，不是 Shift。

⚠ 转换只允许 `hotkey.combo_to_win_mods()` 一处（Qt 掩码 → 系统掩码），
本模块里的 `MOD_*` 是**系统掩码**的唯一真值，别处不许再写一遍数字。

## 为什么不用 `QKeySequence` 解析

`QKeySequence.fromString()` 对**中文/本地化**的写法（`Ctrl+Alt+Shift+P` 是
英文惯例）宽容度不固定，而且它给出的是 Qt 的掩码（`0x0C000000` 那一套
`Qt::ControlModifier`），与系统掩码还得再转一次——中间多一层就多一处会分叉。
说法是我们自己的对外契约（`config.hotkey` 里存的就是这个字符串），
所以**解析权也归我们**：只认下面这张表，解析失败就是失败。
"""

import logging
from typing import Final

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 系统掩码（唯一真值）

MOD_ALT: Final[int] = 0x0001
MOD_CONTROL: Final[int] = 0x0002
MOD_SHIFT: Final[int] = 0x0004
MOD_WIN: Final[int] = 0x0008

#: 修饰键的**展示顺序**：固定 `Ctrl → Alt → Shift → Win`（task12 §4）。
MODIFIER_ORDER: Final[tuple[str, ...]] = ("Ctrl", "Alt", "Shift", "Win")

#: 展示名 → 系统掩码。
MOD_BY_NAME: Final[dict[str, int]] = {
    "Ctrl": MOD_CONTROL,
    "Alt": MOD_ALT,
    "Shift": MOD_SHIFT,
    "Win": MOD_WIN,
}

#: 大小写不敏感（用户手写 `config.json` 时不该被大小写绊倒），
#: 合并起来就是"名字 → 规范写法"的映射。
_NAME_LOOKUP: Final[dict[str, str]] = {
    name.lower(): name for name in MODIFIER_ORDER
}

#: `Ctrl` 的三种常见写法都收（配置文件是人写的，手滑很正常）。
_ALIASES: Final[dict[str, str]] = {
    "control": "Ctrl",
    "ctl": "Ctrl",
    "win": "Win",
    "windows": "Win",
    "meta": "Win",
    "super": "Win",
}

#: 主键的规范写法真值（**手写的表**，见模块开头的理由）。
#:
#: 值是 `(供注册的虚拟键码, 展示文字)`。展示文字与键名的对应关系
#: 与 Windows 上的惯例一致：`A`–`Z`、`0`–`9`、`F1`–`F12`。
_VK_BY_KEY: Final[dict[str, tuple[int, str]]] = {}
for _letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
    _VK_BY_KEY[_letter] = (ord(_letter), _letter)
for _digit in "0123456789":
    _VK_BY_KEY[_digit] = (ord(_digit), _digit)
for _number in range(1, 13):
    _VK_BY_KEY[f"F{_number}"] = (0x6F + _number, f"F{_number}")  # VK_F1 = 0x70

#: 别名（大小写不敏感）：`Escape` 与 `Esc` 是同一个键。
_VK_ALIASES: Final[dict[str, str]] = {"escape": "Esc", "del": "Delete"}
_VK_BY_KEY["Esc"] = (0x1B, "Esc")
_VK_BY_KEY["Space"] = (0x20, "Space")
_VK_BY_KEY["Tab"] = (0x09, "Tab")
_VK_BY_KEY["Enter"] = (0x0D, "Enter")
_VK_BY_KEY["Backspace"] = (0x08, "Backspace")
_VK_BY_KEY["Delete"] = (0x2E, "Delete")
_VK_BY_KEY["Insert"] = (0x2D, "Insert")
_VK_BY_KEY["Home"] = (0x24, "Home")
_VK_BY_KEY["End"] = (0x23, "End")
_VK_BY_KEY["PageUp"] = (0x21, "PageUp")
_VK_BY_KEY["PageDown"] = (0x22, "PageDown")
_VK_BY_KEY["Left"] = (0x25, "Left")
_VK_BY_KEY["Up"] = (0x26, "Up")
_VK_BY_KEY["Right"] = (0x27, "Right")
_VK_BY_KEY["Down"] = (0x28, "Down")

#: 展示文字 → 虚拟键码（注册用）。同一个键的多种写法都在这里收口。
_VK_BY_NAME: Final[dict[str, int]] = {}
for _key_name, (_vk, _shown) in _VK_BY_KEY.items():
    _VK_BY_NAME[_key_name.lower()] = _vk
    _VK_BY_NAME[_shown.lower()] = _vk

#: 虚拟键码 → 展示文字（把 `keycapture` 录到的 Qt 键码翻译回来时用）。
_SHOWN_BY_VK: Final[dict[int, str]] = {}
for _vk, _shown in _VK_BY_KEY.values():
    _SHOWN_BY_VK.setdefault(_vk, _shown)

#: 用户的 Qt 键码不是虚拟键码，但**字母/数字/功能键/常用键**在两边数值相同
#: （Qt 的 `Key_A == 0x41 == VK_A`）。下面这张表补上**数值不同**的那些键。
_QT_KEY_ALIASES: Final[dict[int, str]] = {
    0x01000000: "Esc",      # Qt.Key_Escape
    0x01000001: "Tab",
    0x01000003: "Backspace",
    0x01000004: "Enter",    # Qt.Key_Return
    0x01000005: "Enter",    # Qt.Key_Enter（小键盘）
    0x01000007: "Delete",
    0x01000006: "Insert",
    0x01000010: "Home",
    0x01000011: "End",
    0x01000016: "PageUp",
    0x01000017: "PageDown",
    0x01000013: "Up",       # Qt.Key_Up
    0x01000015: "Down",
    0x01000012: "Left",
    0x01000014: "Right",
    0x01000020: "Space",    # Qt.Key_Space
}

#: 非法说法的统一报错文字（task12 §4 逐字）。
REASON_NEED_MODIFIER: Final[str] = "快捷键必须包含 Ctrl / Alt / Shift / Win 之一"

#: 主键不合法时的报错文字。
REASON_BAD_KEY: Final[str] = "快捷键要有且只有一个主键（例如 A / F12 / Space）"

#: 被占用的报错文字（task12 §4 逐字，错误码是系统给的）。
REASON_OCCUPIED: Final[str] = "快捷键已被其他程序占用（错误 1409）"


class ComboError(ValueError):
    """一个说法不能被解析成合法的快捷键（带**用户可读**的原因）。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def parse_combo(text: str) -> tuple[int, int]:
    """`"Ctrl+Alt+Shift+P"` → `(系统修饰键掩码, 主键虚拟键码)`。

    | 规则 | 约定 |
    | --- | --- |
    | 必须含修饰键 | 裸键（`A` / `Space`）不能做热键（`REASON_NEED_MODIFIER`） |
    | 只能有一个主键 | 两个主键 → 非法（`REASON_BAD_KEY`） |
    | 大小写不敏感 | `ctrl+alt+p` 与 `Ctrl+Alt+P` 等价 |
    | 展示顺序 | 解析结果与写法无关，`format_combo()` 会重排成固定顺序 |

    不合法时抛 `ComboError`（**带原因**），调用方据此报错——
    配置文件里的坏值仍按"读取永不致命"处理（见 `hotkey.HotkeyManager.register`）。
    """
    if not isinstance(text, str) or not text.strip():
        raise ComboError(REASON_NEED_MODIFIER)

    mods = 0
    keys: list[int] = []
    for raw_part in text.split("+"):
        part = raw_part.strip()
        if not part:
            # `Ctrl++A` / 结尾多一个 `+` —— 空段直接判非法，不猜。
            raise ComboError(REASON_BAD_KEY)
        name = _ALIASES.get(part.lower(), _NAME_LOOKUP.get(part.lower()))
        if name is not None:
            mods |= MOD_BY_NAME[name]
            continue
        vk = _VK_BY_NAME.get(part.lower())
        if vk is None:
            raise ComboError(f"认不出这个键：{part}")
        keys.append(vk)

    if mods == 0:
        raise ComboError(REASON_NEED_MODIFIER)
    if len(keys) != 1:
        raise ComboError(REASON_BAD_KEY)
    return mods, keys[0]


def format_combo(mods: int, key: int) -> str:
    """`(掩码, 主键)` → `"Ctrl+Alt+P"`（顺序固定 `Ctrl → Alt → Shift → Win`）。

    认不出的主键退化成 `"key=0xNN"`：**展示可以退化，注册不许**——
    调用方拿到的字符串只用于显示与写配置。
    """
    names = [name for name in MODIFIER_ORDER if mods & MOD_BY_NAME[name]]
    shown = _SHOWN_BY_VK.get(int(key), f"key=0x{int(key):02X}")
    return "+".join([*names, shown])


def format_qt_key(qt_key: int) -> str | None:
    """Qt 的 `event.key()` → 主键展示文字；不是主键（未知键）返回 `None`。

    这是"录入控件看到的键"与"注册要用的键"之间**唯一**的翻译口
    （`keycapture` 只调它，不自己拼字母表——拼表会在小键盘、功能键上出错）。
    """
    key = int(qt_key)
    alias = _QT_KEY_ALIASES.get(key)
    if alias is not None:
        return alias
    shown = _SHOWN_BY_VK.get(key)
    if shown is not None:
        return shown
    return None


def qt_key_to_vk(qt_key: int) -> int | None:
    """Qt 的 `event.key()` → 虚拟键码；认不出返回 `None`。"""
    shown = format_qt_key(qt_key)
    if shown is None:
        return None
    return _VK_BY_NAME.get(shown.lower())


def win_mods_to_names(mods: int) -> list[str]:
    """系统掩码 → 修饰键**名字**（`["Ctrl", "Alt"]`，顺序固定）。

    刻意返回名字而不是 Qt 的数字掩码：两套掩码的数值不同正是本 task 的
    头号坑（agent.md §4.9），回到"名字"这一层就没有数字可以混。
    """
    return [name for name in MODIFIER_ORDER if mods & MOD_BY_NAME[name]]
