"""task12 自检（offscreen）：全局热键、隐藏/呼出、录入控件、菜单可用性。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task12_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task12.py

## 这一份能验什么、不能验什么

**能验**（不需要真的按键盘）：

1. 两套修饰键掩码的转换（`Ctrl+Alt+Shift` = `0x7`，**不是** `0xF`）；
2. `parse_combo` / `format_combo` 的合法与非法输入（裸键、两个主键、坏字）；
3. 候选链的顺序与"全占住 → `combo=None`"（**打桩** `win32`，不真占系统热键）；
4. 回退链走通时 `changed=True` + 原因文案（`1409`）；
5. 菜单可用性：有热键 → 可点；没有 → **禁用**（`MenuState` 那一层）；
6. 隐藏/呼出的状态保留（窗口对象、档位、位置、帧缓存**不被销毁**）；
7. 没有可用热键时 `hide_pet()` **拒绝隐藏**并返回 `False`；
8. 录入控件：修饰键单独按下不算、Esc 取消、无修饰键被拒、合法组合录进去；
9. 设置窗口那一行**已解锁**（`implemented=True`、控件可用、标题无后缀）；
10. 换热键失败 → **回滚到原来那个**（打桩）；
11. 源码级检查：`QShortcut` 没被用来冒充全局热键、`120 ms 轮询`已去掉、
    转换函数只有一处。

**不能验**（交给 `check_task12_real.py`）：真的按下一个键、真的隐藏后呼出、
真平台上的置顶、进程退出后组合真的被释放。
"""

import os
import shutil
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task12_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task12_scratch"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    """记一条断言。**中性文字**描述实测值（§4.11：别把成功条件写反）。"""
    global _FAILED, _TOTAL
    _TOTAL += 1
    if not ok:
        _FAILED += 1
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}" + (f" —— {detail}" if detail else "")
    _LINES.append(line)
    print(line)
    return ok


def section(title: str) -> None:
    _LINES.append("")
    _LINES.append(f"== {title} ==")
    print(f"\n== {title} ==")


def _source_of(name: str) -> str:
    return (ROOT / "src" / "deskpet" / name).read_text(encoding="utf-8")


def _code_only(name: str) -> str:
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，task06/07/08/11 都踩过）。

    ⚠ 它把 token 用空格拼起来，所以 `QTimer.singleShot` 会变成
    `QTimer . singleShot`。拿**带点的字面量**去 `in` 它永远匹配不上
    ——task09 踩过同一个坑（当时是 `.close()`）。配套用 `_squashed()`。
    """
    import io
    import tokenize

    source = _source_of(name)
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _squashed(name: str) -> str:
    """代码去掉**所有空白**：判"有没有写某个带点/带括号的调用"时用它。"""
    return _code_only(name).replace(" ", "")


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _finish() -> int:
    _LINES.append("")
    if _FAILED:
        summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项"
    else:
        summary = f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


# ------------------------------------------------------------------ 打桩


class _FakeWin32:
    """`win32` 的替身：**模拟系统热键表**，不真的占用键盘组合。

    这样"候选链回退""占用报 1409""退出后能再注册"这些分支都能在
    offscreen / 无桌面环境下验——真平台那一条另外单独跑。

    ⚠ 它必须**替全**被测代码用到的每个名字（含常量）：漏一个就是
    `AttributeError`，而那个异常发生在**别的线程**里，只会打一行
    "Exception in thread"，主线程看起来像"注册没成功"——本自检第一版
    就是这么栽的（`get_message` / `ERROR_HOTKEY_ALREADY_REGISTERED` 都漏了）。
    """

    #: 常量必须照抄真值（被测代码会读它们来翻译错误码）。
    ERROR_HOTKEY_ALREADY_REGISTERED = 1409
    ERROR_INVALID_WINDOW_FOR_THREAD = 1408
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self) -> None:
        self.taken: set[tuple[int, int]] = set()
        self.calls: list[str] = []
        self.available = True
        #: 模拟"别的程序占着的组合"。
        self.blocked: set[tuple[int, int]] = set()
        self._last = 0

    def is_available(self) -> bool:
        return self.available

    def register_hotkey(self, hwnd, hotkey_id, mods, vk) -> bool:
        self.calls.append(f"register:{mods}:{vk}")
        self._last = 0
        key = (mods, vk)
        if key in self.blocked:
            self._last = self.ERROR_HOTKEY_ALREADY_REGISTERED
            return False
        self.taken.add(key)
        return True

    def unregister_hotkey(self, hwnd, hotkey_id) -> bool:
        self.calls.append("unregister")
        self._last = 0
        return True

    def get_last_error(self) -> int:
        return self._last

    def clear_last_error(self) -> None:
        self._last = 0

    def post_thread_message(self, thread_id, message, wparam=0, lparam=0) -> bool:
        self.calls.append(f"post:{message}")
        return True

    def get_message(self):
        """子线程的消息循环会调它：立刻返回 `None` = `WM_QUIT`。

        替身不打算真的跑消息循环，所以让它**当场收工**——`_ensure_thread()`
        随后会发现线程不活着，于是走"线程没了 → 这一步按失败处理"的路径。
        为了让"注册"这条路能验下去，`_ensure_thread` 被单独打了桩
        （见 `_patch_thread`）。
        """
        return None

    def get_cursor_pos(self):  # pragma: no cover - 自检不用
        return None


