"""快捷键录入控件：**本 task 只建骨架**（task12 接线，task11 §3）。

## 为什么现在就要有它

task11 的设置窗口里 `呼出快捷键` 那一行**必须是灰色不可点**、标题带 `（暂未开放）`
——控件本身照常出现在界面上（"未实现的项不许隐藏掉"，task11 §5）。
把控件造出来、把禁用态与展示格式定死，task12 就只需要"解锁 + 接上注册"。

## 录入的三条硬约定（task11 §4 / agent.md §4.9）

1. **判定以"实测按下的按键"为准，不看 `event.modifiers()`**：
   中文键盘布局下 AltGr 会被报成 Alt（§3.4），拿 `modifiers()` 判断会把
   `Ctrl+Alt` 认成别的东西。本类因此只在 `keyPressEvent` 里看 `event.key()`。
2. **修饰键单独按下不算一次录入**：只按 `Ctrl` 不松手时，界面显示
   "已按下的修饰键 + `…`"，等真正的主键到来才成立。
3. **至少一个修饰键**：`A` 单独按下不成组合，`Ctrl+A` 才成。
   Esc 是**取消**（不是"录成 Esc"）。

`task11` 阶段这些行为**不会真的发生**——控件是禁用的，收不到按键。
所以实现刻意做成"最小但正确"：task12 的活只剩"把这行解锁 + 接上热键注册"。
"""

import logging
from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QPushButton, QWidget

logger = logging.getLogger(__name__)

#: 展示顺序**固定**为 `Ctrl → Alt → Shift → Win`（task11 §4）。
#: 这不是"按用户按下的次序"——同一组合每次都要长得一样，用户才比得出来。
MODIFIER_ORDER: Final[tuple[str, ...]] = ("Ctrl", "Alt", "Shift", "Win")

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


def modifiers_text(names: list[str] | tuple[str, ...]) -> str:
    """把已按下的修饰键按**固定顺序**拼成 `Ctrl+Alt` 这样的文字。

    传入的顺序无所谓（按下次序不该影响展示），这里按 `MODIFIER_ORDER` 重排。
    """
    ordered = [name for name in MODIFIER_ORDER if name in set(names)]
    return "+".join(ordered)


class KeyCaptureEdit(QWidget):
    """一个按钮：点一下进入录入态，再按一次组合就录下来。

    | 信号 | 含义 |
    | --- | --- |
    | `captured(text)` | 录到了一个**合法组合**（`Ctrl+Alt+P` 这种文字） |
    | `cancelled()` | 用户按 Esc 取消（**保留原快捷键**） |
    | `rejected(reason)` | 按键不成组合（只有修饰键 / 没有修饰键）——给一句话，**保留原值** |

    ⚠ **本类不碰配置**：它只负责"录到一个说法"。写配置是调用方（设置窗口）
    的事——那样"写失败回滚"只有一条路径（task11 §4"勾选失败回滚"）。
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

        main_key = _key_text(key)
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
        self._button.setText(self._text or "未设置")


def _key_text(key: int) -> str | None:
    """主键的展示文字；判不出来（纯修饰键 / 未知键）返回 `None`。

    `QKeySequence.toString()` 在 Windows 上给的就是 `Ctrl+Alt+P` 这种写法，
    与配置里 `hotkey` 的既有格式一致（`"Ctrl+Alt+Shift+P"`，agent.md §2.4），
    所以这里**用它而不是自己拼一张字母表**：拼表会在小键盘、功能键上出错。
    """
    from PySide6.QtGui import QKeySequence

    text = QKeySequence(key).toString()
    return text or None
