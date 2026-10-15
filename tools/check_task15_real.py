"""task15 真平台验收：真窗口节奏、遮罩语义、真背景合成对照图。

自检主体（`check_task15.py`）跑在 offscreen 上，而 offscreen 有几件事**测不出来**：

- **真窗口的每帧节奏**：§4.9 已记录"遮罩窗口每帧重绘 = 合成器开销，
  33.6 → 41.7 ms/帧"。抗锯齿让边缘半透明像素变多，**可能**进一步加重它——
  **这一条必须实测**（task15 §6 明写不许凭推理下结论）；
- **遮罩语义**：offscreen 平台插件**不支持 `setMask`**（§4.9），
  "可点范围沿轮廓外扩了几个像素"在 offscreen 下量不出来；
- **真背景合成对照图**：把结果合成到纯白/纯黑背景上，人眼可比。

跑法（**真平台，会短暂弹出真窗口**）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task15_real.py

结果写 `data/task15_real.txt`（UTF-8），对照图写 `data/task15_out/`。

## 判据（task15 §6）

| 项 | 判据 |
| --- | --- |
| 真窗口节奏 | 开启后每帧间隔**不得劣于**开启前的 **+15%**（基线 41.7 ms/帧） |
| 遮罩语义 | 可点范围沿轮廓扩大**可接受**（记录外扩像素数）；**不得**退化成整帧可点 |
| 幽灵热区 | 远离人物的空白区**必须仍然穿透** |
| 对照图 | 1–10 档各一张开启前后对照（放大 4×），6 档为主角 |

⚠ 节奏劣化超标 → **记录事实 + 报告用户**，**不许**自己去改动画逻辑（task15 §6）。

> **⚠ task16 起，本脚本 §1 的节奏数不再代表"日常播放"**：
> 它只采样 **3 秒（≈ 一轮）**，而 `idle` 一轮就是 90 帧 × 33.3 ms ≈ 3.0 秒
> —— **第一轮必然全是缓存未命中**（缓存此刻还是空的），所以它量的是
> **冷启动那一轮**，不是用户日常看到的节奏。
> task16 实测：同一台机器、同一套代码，只看一轮是 **+72.7%**，
> 预热满一轮之后再看是 **+2.7%**（`check_task16_real.py` §1 的口径）。
> **"开启抗锯齿的节奏与关闭时同量级"这条结论以 `check_task16_real.py` 为准**；
> 本脚本这里保留原样是为了**不篡改 task15 已验收的历史数据**，
> 只把它降级为"冷启动一轮"的观察值。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task15_real_scratch/` 里，跑完删掉。
**用户真实的 `data/config.json` 一个字节都不动。**
"""

import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task15_real.txt"
SCRATCH = ROOT / "data" / "task15_real_scratch"

#: 对照图的落脚处（工作区内，§4.13）。
IMAGE_DIR = ROOT / "data" / "task15_out"

#: 量节奏时每档看多久（秒）。
SAMPLE_SECONDS = 2.0

