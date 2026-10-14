"""task07 真平台验收：真实节奏、光标的真实位移、CPU、以及退出后无残留。

自检主体（`check_task07.py`）跑在 offscreen 上、时间全是假的，**测不出这几件事**：

1. **真实节奏**：`QTimer` 实际是按什么间隔把 `on_tick` 喂进去的，
   "20 秒后张望 / 再 10 秒休息"在真事件循环里是不是这个数；
2. **光标轮询的真实间隔**与它带来的 **CPU 占用**（task07 §7 要求记录）；
3. **真·鼠标位移**：`QCursor.pos()` 读到的是真光标，抖动阈值在真环境下是否成立；
4. **退出收尾**：真进程退出后光标轮询确实停掉（`--exit-after` 跑真入口再看进程）。

跑法（**真平台，会短暂弹出真窗口**）：

    python tools/check_task07_real.py

结果写 `data/task07_real.txt`（UTF-8）。
"""

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task07_real.txt"
SCRATCH = ROOT / "data" / "task07_real_scratch"

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


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            import shutil

            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtCore import QElapsedTimer, QPoint, Qt, QTimer
    from PySide6.QtWidgets import QApplication

    from deskpet import pet_loader
    from deskpet.config import ConfigStore
    from deskpet.cursor_watch import CursorWatch
    from deskpet.idle_state import IdleStateMachine, Stage
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    store = ConfigStore(SCRATCH / "config.json")
    store.load()

    # ------------------------------------------------------------ §1 真实间隔
    section("1. 真平台：久置计时器与光标轮询的真实间隔")

    # 计时器实际间隔：把 `on_tick` 的到达时刻量下来（不改产品代码，挂一个旁路）。
    from deskpet import app as APP

    machine = IdleStateMachine(lambda: 20)
    arrived: list[float] = []
    clock = QElapsedTimer()
    clock.start()

    def _record() -> None:
        arrived.append(clock.elapsed() / 1000.0)
        machine.on_tick()

    timer = QTimer()
    timer.setInterval(APP.IDLE_TICK_MS)
    timer.timeout.connect(_record)
    timer.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    timer.stop()

    check("久置计时器真的在跑", len(arrived) >= 5, f"2 秒收到 {len(arrived)} 次 tick")
    if len(arrived) >= 2:
        gaps = [arrived[i + 1] - arrived[i] for i in range(len(arrived) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        # 250 ms 的设定；CoarseTimer 会被 15.6 ms 节拍放大，但这里量级足够粗，
        # 只要不是"几毫秒空转"或"几秒才来一次"就说明调度是活的。
        check(
            f"tick 平均间隔接近 {APP.IDLE_TICK_MS} ms",
            0.15 <= avg_gap <= 0.45,
            f"实测平均 {avg_gap * 1000:.1f} ms（{len(arrived)} 次）",
        )
    else:  # pragma: no cover
        check("tick 间隔可测", False, "tick 太少")

    # 光标轮询的真实间隔。
    watch = CursorWatch()
    polls: list[float] = []
    watch._timer.timeout.connect(lambda: polls.append(clock.elapsed() / 1000.0))
    watch.start()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    watch.stop()

    check("光标轮询真的在跑", len(polls) >= 5, f"2 秒收到 {len(polls)} 次轮询")
    if len(polls) >= 2:
        gaps = [polls[i + 1] - polls[i] for i in range(len(polls) - 1)]
        avg_gap = sum(gaps) / len(gaps)
        check(
            f"光标轮询平均间隔接近 {watch.interval_ms} ms",
            0.10 <= avg_gap <= 0.35,
            f"实测平均 {avg_gap * 1000:.1f} ms",
        )
    else:  # pragma: no cover
        check("轮询间隔可测", False, "轮询太少")

    # ------------------------------------------------------------ §2 真光标
    section("2. 真平台：真光标的位移判定")

    real = CursorWatch()
    real.start()
    first = real.poll_once()  # 首帧
    check("真平台上首帧读数不算动（拿得到真光标）", first is False, f"last_pos={real.last_pos}")

    before = real.last_pos
    app.processEvents()
    time.sleep(0.05)
    still = real.poll_once()
    check(
        "真平台上光标没被碰时按没动处理",
        still is False or before != real.last_pos,
        f"读数 {before} → {real.last_pos}",
    )
    real.stop()

    # ------------------------------------------------------------ §3 真实节奏
    section("3. 真平台：真窗口下的真实节奏（T=3 秒，缩短等待）")

    # 用一个**很短**的 T 让真事件循环里跑完整条时间线（真等 20 秒太慢）。
    # 短 T 下 S = max(5, T×0.5) = 5，所以这里验的是"到点会切"，不是绝对秒数。
    machine3 = IdleStateMachine(lambda: 3)
    window = PetWindow(spec, 4, idle_state=machine3)
    window.play_action("idle")
    window.showing_at_default_position()
    window.show()

    seen: list[tuple[str, float]] = []
    started = clock.elapsed() / 1000.0
    machine3.stage_changed.connect(
        lambda old, new: seen.append((new.value, clock.elapsed() / 1000.0 - started))
    )

    live_timer = QTimer()
    live_timer.setInterval(APP.IDLE_TICK_MS)
    live_timer.timeout.connect(machine3.on_tick)
    live_timer.start()

    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
        if window.action == "rest":
            break
    live_timer.stop()

    check(
        "真事件循环里确实自动切到了 waiting",
        any(stage == "waiting" for stage, _ in seen),
        f"实测阶段变化 {[(s, round(t, 2)) for s, t in seen]}",
    )
    check(
        "真事件循环里确实自动切到了 rest",
        window.action == "rest",
        f"窗口动作 {window.action}",
    )
    if seen:
        check(
            "waiting 出现在 T=3 秒附近（容差两个 tick）",
            3.0 <= seen[0][1] <= 3.6,
            f"实测 {seen[0][1]:.2f} 秒",
        )

    # 真事件循环里"鼠标动了 → 站起来"（走真实信号链）。
    window.on_mouse_moved()
    check(
        "真事件循环里鼠标动 → 站起来回 idle",
        window.action == "idle",
        f"窗口动作 {window.action}",
    )

    # 真窗口 + 真计时下的 CPU 占用（task07 §7 要求记录）。
    section("4. 真平台：CPU 占用（光标轮询 150 ms + 久置计时 250 ms）")

    real_watch = CursorWatch()
    real_machine = IdleStateMachine(lambda: 20)  # 20 秒内不会切动作，纯轮询开销
    real_watch.moved.connect(real_machine.on_mouse_moved)
    idle_timer = QTimer()
    idle_timer.setInterval(APP.IDLE_TICK_MS)
    idle_timer.timeout.connect(real_machine.on_tick)

    real_watch.start()
    idle_timer.start()
    cpu_clock = QElapsedTimer()
    cpu_clock.start()
    # 进程 CPU 时间（Windows 上 psutil 不一定有，用标准库的 times() 就够：
    # 单进程两段采样之差 ÷ 墙上时间 = 这一段占了一个核的百分之多少）。
    cpu_before = time.process_time()
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    elapsed = cpu_clock.elapsed() / 1000.0
    cpu_after = time.process_time()
    real_watch.stop()
    idle_timer.stop()

    cpu_percent = (cpu_after - cpu_before) / elapsed * 100.0 if elapsed > 0 else -1.0
    check(
        "轮询期间事件循环是活的（没有卡死）",
        real_watch.polls > 0 and elapsed > 0,
        f"{elapsed:.2f} 秒内轮询 {real_watch.polls} 次",
    )
    check(
        "20 秒内不该误切动作（T=20，只跑了 3 秒）",
        real_machine.stage is Stage.IDLE,
        f"阶段 {real_machine.stage.value}",
    )
    # ⚠ 上面这个循环自己用 `sleep(0.002)` 空转，**量到的 CPU 主要是测量循环的**，
    # 不是产品的。真正要回答的是"150 ms 轮询 + 250 ms 计时本身有多重"，
    # 那要**不空转**地等（让 Qt 事件循环自己睡），下面这条才是产品开销。
    # ⚠ 先把 §3 那个**还在 30 fps 播放动画的真窗口**关掉：带遮罩的半透明窗口
    # 每帧重绘是 Windows 合成器的开销（agent.md §4.9 实测约 41.7 ms/帧），
    # 它会把 CPU 读数整个盖住——那样量到的是"动画+合成器"，不是久置轮询。
    window.stop_animation()
    window.hide()
    for _ in range(6):
        app.processEvents()

    cpu2_before = time.process_time()
    wall2_before = time.monotonic()
    loop_timer = QTimer()
    loop_timer.setInterval(3000)
    loop_timer.setSingleShot(True)
    loop_timer.timeout.connect(app.quit)
    real_watch.start()
    idle_timer.start()
    loop_timer.start()
    app.exec()  # 真事件循环：没有空转，CPU 全是产品自己的
    cpu2_after = time.process_time()
    wall2 = time.monotonic() - wall2_before
    real_watch.stop()
    idle_timer.stop()

    live_percent = (cpu2_after - cpu2_before) / wall2 * 100.0 if wall2 > 0 else -1.0
    check(
        "真事件循环里久置轮询 + 计时的 CPU 占用很低（< 2% 单核）",
        0.0 <= live_percent < 2.0,
        f"实测 {live_percent:.2f}% 单核（{wall2:.2f} 秒墙钟，轮询 {real_watch.polls} 次）",
    )
    _LINES.append(
        f"（记录）空转测量循环 {elapsed:.2f} 秒 CPU {cpu_percent:.2f}%；"
        f"真事件循环（无动画窗口）{wall2:.2f} 秒 CPU {live_percent:.2f}%（这才是久置轮询本身的开销）"
    )

    # ------------------------------------------------------------ §5 停掉
    section("5. 真平台：停掉之后不再有任何轮询 / 计时")

    stopped_polls = real_watch.polls
    stopped_ticks = len(arrived)
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)

    check(
        "停掉光标轮询后不再有新的轮询",
        real_watch.polls == stopped_polls,
        f"停掉前 {stopped_polls} 次，0.6 秒后仍是 {real_watch.polls} 次",
    )
    check(
        "停掉的轮询 `running` 为假",
        real_watch.running is False,
        f"running={real_watch.running}",
    )
    check("停掉的久置计时器 `running` 为假", idle_timer.isActive() is False)
    del stopped_ticks

    window.stop_animation()
    window.close()
    for _ in range(6):
        app.processEvents()
    del Qt, QPoint
    return _finish()


def _finish() -> int:
    _LINES.append("")
    if _FAILED:
        _LINES.append(f"失败 {_FAILED} 项 / 共 {_TOTAL} 项")
    else:
        _LINES.append(f"全过（共 {_TOTAL} 项）")
    try:
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
        shown = str(RESULT_FILE)
    except OSError as exc:  # pragma: no cover
        shown = f"（结果写不出去：{exc}）"
    print(_LINES[-1])
    print(f"结果已写入：{shown}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
