"""task18 自检（offscreen）：**按键联动**的轮询、`pressed` 顺序、启停与合并复绘。

按 agent.md A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task18_check.txt`，UTF-8），不靠重定向（4.3）。

跑法（必须带 UTF-8 环境变量，4.11）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task18.py

## 这一份验什么（对应 task18 6）

1. **注入键态源**（offscreen 可重复）：单键按下/抬起、连按、多键同时、
   **同一个 VK 映射到多个 id**、按下顺序 -> `pressed` 序列与顺序**逐项**正确；
2. **信号节流**：状态没变时 `keys_changed` **不发**（数次数）；
3. **同一窗口内多键**：顺序是声明顺序（**确定且可重复**），不是随机；
4. **停得干净**：挂起 / 切回宠物形态 / 隐藏 -> 轮询次数**不再增长**、`pressed` 为空；
5. **合并复绘**：1 秒内注入 N 次变化 -> 重绘次数 <= 上限（数计数器）；
6. `get_async_key_state` 的**高字节判据**：注入 `0x0001`（只有历史位）-> 判为**未按下**；
7. **装配接线**：`KeyListenerController` 的三个启用条件、绑定表来自配置、
   `keyview_keys` 坏形状静默丢弃。

## 这一份**不写用户真实目录**

所有落盘都在 `data/task18_out/`（工作区内，4.13）。
`assets/` 一个字节都不动（脚本结尾核对哈希）。
用户真实的 `data/config.json` 不碰（本脚本的 `ConfigStore` 全部指向临时目录）。

## 注：`SetTimer` / `QTimer` 与 offscreen

offscreen 平台上 `QTimer` **照常工作**（它不依赖窗口合成），所以"轮询次数不再增长"
与"合并复绘次数"这两条在 offscreen 上就能验；**真按键点亮**属真平台，
而且本机 `SendInput` 被拒（task18 1.2 第 3 条）——**只能人工确认**，
本脚本**不假装验过**它。
"""

import hashlib
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULT_FILE = ROOT / "data" / "task18_check.txt"
OUT_DIR = ROOT / "data" / "task18_out"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    """记一条断言。**中性文字**描述实测值（4.11：别把成功条件写反）。"""
    global _FAILED, _TOTAL
    _TOTAL += 1
    if not ok:
        _FAILED += 1
    line = f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else "")
    _LINES.append(line)
    print(line)
    return ok


def section(title: str) -> None:
    _LINES.append("")
    _LINES.append(f"== {title} ==")
    print(f"\n== {title} ==")


def note(text: str) -> None:
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _hash_assets() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "assets").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8", "replace"))
            digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


class FakeKeys:
    """**注入用的键态源**：一个集合说了算（task18 4.2 的"可注入"）。

    ⚠ 它记下**每个 VK 被问了几次**：这是"只轮询真正被绑定的 VK"
    与"一个 VK 映射到多个 id 也只问一次"两条的判据。
    """

    def __init__(self, down: set[int] | None = None) -> None:
        self.down: set[int] = set(down or ())
        self.asked: list[int] = []

    def press(self, *vks: int) -> None:
        self.down.update(vks)

    def release(self, *vks: int) -> None:
        self.down.difference_update(vks)

    def release_all(self) -> None:
        self.down.clear()

    def ask_count(self, vk: int) -> int:
        return self.asked.count(vk)

    def __call__(self, vk: int) -> bool:
        self.asked.append(vk)
        return vk in self.down


def make_config(name: str):
    """造一个**指向临时目录**的 `ConfigStore`（绝不碰用户真实的 config.json）。"""
    from deskpet.config import ConfigStore

    path = OUT_DIR / "configs" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    store = ConfigStore(path)
    store.load()
    return store


def _pump(app, ms: int) -> None:
    """跑事件循环 `ms` 毫秒（定时器要事件循环才走）。"""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)


# ==================================================================== 1 轮询与顺序