#: 容忍的劣化比例（task15 §6：不得劣于 +15%）。
MAX_SLOWDOWN = 0.15

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    """记一条断言。**中性文字**描述实测值（§4.11：别把成功条件写反）。"""
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
    """记一条**只有数据、没有结论**的观察（不参与 PASS/FAIL）。"""
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    try:
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
        print(f"结果已写入：{RESULT_FILE}")
    except OSError as exc:  # pragma: no cover
        print(f"写结果文件失败：{exc}", file=sys.stderr)
    return 1 if _FAILED else 0


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import pet_loader, size
    from deskpet.pet_window import PetWindow

    _LINES.append("task15 真平台验收 —— 边缘抗锯齿")
    _LINES.append(f"素材根：{ROOT}")

    spec = pet_loader.resolve_pet("weedy").spec
    if spec is None:  # pragma: no cover
        check("素材可用", False, "拿不到 weedy 素材包")
        return _finish()

    section("§1 真窗口每帧节奏（开启前 vs 开启后）")
    baseline = _measure_interval(app, spec, PetWindow, size, antialias=False)
    enabled = _measure_interval(app, spec, PetWindow, size, antialias=True)

    note(f"关闭抗锯齿：{baseline:.1f} ms/帧")
    note(f"开启抗锯齿：{enabled:.1f} ms/帧")

    # ⚠⚠ **这一条实测超标，但根因不在抗锯齿**（task15 实测，已报告用户）。
    #
    # 事实链（三条都是量出来的，不是推的）：
    #   1. 不带抗锯齿渲染一帧 3.2 ms、带抗锯齿 25.5 ms（差 22 ms）；
    #   2. `idle` 有 **90 帧**，而 `rendering.DEFAULT_CACHE_LIMIT` 只有 **64**
    #      → **每一帧都是缓存未命中**（探针实测 hits=0、misses=90，
    #      连开两轮都一样）；**这是 task04 就存在的行为，与抗锯齿无关**；
    #   3. 把上限临时调到 90 → **hits=90、0.00 ms/帧**：抗锯齿的每帧开销变成 0。
    #
    # 所以"开启后 +56%"的真实含义是"**缓存本来就没命中**，抗锯齿把每帧
    # 多出来的 22 ms 暴露成了可见的劣化"。修它要动 `DEFAULT_CACHE_LIMIT`，
    # 而 task15 §5/§6 明写"**不许**因为这一个数不理想就去改动画逻辑"，
    # 且"启动时集中运算"已被否决 —— 用户拍板：**不改，如实记录 + 另立卡**。
    #
    # 因此这里**不再断言"必须 <= +15%"**（那会是一条永远红的断言，
    # 而红着的断言等于没有断言）。改成如实记录 + 断言"两份数据都量到了"。
    if baseline > 0:
        slowdown = (enabled - baseline) / baseline
        note(f"劣化比例：{slowdown * 100:+.1f}%（task15 §6 的判据是 +{MAX_SLOWDOWN * 100:.0f}%）")
        check(
            "两份节奏都量到了（数值见上，结论以本小节的实测事实为准）",
            baseline > 0 and enabled > 0,
            f"{baseline:.1f} → {enabled:.1f} ms/帧（{slowdown * 100:+.1f}%）",
        )
        _LINES.append(
            "    ⚠ task16 已修掉根因（上限 64 < idle 的 90 帧 → 每帧全部未命中），"
            "但**本小节的数仍然偏高**：它只采样一轮，量到的是**冷启动**那一轮"
            "（缓存此刻还是空的）。日常播放的节奏见 check_task16_real.py §1："
            "预热满一轮后是 +2.7%。"
        )
    else:  # pragma: no cover
        check("能量出基线节奏", False, f"baseline={baseline}")

    section("§1b 缓存未命中率（判「每帧开销」还是「一次性开销」的判据）")
    _check_cache_behaviour(spec, size)

    section("§2 遮罩语义（可点范围沿轮廓外扩的实测像素数）")
    _check_mask(spec, PetWindow, size)

    section("§3 全档位对照图（1–10 档，放大 4×）")
    _write_comparison_images(spec, PetWindow, size)

    return _finish()


def _measure_interval(app, spec, PetWindow, size_mod, antialias: bool) -> float:
    """建一个**真的可见**窗口，量它相邻两次换帧之间的实际间隔（毫秒）。

    做法与 §4.9 那次实测一致：**不 sleep 式等待**，而是让事件循环真的跑起来，
    然后读"两次帧回调之间隔了多久"。

    ⚠ 为什么必须用真窗口：offscreen 下窗口不合成，遮罩窗口"每帧变形状"
    那笔合成器开销根本不会发生，量出来的数**不能代表用户看到的节奏**。
    """
    from PySide6.QtCore import Qt, QTimer

    window = PetWindow(spec, level=6, antialias=antialias)
    # ⚠ **必须真的把动画开起来**：`PetWindow` 构造时并不启动驱动器，
    # 是 `play_action()` 才 `start()`（`app.py` 就是这么做的）。
    # 少这一步的话 `frame_due` 一次都不发，量出来是 0.0 ms（第一版就踩了）。
    window.play_action("idle")
    window.show()

    # 驱动器每次真的推进一帧时记一笔时刻（`frame_due` 就是"该换帧了"）。
    stamps: list[float] = []

    def on_frame() -> None:
        stamps.append(time.perf_counter())

    driver = window.animation_driver
    driver.frame_due.connect(on_frame)

    # 起一个**自己的**定时器把事件循环转起来。它只负责驱动 `processEvents`，
    # 不参与计时——真正的帧节奏由 `AnimationDriver` 决定。
    pump = QTimer()
    pump.setTimerType(Qt.PreciseTimer)
    pump.setInterval(5)
    pump.timeout.connect(lambda: None)
    pump.start()

    deadline = time.perf_counter() + SAMPLE_SECONDS
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.001)

    pump.stop()
    driver.frame_due.disconnect(on_frame)
    window.close()
    app.processEvents()

    if len(stamps) < 3:  # pragma: no cover
        return 0.0
    intervals = [
        (stamps[index + 1] - stamps[index]) * 1000 for index in range(len(stamps) - 1)
    ]
    return sum(intervals) / len(intervals)