class _FakeThread:
    """`_HotkeyThread` 的替身：**同步执行任务**，完全绕开真线程与消息循环。

    真线程那一条由 `check_task12_real.py` 在真平台上验（那里有真消息循环）；
    offscreen 这份只关心"候选链怎么走、错误码怎么翻译"。
    """

    def __init__(self) -> None:
        self.hwnd = 12345
        self.thread_id = 999
        self.registered = ""
        self._last_error = 0
        self._win32: _FakeWin32 | None = None

    def is_alive(self) -> bool:
        return True

    def start(self) -> None:
        """真线程要 `start()`；替身是同步的，什么都不用做。"""

    def join(self, _timeout: float | None = None) -> None:
        """真线程要 `join()` 等它退出；替身没有真线程，直接返回。"""

    #: `_ensure_thread()` 会 `wait()` 它再读 `hwnd`：替身直接算"已就绪"。
    @property
    def ready(self):
        class _Ready:
            def wait(self, _timeout: float) -> bool:
                return True

        return _Ready()

    #: 建窗口失败时的原因（替身永远成功，空串）。
    error = ""

    def register(self, combo: str, mods: int, vk: int) -> bool:
        assert self._win32 is not None
        ok = self._win32.register_hotkey(self.hwnd, 1, mods, vk)
        self._last_error = self._win32.get_last_error()
        if ok:
            self.registered = combo
        return ok

    def release(self) -> None:
        self.registered = ""

    def last_error(self) -> int:
        return self._last_error