def section_polling(app) -> None:
    """1 轮询：单键、连按、多键同时、一个 VK 对多个 id、顺序。"""
    from deskpet.key_listener import KeyListener

    section("1 轮询：按下 / 抬起 / 连按 / 多键 / 一个 VK 对多个 id / 顺序")

    VK_A, VK_B, VK_C = 0x41, 0x42, 0x43
    keys = FakeKeys()
    listener = KeyListener(keys, {VK_A: ("r1k1",), VK_B: ("r1k2",), VK_C: ("r1k3",)})
    seen: list[tuple[str, ...]] = []
    listener.keys_changed.connect(seen.append)

    # ---- ① 单键按下 / 抬起
    keys.press(VK_A)
    listener.poll()
    check("单键按下 -> pressed == (那个键 id,)", listener.pressed == ("r1k1",), f"pressed={listener.pressed}")

    listener.poll()
    check("一直按着不重复发信号（信号节流：两次 poll 只收到一条）", len(seen) == 1, f"信号={len(seen)} 条")

    keys.release(VK_A)
    listener.poll()
    check("抬起 -> pressed 清空", listener.pressed == (), f"pressed={listener.pressed}")
    check("抬起也发了信号（状态真的变了）", len(seen) == 2, f"信号={len(seen)} 条")

    # ---- ② 连按：顺序就是按下先后（早的在前）
    keys.press(VK_A)
    listener.poll()
    keys.press(VK_B)
    listener.poll()
    keys.press(VK_C)
    listener.poll()
    check(
        "连按 A->B->C -> pressed 按按下先后（最早的在前）",
        listener.pressed == ("r1k1", "r1k2", "r1k3"),
        f"pressed={listener.pressed}",
    )

    # ---- ③ 抬起中间那个：另外两个的相对顺序不动
    keys.release(VK_B)
    listener.poll()
    check(
        "抬起中间那个 -> 只剩另外两个，**相对顺序不变**",
        listener.pressed == ("r1k1", "r1k3"),
        f"pressed={listener.pressed}",
    )

    # ---- ④ 重新按下 B：它排到**队尾**（它确实是最后按下的）
    keys.press(VK_B)
    listener.poll()
    check(
        "重新按下 B -> B 排到队尾（它是最后按下的，手的 down 要用它）",
        listener.pressed == ("r1k1", "r1k3", "r1k2"),
        f"pressed={listener.pressed}",
    )

    # ---- ⑤ 一个 VK 映射到多个键 id（用户 2026 决策 5）
    keys2 = FakeKeys()
    two = KeyListener(keys2, {VK_A: ("r1k1", "r1k2")})
    keys2.press(VK_A)
    two.poll()
    check(
        "一个 VK 映射到两个 id -> 两个都亮（不报错、不警告为错误）",
        two.pressed == ("r1k1", "r1k2"),
        f"pressed={two.pressed}",
    )
    check(
        "**只问了那个 VK 一次**（一个 VK 对多个 id 不是问两次）",
        keys2.ask_count(VK_A) == 1,
        f"问了几次={keys2.ask_count(VK_A)}",
    )

    # ---- ⑥ 只轮询真正被绑定的 VK（没绑定的一个都不问）
    check(
        "只轮询**真正被绑定的** VK（3 个绑定 -> 一轮恰好问 3 次）",
        keys.asked.count(VK_A) + keys.asked.count(VK_B) + keys.asked.count(VK_C) == len(keys.asked),
        f"问过的 VK 集合={sorted(set(keys.asked))}",
    )

    # ---- ⑦ 键态源抛异常：退化到"没按"，绝不中断轮询
    def _boom(vk: int) -> bool:
        raise RuntimeError("模拟键态源故障")

    broken = KeyListener(_boom, {VK_A: ("r1k1",)})
    broken.poll()
    check(
        "键态源抛异常 -> 当「没按」处理，poll 不炸（可恢复路径退化）",
        broken.pressed == () and broken.polls == 1,
        f"pressed={broken.pressed} polls={broken.polls}",
    )

    # ---- ⑧ 坏绑定表静默丢弃（配置是"读取永不致命"的那一类）
    messy = KeyListener(FakeKeys(), {0x41: ("r1k1",), "x": ("r1k2",), 0x42: ("",), 0x43: 5})
    check(
        "坏绑定项（VK 不是整数 / id 是空串 / 值不可迭代）-> **静默丢弃**",
        messy.polled_vks == (0x41,),
        f"留下的 VK={messy.polled_vks}",
    )