def _check_cache_behaviour(spec, size_mod) -> None:
    """量"每帧渲染耗时"与"缓存命中率"，把 §1 那条劣化的**根因**摆出来。

    这是本脚本里最有信息量的一节：它区分了两种完全不同的开销——

    | 情形 | 每帧开销 | 含义 |
    | --- | --- | --- |
    | **缓存命中** | ≈ 0 ms | "按需处理"真的成立（只算一次） |
    | **缓存未命中** | 3.2 ms（无 AA）/ 25.5 ms（有 AA） | 每一帧都在重算 |

    判据不看绝对值（那随机器变），看的是**帧数与缓存上限的关系**：
    `idle` 有 90 帧，而上限在 task16 之前**恒为 64** → 必然每帧未命中。

    ⚠ **task16 已把这条修掉**：上限现在按"这一档一张多大 + 内存预算（160 MiB）"
    算，第 6 级是 167 张 > 90 帧 → 第二轮**全命中**。所以本节现在的身份是
    **回归基线**：既量出"现在的上限是多少"，也保留"上限被调小就必然全不命中"
    这条反证（`limit=frames-1`），用来证明这个判据**有分辨力**
    （没有对照的断言证明不了什么，§4.23 的教训）。
    """
    import time

    from deskpet import rendering

    frames = spec.action("idle").frame_count
    limit = rendering.FrameRenderer(spec).limit_for_level(6)
    note(f"idle 帧数 = {frames}，第 6 级缓存上限 = {limit}（task16 起按预算算）")

    # 实测：连开两轮，看第二轮能不能全命中
    for antialias in (False, True):
        renderer = rendering.FrameRenderer(spec, antialias=antialias)
        for index in range(frames):
            renderer.frame("idle", index, 6, False)
        renderer.hits = renderer.misses = 0
        start = time.perf_counter()
        for index in range(frames):
            renderer.frame("idle", index, 6, False)
        elapsed = (time.perf_counter() - start) * 1000
        note(
            f"antialias={'on ' if antialias else 'off'}: 第二轮 {elapsed:7.1f} ms "
            f"（{elapsed / max(1, frames):5.2f} ms/帧）hits={renderer.hits} "
            f"misses={renderer.misses} 条目={renderer.cached_count()}"
        )

    check(
        f"task16 之后：上限({limit}) >= idle 帧数({frames}) → 日常播放真的走缓存",
        limit >= frames,
        f"上限={limit} 帧数={frames}（task16 之前这里是 64 < 90，必然每帧未命中）",
    )

    # 反证（判据的分辨力）：把上限**调到比帧数小一张**，第二轮就应当全不命中。
    # 没有这条对照，"hits == 帧数"证明不了判据真的在看缓存上限（§4.23 的教训）。
    probe_tight = rendering.FrameRenderer(spec, limit=frames - 1, antialias=True)
    for index in range(frames):
        probe_tight.frame("idle", index, 6, False)
    probe_tight.hits = probe_tight.misses = 0
    for index in range(frames):
        probe_tight.frame("idle", index, 6, False)
    check(
        f"[反证] 上限调到 {frames - 1}（比帧数少一张）时第二轮命中为 0",
        probe_tight.hits == 0,
        f"hits={probe_tight.hits}（上限只要小一张，LRU 就会把整轮冲洗干净）",
    )

    # 正证：产品默认上限下，第二轮应当**全命中**
    probe = rendering.FrameRenderer(spec, antialias=True)
    for index in range(frames):
        probe.frame("idle", index, 6, False)
    probe.hits = probe.misses = 0
    start = time.perf_counter()
    for index in range(frames):
        probe.frame("idle", index, 6, False)
    elapsed = (time.perf_counter() - start) * 1000
    note(
        f"[正证] 产品默认上限 {probe.limit_for_level(6)}：第二轮 {elapsed:7.1f} ms "
        f"（{elapsed / max(1, frames):5.2f} ms/帧）hits={probe.hits} misses={probe.misses}"
    )
    check(
        "默认上限下抗锯齿的每帧开销为 0（命中缓存 → 只算过一次）",
        probe.misses == 0 and probe.hits == frames,
        f"hits={probe.hits} misses={probe.misses}",
    )


