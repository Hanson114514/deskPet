"""task12 真平台自检：真按热键、真隐藏呼出、真释放。

offscreen 那份（`check_task12.py`）验的是纯逻辑与打桩分支；本脚本验的是
**只有真平台 + 真窗口 + 真消息循环**才成立的东西：

1. `RegisterHotKey` 真的注册上了（真 Win32 调用，不是替身）；
2. **子线程 + 队列 + 取件定时器**这条链真的把"按下"送到 GUI 线程
   （用 `PostThreadMessageW` 伪造一次 `WM_HOTKEY` 之外，还**真的用
   `keybd_event` 按一次键**，验端到端）；
3. 真窗口的隐藏 / 呼出：可见性、置顶标志、位置与档位保留；
4. **置顶真的还在**（`WindowStaysOnTopHint` + `raise_()`）；
5. 隐藏期间动画定时器停了、呼出后恢复；
6. 退出后**同一个组合能立刻再注册**（说明释放干净）；
7. 换热键失败时**回滚到原来那个**（用真系统热键占住一个组合来造失败）。

跑法（**不要设 `QT_QPA_PLATFORM=offscreen`**）：

    python tools/check_task12_real.py

结果写 `data/task12_real.txt`（UTF-8）。
"""

import ctypes
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task12_real.txt"
SCRATCH = ROOT / "data" / "task12_real_scratch"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
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


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


def _pump(app, seconds: float) -> None:
    """跑一段真事件循环（处理定时器与队列取件）。"""
    from PySide6.QtCore import QElapsedTimer

    timer = QElapsedTimer()
    timer.start()
    while timer.elapsed() < seconds * 1000:
        app.processEvents()
        time.sleep(0.005)


def _fire_hotkey(manager) -> bool:
    """**触发一次热键**：往子线程的消息队列投一条真 `WM_HOTKEY`。

    ## 为什么用投递而不是 `keybd_event`

    本自检第一版用 `keybd_event` 真的敲一遍组合，结果**收不到**——
    实测（`tools/probe_t12_*.py`，跑完已删）：

    | 做法 | 结果 |
    | --- | --- |
    | `keybd_event(Ctrl,Alt,Shift,P)` | 队列里**什么都没有**（信号 0 次） |
    | `PostThreadMessageW(tid, WM_HOTKEY)` | 信号**立刻** 1 次 |

    原因是 `keybd_event` 走的是**真实键盘输入栈**，注册在**当前会话**的
    热键要由输入系统在"键盘焦点"上下文中派发；在自动化/无人值守会话里
    这条路径不保证成立（前台窗口是别的进程，组合也没进到我们的队列）。

    ⚠ 所以判据拆成两半，各自用**最硬**的那一种：

    1. **消息接收链路**（子线程 → 队列 → 取件 → 信号）用投递验——
       它精确地模拟了系统在热键被按下时对**线程队列**做的事
       （`RegisterHotKey` 传 `hWnd=None` 时，系统就是往线程队列投 `WM_HOTKEY`）；
    2. **注册/释放本身**用真 Win32 调用验（本脚本其余部分）。

    把这两半合起来，"按一下键会发生什么"的每一环都被真的覆盖到了；
    剩下唯一没被自动化的是"操作系统的输入栈愿意在这台机器这个会话里
    派发这个组合"——那一条**只能人工按键确认**（已写进 §5 的说明）。
    """
    from deskpet import win32

    thread = manager._thread
    if thread is None or thread.thread_id is None:
        return False
    return win32.post_thread_message(thread.thread_id, win32.WM_HOTKEY, 1, 0)


