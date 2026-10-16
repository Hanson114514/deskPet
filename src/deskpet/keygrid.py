"""键位网格：按**布局的 `rows`** 摆一组按钮，点一个就能录一个键（task19）。

## 行内顺序 == 画面左→右

`layouts.<名>.rows` 的行内书写顺序**就是画面里的左→右顺序**（agent.md §2.23），
所以"界面上第几行第几个"与"画面上第几行第几个"**一一对应**——
这正是不需要任何置换表的原因：键的身份是 `r{行}k{序号}`，序号从中间往两边排，
"第几列"与"键是谁"本来就是解耦的。

## 这个控件只做三件事

| 事 | 谁负责 |
| --- | --- |
| 摆按钮 / 显示当前绑定 | 本类 |
| 录入一个键（含挂起/恢复监听的那对回调） | 本类 |
| **写配置** | **调用方**（设置窗口）——本类只发信号 |

⚠ **不碰配置**：与 `keycapture` 同一条纪律（"写失败回滚"只能有一条路径）。
本类发出去的信号是"用户想要什么"，调用方决定写不写、写失败了怎么回滚。

## 录入期间必须挂起监听（task19 §4.3）

不挂起的话：用户按 `F` 去绑定，**宠物自己先亮了一下**（监听器照常在 8 ms 轮询），
而且那一轮按下会被记进 `pressed`，退出录入后还可能残留。

所以挂起/恢复是**成对**的，并且收在一个地方（`_end_capture`）：
开始录入挂起、**任何**退出路径（录成了 / Esc 取消 / 换布局 / 窗口关了）都恢复。
只靠"录成了才恢复"会漏掉后几条，表现是"取消一次之后按键就再也不亮了"。
"""

