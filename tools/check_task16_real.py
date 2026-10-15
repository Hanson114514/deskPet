"""task16 真平台验收：真窗口的每帧节奏（task15 那个 +56% 应当随本卡消失）。

脚本主体（`check_task16.py`）跑在 offscreen 上，而 **offscreen 下窗口不合成**，
"遮罩窗口每帧变形状"那笔合成器开销根本不会发生，所以"用户看到的节奏"
**只能在真平台量**（agent.md §4.9 已记录：真窗口比纯调度慢一截，
那是 Windows 合成器的开销，不是动画 bug）。

跑法（**真平台，会短暂弹出真窗口**）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task16_real.py

结果写 `data/task16_real.txt`（UTF-8）。

## 判据（task16 §6 最后两条）

| 项 | 判据 |
| --- | --- |
| 开启抗锯齿的节奏 | 与关闭时**同一量级**（task15 是 43.2 → 69.2 ms/帧 = **+60%**，本卡要它消失） |
| 缓存命中 | 连播下的每一帧都**走缓存**（命中率 100%）——这是上面那条的因 |
| 每帧开销 | 命中缓存后，单帧渲染 ≈ 0 ms |

⚠ 与 task15 同一条纪律：节奏数**不达标就如实记录 + 报告用户**，
**不许**为了让数字好看去改动画/渲染逻辑（task16 §5 明写）。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task16_real_scratch/` 里，跑完删掉。
**用户真实的 `data/config.json` 一个字节都不动。**
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task16_real.txt"
SCRATCH = ROOT / "data" / "task16_real_scratch"

#: 量节奏时看多久（秒）。
#:
#: ⚠ **必须够长，至少要覆盖好几轮**（task16 实测踩到的坑）：
#: `idle` 是 90 帧 × 33.3 ms ≈ **3.0 秒一轮**。只看 3 秒 = **只看第一轮**，
#: 而第一轮**必然全是 miss**（缓存是空的），于是量到的是"冷启动那一轮"，
#: 命中的收益一点都体现不出来 —— 第一版就是这么量的，得到 +89.5% 的假劣化。
#: 取 **3 轮**（约 9 秒）让第二、三轮走缓存，量的才是"日常播放"的节奏。
SAMPLE_ROUNDS = 3

#: 单轮的估算时长（秒）。真实值由素材帧率算，这里只用它定采样窗口的下限。
ROUND_SECONDS = 3.0
SAMPLE_SECONDS = SAMPLE_ROUNDS * ROUND_SECONDS

#: task16 §6 的口径：开启抗锯齿后应当回到与关闭时**同一量级**。
#: "同一量级"取 **+15%**（与 task15 当初给的容忍带一致，便于前后对照）。
MAX_SLOWDOWN = 0.15

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
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


def note(text: str) -> None:
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _hit_rate(stats: str) -> float:
    """从 `_measure_interval()` 的说明串里把命中率读回来（`（命中率 84.8%）`）。

    写成解析而不是返回结构化元组，是为了让**日志与断言用的是同一个数**
    —— 两处各算一遍迟早分叉（agent.md §4.1 的同一条道理）。
    """
    marker = "命中率 "
    if marker not in stats:  # pragma: no cover
        return 0.0
    tail = stats.split(marker, 1)[1]
    return float(tail.split("%", 1)[0])


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


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import pet_loader, rendering, size
    from deskpet.pet_window import PetWindow

    _LINES.append("task16 真平台验收 —— 帧缓存上限")
    _LINES.append(f"素材根：{ROOT}")

    spec = pet_loader.resolve_pet("weedy").spec
    if spec is None:  # pragma: no cover
        check("素材可用", False, "拿不到 weedy 素材包")
        return _finish()

    frames = spec.action("idle").frame_count
    limit6 = rendering.FrameRenderer(spec).limit_for_level(6)
    note(
        f"idle = {frames} 帧；第 6 级上限 = {limit6} 张（task15 时恒为 "
        f"{rendering.DEFAULT_CACHE_LIMIT}，所以每帧都在重算）"
    )

    # ---------------------------------------------------------------- §1 节奏
    section(f"§1 真窗口每帧节奏（关闭 vs 开启抗锯齿；采样 {SAMPLE_SECONDS:.0f} 秒 ≈ {SAMPLE_ROUNDS} 轮）")
    note(
        "⚠ 采样窗口必须覆盖**好几轮**：第一轮必然全是 miss（缓存是空的），"
        "只看一轮量到的是冷启动，会得出假的劣化（task16 第一版就栽在这里）。"
    )
    off, off_stats = _measure_interval(app, spec, PetWindow, antialias=False)
    on, on_stats = _measure_interval(app, spec, PetWindow, antialias=True)

    note(f"关闭抗锯齿：{off:.1f} ms/帧；{off_stats}")
    note(f"开启抗锯齿：{on:.1f} ms/帧；{on_stats}")

    # 稳态命中率必须 ~100%（预热到"缓存装齐整轮 90 帧"之后）。
    # 这是"日常播放走缓存"最直接的判据，也是任务卡 §2 第 1 条的真身。
    for label, stats in (("关闭", off_stats), ("开启", on_stats)):
        rate = _hit_rate(stats)
        check(
            f"antialias={label}：真窗口稳态命中率 ≈ 100%（不再每帧重算）",
            rate >= 99.0 and "预热装齐=是" in stats,
            f"{stats} —— 命中率 {rate:.1f}%（task15 时恒为 0%）",
        )

    if off > 0:
        slowdown = (on - off) / off
        note(
            f"劣化比例：{slowdown * 100:+.1f}%"
            f"（task15 实测是 +60%；本卡判据是 ≤ +{MAX_SLOWDOWN * 100:.0f}%）"
        )
        check(
            f"开启抗锯齿的节奏回到与关闭时同一量级（≤ +{MAX_SLOWDOWN * 100:.0f}%）",
            slowdown <= MAX_SLOWDOWN,
            f"{off:.1f} → {on:.1f} ms/帧（{slowdown * 100:+.1f}%）；"
            f"task15 是 43.2 → 69.2（+60%）",
        )
    else:  # pragma: no cover
        check("能量出基线节奏", False, f"baseline={off}")

    # ---------------------------------------------------------------- §2 命中率
    section("§2 缓存命中率：日常播放走缓存这条设计意图真的成立吗")

    for antialias in (False, True):
        renderer = rendering.FrameRenderer(spec, 1.0, antialias=antialias)
        # 第一轮：预热（全部是 miss，符合预期）
        for index in range(frames):
            renderer.frame("idle", index, 6, False)
        warm_entries = renderer.cached_count()
        # 第二轮：应当**全部命中**
        renderer.hits = renderer.misses = 0
        start = time.perf_counter()
        for index in range(frames):
            renderer.frame("idle", index, 6, False)
        elapsed = (time.perf_counter() - start) * 1000
        per_frame = elapsed / max(1, frames)
        check(
            f"antialias={'on ' if antialias else 'off'}：第二轮 hits={frames} / misses=0",
            renderer.hits == frames and renderer.misses == 0,
            f"hits={renderer.hits} misses={renderer.misses} "
            f"（{elapsed:.1f} ms，{per_frame:.2f} ms/帧）条目={warm_entries}",
        )
        check(
            f"antialias={'on ' if antialias else 'off'}：命中缓存的每帧开销 ≈ 0 ms",
            per_frame < 0.5,
            f"{per_frame:.3f} ms/帧（task15 实测未命中时是 3.2 / 25.5 ms/帧）",
        )

    # ---------------------------------------------------------------- §3 大动作
    section("§3 放不下的动作（weedy.waiting 240 帧）在真机上仍然不超预算")

    waiting_frames = spec.action("waiting").frame_count
    renderer = rendering.FrameRenderer(spec, 1.0)
    for index in range(waiting_frames):
        renderer.frame("waiting", index, 6, False)
    per = rendering.bytes_per_frame(size.pixel_size(spec.cell, 6))
    used = renderer.cached_count() * per
    note(
        f"waiting = {waiting_frames} 帧；条目 = {renderer.cached_count()}；"
        f"占用 ≈ {used / 1024 / 1024:.1f} MiB"
    )
    check(
        "被截断的动作条目数正好等于上限（缓存被用满）",
        renderer.cached_count() == renderer.limit_for_level(6),
        f"条目={renderer.cached_count()} 上限={renderer.limit_for_level(6)}",
    )
    check(
        "真机占用不超预算（不是「上限提上去就行」）",
        used <= rendering.MEMORY_BUDGET_BYTES,
        f"占用={used} B 预算={rendering.MEMORY_BUDGET_BYTES} B",
    )

    return _finish()


def _measure_interval(app, spec, PetWindow, antialias: bool) -> tuple[float, str]:
    """建一个**真的可见**窗口，量相邻两次换帧之间的实际间隔（毫秒）。

    做法与 task15 的 `_measure_interval()` 一致（§4.9 那次实测的口径）：
    **不 sleep 式等待**，让事件循环真的跑起来，读"两次帧回调隔了多久"。

    ## 返回值：`(平均间隔, 命中率说法)`

    第一轮必然全是 miss（缓存是空的），所以命中率统计要在**预热一轮之后**
    才归零重算——否则量到的是冷启动，而不是"日常播放"（本卡第一版就栽在这）。
    但**间隔**是全程算的：用户看到的节奏包含冷启动那一轮，
    只统计稳态就成了报喜不报忧。
    """
    from PySide6.QtCore import Qt, QTimer

    window = PetWindow(spec, level=6, antialias=antialias)
    # ⚠ **必须真把动画开起来**：`PetWindow` 构造时不启动驱动器，
    # 是 `play_action()` 才 `start()`（少这一步 `frame_due` 一次都不发）。
    window.play_action("idle")
    window.show()

    stamps: list[float] = []
    renderer = window._renderer  # noqa: SLF001

    #: 预热：一直空转到**缓存里真的装齐了整轮 90 帧**为止。
    #:
    #: ⚠ 不能用"睡满 N 秒"当预热条件（第一、二版都栽在这）：动画比墙钟慢
    #: （真窗口 + 抗锯齿时 52 ms/帧），3.6 秒的墙钟只推得动约 46 帧，
    #: 于是剩下的帧在"稳态"里被当成冷键补填 → 命中率虚报成 73.8%。
    #: **探针证明**：那些 miss 全是**冷键**（当时不在缓存里）、
    #: 且预热跑完缓存里 90 帧一个不缺、"热键却 miss" 一次都没出现。
    #: 所以判据要盯"缓存装齐了没有"，而不是"过了多久"。
    frames = spec.action("idle").frame_count
    round_seconds = frames * spec.action("idle").frame_ms / 1000.0
    pump = QTimer()
    pump.setTimerType(Qt.PreciseTimer)
    pump.setInterval(5)
    pump.timeout.connect(lambda: None)
    pump.start()
    warmup_deadline = time.perf_counter() + round_seconds * 4.0  # 兜底上限
    while renderer.cached_count() < frames and time.perf_counter() < warmup_deadline:
        app.processEvents()
        time.sleep(0.001)
    renderer.hits = renderer.misses = 0

    def on_frame() -> None:
        stamps.append(time.perf_counter())

    driver = window.animation_driver
    driver.frame_due.connect(on_frame)

    deadline = time.perf_counter() + SAMPLE_SECONDS
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)

    pump.stop()
    driver.frame_due.disconnect(on_frame)
    hits, misses = renderer.hits, renderer.misses  # noqa: SLF001
    window.close()
    app.processEvents()

    total = hits + misses
    stats = (
        f"预热后采样 {len(stamps)} 帧；hits={hits} misses={misses} "
        f"（命中率 {hits / max(1, total) * 100:.1f}%）"
    )
    # 把"预热到底装齐了没有"一并带出去：没装齐的话，下面那条命中率断言
    # 测的就是预热，而不是缓存（第一版就是这么误报的）。
    stats += f"；预热装齐={'是' if renderer.cached_count() >= frames else '否'}"

    if len(stamps) < 3:  # pragma: no cover
        return 0.0, stats
    intervals = [
        (stamps[index + 1] - stamps[index]) * 1000 for index in range(len(stamps) - 1)
    ]
    return sum(intervals) / len(intervals), stats


if __name__ == "__main__":
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":  # pragma: no cover
        print("这是真平台自检，请不要设 QT_QPA_PLATFORM=offscreen")
        raise SystemExit(2)
    raise SystemExit(main())