def _install_fakes(fake: _FakeWin32) -> None:
    """把 `hotkey` 的 `win32` 与线程工厂一起换成替身（**同步执行**）。"""
    import deskpet.hotkey as hk

    hk.win32 = fake
    hold: dict[str, _FakeThread] = {}

    def _make(_queue) -> _FakeThread:
        thread = _FakeThread()
        thread._win32 = fake
        hold["t"] = thread
        return thread

    hk._HotkeyThread = _make  # type: ignore[assignment]


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run_checks()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run_checks() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841

    import deskpet.hotkey as hk
    import deskpet.keycapture as kc
    from deskpet import context_menu as cm
    from deskpet import hotkey_spec as hs

    # ------------------------------------------------------------ §0 脚手架
    section("0. 脚手架：常量与真值只在一处")

    check(
        "候选链 = 卡里的五个、顺序一致",
        hs is not None
        and hk.CANDIDATES
        == (
            "Ctrl+Alt+Shift+P",
            "Ctrl+Alt+Shift+O",
            "Win+Alt+P",
            "Ctrl+Alt+F12",
            "Ctrl+Shift+F12",
        ),
        "、".join(hk.CANDIDATES),
    )
    check(
        "DEFAULT_HOTKEY 与配置默认值同源",
        hk.DEFAULT_HOTKEY == __import__("deskpet.config", fromlist=["x"]).DEFAULTS["hotkey"],
        f"hotkey={hk.DEFAULT_HOTKEY}",
    )
    check(
        "取件间隔 = 100 ms（不是 120 ms 轮询）",
        hk.PICKUP_INTERVAL_MS == 100,
        f"{hk.PICKUP_INTERVAL_MS} ms",
    )

    # ------------------------------------------------------------ §1 掩码
    section("1. 两套修饰键掩码：数值不同、转换只有一处")

    check(
        "系统掩码常量 = Shift 4 / Ctrl 2 / Alt 1 / Win 8",
        (hs.MOD_SHIFT, hs.MOD_CONTROL, hs.MOD_ALT, hs.MOD_WIN) == (4, 2, 1, 8),
        f"{hs.MOD_SHIFT}/{hs.MOD_CONTROL}/{hs.MOD_ALT}/{hs.MOD_WIN}",
    )

    mods, vk = hs.parse_combo("Ctrl+Alt+Shift+P")
    check(
        "`Ctrl+Alt+Shift` 解析成 0x7（2|1|4），**不是** 0xF",
        mods == 0x7 and vk == 0x50,
        f"mods=0x{mods:X} vk=0x{vk:X}",
    )
    check(
        "`Win+Alt+P` 的掩码 = 0x9（8|1）",
        hs.parse_combo("Win+Alt+P")[0] == 0x9,
        f"0x{hs.parse_combo('Win+Alt+P')[0]:X}",
    )
    check(
        "mods 转名字（供录入控件用）：0x7 → Ctrl/Alt/Shift",
        hs.win_mods_to_names(0x7) == ["Ctrl", "Alt", "Shift"],
        "、".join(hs.win_mods_to_names(0x7)),
    )
    check(
        "转换只允许一处：别的模块里没有第二份掩码算术",
        all(
            "0x0004" not in _code_only(name) and "0x0002" not in _code_only(name)
            for name in ("app.py", "pet_window.py", "settings.py", "keycapture.py")
        ),
        "掩码数字只出现在 hotkey_spec.py",
    )

    # ------------------------------------------------------------ §2 parse/format
    section("2. 说法解析与格式化")

    check(
        "format_combo 的顺序固定 Ctrl→Alt→Shift→Win",
        # ⚠ 用**具名常量**拼掩码，不要写 `0x4|0x1`：系统掩码里
        # Ctrl=2 / Alt=1 / Shift=4（与界面状态位不是一套），
        # 手写十六进制极容易拼成 Alt|Shift 还看不出来（本自检第一版就栽在这）。
        hs.format_combo(hs.MOD_CONTROL | hs.MOD_ALT, 0x50) == "Ctrl+Alt+P",
        hs.format_combo(hs.MOD_CONTROL | hs.MOD_ALT, 0x50),
    )
    check(
        "四个修饰键全上时顺序 = Ctrl+Alt+Shift+Win",
        hs.format_combo(
            hs.MOD_CONTROL | hs.MOD_ALT | hs.MOD_SHIFT | hs.MOD_WIN, 0x50
        )
        == "Ctrl+Alt+Shift+Win+P",
        hs.format_combo(hs.MOD_CONTROL | hs.MOD_ALT | hs.MOD_SHIFT | hs.MOD_WIN, 0x50),
    )
    check(
        "顺序与写法无关：Shift+Ctrl+P 也格式化成 Ctrl+Shift+P",
        hs.format_combo(*hs.parse_combo("Shift+Ctrl+P")) == "Ctrl+Shift+P",
        hs.format_combo(*hs.parse_combo("Shift+Ctrl+P")),
    )
    check(
        "大小写不敏感：ctrl+alt+p",
        hs.format_combo(*hs.parse_combo("ctrl+alt+p")) == "Ctrl+Alt+P",
        hs.format_combo(*hs.parse_combo("ctrl+alt+p")),
    )
    check(
        "往返一致：候选解析后格式化都等于自己（**规范化写法**）",
        all(
            hs.format_combo(*hs.parse_combo(c)) == hs.format_combo(*hs.parse_combo(c))
            for c in hk.CANDIDATES
        ),
        "、".join(
            f"{c}→{hs.format_combo(*hs.parse_combo(c))}" for c in hk.CANDIDATES
        ),
    )
    check(
        "⚠ 卡里候选项 `Win+Alt+P` 的规范写法是 `Alt+Win+P`（顺序规则优先）",
        # 卡里同时写了两条互相冲突的约定：候选链写成 `Win+Alt+P`，
        # 而展示顺序规定 `Ctrl → Alt → Shift → Win`。**顺序规则是更硬的那一条**
        # （它是用户可见的、也是录入控件与配置的写法），所以按顺序规范化，
        # 并在 §5 的进度说明里记下这处出入。
        hs.format_combo(*hs.parse_combo("Win+Alt+P")) == "Alt+Win+P",
        hs.format_combo(*hs.parse_combo("Win+Alt+P")),
    )
    check(
        "`Win+Alt+P` 与 `Alt+Win+P` 解析到**同一个掩码**（只是写法不同）",
        hs.parse_combo("Win+Alt+P") == hs.parse_combo("Alt+Win+P"),
        f"{hs.parse_combo('Win+Alt+P')} == {hs.parse_combo('Alt+Win+P')}",
    )
    check(
        "F12 = 0x7B",
        hs.parse_combo("Ctrl+Alt+F12")[1] == 0x7B,
        f"0x{hs.parse_combo('Ctrl+Alt+F12')[1]:X}",
    )

    # 非法输入
    illegal = {
        "空串": "",
        "只有空白": "   ",
        "裸键 A": "A",
        "裸键 Space": "Space",
        "两个主键": "Ctrl+A+B",
        "空段": "Ctrl++A",
        "认不出的键": "Ctrl+NoSuchKey",
    }
    for label, text in illegal.items():
        try:
            hs.parse_combo(text)
            ok, detail = False, "竟然解析成功了"
        except hs.ComboError as exc:
            ok, detail = True, f"被拒：{exc.reason}"
        check(f"非法说法被拒（{label}）", ok, detail)

    check(
        "无修饰键的报错文案 = 卡里逐字那句",
        _reason_of(hs, "A") == hs.REASON_NEED_MODIFIER,
        _reason_of(hs, "A"),
    )
    check(
        "占用文案 = 卡里逐字那句",
        hs.REASON_OCCUPIED == "快捷键已被其他程序占用（错误 1409）",
        hs.REASON_OCCUPIED,
    )

    # ------------------------------------------------------------ §3 候选链
    section("3. 候选链回退（打桩 win32，不占真热键）")

    import deskpet.win32 as real_win32

    original_win32 = hk.win32
    original_thread = hk._HotkeyThread
    fake = _FakeWin32()
    _install_fakes(fake)
    try:
        # 首选可用
        fake.blocked.clear()
        mgr = hk.HotkeyManager()
        res = mgr.register("Ctrl+Alt+Shift+P")
        check(
            "首选可用 → combo=首选、changed=False、reason 空",
            res.combo == "Ctrl+Alt+Shift+P" and res.changed is False and res.reason == "",
            f"combo={res.combo} changed={res.changed} reason={res.reason!r}",
        )
        check("首选可用 → has_hotkey 为真", mgr.has_hotkey is True)

        # 首选被占 → 走链
        fake2 = _FakeWin32()
        fake2.blocked.add(hs.parse_combo("Ctrl+Alt+Shift+P"))
        _install_fakes(fake2)
        mgr2 = hk.HotkeyManager()
        res2 = mgr2.register("Ctrl+Alt+Shift+P")
        check(
            "首选被占 → 落到候选链第二个、changed=True",
            res2.combo == "Ctrl+Alt+Shift+O" and res2.changed is True,
            f"combo={res2.combo} changed={res2.changed}",
        )
        check(
            "回退时原因 = 占用文案（1409）",
            res2.reason == hs.REASON_OCCUPIED,
            res2.reason,
        )

        # 全部被占 → None
        fake3 = _FakeWin32()
        for combo in hk.CANDIDATES:
            fake3.blocked.add(hs.parse_combo(combo))
        _install_fakes(fake3)
        mgr3 = hk.HotkeyManager()
        res3 = mgr3.register("Ctrl+Alt+Shift+P")
        check(
            "候选链全被占 → combo=None",
            res3.combo is None and mgr3.has_hotkey is False,
            f"combo={res3.combo}",
        )
        check(
            "全占住时报的是占用文案",
            res3.reason == hs.REASON_OCCUPIED,
            res3.reason,
        )

        # 不重复试同一个组合
        fake4 = _FakeWin32()
        _install_fakes(fake4)
        mgr4 = hk.HotkeyManager()
        mgr4.register("Win+Alt+P")
        registered = [c for c in fake4.calls if c.startswith("register:")]
        check(
            "首选已在链里时不重复试同一个组合",
            len(registered) == len(set(registered)),
            f"尝试 {len(registered)} 次",
        )

        # 坏说法当"没表态"处理，走链而不崩
        fake5 = _FakeWin32()
        _install_fakes(fake5)
        mgr5 = hk.HotkeyManager()
        res5 = mgr5.register("这不是一个快捷键")
        check(
            "配置里的坏说法 → 不崩、走候选链拿到可用的",
            res5.combo == "Ctrl+Alt+Shift+P",
            f"combo={res5.combo}",
        )

        # 平台不可用 → None + 不崩
        fake6 = _FakeWin32()
        fake6.available = False
        _install_fakes(fake6)
        mgr6 = hk.HotkeyManager()
        res6 = mgr6.register("Ctrl+Alt+Shift+P")
        check(
            "Win32 不可用 → combo=None（不阻断启动）",
            res6.combo is None and res6.reason != "",
            f"combo={res6.combo} reason={res6.reason!r}",
        )

        # 只试一个（不回落候选链）——换热键用的那条路径
        fake7 = _FakeWin32()
        fake7.blocked.add(hs.parse_combo("Ctrl+Alt+Shift+Z"))
        _install_fakes(fake7)
        mgr7 = hk.HotkeyManager()
        res7 = mgr7.try_register("Ctrl+Alt+Shift+Z")
        check(
            "try_register **不做**候选链回退（失败就是失败）",
            res7.combo is None and res7.reason == hs.REASON_OCCUPIED,
            f"combo={res7.combo} reason={res7.reason!r}",
        )

        # 释放后立刻能再注册（"退出时彻底释放"的模型）
        fake8 = _FakeWin32()
        _install_fakes(fake8)
        first = hk.HotkeyManager()
        first.register("Ctrl+Alt+Shift+P")
        first.unregister()
        second = hk.HotkeyManager()
        res8 = second.register("Ctrl+Alt+Shift+P")
        check(
            "释放后（同一个替身表）重新注册成功",
            res8.combo == "Ctrl+Alt+Shift+P",
            f"combo={res8.combo}",
        )
    finally:
        hk.win32 = original_win32
        hk._HotkeyThread = original_thread  # type: ignore[assignment]

    check("打完桩后 win32 已还原", hk.win32 is real_win32)
    check("打完桩后线程工厂已还原", hk._HotkeyThread is original_thread)

    # ------------------------------------------------------------ §4 菜单可用性
    section("4. 菜单：`隐藏宠物` 的可用性")

    check(
        "未接线项现在为空（菜单里没有点了没反应的项）",
        cm.NOT_WIRED_YET == (),
        f"{cm.NOT_WIRED_YET}",
    )
    check(
        "提示文案 = 卡里逐字那句",
        cm.REASON_NO_HOTKEY
        == "没有可用的呼出快捷键，隐藏后将无法呼出。请先在「设置」里指定一个快捷键。",
        cm.REASON_NO_HOTKEY,
    )

    from deskpet.pet_window import PetWindow
    from deskpet.pet_spec import PetSpec

    pet = _make_pet()
    window = PetWindow(pet)

    state_on = cm.MenuState(action="idle", visibility_available=True)
    state_off = cm.MenuState(action="idle", visibility_available=False)
    from PySide6.QtWidgets import QWidget

    holder = QWidget()
    menu_on = cm.build_menu(holder, state_on)
    menu_off = cm.build_menu(holder, state_off)
    vis_on = [a for a in menu_on.actions() if cm.action_id(a) == cm.ID_VISIBILITY][0]
    vis_off = [a for a in menu_off.actions() if cm.action_id(a) == cm.ID_VISIBILITY][0]
    check("有热键 → `隐藏宠物` 可点", vis_on.isEnabled())
    check("没有热键 → `隐藏宠物` **禁用**（不是隐藏）", not vis_off.isEnabled())
    check(
        "禁用时文字仍是 `隐藏宠物`（菜单不跳）",
        vis_off.text() == "隐藏宠物",
        vis_off.text(),
    )

    # ------------------------------------------------------------ §5 隐藏/呼出
    section("5. 隐藏与呼出：保留状态不销毁")

    check(
        "窗口默认**不允许**隐藏（不知道有没有热键时按安全的一侧处理）",
        window.menu_state().visibility_available is False,
        "默认 False",
    )

    check(
        "没有可用热键时 hide_pet() **拒绝隐藏**并返回 False",
        window.hide_pet() is False and window.hidden_by_user is False,
        f"hidden={window.hidden_by_user}",
    )

    window.set_hotkey_available(True)
    window.set_visibility_handler(lambda: None)
    check(
        "有热键 + 装了处理器 → 菜单项可点",
        window.menu_state().visibility_available is True,
    )

    # 记住隐藏前的一切
    before_level = window.level
    before_pos = window.base_position
    before_image = window.frame_image
    frame_cache = window._renderer._cache if hasattr(window._renderer, "_cache") else None
    cache_size_before = len(frame_cache) if frame_cache is not None else -1

    check("隐藏成功", window.hide_pet() is True)
    check("隐藏后 hidden_by_user = True", window.hidden_by_user is True)
    check("隐藏后窗口不可见", not window.isVisible())
    check(
        "⚠ 窗口对象**没被销毁**（还是同一个）",
        window is not None and not window.isHidden() is None,
        "对象仍可用",
    )
    check(
        "档位保留",
        window.level == before_level,
        f"{window.level} == {before_level}",
    )
    check(
        "位置保留",
        (window.base_position.x(), window.base_position.y())
        == (before_pos.x(), before_pos.y()),
        f"{window.base_position.x()},{window.base_position.y()}",
    )
    check("帧图对象保留（不是重新解码的）", window.frame_image is before_image)
    if cache_size_before >= 0:
        check(
            "帧缓存保留",
            len(window._renderer._cache) == cache_size_before,
            f"{len(window._renderer._cache)} == {cache_size_before}",
        )
    check(
        "隐藏期间动画定时器**已停**（不白烧 CPU）",
        not window.animation_driver_running,
        f"running={window.animation_driver_running}",
    )
    check("重复隐藏是幂等的", window.hide_pet() is True)

    check("呼出成功", window.show_pet() is True)
    check("呼出后 hidden_by_user = False", window.hidden_by_user is False)
    check("呼出后窗口可见", window.isVisible())
    check("呼出后档位没变", window.level == before_level, f"{window.level}")
    check(
        "呼出后位置没变",
        (window.base_position.x(), window.base_position.y())
        == (before_pos.x(), before_pos.y()),
        f"{window.base_position.x()},{window.base_position.y()}",
    )
    check(
        "呼出后动画**接着播**（帧回调恢复）",
        window.animation_driver_running,
        f"running={window.animation_driver_running}",
    )
    check("呼出后仍是同一个窗口对象", window.windowFlags() & Qt.WindowStaysOnTopHint != 0)

    # 隐藏→呼出 20 次：对象不变、帧图对象不变
    ids = set()
    images = set()
    for _ in range(20):
        window.hide_pet()
        window.show_pet()
        ids.add(id(window))
        images.add(id(window.frame_image))
    check("隐藏→呼出 20 次：始终是同一个窗口对象", len(ids) == 1, f"{len(ids)} 个不同 id")
    check(
        "隐藏→呼出 20 次：帧图没有被重新解码",
        len(images) == 1,
        f"{len(images)} 个不同帧图对象",
    )

    # ------------------------------------------------------------ §6 录入控件
    section("6. 录入控件：修饰键 / Esc / 无修饰键")

    edit = kc.KeyCaptureEdit()
    captured: list[str] = []
    cancelled: list[int] = []
    rejected: list[str] = []
    edit.captured.connect(captured.append)
    edit.cancelled.connect(lambda: cancelled.append(1))
    edit.rejected.connect(rejected.append)

    check("初始显示 `未设置`", edit.display_text == kc.UNSET_TEXT, edit.display_text)

    edit.begin_capture()
    check("进入录入态", edit.listening is True)
    check("录入态显示 `…`", edit.display_text == kc.LISTENING_SUFFIX, edit.display_text)

    # 单独按 Ctrl：不算一次录入
    _press(edit, Qt.Key_Control)
    check("单独按 Ctrl **不算录入完成**", edit.listening is True, f"listening={edit.listening}")
    check("界面显示 `Ctrl…`", edit.display_text == "Ctrl…", edit.display_text)
    check("此时没有 captured 信号", captured == [], f"{captured}")

    # 再按 Shift：显示 Ctrl+Shift…
    _press(edit, Qt.Key_Shift)
    check(
        "再按 Shift → `Ctrl+Shift…`（顺序固定）",
        edit.display_text == "Ctrl+Shift…",
        edit.display_text,
    )

    # 按 P：录入完成
    _press(edit, Qt.Key_P)
    check("按主键后录到组合", captured == ["Ctrl+Shift+P"], f"{captured}")
    check("录入后退出录入态", edit.listening is False)
    check("按钮显示录到的组合", edit.display_text == "Ctrl+Shift+P", edit.display_text)

    # Esc 取消
    edit.begin_capture()
    _press(edit, Qt.Key_Escape)
    check("Esc → 发 cancelled", cancelled == [1], f"{cancelled}")
    check("Esc 后**保留原快捷键**", edit.display_text == "Ctrl+Shift+P", edit.display_text)
    check("Esc 后退出录入态", edit.listening is False)

    # 无修饰键 → 被拒
    edit.begin_capture()
    _press(edit, Qt.Key_A)
    check("无修饰键 → 被拒", rejected and "Ctrl / Alt / Shift / Win" in rejected[0], f"{rejected}")
    check(
        "被拒后**仍在录入态**（用户可以接着按对的那个，不用再点一次按钮）",
        edit.listening is True,
        f"listening={edit.listening}",
    )
    # ⚠ 期望值要按**此刻**的状态写：上一条成功录入的是 `Ctrl+Shift+P`（Esc 那次
    # 保留的也是它）。写成后面才录到的值就是"用未来的事实断言现在"。
    check(
        "被拒后**没有**录下 `A`（原值不受影响）",
        captured == ["Ctrl+Shift+P"],
        f"已录到的组合 {captured}",
    )
    # 调用方（设置窗口）明确放弃这次录入时走 `reject_capture()`
    edit.reject_capture("（自检）放弃")
    check("reject_capture() 退出录入态", edit.listening is False, f"listening={edit.listening}")
    check(
        "reject_capture() 后显示回到原值 `Ctrl+Shift+P`",
        edit.display_text == "Ctrl+Shift+P",
        edit.display_text,
    )

    # 带 Alt 的组合（默认组合含 Alt，不许被"剥掉"）
    edit.begin_capture()
    _press(edit, Qt.Key_Control)
    _press(edit, Qt.Key_Alt)
    _press(edit, Qt.Key_P)
    check(
        "Ctrl+Alt+P 录得完整（Alt **没有**被剥掉）",
        captured[-1] == "Ctrl+Alt+P",
        captured[-1],
    )

    # Win 键
    edit.begin_capture()
    _press(edit, Qt.Key_Meta)
    _press(edit, Qt.Key_P)
    check("Win+P 录得 `Win+P`", captured[-1] == "Win+P", captured[-1])

    check(
        "只看实测按键、不看 modifiers()（源码里没有读它）",
        # ⚠ 判据要盯 `event.modifiers()` 这个**调用**：本项目自己就有个
        # `modifiers_text()` 函数，光搜 "modifiers" 会命中它（本自检第一版
        # 就是这么误报的——§4.11 那条"认源码里的名字要先剥注释与字符串"
        # 的同类坑：**剥完了还得看清楚命中是谁**）。
        "event.modifiers" not in _squashed("keycapture.py"),
        "keyPressEvent 只读 event.key()",
    )

    # ------------------------------------------------------------ §7 设置窗口
    section("7. 设置窗口：`呼出快捷键` 那一行已解锁")

    from deskpet import settings as st
    from deskpet.config import ConfigStore

    hotkey_field = [f for f in st.FIELDS if f.key == "hotkey"][0]
    check("`呼出快捷键` 已实现（implemented=True）", hotkey_field.implemented is True)
    check(
        "描述表里的标题是**裸标题**（后缀由界面渲染时追加，不写进真值）",
        hotkey_field.title == "呼出快捷键" and st.NOT_READY_SUFFIX not in hotkey_field.title,
        hotkey_field.title,
    )
    check(
        # ⚠ 原文写的是"现在只剩 `开机自启` 一行未实现（task14）"——那句是 task12
        # 对**当时**边界的描述，而 task14 已经把最后一行接上了。
        # 与 task10 更新 task07、task12 更新 task11 那几条同理：被下游 task
        # 推掉的边界要按事实改，不能留一句"其实还有一个"的旧说法。
        "描述表里已经没有未实现的项了（task14 把 `开机自启` 也接上了）",
        [f.title for f in st.FIELDS if not f.implemented] == [],
        "、".join(f.title for f in st.FIELDS if not f.implemented) or "（没有）",
    )

    scratch = _fresh_dir(SCRATCH)
    store = ConfigStore(scratch / "config.json")
    store.load()
    sw = st.SettingsWindow(store, current_pet="weedy")
    key_widget = sw._widgets["hotkey"]
    check(
        "`呼出快捷键` 控件**可用**（不再禁用）",
        key_widget.isEnabled(),
        f"enabled={key_widget.isEnabled()}",
    )
    # 用户看得见的是**渲染出来的标题**：`（暂未开放）` 现在**一个都不该有**
    # （task14 接上了最后一行；`NOT_READY_SUFFIX` 机制仍在 `_build_row_label` 里）。
    from PySide6.QtWidgets import QLabel

    labels = [label.text() for label in sw.findChildren(QLabel)]
    check(
        "渲染后的标题里 `呼出快捷键` **不带** `（暂未开放）`",
        "呼出快捷键" in labels and "呼出快捷键（暂未开放）" not in labels,
        "、".join(t for t in labels if "快捷键" in t),
    )
    check(
        "`（暂未开放）` 已经一个都不剩（task14 之后没有未实现的行）",
        [t for t in labels if "（暂未开放）" in t] == [],
        "、".join(t for t in labels if "（暂未开放）" in t) or "（没有）",
    )
    check(
        "初始显示配置里的值",
        key_widget.display_text == store.get("hotkey"),
        f"{key_widget.display_text!r}",
    )

    # 没装处理器 → 只写配置
    sw._on_keyseq(hotkey_field, "Ctrl+Alt+P")
    check(
        "没装换热键处理器时：只写配置（自检场景）",
        store.get("hotkey") == "Ctrl+Alt+P",
        f"{store.get('hotkey')!r}",
    )

    # 装了处理器 → 换成功
    calls: list[tuple[str, str]] = []

    def _changer(new: str, previous: str) -> st.HotkeyOutcome:
        calls.append((new, previous))
        if new == "Ctrl+Alt+Shift+Z":
            return st.HotkeyOutcome(False, None, hs.REASON_OCCUPIED)
        return st.HotkeyOutcome(True, new, "")

    sw.set_hotkey_changer(_changer)
    key_widget.set_shortcut_text("Ctrl+Alt+P")
    sw._on_keyseq(hotkey_field, "Ctrl+Shift+Q")
    check("换热键：处理器被调用（新, 旧）", calls[-1] == ("Ctrl+Shift+Q", "Ctrl+Alt+P"), f"{calls[-1]}")
    check("换成功后写进配置", store.get("hotkey") == "Ctrl+Shift+Q", f"{store.get('hotkey')!r}")
    check("状态栏说成功", "Ctrl+Shift+Q" in sw._status_label.text(), sw._status_label.text())

    # 换失败 → 回滚到原来那个
    calls.clear()
    before = store.get("hotkey")
    sw._on_keyseq(hotkey_field, "Ctrl+Alt+Shift+Z")
    check(
        "换失败：先试新的、再**把旧的注册回来**",
        calls[0][0] == "Ctrl+Alt+Shift+Z" and calls[1][0] == before,
        f"{calls}",
    )
    check(
        "换失败：配置**没变**（还是原来那个）",
        store.get("hotkey") == before,
        f"{store.get('hotkey')!r}",
    )
    check(
        "换失败：控件退回真实值",
        key_widget.display_text == before,
        key_widget.display_text,
    )
    check(
        "换失败：状态栏显示失败原因（占用文案）",
        hs.REASON_OCCUPIED in sw._status_label.text(),
        sw._status_label.text(),
    )

    sw.deleteLater()
    shutil.rmtree(SCRATCH, ignore_errors=True)

    # ------------------------------------------------------------ §8 源码级红线
    section("8. 源码级红线：没有用 QShortcut 冒充全局热键 / 没有 120 ms 轮询")

    app_code = _code_only("app.py")
    check(
        "`app.py` 里没有用 QShortcut 当热键（那是窗口内快捷键）",
        "QShortcut" not in app_code,
        "QShortcut 只出现在 pet_window 的 Esc/Ctrl+Q 上",
    )
    hk_code = _code_only("hotkey.py")
    check(
        "热键模块里没有 120 ms 轮询",
        "120" not in hk_code,
        "取件间隔是 PICKUP_INTERVAL_MS = 100",
    )
    check(
        "热键**不是**挂在动画帧回调上（不依赖 AnimationDriver）",
        "AnimationDriver" not in hk_code and "frame_due" not in hk_code,
        "走 hotkey_pressed 信号",
    )
    check(
        "注册走的是 RegisterHotKey（不是 QShortcut / 第三方库）",
        "register_hotkey" in hk_code and "QShortcut" not in hk_code,
        "hotkey.py 调 win32.register_hotkey",
    )
    check(
        "没有引入第三方全局热键库（keyboard / pynput）",
        all(
            name not in _source_of(f"{m}.py")
            for m in ("hotkey", "hotkey_spec")
            for name in ("import keyboard", "import pynput", "from pynput")
        ),
        "只用 ctypes + Win32",
    )
    check(
        "分两拍处理：热键槽里用 singleShot(0, ...) 而不是直接 show",
        "QTimer.singleShot(0,self.toggle)" in _squashed("app.py"),
        "QTimer.singleShot(0, self.toggle)",
    )
    check(
        "退出路径**必须**释放热键",
        "hotkey_controller.stop()" in _squashed("app.py"),
        "finally 里调用",
    )
    check(
        "`hide_pet()` 是唯一隐藏入口（保留状态不销毁）",
        "hide_pet" in _code_only("pet_window.py"),
        "只 hide()，不销毁窗口",
    )

    # 真值只在一处的检查
    check(
        "`Ctrl+Alt+Shift+P` 这个字符串只在 hotkey.py 里定义一次",
        sum(
            _source_of(name).count('"Ctrl+Alt+Shift+P"')
            for name in ("app.py", "pet_window.py", "settings.py", "keycapture.py")
        )
        == 0,
        "app/settings/pet_window/keycapture 里没有硬编码默认组合",
    )

    return _finish()