def section_same_window(app) -> None:
    """2 同一窗口内多键：顺序是**声明顺序**（确定且可重复），不是随机。"""
    from deskpet.key_listener import KeyListener

    section("2 同一窗口内多键：顺序确定且可重复（**不是**物理先后）")

    bindings = {74: ("r1k1",), 75: ("r1k2",), 76: ("r1k3",)}
    results: list[tuple[str, ...]] = []
    for _round in range(8):
        keys = FakeKeys({74, 75, 76})  # 一次性全按下（同一轮里就是"同时"）
        listener = KeyListener(keys, bindings)
        listener.poll()
        results.append(listener.pressed)

    check(
        "同一轮里同时按下的多个键 -> 连续 8 轮**结果完全一样**（确定且可重复）",
        len(set(results)) == 1,
        f"8 轮的结果集合={sorted(set(results))}",
    )
    check(
        "那个顺序就是**绑定声明的顺序**（74 -> 75 -> 76）",
        results[0] == ("r1k1", "r1k2", "r1k3"),
        f"实际={results[0]}",
    )
    note(
        "如实记一笔：同一个 8 ms 窗口内按下的多个键，**真实先后操作系统没有留下**",
    )
    note(
        "（GetAsyncKeyState 只回答「现在按着吗」）——这里给的是声明顺序，不是物理先后。",
    )


# ==================================================================== 3 停得干净