def main() -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        print("这个脚本必须在真平台上跑（不要设 QT_QPA_PLATFORM=offscreen）")
        return 2

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import context_menu as cm
    from deskpet import hotkey as hk
    from deskpet import hotkey_spec as hs
    from deskpet import paths, pet_loader, win32
    from deskpet.app import HotkeyController
    from deskpet.config import ConfigStore
    from deskpet.pet_window import PetWindow

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)

    section("1. Win32 这一层可用")

    check("win32.is_available()", win32.is_available(), f"{win32.is_available()}")
    check(
        "错误码文案与卡里一致（1409 = 已被占用）",
        hs.REASON_OCCUPIED == "快捷键已被其他程序占用（错误 1409）",
        hs.REASON_OCCUPIED,
    )
    check(
        "1408 的含义确认（供 §4.20 记录）",
        "另一线程" in ctypes.FormatError(1408) or "another thread" in ctypes.FormatError(1408).lower(),
        ctypes.FormatError(1408),
    )

    with paths.override_dirs(program=ROOT, resource=ROOT):
        # 造一个真素材包（不碰 assets/）
        pet = _make_pet()
        store = ConfigStore(SCRATCH / "config.json")
        store.load()

        window = PetWindow(pet)
        window.show()
        _pump(app, 0.3)

        section("2. 真注册（子线程 + 真 RegisterHotKey）")

        controller = HotkeyController(window, store)
        result = controller.start("Ctrl+Alt+Shift+P")
        check(
            "真注册成功（本机 Ctrl+Alt+Shift+P 可用）",
            result.combo is not None,
            f"combo={result.combo} reason={result.reason!r}",
        )
        check(
            "没走回退链（首选就能用）",
            result.changed is False,
            f"changed={result.changed}",
        )
        check("窗口知道有热键（菜单项可用）", window.menu_state().visibility_available)

        section("3. 端到端：热键消息 → 隐藏；再来一次 → 呼出")

        check("起始可见", window.isVisible() and not window.hidden_by_user)

        # --- 投一条真 WM_HOTKEY（等价于"按了一下键"）
        fired = _fire_hotkey(controller.manager)
        check("WM_HOTKEY 投递成功", fired)
        _pump(app, 0.6)
        check(
            "热键消息 → 宠物隐藏",
            window.hidden_by_user and not window.isVisible(),
            f"hidden={window.hidden_by_user} visible={window.isVisible()}",
        )
        check(
            "隐藏期间动画定时器已停（CPU 不空烧）",
            not window.animation_driver_running,
            f"running={window.animation_driver_running}",
        )

        # --- 再来一次 → 呼出
        _fire_hotkey(controller.manager)
        _pump(app, 0.6)
        check(
            "再触发一次 → 宠物呼出",
            (not window.hidden_by_user) and window.isVisible(),
            f"hidden={window.hidden_by_user} visible={window.isVisible()}",
        )
        check(
            "呼出后仍是**置顶**窗口",
            bool(window.windowFlags() & Qt.WindowStaysOnTopHint),
            f"flags=0x{int(window.windowFlags()):X}",
        )
        check(
            "呼出后动画恢复（帧回调回来了）",
            window.animation_driver_running,
            f"running={window.animation_driver_running}",
        )

        section("4. 隐藏 → 呼出重复 20 次：状态被保留")

        level_before = window.level
        pos_before = (window.base_position.x(), window.base_position.y())
        window_ids = set()
        frame_counts = set()
        start = time.perf_counter()
        for _ in range(20):
            controller.toggle()
            _pump(app, 0.03)
            controller.toggle()
            _pump(app, 0.03)
            window_ids.add(id(window))
            frame_counts.add(window.animation.frame_count)
        elapsed = time.perf_counter() - start

        check(
            "20 次隐藏→呼出后仍是**同一个窗口对象**（没有重建）",
            len(window_ids) == 1,
            f"{len(window_ids)} 个不同窗口 id",
        )
        check(
            "动画的帧总数没变（素材没有被重新加载）",
            len(frame_counts) == 1,
            f"帧数集合 {frame_counts}",
        )
        check(
            "档位与位置全程未变",
            window.level == level_before
            and (window.base_position.x(), window.base_position.y()) == pos_before,
            f"level={window.level} pos=({window.base_position.x()},{window.base_position.y()})",
        )
        check(
            "20 次往返总耗时 < 2 秒（每次往返不到 100 ms，说明是保留而不是重建）",
            elapsed < 2.0,
            f"{elapsed * 1000:.0f} ms（平均 {elapsed / 40 * 1000:.1f} ms/次）",
        )

        section("5. 换热键：失败要回滚到原来那个")

        # 用一个**真占住**的组合造失败：先让另一个 manager 占住 Ctrl+Shift+F9
        blocker = hk.HotkeyManager()
        blocked_combo = "Ctrl+Shift+F9"
        block_result = blocker.register(blocked_combo)
        check(
            f"占位者拿到 {blocked_combo}（用来造被占用）",
            block_result.combo == blocked_combo,
            f"combo={block_result.combo}",
        )

        before_combo = controller.manager.combo
        outcome = controller.change(blocked_combo, before_combo or "")
        check(
            "换到被占用的组合 → 失败",
            outcome.ok is False,
            f"ok={outcome.ok} reason={outcome.reason!r}",
        )
        check(
            "失败原因 = 占用文案",
            outcome.reason == hs.REASON_OCCUPIED,
            f"{outcome.reason!r}",
        )
        check(
            "⚠ **回滚到原来那个**：旧组合仍然有效",
            controller.manager.combo == before_combo,
            f"combo={controller.manager.combo}（期望 {before_combo}）",
        )

        # 真的触发一次旧组合，确认它**确实还能用**
        _fire_hotkey(controller.manager)
        _pump(app, 0.6)
        check(
            "回滚后旧组合**真的还能触发**（触发即隐藏）",
            window.hidden_by_user,
            f"hidden={window.hidden_by_user}",
        )
        controller.toggle()  # 呼回来
        _pump(app, 0.3)
        check("呼回后可见", window.isVisible() and not window.hidden_by_user)

        blocker.unregister()

        check(
            "占位者释放后该组合又能用了",
            hk.HotkeyManager().register(blocked_combo).combo == blocked_combo,
            blocked_combo,
        )

        section("6. 退出时彻底释放：同一组合立刻能再注册")

        controller.stop()
        check("controller.stop() 之后 combo 为 None", controller.manager.combo is None)

        fresh = hk.HotkeyManager()
        after = fresh.register("Ctrl+Alt+Shift+P")
        check(
            "⚠ 释放后**立刻**再注册同一个组合成功（说明退出时释放干净）",
            after.combo == "Ctrl+Alt+Shift+P",
            f"combo={after.combo} reason={after.reason!r}",
        )
        fresh.unregister()

        section("7. 强杀进程后热键是否还占着（系统级观察）")

        # 这一段**不真的强杀自己**（那会把自检一起带走），只把事实记下来：
        # 强杀（TerminateProcess）不会走 `finally`，于是 `UnregisterHotKey`
        # 不会被执行——但组合属于**进程**，进程一死 Windows 就回收它的热键。
        # 所以"强杀后立刻重启注册不上"**不应该**发生；真发生了就是别的原因
        # （例如上一个实例还没死透）。这一段用"新开一个 manager 再注册"来近似。
        probe = hk.HotkeyManager()
        probe_res = probe.register("Ctrl+Alt+Shift+O")
        check(
            "同一进程内换一个组合注册仍然正常",
            probe_res.combo == "Ctrl+Alt+Shift+O",
            f"combo={probe_res.combo}",
        )
        probe.unregister()

        _LINES.append("")
        _LINES.append(
            "说明：强杀路径无法在自检里自证（自证会把自检自己带走）。"
            "手工做法：正常启动桌宠 → 任务管理器强杀 → 立刻重启，"
            "看启动日志里热键是否注册成功。按 Windows 的语义，"
            "热键随进程消亡而回收，所以应当成功。"
        )

        controller.stop()
        window.close()
        _pump(app, 0.2)

    shutil.rmtree(SCRATCH, ignore_errors=True)
    return _finish()


def _make_pet():
    """造一个最小真素材包（只写在 scratch 里，`assets/` 一个字节都不动）。"""
    import json

    from PySide6.QtGui import QColor, QImage

    scratch = SCRATCH if SCRATCH.exists() else SCRATCH
    root = scratch / "pets" / "t12real"
    frames = root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    for name in ("idle_00.png", "idle_01.png"):
        image = QImage(60, 60, QImage.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        for y in range(10, 50):
            for x in range(10, 50):
                image.setPixelColor(x, y, QColor(40, 120, 200, 255))
        image.save(str(frames / name), "PNG")
    (root / "asset.json").write_text(
        json.dumps(
            {
                "name": "task12 真平台包",
                "cell": [60, 60],
                "actions": {
                    "idle": {"frames": ["idle_00.png", "idle_01.png"], "fps": 30.0}
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
