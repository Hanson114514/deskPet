"""快捷键录入控件：点一下进入录入态，按一次组合就录下来（task11 建骨架、task12 接线）。

## 录入的三条硬约定（task11 §4 / agent.md §4.9）

1. **判定以"实测按下的按键"为准，不看 `event.modifiers()`**：
   中文键盘布局下 AltGr 会被报成 Alt（§4.9），拿 `modifiers()` 判断会把
   `Ctrl+Alt` 认成别的东西。本类因此只在 `keyPressEvent` 里看 `event.key()`。
2. **修饰键单独按下不算一次录入**：只按 `Ctrl` 不松手时，界面显示
   "已按下的修饰键 + `…`"，等真正的主键到来才成立。
3. **至少一个修饰键**：`A` 单独按下不成组合，`Ctrl+A` 才成。
   Esc 是**取消**（不是"录成 Esc"）。

## 主键名字的真值在 `hotkey_spec`

本类**不自己拼字母表**（拼表会在小键盘、功能键上出错），而是问
`hotkey_spec.format_qt_key()`。这样"录入时显示成什么"与"注册时用什么"
走的是**同一张表**——两处各有一张表就迟早对不上。

⚠ 本类**不碰配置**：它只负责"录到一个说法"。写配置是调用方（设置窗口）的事
——那样"写失败回滚"只有一条路径（task11 §4"勾选失败回滚"）。
"""

import logging
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QWidget

from . import hotkey_spec

logger = logging.getLogger(__name__)

#: 展示顺序**固定**为 `Ctrl → Alt → Shift → Win`（task11 §4）。
#: 这不是"按用户按下的次序"——同一组合每次都要长得一样，用户才比得出来。
MODIFIER_ORDER: Final[tuple[str, ...]] = hotkey_spec.MODIFIER_ORDER

#: 各修饰键的 Qt 键值 → 展示名。**真值只在这里一处**（QKeySequence 的
#: 字符串形式会随平台变化，不能拿来当判据）。
MODIFIER_KEYS: Final[dict[int, str]] = {
    int(Qt.Key_Control): "Ctrl",
    int(Qt.Key_Alt): "Alt",
    int(Qt.Key_Shift): "Shift",
    # Win 键：Qt6 里 `Qt.Key_Meta` 就是 Windows 键（`Key_Super_L` 是它的别名）。
    int(Qt.Key_Meta): "Win",
}

#: 录入态的提示文字（末尾的 `…` 表示"正在等你按"）。
LISTENING_SUFFIX: Final[str] = "…"

#: 只按了修饰键、还没有主键时的占位。
NO_MAIN_KEY: Final[str] = ""

#: 没设过快捷键时按钮上的文字。
UNSET_TEXT: Final[str] = "未设置"

#: 键位录入态的按钮文字（task19 §4.3 逐字：按钮文字变 `按下按键…`）。
#: 与组合录入的 `LISTENING_SUFFIX` **故意不同**——两处的措辞各自是契约的一部分。
LISTENING: Final[str] = "按下按键…"

# ------------------------------------------------------------- 单键录入（task19）

#: 取不到可读名字时的兜底（**绝不给空串**：按钮上总得有点东西）。
UNNAMED_KEY: Final[str] = "按键"

#: Qt 的鼠标键 → **虚拟键码 + 展示名**（task20：鼠标五键当成普通键位录）。
#:
#: ⚠ 鼠标**不产生 `QKeyEvent`**，走的是 `mousePressEvent`；所以这张表按
#: `Qt.MouseButton` 建，与键盘那条路分开查。真值的另一半（VK ↔ 名字）在
#: `hotkey_spec._VK_BY_KEY`——两处对不上就会"录上了却不亮"，有模块级断言守着。
MOUSE_BUTTONS: Final[dict[int, tuple[int, str]]] = {
    int(Qt.LeftButton.value): (0x01, "MouseLeft"),
    int(Qt.RightButton.value): (0x02, "MouseRight"),
    int(Qt.MiddleButton.value): (0x04, "MouseMiddle"),
    int(Qt.BackButton.value): (0x05, "MouseX1"),
    int(Qt.ForwardButton.value): (0x06, "MouseX2"),
}

