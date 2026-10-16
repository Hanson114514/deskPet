"""task18 真平台验收：**轮询开销**、**非 KV 形态下 CPU 0**、**收尾**、真按键路径。

脚本主体（`check_task18.py`）跑在 offscreen 上，用注入的键态源验逻辑。
那验不了两件事，只能在**真平台**上量：

1. **8 ms 轮询的真实开销**（`GetAsyncKeyState` 走 `ctypes` 直调，offscreen 下
   同样能调——但"真的没在轮询"要看定时器与真实事件循环的配合）；
2. **非 KV 形态下轮询计数为 0**（验收 task18 6 的最后一条）。

跑法（**真平台，会短暂弹出真窗口**）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task18_real.py

结果写 `data/task18_real.txt`（UTF-8）。

## 判据（task18 6 里只能真平台验的那两条）

| 项 | 判据 |
| --- | --- |
| **非 KV 形态下轮询计数为 0** | 起窗口（宠物形态）跑一段时间 -> `listener.polls == 0`；切到 KV -> 计数开始增长 |
| **8 ms 轮询的实际开销与实测同量级** | 量一段时间的轮询次数与总耗时；判据是"一轮的平均耗时"在**微秒级**（实测 16 键 28.9 us） |
| **真按键路径** | `get_async_key_state` 走真 `user32`；用一个**必然没按**的 VK 证明调用路径通 |
| **收尾** | `stop()` 之后轮询计数不再增长 |

## 这一份**不做**的事

⚠ **"真按键点亮"无法自动化**：本机 `SendInput` 造合成按键**被拒（返回 0）**
（task18 1.2 第 3 条，与 4.20 同处境）。本脚本**不假装验过**它——
那一页**只能人工确认**：按下真键 -> 键位点亮、抬起熄灭。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task18_real_scratch/` 里，跑完删掉。
**用户真实的 `data/config.json` 一个字节都不动。**
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# ⚠ **必须在 import 任何"会拉起 `deskpet.logger`"的模块之前**把崩溃兜底的目录钉住。
#   `logger` 的崩溃兜底自己用 `sys.argv[0]` 复刻一遍"程序目录 + data"规则，并与
#   `paths.py` 对一次（防漂移，§4.13）。从 `tools/` 下启动时那两条推导会分叉
#   （`tools/data` vs `<仓库根>/data`），于是 import 期就往 stderr 打一句诊断——
#   它**不阻断启动**，但会污染本脚本的输出。`_CRASH_DIR_ENV` 就是为这种场合准备的
#   测试缝（见 `logger._CRASH_DIR_ENV` 的说明），设上它两条推导就一致了。
#: `logger` 的崩溃兜底测试缝（`logger._CRASH_DIR_ENV` 的值，**这里抄一份字面量**：
#: 它只是"自检为了输出干净而设的环境变量名"，不是产品逻辑的第二份真值）。
_CRASH_DIR_ENV_NAME = "DESKPET_CRASH_PROBE_DIR"
os.environ[_CRASH_DIR_ENV_NAME] = str(ROOT / "data")

RESULT_FILE = ROOT / "data" / "task18_real.txt"
SCRATCH = ROOT / "data" / "task18_real_scratch"

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


def note(text: str) -> None:
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _settle(app, rounds: int = 30) -> None:
    """把事件队列跑干净（窗口的异步生效靠它）。"""
    for _ in range(rounds):
        app.processEvents()


def _pump(app, ms: int) -> None:
    """跑**真实**事件循环 `ms` 毫秒（定时器要事件循环才走）。

    ⚠ 与主体脚本里的同名助手不同：这里用的是 `QApplication.processEvents`，
    在真平台上会把真实的窗口消息也排掉——这正是"真定时器节奏"要量的东西。
    """
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtWidgets import QApplication

    from deskpet import context_menu as cm
    from deskpet import pet_loader, win32
    from deskpet.app import KeyListenerController
    from deskpet.config import ConfigStore
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    keyview = pet_loader.load_keyview(ROOT / "assets" / "weedy")[0]

    _LINES.append("task18 真平台验收 —— 键盘形态之二：按键联动")
    _LINES.append(f"素材根：{ROOT}")
    _LINES.append(f"Win32 可用：{win32.is_available()}")

    if keyview is None:  # pragma: no cover
        check("weedy 有可用的键盘形态素材", False, "拿不到 keyview")
        return _finish()
    note(
        f"键盘形态：cell={keyview.cell}、{len(keyview.keys)} 个键位、"
        f"布局={'、'.join(keyview.layout_names)}"
    )

    SCRATCH.mkdir(parents=True, exist_ok=True)
    cfg = SCRATCH / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    store = ConfigStore(cfg)
    store.load()
    # 造一份绑定：8 个键（相当于 8 键布局那一套）。
    keys = list(keyview.keys)[:8]
    store.set(
        "keyview_keys",
        {key_id: {"vk": 0x41 + index, "name": chr(0x41 + index)} for index, key_id in enumerate(keys)},
    )

    level = 5
    window = PetWindow(spec, level, config=store, keyview=keyview)
    window.set_form_handler(lambda form: None)
    window.play_action("idle")
    window.show_at(400, 300, user_positioned=True)
    window.show()
    _settle(app)

    controller = KeyListenerController(window, store)
    listener = controller.listener

    section("1. 真平台：宠物形态下**轮询计数为 0**（CPU 0）")

    controller.sync()
    _pump(app, 300)
    check(
        "宠物形态 + 窗口可见 -> **一次都没轮询**（启用条件不成立）",
        listener.polls == 0,
        f"形态={window.form!r} 轮询={listener.polls} 次",
    )
    check(
        "宠物形态下定时器**没在跑**（isActive 为假）",
        not listener.running,
        f"running={listener.running}",
    )

    section("2. 真平台：切到 KV 之后轮询真的转起来")

    window.set_form(cm.FORM_KEYVIEW)
    _settle(app)
    controller.sync()
    check(
        "切到 KV -> 启用条件成立、定时器转起来",
        controller.should_poll() and listener.running,
        f"should_poll={controller.should_poll()} running={listener.running}",
    )

    before = listener.polls
    started = time.perf_counter()
    _pump(app, 500)
    elapsed = time.perf_counter() - started
    delta = listener.polls - before
    check(
        "KV 形态下轮询次数**真的在增长**",
        delta > 0,
        f"500 ms 内轮询 {delta} 次",
    )

    # ---- 开销：8 ms 轮询、8 个绑定，期望约 62 轮/500 ms（实际按定时器精度浮动）
    expected = elapsed * 1000.0 / listener.interval_ms
    ratio = delta / expected if expected > 0 else 0.0
    note(
        f"实测：间隔 {listener.interval_ms} ms、{len(listener.bindings)} 个绑定、"
    )
    note(
        f"      {elapsed * 1000:.0f} ms 内跑了 {delta} 轮（理论 {expected:.0f} 轮，比值 {ratio:.2f}）"
    )
    check(
        "轮询节奏与 8 ms 间隔同量级（0.5 <= 实测/理论 <= 1.5）",
        0.5 <= ratio <= 1.5,
        f"比值={ratio:.2f}（{delta} / {expected:.0f}）",
    )

    # ---- 单轮开销：直接调 poll() 量微秒数（与 task18 1.2 第 2 条的 28.9 us 对照）
    rounds = 200
    started = time.perf_counter()
    for _ in range(rounds):
        listener.poll()
    per_round_us = (time.perf_counter() - started) / rounds * 1e6
    note(f"单轮 poll() 实测：{per_round_us:.2f} us / 轮（{len(listener.bindings)} 个绑定）")
    check(
        "单轮轮询开销在**微秒级**（与实测 28.9 us 同量级，不是毫秒级）",
        per_round_us < 500.0,
        f"{per_round_us:.2f} us/轮",
    )
    # 8 ms 一轮 -> 单核占用率
    cpu_ratio = per_round_us / (listener.interval_ms * 1000.0)
    note(f"折算单核占用：约 {cpu_ratio * 100:.3f}%（8 ms 间隔）")

    section("3. 真平台：真 user32 的调用路径（不装钩子、不要管理员）")

    if win32.is_available():
        # 0xFF 不是任何真实按键的 VK -> 必然读到"未按下"。这条只证明**调用路径通**。
        probe = win32.get_async_key_state(0xFF)
        check(
            "真 user32 调用路径通（一个必然没按的 VK 读到 False）",
            probe is False,
            f"get_async_key_state(0xFF) = {probe}",
        )
        # 与"真按键点亮"的区别：这里只证明 API 能调，**不假装验过**按键联动。
        note(
            "⚠ 「真按键点亮」**无法自动化**：本机 SendInput 造合成按键被拒（返回 0）"
        )
        note(
            "   —— 那一页只能**人工确认**：按下真键 -> 键位点亮、抬起熄灭。"
        )
    else:  # pragma: no cover - 非 Windows
        note("Win32 不可用：跳过真 user32 那一条")

    section("4. 真平台：隐藏 / 切回宠物形态都会让轮询停下来")

    running_polls = listener.polls
    window.set_hotkey_available(True)
    hidden = window.hide_pet()
    _pump(app, 100)
    check(
        "隐藏 -> 轮询次数**一次都不再增长**",
        hidden and listener.polls == running_polls and not listener.running,
        f"隐藏={hidden} 轮询 {running_polls} -> {listener.polls}",
    )

    window.show_pet()
    _settle(app)
    controller.sync()
    _pump(app, 100)
    check(
        "呼出 -> 轮询重新开始",
        listener.polls > running_polls,
        f"轮询={listener.polls} 次",
    )

    window.set_form(cm.FORM_PET)
    _settle(app)
    controller.sync()
    pets_at = listener.polls
    _pump(app, 200)
    check(
        "切回宠物形态 -> 轮询不再增长、pressed 清空",
        listener.polls == pets_at and listener.pressed == (),
        f"轮询 {pets_at} -> {listener.polls}；pressed={listener.pressed}",
    )

    section("5. 真平台：退出收尾")

    controller.stop()
    stopped_at = listener.polls
    _pump(app, 150)
    check(
        "`stop()` -> 停表、轮询次数不再增长、pressed 为空",
        not listener.running and listener.polls == stopped_at and listener.pressed == (),
        f"running={listener.running} 轮询 {stopped_at} -> {listener.polls}",
    )

    window.close()
    _settle(app)
    return _finish()


def _finish() -> int:
    section("结论")
    if _FAILED:
        note(f"失败 {_FAILED} 项 / 共 {_TOTAL} 项")
    else:
        note(f"全过（{_TOTAL} 项断言）")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    print(f"\n结果已写入：{RESULT_FILE}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