import logging
from collections.abc import Callable, Mapping
from typing import Final

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import (
    QApplication,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import keycapture
from .keyview_spec import KeyBinding, LayoutSpec

logger = logging.getLogger(__name__)

#: 网格那一组的标题（附录 B）。
GROUP_TITLE: Final[str] = "键位映射"

#: 没有键盘形态时那一组的说明（task19 §4.2：与"开机自启"未实现时的禁用样式一致）。
TEXT_NO_KEYVIEW: Final[str] = "当前素材包没有键盘形态"

#: 存在但一个可用布局都没有时的说明（与上面那句分开：原因不同，用户要做的事也不同）。
TEXT_NO_LAYOUT: Final[str] = "这个素材包的键盘形态没有可用布局"

#: 配置里的布局指向一个不存在的布局时的提示（task19 §4.2：**不静默改配置**）。
TEXT_LAYOUT_FALLBACK: Final[str] = "配置里的布局「{missing}」不存在，已显示「{used}」"

#: 重置按钮的文字与确认框（task19 §4.4）。
TEXT_RESET: Final[str] = "重置"
RESET_CONFIRM_TITLE: Final[str] = "重置键位"
RESET_CONFIRM_TEXT: Final[str] = "清空全部键位映射？"

#: 同一个物理键绑到多个键位时的提示日志（决策 2：**允许**，只记一条）。
TEXT_DUPLICATE_HINT: Final[str] = "同一个键 %s 现在绑在多个键位上（这是允许的，不解绑别的键位）"


def key_ids(layout: LayoutSpec | None) -> tuple[str, ...]:
    """布局里全部键 id（**画面左→右、先上后下**）。没有布局 → 空元组。"""
    return layout.key_ids if layout is not None else ()


class KeyGrid(QWidget):
    """一个布局的键位网格 + 重置按钮。

    | 信号 | 含义 |
    | --- | --- |
    | `binding_requested(key_id, vk, name)` | 用户给某个键位录到了键（**还没写配置**） |
    | `reset_requested()` | 用户确认了重置（**还没写配置**） |

    调用方（设置窗口）拿到信号后写配置，成功调 `apply_bindings()` 回填、
    失败调 `apply_bindings()` 传**配置里的真值**（= 回滚）。

    ⚠ **按钮上的文字不是真值**：真值永远是配置；本类只是照着摆。
    """

    binding_requested = Signal(str, int, str)
    reset_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        suspend: Callable[[bool], None] | None = None,
    ) -> None:
        super().__init__(parent)
        #: 挂起 / 恢复按键监听的钩子（`app.py` 装）。`None` = 没有监听器可挂起
        #: （自检、或这个包没有 KV）——那时挂起是空操作，**不是**错误。
        self._suspend = suspend
        #: 现在是不是"我们"挂起了它。**只有我们挂起的才由我们恢复**
        #: （别人也可能出于别的原因把监听停着，别去替他恢复）。
        self._suspended_by_us = False
        #: `键 id -> SingleKeyCaptureButton`。**每次换布局重建**。
        self._buttons: dict[str, keycapture.SingleKeyCaptureButton] = {}
        #: 当前布局（`None` = 没有可用布局）。
        self._layout: LayoutSpec | None = None
        #: **现在正在录入的那一个**按钮（一次只允许一个）。`None` = 不在录入态。
        self._capturing: keycapture.SingleKeyCaptureButton | None = None
        #: 当前生效的绑定（显示的真值来源；由 `apply_bindings()` 写）。
        self._bindings: dict[str, KeyBinding] = {}
        #: 录入期间装了**应用级**事件过滤器（见 `_install_mouse_grab`）。
        self._mouse_grabbed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)

        # ---- 标题行：左边组名，**右上角是重置**（task19 §4.4 的位置要求）
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel(GROUP_TITLE)
        header.addWidget(self._title)
        header.addStretch(1)
        self._reset_button = QPushButton(TEXT_RESET)
        self._reset_button.setToolTip(RESET_CONFIRM_TEXT)
        self._reset_button.clicked.connect(self._on_reset_clicked)
        header.addWidget(self._reset_button)
        outer.addLayout(header)

        # ---- 提示行（"没有键盘形态" / "布局回退了"这类说明；空串 = 不占地方）
        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setEnabled(False)
        self._hint.hide()
        outer.addWidget(self._hint)

        # ---- 网格本体（`rows` 决定行列；换布局时整块重建）
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        outer.addWidget(self._grid_host)

        self.set_layout(None)

    # ------------------------------------------------------------ 只读

    @property
    def layout(self) -> LayoutSpec | None:
        """当前摆的是哪个布局（`None` = 没有可用布局）。"""
        return self._layout

    @property
    def capturing(self) -> bool:
        """现在是不是在等用户按键（诊断与自检用）。"""
        return self._capturing is not None

    @property
    def bindings(self) -> dict[str, KeyBinding]:
        """当前显示着的绑定（一份拷贝）。"""
        return dict(self._bindings)

    @property
    def suspended(self) -> bool:
        """**我们**现在是不是把监听挂起了（诊断与自检用）。"""
        return self._suspended_by_us

    def button(self, key_id: str) -> "keycapture.SingleKeyCaptureButton | None":
        """按键 id 取按钮（自检与诊断用；界面上没有就是 `None`）。"""
        return self._buttons.get(key_id)

    def button_texts(self) -> dict[str, str]:
        """`键 id -> 按钮上的文字`（自检逐格比对用的判据）。"""
        return {key_id: button.display_text for key_id, button in self._buttons.items()}

    # ------------------------------------------------------------ 组装

    def set_layout(self, layout: LayoutSpec | None) -> None:
        """换成另一个布局的网格（**整块重建**，不留上一个布局的按钮）。

        重建而不是"复用一个网格再改内容"：两个布局的键位集合本来就不同
        （4 键 ⊂ 6 键 ⊂ 8 键），复用会让"多出来的那几格"变成要单独清理的残留。
        """
        # 换布局时先把录入态收掉：正在等按键的按钮马上就要被销毁了。
        self._end_capture()
        self._layout = layout
        self._bindings = {}
        self._buttons = {}

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        if layout is None:
            self._reset_button.setEnabled(False)
            return

        # **外层行、内层列**。用 `display_rows`（= `rows` 旋转 180°）而不是 `rows`：
        # 网格是给人看的，而人（与低头看键盘的角色本人）的视角与渲染画面
        # **上下左右都相反** —— 两个轴一起反就是旋转 180°，理由写在
        # `LayoutSpec.display_rows` 那儿。照 `rows` 摆会"上下左右完全反了"
        # （用户 2026 实测反馈）。⚠ 只改显示顺序，键 id 一个都不动。
        for row_index, row in enumerate(layout.display_rows):
            for column_index, key_id in enumerate(row):
                button = keycapture.SingleKeyCaptureButton()
                button.setToolTip(key_id)
                button.captured.connect(
                    lambda vk, name, kid=key_id: self._on_captured(kid, vk, name)
                )
                # ⚠ **取消也必须收尾**（task19 §4.3 明写"包括取消"这条路径）：
                # 按钮自己按 Esc 退出录入态时，网格这边必须知道——
                # 否则 `_capturing` 一直指着它、监听也一直挂着，
                # 表现就是"取消一次之后按什么键宠物都不亮了"。
                button.cancelled.connect(
                    lambda kid=key_id: self._on_cancelled(kid)
                )
                # ⚠ **进录入态也要通知网格**（task20 修的 bug）：按钮的 `clicked`
                # 直接连着它自己的 `begin_capture()`，用户点按钮那条路**绕过**了
                # `KeyGrid.begin_capture()`。只听 `cancelled` 是不够的——
                # 网格的 `_capturing` 会一直空着，于是"录入中途点另一个键位"时
                # 上一个的标签**卡在 `按下按键…`**。
                button.capture_started.connect(
                    lambda kid=key_id: self._on_capture_started(kid)
                )
                self._buttons[key_id] = button
                self._grid.addWidget(button, row_index, column_index)

        self._sync_reset_enabled()

    def apply_bindings(self, bindings: Mapping[str, KeyBinding]) -> None:
        """把绑定摆到按钮上（**唯一**改按钮文字的地方）。

        调用方用它做三件事：初值填充 / 写成功后回填 / 写失败后**回滚**
        （回滚时传的是配置里的真值，不是"改之前那个值"——与设置窗口同一条规矩）。
        不在这个布局里的键位**照样留着**：键 id 跨布局通用，换个布局就会重现。

        ⚠ **先收掉录入态**（task20 修的 bug）：重置（写 `keyview_keys = {}`）
        之后调用方会调本方法回填，而正在录入的那个按钮**拒绝**在录入态下改
        文字（见 `SingleKeyCaptureButton.set_key_text`）—— 不收尾的话它的标签
        就**卡在 `按下按键…`**，而且监听也一直挂着。
        """
        self._end_capture()
        self._bindings = dict(bindings)
        for key_id, button in self._buttons.items():
            binding = self._bindings.get(key_id)
            button.set_key_text(binding.name if binding is not None else "")
        self._sync_reset_enabled()

    def set_suspend_hook(self, suspend: Callable[[bool], None] | None) -> None:
        """换上（或卸下）挂起监听的回调（设置窗口装处理器时用）。

        ⚠ 换钩子时**先把当前那次挂起还回去**：正在录入时换掉钩子，
        新钩子不知道旧的那次挂起，于是那一次就永远恢复不了
        （表现是"换过处理器之后按键再也不亮"）。
        """
        self._end_capture()
        self._suspend = suspend

    def set_group_enabled(self, enabled: bool, hint: str = "") -> None:
        """整组启用 / 禁用 + 显示一句说明（task19 §4.2 的"与开机自启同一种样式"）。

        ⚠ **禁用而不是隐藏**（task11 §5）：位置照常占着，布局不跳。

        ⚠ 这一句是**整组的结论**（"当前素材包没有键盘形态"），
        与 `set_hint()` 那句"这一次为什么换了布局"是两回事：
        启用状态下调用本方法（`enabled=True`）**不动提示行**，
        免得把刚设好的回退提示擦掉。
        """
        self._grid_host.setEnabled(bool(enabled))
        if enabled:
            self._sync_reset_enabled()
            return
        self._reset_button.setEnabled(False)
        self.set_hint(hint)

    def set_hint(self, text: str) -> None:
        """设 / 清提示行（空串 = 隐藏）。**只改显示，不算结论。**"""
        if text:
            self._hint.setText(text)
            self._hint.show()
            return
        self._hint.clear()
        self._hint.hide()

    def hideEvent(self, event) -> None:  # noqa: ANN001 - Qt 签名
        """**窗口关了 / 被藏起来了** → 一定要把监听还回去。

        ⚠ 这是"所有退出路径都要恢复"里的最后一条，也是最容易漏的一条：
        用户点开录入态之后**直接关掉设置窗口**，那一次挂起如果没有收尾，
        监听就永远停着——表现是"关过一次设置，键盘就再也不亮了"，
        而且它**不会**因为下次打开设置而自愈。

        放在 `hideEvent` 而不是 `closeEvent`：模态对话框关闭与隐藏走的是
        同一条路，而 `hideEvent` 两条都覆盖到。
        """
        self._end_capture()
        super().hideEvent(event)

    # ------------------------------------------------------------ 录入

    def begin_capture(self, key_id: str) -> bool:
        """让某个键位进入录入态；返回"真进去了吗"（自检与装配用）。

        界面上是**点按钮**走这条路（`SingleKeyCaptureButton.clicked`）。
        显式开一个方法是为了让自检不必模拟鼠标点击就能验挂起/恢复。
        """
        button = self._buttons.get(key_id)
        if button is None or not button.isEnabled():
            return False
        self._begin_capture(button)
        # `begin_capture()` 会发 `capture_started` —— 但网格这边**已经在**
        # `_begin_capture` 里把它记下了，所以槽函数再来一次是幂等的（见那儿）。
        button.begin_capture()
        return button.listening

    def cancel_capture(self) -> None:
        """退出录入态（Esc / 窗口关闭走这里）。**保留原值**。"""
        self._end_capture()

    def _on_captured(self, key_id: str, vk: int, name: str) -> None:
        """某个键位录到了一个键：**收尾 + 发信号**（写配置是调用方的事）。

        ⚠ 顺序：**先恢复监听、再发信号**。反过来的话，调用方在信号处理里
        写配置（可能弹提示、可能失败回滚）期间监听还挂着，那一段时间里
        按键不亮——用户会觉得"录完这一个之后按什么都不灵了"。
        """
        self._end_capture()
        if self._duplicate_of(key_id, vk):
            # 决策 2：**允许**同一个键绑到多个键位，只记一条提示（不自动解绑）。
            logger.info(TEXT_DUPLICATE_HINT, name or vk)
        self.binding_requested.emit(key_id, int(vk), str(name))

    def _on_cancelled(self, key_id: str) -> None:
        """按钮自己取消了（Esc）：**把网格这边的录入态也收掉**。

        ⚠ 这一条**不能省**：按钮与网格各记了一份"在不在录入态"，
        只让按钮退出的话网格还以为在录——监听就一直挂着了。
        """
        button = self._buttons.get(key_id)
        if button is not None and button is self._capturing:
            self._capturing = None
        self._restore_listener()

    def _end_capture(self) -> None:
        """**单点收尾**：退出录入态 + 恢复监听。所有退出路径都走这里。

        四条路径都汇到这一个函数（task19 §4.3 明写"包括取消、窗口关闭"）：
        录成了 / Esc 取消 / 换布局（按钮要被销毁）/ 窗口关闭。

        ⚠ **幂等**：不在录入态时调用什么都不做（换布局会顺手调它）。
        """
        button = self._capturing
        self._capturing = None
        # 撤鼠标抓取**先于**改按钮文字：撤晚了会把下面这次 `clicked` 也吃掉。
        self._remove_mouse_grab()
        if button is not None:
            button.cancel_capture()
        # 恢复监听**不看有没有按钮**：挂起状态是"我们"置的，收尾就得还回去。
        self._restore_listener()

    def _begin_capture(self, button: "keycapture.SingleKeyCaptureButton") -> None:
        """进入录入态：**先挂起监听**，再让按钮开始等按键。

        ⚠ 挂起必须**早于**用户按第一个键：不挂起的话那个键当场点亮宠物
        （task19 §4.3 的那条要求）。
        """
        if self._capturing is button:
            return
        self._end_capture()
        self._capturing = button
        self._suspend_listener()
        # 抓住全窗口的鼠标按下：录入期间点任何地方都算"按下鼠标键"。
        self._install_mouse_grab()

    def _on_capture_started(self, key_id: str) -> None:
        """某个按钮**进了录入态**（不管是网格叫的，还是用户直接点的）。

        ⚠ 这里**不能**再调 `button.begin_capture()`：信号就是它发出来的，
        再调一次会被 `if self._listening: return` 挡掉（无害），但真正要做的是
        **把上一个收尾** —— 这正是"录入中途改点别的键位"要的行为。
        """
        button = self._buttons.get(key_id)
        if button is None:
            return
        # 按钮**已经**在录入态了（信号是它发的），所以这里只做网格这一半：
        # 收掉上一个 + 挂起监听。`_begin_capture` 里那步 `button.begin_capture()`
        # 不在这里重复调（它在 `KeyGrid.begin_capture()` 那条路上才需要）。
        self._begin_capture(button)

    # ------------------------------------------------------- 录入期的鼠标抓取

    def _install_mouse_grab(self) -> None:
        """录入期间**抓住全窗口的鼠标按下**（task20）。

        ## 为什么是应用级过滤器，而不是重写按钮的 `mousePressEvent`

        用户 2026 明确要求：**B 键位录入中时点 A，这一下算"按下鼠标左键"**
        （录给 B），而不是"切到 A"。只重写按钮自己的 `mousePressEvent`
        做不到这一点 —— 点 A 时事件先落到 **A 按钮**上，A 会说"我要进录入态"，
        于是变成切换，B 什么也录不到（实测就是这个结果）。

        装到 `QApplication` 上之后，**任何位置**的左键按下都先经过这里，
        统一记到当前正在录入的那个键位上，`return True` 把事件吃掉
        —— A 收不到点击，也就不会切成录入态。

        ⚠ **成对**：`_remove_mouse_grab()` 收在 `_end_capture()` 里，
        四条退出路径（录成 / Esc / 换布局 / 窗口关闭）全都汇到那儿。
        """
        if self._mouse_grabbed:
            return
        app = QApplication.instance()
        if app is None:  # pragma: no cover - 正常路径一定有 QApplication
            return
        app.installEventFilter(self)
        self._mouse_grabbed = True

    def _remove_mouse_grab(self) -> None:
        """撤掉录入期的鼠标抓取（**幂等**）。"""
        if not self._mouse_grabbed:
            return
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._mouse_grabbed = False

    def eventFilter(self, obj, event) -> bool:  # noqa: ANN001 - Qt 签名
        """录入期间的鼠标与失活处理（task20）。

        ## 鼠标按下：**一律**记到正在录入的那个键位上

        只处理 `MouseButtonPress`，其余（抬起 / 移动 / 键盘）**原样放行**：
        抬起放行是必须的，否则按钮的 `clicked` 永远不触发（Qt 要 press+release
        配对），"非录入态下点按钮进录入态"这条就坏了。

        ## ⚠ 失活：焦点离开 -> 必须收掉录入态（task20 修的严重 bug）

        用户 2026 实测："在窗口失焦时按下按键 KV 就不生效"（**分发版**必现、
        开发时看不出来）。成因是一条链：

        ```text
        录入态下点别的程序   -> 我们**收不到任何鼠标事件**（窗口失焦）
                            -> 录入态永不结束
                            -> 监听一直挂着（suspended=True）
                            -> 回宠物窗口按什么键都不亮
        ```

        关键点是第二节：点**本程序之外**的窗口不会给我们送鼠标事件，所以
        靠"点击"收尾是收不到的 —— 必须监听"焦点离开"这件事本身。

        ## ⚠ 为什么**两个**失活事件都要收（只收一个在分发版上不管用）

        | 事件 | 什么时候发 | 单独用它够不够 |
        | --- | --- | --- |
        | `ApplicationDeactivate` | **程序从"激活"变成"未激活"**时 | ❌ |
        | `WindowDeactivate` | 本窗口失去激活时 | ✅ 兜底 |

        第一版只收了 `ApplicationDeactivate`，**开发时好了、分发版仍然坏**。
        实测原因是：Qt 只在"激活 -> 未激活"**这个转变**上发它，而分发版
        （双击 exe 启动、窗口是 `Qt.Tool` 且 `WA_ShowWithoutActivating`）
        **整个生命周期都处于 `ApplicationInactive`** —— 从来没有"激活"过，
        也就永远等不到那个转变，事件一次都不发。开发时从控制台/IDE 启动
        会先激活一次，所以掩盖了这个 bug。

        `WindowDeactivate` 是**窗口级**的，只要有焦点转移就会发，与
        "程序整体是否激活过"无关，所以在两种启动方式下都可靠。

        ⚠ **顺序保证**了它不会抢掉"点别的键位录鼠标左键"那条：
        点本程序**内部**的按钮时，`MouseButtonPress` **先**到（上面那个分支
        当场录完并 `_end_capture()`），随后即使再来 `WindowDeactivate`，
        此时 `_capturing` 已经是 `None`，本函数开头就返回了（实测确认顺序）。
        """
        if not self._mouse_grabbed or self._capturing is None:
            return super().eventFilter(obj, event)

        if event.type() in (
            QEvent.Type.ApplicationDeactivate,
            QEvent.Type.WindowDeactivate,
        ):
            # 焦点离开我们的窗口：不可能再等到用户按鼠标了，收尾。
            # 收尾只做"退录入态 + 恢复监听"，**不记录任何键**
            # （真正的点击记录在上面那条 `MouseButtonPress` 分支里）。
            self._end_capture()
            return super().eventFilter(obj, event)

        if event.type() != QEvent.Type.MouseButtonPress:
            return super().eventFilter(obj, event)

        mapped = keycapture.MOUSE_BUTTONS.get(int(event.button().value))
        if mapped is None:
            return super().eventFilter(obj, event)

        vk, name = mapped
        self._capturing._finish_capture(vk, name)
        # 事件**吃掉**：被点的那个按钮收不到点击，也就不会切成录入态。
        return True

    def _suspend_listener(self) -> None:
        if self._suspend is None or self._suspended_by_us:
            return
        self._suspended_by_us = True
        self._suspend(True)

    def _restore_listener(self) -> None:
        """恢复监听（**幂等**：没挂起过就什么都不做）。"""
        if self._suspend is None or not self._suspended_by_us:
            return
        self._suspended_by_us = False
        self._suspend(False)

    # ------------------------------------------------------------ 重置

    def _on_reset_clicked(self) -> None:
        """重置：**一次确认**（task19 §4.4；16 个键位重录成本高）。

        取消 → 什么都不做（不写配置、不改界面、不发信号）。
        确认 → 发 `reset_requested`，由调用方写 `keyview_keys = {}`。

        ⚠ **任何一条路都先收掉录入态**（task20）：确认框弹出来的那一刻，
        那个按钮就不该再显示 `按下按键…`（用户看到的是一句过期提示）。
        在**问之前**收，是因为"取消"也要收 —— 用户已经用鼠标点了别处，
        录入态早就该结束了。
        """
        self._end_capture()
        if not self._reset_button.isEnabled():
            return
        answer = QMessageBox.question(
            self,
            RESET_CONFIRM_TITLE,
            RESET_CONFIRM_TEXT,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            logger.info("重置键位：用户取消了")
            return
        self.reset_requested.emit()

    def _sync_reset_enabled(self) -> None:
        """空映射时重置按钮**禁用**（task19 §4.4：避免"点了没反应"的困惑）。

        判据是"**这个布局用到**的键位里有没有绑上的"：跨布局多配的那几个
        不该让一个空布局的重置亮起来——那正是"点了没反应"。
        """
        has_any = any(key_id in self._bindings for key_id in self._buttons)
        self._reset_button.setEnabled(has_any)

    # ------------------------------------------------------------ 内部

    def _duplicate_of(self, key_id: str, vk: int) -> bool:
        """这个 VK 是不是已经绑在**别的**键位上了（只用于记一条提示）。"""
        return any(
            other != key_id and binding.vk == vk
            for other, binding in self._bindings.items()
        )


def build_group(
    parent: QWidget | None = None,
    *,
    suspend: Callable[[bool], None] | None = None,
) -> tuple[QGroupBox, KeyGrid]:
    """把网格包成一个 `QGroupBox`（与设置窗口其余各组的观感一致）。

    返回 `(组, 网格)`——**组**进布局，**网格**留着接信号。
    """
    box = QGroupBox(parent)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(9, 9, 9, 9)
    grid = KeyGrid(box, suspend=suspend)
    layout.addWidget(grid)
    # ⚠ 组名已经在网格自己的标题行里了（那里还要放重置按钮），
    # 所以 `QGroupBox` 的标题留空 + 扁平：两处都写会渲染出"键位映射"两遍。
    box.setTitle("")
    box.setFlat(True)
    return box, grid