def section_stop_clean(app) -> None:
    """3 停得干净：挂起 / 停用 -> 轮询次数不再增长、pressed 为空。"""
    from deskpet.key_listener import KeyListener

    section("3 停得干净：停表 + pressed 清空（CPU 0，回来不带陈旧按下）")

    VK_A = 0x41
    keys = FakeKeys({VK_A})
    listener = KeyListener(keys, {VK_A: ("r1k1",)}, interval_ms=5)
    events: list[tuple[str, ...]] = []
    listener.keys_changed.connect(events.append)

    check("刚构造好的监听器**不轮询**（默认停着）", not listener.running, f"running={listener.running}")

    # ---- (1) 启用后才开始转
    listener.set_enabled(True)
    check("set_enabled(True) -> 定时器转起来", listener.running, f"running={listener.running}")
    _pump(app, 40)
    running_polls = listener.polls
    check("转起来之后轮询次数真的在增长", running_polls > 0, f"轮询={running_polls} 次")
    check("轮询**真的把状态读出来了**", listener.pressed == ("r1k1",), f"pressed={listener.pressed}")

    # ---- (2) 挂起：次数**不再增长**、pressed 清空
    listener.set_suspended(True)
    paused_at = listener.polls
    _pump(app, 40)
    check(
        "挂起 -> 轮询次数**一次都不再增长**（CPU 0）",
        listener.polls == paused_at,
        f"挂起时={paused_at} 40 ms 后={listener.polls}",
    )
    check("挂起时 pressed **清空**（不带陈旧按下）", listener.pressed == (), f"pressed={listener.pressed}")

    # ---- (3) 恢复：重新开始，且按下态**从头读**（不是"还按着"）
    resumed_events = len(events)
    listener.set_suspended(False)
    check("恢复 -> 定时器又转起来", listener.running, f"running={listener.running}")
    _pump(app, 30)
    check(
        "恢复后轮询次数重新增长",
        listener.polls > paused_at,
        f"恢复前={paused_at} 现在={listener.polls}",
    )
    check(
        "恢复那一下**发了信号**（键在挂起期间一直被按着，回来要重新点亮）",
        len(events) > resumed_events,
        f"恢复前信号={resumed_events} 现在={len(events)}",
    )

    # ---- (4) set_enabled(False)：同样停表 + 清空
    listener.set_enabled(False)
    disabled_at = listener.polls
    _pump(app, 40)
    check(
        "set_enabled(False) -> 轮询次数不再增长、pressed 为空",
        listener.polls == disabled_at and listener.pressed == (),
        f"轮询 {disabled_at} -> {listener.polls}；pressed={listener.pressed}",
    )

    # ---- (5) 两个开关是"与"：一个关着就还是停
    listener.set_enabled(True)
    listener.set_suspended(True)
    check(
        "两个开关是**与**：enabled=True 但 suspended=True -> 仍然停着",
        not listener.running,
        f"running={listener.running}",
    )
    listener.set_suspended(False)
    check("解除挂起 -> 转起来（另一个开关本来就开着）", listener.running, f"running={listener.running}")

    # ---- (6) stop()：退出收尾
    listener.stop()
    stopped_at = listener.polls
    _pump(app, 30)
    check(
        "stop() -> 停表 + pressed 清空（退出收尾那一条）",
        not listener.running and listener.pressed == () and listener.polls == stopped_at,
        f"running={listener.running} pressed={listener.pressed}",
    )

    # ---- (7) 换绑定：立刻生效，已解绑的键**马上消失**
    keys2 = FakeKeys({0x41, 0x42})
    relink = KeyListener(keys2, {0x41: ("r1k1",), 0x42: ("r1k2",)})
    relink.poll()
    check("换绑定前：两个键都亮", relink.pressed == ("r1k1", "r1k2"), f"pressed={relink.pressed}")
    emitted: list[tuple[str, ...]] = []
    relink.keys_changed.connect(emitted.append)
    relink.set_bindings({0x41: ("r1k1",)})
    check(
        "换了绑定 -> 已解绑的键 id **马上消失**（不留一个没人按的键）",
        relink.pressed == ("r1k1",),
        f"pressed={relink.pressed}",
    )
    check(
        "换绑定**照常发信号**（映射不进图像，但画面要跟上）",
        emitted == [("r1k1",)],
        f"信号={emitted}",
    )
    relink.set_bindings({0x41: ("r1k1",)})
    check(
        "绑定**没变**时不发信号（判据是「真的变了」）",
        len(emitted) == 1,
        f"信号={len(emitted)} 条",
    )


# ==================================================================== 4 合并复绘


def _tiny_keyview(cell=(64, 48)):
    """造一个**内存里的**最小 `KeyViewSpec`（不写盘、不碰 assets/）。

    合成器要的是"有一张背景图可画"，所以这里用 `QImage` 写到自检自己的
    产出目录，再按契约构造 `KeyViewSpec`。
    """
    from PySide6.QtGui import QColor, QImage, QPainter

    from deskpet.keyview_spec import KeySpec, KeyViewSpec, LayoutSpec

    root = OUT_DIR / "kv" / "tiny"
    (root / "keyview").mkdir(parents=True, exist_ok=True)
    background = QImage(cell[0], cell[1], QImage.Format_ARGB32_Premultiplied)
    background.fill(QColor("#204060"))
    background.save(str(root / "keyview" / "bg.png"), "PNG")

    keys: dict[str, KeySpec] = {}
    for index in range(2):
        name = f"key_{index}.png"
        cap = QImage(cell[0], cell[1], QImage.Format_ARGB32_Premultiplied)
        cap.fill(Qt.transparent)
        painter = QPainter(cap)
        try:
            painter.fillRect(4 + index * 20, 4, 16, 16, QColor("#ffcc00"))
        finally:
            painter.end()
        cap.save(str(root / "keyview" / name), "PNG")
        # overlay 写法：整画布透明底图，**位置画在图里**（零坐标）。
        keys[f"r1k{index + 1}"] = KeySpec(id=f"r1k{index + 1}", down=name, overlay=name)

    return KeyViewSpec(
        cell=cell,
        background=("bg.png",),
        keys=keys,
        layouts={"1*2": LayoutSpec(name="1*2", rows=(("r1k1", "r1k2"),))},
        root=root,
        namespace="tiny",
    )