#: 修饰键的**虚拟键码**（Win32 说法：`VK_SHIFT` / `VK_CONTROL` / `VK_MENU` / 左右 Win）。
#:
#: ⚠ 与上面那份 Qt 键值表管的是**同一件事的两个名字空间**（`event.key()` vs
#: `nativeVirtualKey()`），两套**不是同一个数字**：实测 Qt 的 `Key_Control` 是
#: `0x1000021`，而 `VK_CONTROL` 是 `0x11`。
#:
#: ⚠ task20 起**单键录入态下它们不再被排除**（修饰键本身就是合法键位）；
#: 这张表现在只剩"两条路互相对得上"的断言在用（见文件末尾）。
MODIFIER_NATIVE_VKS: Final[frozenset[int]] = frozenset(
    {
        0x10,  # VK_SHIFT
        0x11,  # VK_CONTROL
        0x12,  # VK_MENU（Alt）
        0x5B,  # VK_LWIN
        0x5C,  # VK_RWIN
    }
)

#: Qt 那一侧的修饰键键值（从 `MODIFIER_KEYS` 推出来，**不另写一份**）。
MODIFIER_QT_KEYS: Final[frozenset[int]] = frozenset(int(value) for value in MODIFIER_KEYS)

#: 修饰键的**名字** → Win32 VK（只为下面那条模块级断言服务）。
_MODIFIER_VK_BY_NAME: Final[dict[str, int]] = {
    "Ctrl": 0x11,
    "Shift": 0x10,
    "Alt": 0x12,
}


def native_virtual_key(event) -> int:  # noqa: ANN001 - Qt 事件对象
    """从一次按键事件里取**虚拟键码**；取不到返回 0。

    ⚠ **不手写 VK 表**（task19 §5 明令）：`nativeVirtualKey()` 给什么就是什么。
    拿不到时（非 Windows / 合成事件）退到 `event.key()`，再退到 0——
    调用方见 0 就当"这一下不算数"。
    """
    raw = getattr(event, "nativeVirtualKey", None)
    if callable(raw):
        try:
            value = int(raw())
        except (TypeError, ValueError):  # pragma: no cover - Qt 不该给非数字
            value = 0
        if value:
            return value
    try:
        return int(event.key())
    except (TypeError, ValueError):  # pragma: no cover
        return 0


def key_name(event) -> str:  # noqa: ANN001 - Qt 事件对象
    """按键的**可读名字**（`J` / `Space` / `Num5`），只用于显示。

    复用 `hotkey_spec.format_qt_key()`：那是"Qt 键值 → 名字"的唯一一张表
    （本模块开头说明了这条分工）。给不出名字时退回 `按键<vk>`，**绝不给空串**。

    ⚠ 名字**不参与判定**：判定只看 VK。名字进配置只是"下次打开能显示回来"。
    """
    try:
        name = hotkey_spec.format_qt_key(int(event.key()))
    except (TypeError, ValueError):  # pragma: no cover
        name = None
    if name:
        return name
    return f"{UNNAMED_KEY}{native_virtual_key(event)}"


def modifiers_text(names: list[str] | tuple[str, ...]) -> str:
    """把已按下的修饰键按**固定顺序**拼成 `Ctrl+Alt` 这样的文字。

    传入的顺序无所谓（按下次序不该影响展示），这里按 `MODIFIER_ORDER` 重排。
    """
    ordered = [name for name in MODIFIER_ORDER if name in set(names)]
    return "+".join(ordered)