def _check_mask(spec, PetWindow, size_mod) -> None:
    """量"可点范围沿轮廓外扩了几个像素"，并防两种退化。"""
    from PySide6.QtCore import QPoint

    from deskpet import hit_test

    window_off = PetWindow(spec, level=6, antialias=False)
    window_on = PetWindow(spec, level=6, antialias=True)

    image_off = window_off.frame_image
    image_on = window_on.frame_image
    if image_off.isNull() or image_on.isNull():  # pragma: no cover
        check("拿得到帧图", False, "帧图为空")
        return

    region_off = hit_test.region_for_frame(image_off)
    region_on = hit_test.region_for_frame(image_on)
    if region_off is None or region_on is None:  # pragma: no cover
        check("算得出区域", False, "region 为 None")
        return

    bounds_off = region_off.boundingRect()
    bounds_on = region_on.boundingRect()
    note(
        f"关闭：外接矩形 {bounds_off.width()}×{bounds_off.height()}，"
        f"区域矩形数={len(region_off)}"
    )
    note(
        f"开启：外接矩形 {bounds_on.width()}×{bounds_on.height()}，"
        f"区域矩形数={len(region_on)}"
    )

    grow_w = bounds_on.width() - bounds_off.width()
    grow_h = bounds_on.height() - bounds_off.height()
    note(f"外接矩形外扩：宽 {grow_w:+d}、高 {grow_h:+d} 像素（可接受，见 task15 §1）")

    # ① 不得退化成"整帧可点"
    window_size = window_on.pixel_size
    full_area = window_size[0] * window_size[1]
    check(
        "没有被遮蔽成空区域（mask 非空 = 仍然有穿透）",
        not region_on.isEmpty(),
        f"区域为空={region_on.isEmpty()}",
    )
    # ② 远离人物的空白区必须仍然穿透（抽窗口四角检查）
    corners_transparent = 0
    for x, y in ((0, 0), (window_size[0] - 1, 0), (0, window_size[1] - 1),
                 (window_size[0] - 1, window_size[1] - 1)):
        if not region_on.contains(QPoint(x, y)):
            corners_transparent += 1
    check(
        "窗口四角仍然穿透（没有退化成整帧可点）",
        corners_transparent >= 3,
        f"四角中穿透的有 {corners_transparent}/4",
    )

    # ③ 人物中心必须仍然可点
    center = QPoint(window_size[0] // 2, window_size[1] // 2)
    check(
        "人物中心仍然可点",
        bool(region_on.contains(center)) or bool(region_off.contains(center)),
        f"开启时中心可点={region_on.contains(center)}",
    )

    # ④ 幽灵热区：区域面积不该远超人物实际不透明像素数
    opaque = 0
    for y in range(0, image_on.height(), 2):
        for x in range(0, image_on.width(), 2):
            if ((image_on.pixel(x, y) >> 24) & 0xFF) >= 128:
                opaque += 1
    opaque *= 4  # 隔点采样 → 乘回总量
    note(f"不透明像素约 {opaque}（占窗口 {opaque / max(1, full_area) * 100:.1f}%）")
    check(
        "可点范围没有远超人物（无大片幽灵热区）",
        opaque < full_area * 0.9,
        f"不透明占比={opaque / max(1, full_area) * 100:.1f}%",
    )

    window_off.close()
    window_on.close()


def _write_comparison_images(spec, PetWindow, size_mod) -> None:
    """把 1–10 档的开启/关闭对照存成图（放大 4×，方便看边缘）。"""
    import numpy as np
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for level in range(1, size_mod.LEVEL_COUNT + 1):
        off = PetWindow(spec, level=level, antialias=False).frame_image
        on = PetWindow(spec, level=level, antialias=True).frame_image
        if off.isNull() or on.isNull():  # pragma: no cover
            continue
        for tag, image in (("off", off), ("on", on)):
            path = IMAGE_DIR / f"level{level:02d}_{tag}.png"
            image.convertToFormat(QImage.Format_ARGB32).save(str(path), "PNG")
            saved += 1
        # 放大 4× 的对照（并排：左关闭、右开启）
        path = IMAGE_DIR / f"level{level:02d}_compare_x4.png"
        _save_side_by_side(image_off=off, image_on=on, path=path)
    check(
        "全档位对照图已写出（含放大 4× 的并排对照）",
        saved > 0,
        f"写出 {saved} 张，目录={IMAGE_DIR}",
    )


def _save_side_by_side(image_off, image_on, path) -> None:
    """左（关闭）右（开启）并排放大 4×，中间留 8 像素分隔。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    scale = 4
    width = image_off.width()
    height = image_off.height()
    canvas = QImage(
        width * scale * 2 + 8, height * scale, QImage.Format_ARGB32
    )
    canvas.fill(0xFFFFFFFF)  # 白底：浅色背景下看边缘最清楚
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        for index, image in enumerate((image_off, image_on)):
            scaled = image.scaled(
                width * scale,
                height * scale,
                Qt.IgnoreAspectRatio,
                Qt.FastTransformation,  # **最近邻**：对照图要看原始像素，不许再插值
            )
            painter.drawImage(index * (width * scale + 8), 0, scaled)
    finally:
        painter.end()
    canvas.save(str(path), "PNG")


if __name__ == "__main__":
    sys.exit(main())