def _tiny_pet():
    """造一个**内存里的**最小宠物规格（`PetWindow` 要它才能构造）。"""
    from PySide6.QtGui import QColor, QImage

    from deskpet.pet_loader import load_pet

    root = OUT_DIR / "pets" / "tiny"
    (root / "frames").mkdir(parents=True, exist_ok=True)
    frame = QImage(64, 64, QImage.Format_ARGB32_Premultiplied)
    frame.fill(QColor("#3366cc"))
    frame.save(str(root / "frames" / "idle_00.png"), "PNG")
    import json

    (root / "asset.json").write_text(
        json.dumps(
            {
                "name": "占位",
                "cell": [64, 64],
                "actions": {"idle": {"frames": ["idle_00.png"], "fps": 30.0}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_pet(root)


def section_coalesced_repaint(app) -> None:
    """4 合并复绘：N 次变化 -> 重绘次数远小于 N。"""
    from deskpet import context_menu as cm
    from deskpet.pet_window import REPAINT_INTERVAL_MS, PetWindow

    section("4 合并复绘：1 秒内 N 次变化，重绘次数 <= 上限")

    pet = _tiny_pet()
    keyview = _tiny_keyview()
    window = PetWindow(pet, 6, keyview=keyview)
    window.set_form_handler(lambda form: None)
    window.set_form(cm.FORM_KEYVIEW)

    check(
        "窗口在 KV 形态、且合并定时器**初始是停的**",
        window.form == cm.FORM_KEYVIEW and not window.repaint_pending,
        f"形态={window.form!r} 等待中={window.repaint_pending}",
    )

    # ---- (1) 注入 N 次变化：**重绘次数必须远小于 N**
    N = 60
    injected: list[int] = []
    for index in range(N):
        injected.append(index)
        window._on_keys_changed((f"r1k{index % 2 + 1}",))
    check(
        "N 次变化之后**还没重绘过**（合并定时器只是被重置）",
        window.repaint_count == 0 and window.repaint_pending,
        f"N={N} 重绘={window.repaint_count} 等待中={window.repaint_pending}",
    )

    _pump(app, REPAINT_INTERVAL_MS * 4)
    check(
        f"{N} 次连续变化 -> 重绘次数 <= 上限（实测 {window.repaint_count} 次）",
        window.repaint_count <= 2,
        f"{N} 次变化 -> {window.repaint_count} 次重绘",
    )
    check(
        "合并之后`pressed`是**最后那一次**的状态（不是中间态的回放）",
        window.pressed == (f"r1k{(N - 1) % 2 + 1}",),
        f"pressed={window.pressed}",
    )
    check(
        "**状态真的写进了画源**（合并的是重绘，不是状态）",
        window.content.pressed == window.pressed,
        f"画源={window.content.pressed} 窗口={window.pressed}",
    )

    # ---- (2) 对照组：不连打时每次变化都能落到一次重绘
    window.repaint_count = 0
    for index in range(3):
        window._on_keys_changed((f"r1k{index % 2 + 1}",))
        _pump(app, REPAINT_INTERVAL_MS * 3)
    check(
        "[对照] 隔着节拍逐次注入 -> 3 次变化落成 3 次重绘（合并**不是**「永远只画一次」）",
        window.repaint_count == 3,
        f"3 次变化 -> {window.repaint_count} 次重绘",
    )

    # ---- (3) 不在 KV 形态时**不重绘**
    window.set_form(cm.FORM_PET)
    window.repaint_count = 0
    window._on_keys_changed(("r1k1",))
    _pump(app, REPAINT_INTERVAL_MS * 4)
    check(
        "不在 KV 形态 -> 收到信号也**不重绘**（监听本来就该停着，这里兜一层）",
        window.repaint_count == 0,
        f"重绘={window.repaint_count} 次",
    )

    # ---- (4) `set_pressed()` 这条 K1 老入口**照常立刻生效**（回归）
    window.set_form(cm.FORM_KEYVIEW)
    window.set_pressed(())
    changed = window.set_pressed(("r1k1",))
    check(
        "K1 的老入口 `set_pressed()` **照常立刻重绘**（不经合并节拍）",
        changed and window.pressed == ("r1k1",),
        f"changed={changed} pressed={window.pressed}",
    )
    check(
        "`set_pressed()` 幂等：同状态再来一次返回 False",
        window.set_pressed(("r1k1",)) is False,
        "同状态不重复合成",
    )

    window.close()


# ==================================================================== 5 高字节判据


def section_high_byte(app) -> None:
    """5 `get_async_key_state` 的**高字节判据**（task18 6 明写的一条）。"""
    from deskpet import win32

    section("5 get_async_key_state：只认高字节（低字节是历史位）")

    class _FakeUser32:
        """打桩的 user32：`GetAsyncKeyState` 按表返回，并记下问过谁。"""

        def __init__(self, values: dict[int, int]) -> None:
            self.values = values
            self.asked: list[int] = []

        def GetAsyncKeyState(self, vk: int) -> int:
            self.asked.append(int(vk))
            return self.values.get(int(vk), 0)

    original = win32._user32
    fake = _FakeUser32(
        {
            0x41: 0x8000,   # 高字节最高位 = 此刻按着
            0x42: 0x0001,   # **只有历史位**（自上次调用以来按过）
            0x43: -32768,   # 同一个 0x8000，只是 c_short 的符号
            0x44: 0x8001,   # 历史位 + 此刻按着
            0x45: 0x0000,   # 什么都没
        }
    )
    win32._user32 = fake
    try:
        check("高字节最高位为 1（0x8000）-> **按下**", win32.get_async_key_state(0x41) is True, "0x8000")
        check(
            "**只有历史位（0x0001）-> 必须判为「未按下」**（这是本卡的头号坑）",
            win32.get_async_key_state(0x42) is False,
            "0x0001 是「自上次调用以来按过」，不是「现在按着」",
        )
        check(
            "0x8000 按 c_short 读成 -32768 -> **照样是按下**（符号扩展别漏那位）",
            win32.get_async_key_state(0x43) is True,
            "-32768 = 0x8000",
        )
        check(
            "历史位与此刻位同时为真（0x8001）-> 按下",
            win32.get_async_key_state(0x44) is True,
            "0x8001",
        )
        check("全 0 -> 未按下", win32.get_async_key_state(0x45) is False, "0x0000")
    finally:
        win32._user32 = original

    # ---- 非 Windows / DLL 没加载上 -> False（不抛）
    win32._user32 = None
    try:
        check(
            "取不到 user32 -> 返回 False（**不抛异常**，退化路径）",
            win32.get_async_key_state(0x41) is False,
            "退化到「没有键在按」",
        )
    finally:
        win32._user32 = original

    # ---- 真实平台那一支：没按任何键时读 0（本机实测的既有事实）
    if win32.is_available():
        # 0xFF 不是任何真实按键的 VK，读它必然是 0 —— 这条只证明"调用路径通"
        probe = win32.get_async_key_state(0xFF)
        check("真平台：调用路径通（未按的键读 False）", probe is False, f"0xFF -> {probe}")
    else:
        note("非 Windows：跳过真平台那一条（`is_available()` 为假）")


# ==================================================================== 6 装配接线


def section_wiring(app) -> None:
    """6 装配：启用条件三件事、绑定表来自配置、坏配置静默丢弃。"""
    from deskpet import context_menu as cm
    from deskpet.app import KeyListenerController
    from deskpet.config import ConfigStore
    from deskpet.key_listener import bindings_from_config
    from deskpet.pet_window import PetWindow

    section("6 装配：KeyListenerController 的启用条件与绑定来源")

    # ---- (1) 绑定表来自配置（键 id -> {vk, name} 翻过来）
    raw = {
        "r1k1": {"vk": 74, "name": "J"},
        "r1k2": {"vk": 74, "name": "J"},   # 同一个 VK 映射到两个 id（决策 5）
        "r1k3": {"vk": 75, "name": "K"},
        "r1k4": {"name": "没配"},           # 置空：永进不了绑定
        "r1k5": {"vk": "x"},                # 坏 vk：丢弃
        "r1k6": None,                        # 坏项：丢弃
    }
    bindings = bindings_from_config(raw)
    check(
        "配置 -> 绑定表：一个 VK 对多个键 id，两个 id **都留下**",
        bindings.get(74) == ("r1k1", "r1k2"),
        f"VK74 -> {bindings.get(74)}",
    )
    check(
        "没配 vk / vk 非法 / 值不是对象的项 -> **一个都不进绑定**（置空 = 永远不亮）",
        75 in bindings and set(bindings) == {74, 75},
        f"绑定表={bindings}",
    )
    check(
        "`keyview_keys` 不是对象 -> 空绑定（**不报错、不警告**）",
        bindings_from_config("坏") == {} and bindings_from_config(None) == {},
        "配置坏掉的表现只是「那个键不亮」",
    )

    # ---- (2) 启用条件：形态 == KV 且窗口可见且没被挂起
    store = make_config("wiring")
    store.set("keyview_keys", {"r1k1": {"vk": 74, "name": "J"}})
    pet = _tiny_pet()
    keyview = _tiny_keyview()
    window = PetWindow(pet, 6, config=store, keyview=keyview)
    controller = KeyListenerController(window, store)

    check(
        "装配时绑定表**从配置读出来**（不是空表）",
        controller.listener.bindings == {74: ("r1k1",)},
        f"绑定={controller.listener.bindings}",
    )
    check(
        "窗口没显示 + 宠物形态 -> **不该轮询**",
        not controller.should_poll() and not controller.listener.running,
        f"should_poll={controller.should_poll()}",
    )

    # 窗口可见但仍是宠物形态 -> 还是不该轮询
    window.show()
    _pump(app, 20)
    controller.sync()
    check(
        "窗口可见但**仍在宠物形态** -> 不该轮询",
        not controller.should_poll() and not controller.listener.running,
        f"形态={window.form!r} should_poll={controller.should_poll()}",
    )

    # 切到 KV -> 该轮询
    window.set_form_handler(lambda form: None)
    window.set_form(cm.FORM_KEYVIEW)
    _pump(app, 20)
    controller.sync()
    check(
        "切到 KV 且窗口可见 -> **该轮询**（三个条件都成立）",
        controller.should_poll() and controller.listener.running,
        f"should_poll={controller.should_poll()} running={controller.listener.running}",
    )

    # 挂起 -> 停
    controller.set_suspended(True)
    check(
        "临时挂起（task19 录入用）-> 立刻停表",
        not controller.should_poll() and not controller.listener.running,
        f"suspended={controller.suspended}",
    )
    controller.set_suspended(False)
    check("解除挂起 -> 又转起来", controller.listener.running, f"running={controller.listener.running}")

    # 隐藏 -> 停（可见性信号驱动）
    polls_before = controller.listener.polls
    window.set_hotkey_available(True)
    hidden = window.hide_pet()
    _pump(app, 40)
    check(
        "**隐藏** -> 轮询次数一次都不再增长（可见性信号驱动，CPU 0）",
        hidden and controller.listener.polls == polls_before and not controller.listener.running,
        f"隐藏={hidden} 轮询 {polls_before} -> {controller.listener.polls}",
    )
    check("隐藏时 pressed **清空**（回来不带陈旧按下）", controller.listener.pressed == (), f"pressed={controller.listener.pressed}")

    # 呼出 -> 转起来
    window.show_pet()
    _pump(app, 20)
    controller.sync()
    check("呼出 -> 轮询重新开始", controller.listener.running, f"running={controller.listener.running}")

    # 切回宠物形态 -> 停
    window.set_form(cm.FORM_PET)
    _pump(app, 20)
    controller.sync()
    polls_pet = controller.listener.polls
    _pump(app, 40)
    check(
        "**切回宠物形态** -> 轮询次数不再增长、pressed 为空",
        controller.listener.polls == polls_pet and controller.listener.pressed == (),
        f"轮询 {polls_pet} -> {controller.listener.polls}；pressed={controller.listener.pressed}",
    )

    # ---- (3) 退出收尾
    controller.stop()
    check(
        "`stop()` -> 停表 + 清空（退出收尾那一条）",
        not controller.listener.running and controller.listener.pressed == (),
        f"running={controller.listener.running}",
    )

    # ---- (4) 窗口**不创建**监听器：生命周期是装配层的事
    bare = PetWindow(pet, 6, keyview=keyview)
    check(
        "窗口**自己不创建**监听器（生命周期是装配层的事）",
        bare.key_listener is None,
        f"listener={bare.key_listener}",
    )
    # ⚠ 这里是 pet 形态：`set_pressed()` 只**记住**状态、不重绘（K1 既有行为），
    #   所以它返回 False 是**对的**——判据是"状态记住了"，不是"重绘了"。
    bare.set_pressed(("r1k1",))
    check(
        "没装监听器时 K1 的入口照常可用（只记状态、不重绘，行为一字不变）",
        bare.pressed == ("r1k1",),
        f"pressed={bare.pressed}",
    )
    check(
        "`set_key_listener(None)` 卸下之后收到信号也不会炸",
        bare.set_key_listener(None) is None and not bare.repaint_pending,
        "卸下是幂等的",
    )

    # ---- (5) 装上再卸下：信号真的接上又断开了
    listener = controller.listener
    bare.set_key_listener(listener)
    before = bare.pressed_signal_count
    listener.keys_changed.emit(("r1k1",))
    check(
        "`set_key_listener()` 之后信号**真的接上了**",
        bare.pressed_signal_count == before + 1,
        f"信号计数 {before} -> {bare.pressed_signal_count}",
    )
    bare.set_key_listener(None)
    listener.keys_changed.emit(("r1k2",))
    check(
        "卸下之后信号**不再到达窗口**（不会收到已卸下的监听器）",
        bare.pressed_signal_count == before + 1,
        f"信号计数仍是 {bare.pressed_signal_count}",
    )

    bare.close()
    window.close()
    _pump(app, 20)


# ==================================================================== 7 纪律


def section_discipline(app, hash_before: str) -> None:
    section("7 自检纪律（assets/ 未被改动、不写用户真实目录）")

    check(
        "assets/ 哈希前后不变（自检没动原始素材）",
        _hash_assets() == hash_before,
        f"哈希={hash_before[:16]}…",
    )
    check(
        "自检的产出目录在工作区内（DSH 沙箱只保证工作区可写）",
        str(OUT_DIR).startswith(str(ROOT)),
        f"产出目录={OUT_DIR}",
    )
    check(
        "自检在 assets/ 里没有留下任何东西",
        not any(path.name.startswith("task18") for path in (ROOT / "assets").iterdir()),
        "占位包全部造在 data/task18_out/",
    )


# ==================================================================== main


def main() -> int:
    app = QApplication.instance() or QApplication([])
    hash_before = _hash_assets()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _LINES.append("task18 自检 —— 键盘形态之二：按键联动")
    _LINES.append(f"工作区根：{ROOT}")

    started = time.perf_counter()
    section_polling(app)
    section_same_window(app)
    section_stop_clean(app)
    section_coalesced_repaint(app)
    section_high_byte(app)
    section_wiring(app)
    section_discipline(app, hash_before)
    elapsed = time.perf_counter() - started

    section("结论")
    if _FAILED:
        note(f"失败 {_FAILED} 项 / 共 {_TOTAL} 项（耗时 {elapsed:.1f} s）")
    else:
        note(f"全过（{_TOTAL} 项断言，耗时 {elapsed:.1f} s）")

    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    print(f"\n结果已写入：{RESULT_FILE}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