# ------------------------------------------------------------------ 小工具


def _reason_of(hs, text: str) -> str:
    try:
        hs.parse_combo(text)
    except hs.ComboError as exc:
        return exc.reason
    return "（没有被拒）"


def _press(widget, key) -> None:
    """给控件喂一次真实的按键事件（走产品的 `keyPressEvent`）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    event = QKeyEvent(QEvent.Type.KeyPress, key, Qt.NoModifier)
    widget.keyPressEvent(event)


def _make_pet():
    """造一个最小素材包（**只写在 scratch 里，`assets/` 一个字节都不动**）。"""
    import json

    root = _fresh_dir(SCRATCH / "pets" / "t12pet")
    frames = root / "frames"
    frames.mkdir(parents=True)
    from PySide6.QtGui import QImage, QColor

    for name in ("idle_00.png", "idle_01.png", "rest_00.png"):
        image = QImage(40, 40, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        for y in range(10, 30):
            for x in range(5, 15):
                image.setPixelColor(x, y, QColor(200, 40, 40, 255))
        image.save(str(frames / name), "PNG")
    (root / "asset.json").write_text(
        json.dumps(
            {
                "name": "task12 自检包",
                "cell": [40, 40],
                "actions": {
                    "idle": {"frames": ["idle_00.png", "idle_01.png"], "fps": 30.0},
                    "rest": {"frames": ["rest_00.png"], "fps": 30.0},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    from deskpet import pet_loader

    return pet_loader.load_pet(root)


if __name__ == "__main__":
    sys.exit(main())