class SingleKeyCaptureButton(QPushButton):
    """**单键**录入按钮（task19）：点一下 → 按键盘上的任意一个键 → 就绑上了。

    | 信号 | 含义 |
    | --- | --- |
    | `captured(vk, name)` | 录到了一个键（VK + 可读名字） |
    | `cancelled()` | 用户按 Esc 取消（**保留原值**） |

    ## ⚠ 为什么不是复用 `KeyCaptureEdit`（task19 §1.2 明写的那一条）

    `KeyCaptureEdit` 是**组合**录入：它的三条硬约定（至少一个修饰键 / 只有修饰键时
    不成立 / 吐的是 `Ctrl+Alt+P` 这种**文字**）与键位录入的要求正好相反——
    键位要的恰恰是 `F`、`J` 这种**没有修饰键**的单个键，而且要的是 **VK** 不是文字。
    硬把两条路塞进一个类就会出现"录单键时先凑一个假修饰键"这类畸形分支。
    两者共用的只有**交互手感**（点一下 → 等按键 → Esc 取消），那不是代码，是约定。

    ## 判定用的两套键值

    - **身份**：`nativeVirtualKey()`（Win32 的 VK），配置里存的就是它；
    - **可读名字**：`hotkey_spec.format_qt_key(event.key())`（唯一那张表）。

    两套都只从这里取，**不手写 VK 表**（task19 §5）。

    ⚠ **本类不碰配置、不碰监听器**：它只负责"录到一个键"。写配置与挂起监听
    都是调用方（`keygrid.KeyGrid` / 设置窗口）的事——与 `KeyCaptureEdit` 同一条纪律。
    """

    captured = Signal(int, str)
    cancelled = Signal()
    #: **进了录入态**（task20 补）。
    #:
    #: ⚠ 为什么需要它：按钮的 `clicked` 直接连着 `begin_capture()`，于是"点一下"
    #: **绕过了** `KeyGrid.begin_capture()` —— 网格根本不知道有按钮进了录入态。
    #: 症状：录入中途点另一个键位，上一个的标签**卡在 `按下按键…`**
    #: （网格的 `_capturing` 还是空的，没人去收尾它）。
    #: 有了这条信号，**谁触发的不重要**，网格都收得到。
    capture_started = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._listening = False
        self._text = UNSET_TEXT
        self.clicked.connect(self.begin_capture)
        self._apply_text()

    # ------------------------------------------------------------ 只读

    @property
    def listening(self) -> bool:
        """是不是正在等按键（诊断与自检用）。"""
        return self._listening

    @property
    def display_text(self) -> str:
        """按钮上现在显示的文字（**别叫 `text()`**，会与 Qt 撞名）。"""
        return self.text()

    # ------------------------------------------------------------ 显示

    def set_key_text(self, name: str) -> None:
        """把"这个键位现在绑的是谁"摆上去（空串 = 未设置）。**只改显示。**"""
        self._text = str(name or "") or UNSET_TEXT
        if not self._listening:
            self._apply_text()

    # ------------------------------------------------------------ 录入

    def begin_capture(self) -> None:
        """进入录入态：抢焦点、显示 `按下按键…`，并发 `capture_started`。

        ⚠ 信号**必须在这里发**，不能只在 `KeyGrid.begin_capture()` 里发：
        按钮的 `clicked` 直接连的就是本方法（见 `__init__`），用户**点按钮**
        进来的这条路根本不经过网格。不发信号的话网格永远不知道有人进了
        录入态 → 上一个卡在 `按下按键…` 收不掉（task20 修的 bug）。
        """
        if not self.isEnabled() or self._listening:
            return
        self._listening = True
        self.setFocus(Qt.OtherFocusReason)
        self._apply_text()
        self.capture_started.emit()

    def cancel_capture(self) -> None:
        """退出录入态（Esc / 窗口关闭）：**保留原值**。"""
        if not self._listening:
            return
        self._listening = False
        self._apply_text()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """录入态的**鼠标**处理：左/右/中/侧键都能当一个键位（task20）。

        ## 为什么必须重写它

        鼠标**不产生 `QKeyEvent`**，所以只重写 `keyPressEvent` 时，录入态下点
        鼠标走的是"普通按钮点击"——不但录不上，还会**再次触发 `clicked`**
        （用户 2026 实测："鼠标键位都还不能录入"）。

        ## 录入态下鼠标事件**全部吃掉**（不 `super()`）

        四条后果都是想要的：① 不再触发 `clicked`（不会自己点自己）；
        ② 不把点击透给网格（不会顺带切到别的键位）；③ 录完就收尾；
        ④ 右键**不是**上下文菜单，而是"录 MouseRight"。

        ⚠ 只认**按下**（本就只有 `MouseButtonPress` 会走到这里）：否则录完一次
        之后紧接着的抬起又会被当成一次输入。
        """
        if not self._listening:
            super().mousePressEvent(event)
            return

        mapped = MOUSE_BUTTONS.get(int(event.button().value))
        if mapped is None:
            # 认不出的鼠标键（某些驱动报的第 6 个键）：不算一次录入，继续等。
            event.accept()
            return

        vk, name = mapped
        self._finish_capture(vk, name)
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """录入态的按键处理。

        | 按下的是 | 结果 |
        | --- | --- |
        | Esc | **取消**，保留原值（不是"绑成 Esc"） |
        | 其它任意键（字母 / 空格 / 方向键 / **修饰键本身**） | 录下来 |

        ## ⚠ 修饰键**单独按下也算**（task20 改；与组合录入正好相反）

        键位映射要的是"这个格子对应哪个物理键"，而 `Ctrl` / `Shift` 本身就是
        合法的物理键（音游里常有人把 `Ctrl` 当键位用）。第一版照抄了组合录入
        的"单独按修饰键无效"，于是**这四个键根本录不进去**——用户 2026 实测
        反馈后改掉。

        ⚠ **组合录入（`KeyCaptureEdit`）的规矩不变**：热键那边仍然要求
        "至少一个修饰键 + 恰好一个主键"，单独按修饰键依然不成立。两条路
        的要求本来就不同，不要因为这里改了就去动那边（见模块开头第 2 条）。
        """
        if not self._listening:
            super().keyPressEvent(event)
            return

        if int(event.key()) == int(Qt.Key_Escape):
            self.cancel_capture()
            self.cancelled.emit()
            event.accept()
            return

        vk = native_virtual_key(event)
        if not vk:
            # 连 VK 都取不到（非 Windows / 合成事件）：这一下不算数，继续等。
            event.accept()
            return

        self._finish_capture(vk, key_name(event))
        event.accept()

    def _finish_capture(self, vk: int, name: str) -> None:
        """录到了一个键（键盘与鼠标**共用这一条收尾**）。

        两件事必须一起做，顺序也有意义：先退出录入态 + 改文字（用户当场看到
        结果），再发 `captured`（调用方据此写配置，可能慢、可能弹提示）。
        """
        self._listening = False
        self._text = name
        self._apply_text()
        self.captured.emit(int(vk), str(name))

    # ------------------------------------------------------------ 内部

    def _apply_text(self) -> None:
        """按当前状态刷新按钮文字（**唯一**改文字的地方）。"""
        self.setText(LISTENING if self._listening else (self._text or UNSET_TEXT))


class KeyCaptureEdit(QWidget):
    """一个按钮：点一下进入录入态，再按一次组合就录下来。

    | 信号 | 含义 |
    | --- | --- |
    | `captured(text)` | 录到了一个**合法组合**（`Ctrl+Alt+P` 这种文字） |
    | `cancelled()` | 用户按 Esc 取消（**保留原快捷键**） |
    | `rejected(reason)` | 按键不成组合（只有修饰键 / 没有修饰键）——给一句话，**保留原值** |

    ⚠ **本类不碰配置**：它只负责"录到一个说法"。写配置是调用方（设置窗口）
    的事——那样"写失败回滚"只有一条路径（task11 §4"勾选失败回滚"）。

    ⚠ 键位（单键）录入**不要**复用本类：走 `SingleKeyCaptureButton`（见那儿的说明）。
    """

    captured = Signal(str)
    cancelled = Signal()
    rejected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._button = QPushButton(self)
        self._button.clicked.connect(self.begin_capture)
        self._listening = False
        self._pressed: list[str] = []
        #: 控件本身**不持有"当前快捷键"**：真值在配置里，这里只是显示。
        self._text = ""
        self.setFocusProxy(self._button)
        self._apply_button_text()

    # ------------------------------------------------------------ 只读

    @property
    def listening(self) -> bool:
        """是不是正在等按键（诊断与自检用）。"""
        return self._listening

    @property
    def display_text(self) -> str:
        """按钮上现在显示的文字。

        ⚠ **不要**叫 `text()`：`QWidget` 上已经有别的同名成员，
        覆盖它会在调 Qt 自己的代码时炸（本类第一版就叫 `text`，实测
        `TypeError: 'str' object is not callable`）。
        """
        return self._button.text()

    # ------------------------------------------------------------ 显示

    def set_shortcut_text(self, text: str) -> None:
        """把"当前快捷键"摆上去（空串 = 还没设过）。**只改显示，不写配置。**"""
        self._text = str(text or "")
        if not self._listening:
            self._apply_button_text()

    def set_read_only(self, read_only: bool) -> None:
        """把控件设成"只能看不能改"（本 task 的禁用形态）。

        与 `setEnabled(False)` 的区别：禁用连灰都灰得更彻底（子控件一起失效，
        也没法聚焦）。这里让**按钮本身**不可点，但文字照常可读。
        """
        self._button.setEnabled(not read_only)

    # ------------------------------------------------------------ 录入

    def begin_capture(self) -> None:
        """进入录入态：清空已按下的修饰键，抢焦点，显示 `…`。"""
        if not self._button.isEnabled():
            return
        self._listening = True
        self._pressed = []
        self._button.setFocus(Qt.OtherFocusReason)
        self._apply_button_text()

    def cancel_capture(self) -> None:
        """退出录入态（Esc / 失去焦点）：**保留原快捷键**。"""
        if not self._listening:
            return
        self._listening = False
        self._pressed = []
        self._apply_button_text()

    def reject_capture(self, reason: str) -> None:
        """录入态下**被拒**（组合不合法 / 被别的程序占用）：退出录入、保留原值。

        `captured` 只在"录到一个能用的组合"时发；被拒是另一条路径，
        所以这里单独开一个方法给调用方（设置窗口）用——**由它决定说什么**
        （task12 §6：原值必须保留、原因必须说出来）。
        """
        self.cancel_capture()
        logger.info("快捷键录入被拒：%s", reason)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """录入态的按键处理。

        **只看 `event.key()`，不看 `event.modifiers()`**（§3.4：AltGr 报成 Alt，
        无条件剥 Alt 又是错的——默认组合 `Ctrl+Alt+Shift+P` 本身就含 Alt）。
        """
        if not self._listening:
            super().keyPressEvent(event)
            return

        key = int(event.key())
        if key == int(Qt.Key_Escape):
            self.cancel_capture()
            self.cancelled.emit()
            event.accept()
            return

        name = MODIFIER_KEYS.get(key)
        if name is not None:
            # 修饰键**单独按下不算一次录入**：只把它记进"已按下"并刷新提示。
            if name not in self._pressed:
                self._pressed.append(name)
            self._apply_button_text()
            event.accept()
            return

        if not self._pressed:
            # 没有修饰键 → 不成组合（task11 §4："至少一个修饰键"）。
            reason = "快捷键必须包含 Ctrl / Alt / Shift / Win 之一"
            self.rejected.emit(reason)
            event.accept()
            return

        main_key = hotkey_spec.format_qt_key(key)
        if main_key is None:
            self.rejected.emit("这个键不能当快捷键的主键")
            event.accept()
            return

        text = f"{modifiers_text(self._pressed)}+{main_key}"
        self._listening = False
        self._pressed = []
        self._text = text
        self._apply_button_text()
        self.captured.emit(text)
        event.accept()

    # ------------------------------------------------------------ 内部

    def _apply_button_text(self) -> None:
        """按当前状态刷新按钮文字（**唯一**改文字的地方）。"""
        if self._listening:
            prefix = modifiers_text(self._pressed)
            self._button.setText(f"{prefix}{LISTENING_SUFFIX}" if prefix else LISTENING_SUFFIX)
            return
        self._button.setText(self._text or UNSET_TEXT)


# ------------------------------------------------------------------ 模块级断言

# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。

# 两套修饰键表**说的是同一件事**（`QKeyEvent.key()` 与 `nativeVirtualKey()`），
# 必须互相对得上：改了 `MODIFIER_KEYS` 忘了 `MODIFIER_NATIVE_VKS` 的表现是
# "单键录入时单独按 Ctrl 也被当成一次绑定"——只在真机上、只在有人按 Ctrl 时才出现的错。
#
# ⚠ 这两套**不是同一个数字**：Qt 的 `Key_Control` 实测是 `0x1000021`（Qt 自己的
# 键空间），Windows 的 `VK_CONTROL` 是 `0x11`。所以这里**不能**直接比数值，
# 只能比"名字都齐"与"该有 VK 的都真的有"。
for _name, _vk in _MODIFIER_VK_BY_NAME.items():  # pragma: no cover
    if _name not in set(MODIFIER_KEYS.values()):
        raise RuntimeError(f"MODIFIER_KEYS 里没有 {_name}")
    if _vk not in MODIFIER_NATIVE_VKS:
        raise RuntimeError(f"{_name} 的 VK {_vk:#x} 不在 MODIFIER_NATIVE_VKS 里")
del _name, _vk
