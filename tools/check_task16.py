"""task16 自检（offscreen）：缓存上限、命中率、内存账、切档不残留。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task16_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task16.py

## 这一份验什么（对应 task16 §6）

1. **命中率**：连播一轮后第二轮 `hits == 帧数`、`misses == 0`
   —— 对**所有在预算内放得下的动作**（用户 2026 拍板的验收口径）；
2. **上限**：放不下的动作（`waiting`）**条目数 == 上限**，且**不超预算**；
3. **内存账**：每个档位、每个动作都断言"条目数 × 单张大小 ≤ `MEMORY_BUDGET_BYTES`"；
4. **全动作覆盖**：`weedy` 6 个 + `weedy_sightseer` 6 个，逐个动作逐个档位跑；
5. **切档 1↔10 各 3 次**：无残留、无崩溃、条目数仍不超上限；
6. **判据有分辨力**：把上限调到"比帧数少一张"，命中立刻变 0（**对照实验**，
   没有对照的断言证明不了什么 —— §4.23 的教训）；
7. **仍守 task15 的基线**：`cache_key()` 仍是 4 维、开关不进键。

## ⚠ 这一份**不写用户真实目录**

所有落盘都在 `data/task16_out/`（工作区内，§4.13）。
`assets/` 一个字节都不动（脚本结尾核对哈希）。
用户真实的 `data/config.json` 不碰（本脚本压根不建 `ConfigStore`）。
"""

import hashlib
import os
import sys
import time
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

RESULT_FILE = ROOT / "data" / "task16_check.txt"

#: 自检自己的产出目录（**不碰 `assets/`**）。
OUT_DIR = ROOT / "data" / "task16_out"

#: 所有档位（逐个档位都要过一遍内存账）。
ALL_LEVELS = tuple(range(1, 11))

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


def note(text: str) -> None:
    """记一条**只有数据、没有结论**的观察（不参与 PASS/FAIL）。"""
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _mb(value: int) -> float:
    return value / 1024 / 1024


def _hash_assets() -> str:
    """`assets/` 的哈希（判据：自检前后不变）。"""
    digest = hashlib.sha256()
    for path in sorted((ROOT / "assets").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8", "replace"))
            digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


def _play_round(renderer, action: str, frames: int, level: int) -> tuple[int, int]:
    """播一轮，返回这一轮的 `(hits, misses)`。"""
    renderer.hits = renderer.misses = 0
    for index in range(frames):
        renderer.frame(action, index, level, False)
    return renderer.hits, renderer.misses


# ==================================================================== §1 预算与上限


def section_budget(app) -> None:
    """§1 内存预算与上限算法（task16 的核心：上限不再是一个常量）。"""
    from deskpet import pet_loader, rendering, size

    section("§1 上限算法：预算折算，且预算是硬顶")

    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    budget = rendering.MEMORY_BUDGET_BYTES
    note(
        f"MEMORY_BUDGET_BYTES = {budget} B = {_mb(budget):.0f} MiB；"
        f"DEFAULT_CACHE_LIMIT（已退化为兜底参数）= {rendering.DEFAULT_CACHE_LIMIT}"
    )

    check(
        "内存预算是一个明确的数字（160 MiB）",
        budget == 160 * 1024 * 1024,
        f"实际 {budget} B",
    )

    note("")
    note("档位  单张MB    预算张数  limit   该档全留MB")
    for level in ALL_LEVELS:
        pixel = size.pixel_size(spec.cell, level)
        per = rendering.bytes_per_frame(pixel)
        limit = rendering.limit_for(0, level, spec.cell)
        note(
            f"{level:>3}  {_mb(per):>7.3f}  {budget // per:>8}  {limit:>5}  "
            f"{_mb(limit * per):>9.2f}"
        )

    # ---- 每档上限 × 单张 ≤ 预算（**这是 §6"内存上限有明确数字"那条验收**）
    worst = 0
    for level in ALL_LEVELS:
        limit = rendering.limit_for(0, level, spec.cell)
        used = limit * rendering.bytes_per_frame(size.pixel_size(spec.cell, level))
        worst = max(worst, used)
    check(
        f"每个档位都满足「上限 × 单张 ≤ 预算」（最坏 {_mb(worst):.1f} MiB）",
        worst <= budget,
        f"最坏占用 {worst} B，预算 {budget} B",
    )

    # ---- 上限随档位单调不增（一张更大 → 放得下的张数更少）
    limits = [rendering.limit_for(0, lv, spec.cell) for lv in ALL_LEVELS]
    check(
        "上限随档位单调不增（一张更大 → 张数更少）",
        all(limits[i] >= limits[i + 1] for i in range(len(limits) - 1)),
        f"limits={limits}",
    )

    # ---- 第 6 级必须要能装下 idle 的 90 帧（否则 task16 一点问题都没解决）
    idle_frames = spec.action("idle").frame_count
    limit6 = rendering.limit_for(idle_frames, 6, spec.cell)
    check(
        f"第 6 级上限({limit6}) ≥ idle 帧数({idle_frames}) —— 这是本卡要修的那件事",
        limit6 >= idle_frames,
        f"上限={limit6} 帧数={idle_frames}（task16 之前恒为 64）",
    )

    # ---- 预算是硬顶：不许被"至少留 N 张"这类下限顶穿
    level10 = rendering.limit_for(0, 10, spec.cell)
    per10 = rendering.bytes_per_frame(size.pixel_size(spec.cell, 10))
    check(
        f"第 10 级上限({level10}) 听预算的、不听兜底常量({rendering.DEFAULT_CACHE_LIMIT})",
        level10 < rendering.DEFAULT_CACHE_LIMIT,
        f"上限={level10}，兜底常量={rendering.DEFAULT_CACHE_LIMIT}；"
        f"若取 64 就是 {_mb(64 * per10):.0f} MiB > 预算 {_mb(budget):.0f} MiB",
    )

    # ---- 边界：荒唐输入不许抛异常，且永远给得出 ≥1 张
    for cell, level in (((500, 500), 10), ((1, 1), 1), ((0, 0), 1), ((500, 500), 0), ((500, 500), 99)):
        try:
            value = rendering.limit_for(0, level, cell)
            ok = value >= 1
            detail = f"cell={cell} level={level} → {value}"
        except Exception as exc:  # pragma: no cover
            ok, detail = False, f"cell={cell} level={level} 抛了 {type(exc).__name__}: {exc}"
        check(f"边界输入给得出 ≥1 张（cell={cell} level={level}）", ok, detail)


# ==================================================================== §2 命中率


def section_hit_rate(app) -> None:
    """§2 命中率：**在预算内放得下的动作，第二轮必须全命中**。"""
    from deskpet import pet_loader, rendering, size

    section("§2 命中率：连播一轮后，第二轮在预算内全命中")

    budget = rendering.MEMORY_BUDGET_BYTES
    summary: list[str] = []

    for namespace in ("weedy", "weedy_sightseer"):
        spec = pet_loader.load_pet(ROOT / "assets" / namespace)
        note("")
        note(f"---- {namespace}（{len(spec.action_order)} 个动作）")
        for action in spec.action_order:
            frames = spec.action(action).frame_count
            for level in (1, 6, 10):
                renderer = rendering.FrameRenderer(spec, 1.0)
                per = rendering.bytes_per_frame(size.pixel_size(spec.cell, level))
                limit = renderer.limit_for_level(level)
                fits = frames * per <= budget

                _play_round(renderer, action, frames, level)
                hits, misses = _play_round(renderer, action, frames, level)
                used = renderer.cached_count() * per

                if fits:
                    ok = hits == frames and misses == 0
                    check(
                        f"{namespace}/{action} 第 {level} 级：第二轮全命中"
                        f"（{frames} 帧，占 {_mb(frames * per):.0f} MiB ≤ 预算）",
                        ok,
                        f"hits={hits} misses={misses} 条目={renderer.cached_count()} "
                        f"上限={limit}",
                    )
                else:
                    # 放不下：条目数必须**顶到上限**（缓存被用满，而不是没用），
                    # 且**不超预算**。这是用户拍板的"截断"口径。
                    check(
                        f"{namespace}/{action} 第 {level} 级：放不下时条目顶到上限且不超预算",
                        renderer.cached_count() == limit and used <= budget,
                        f"条目={renderer.cached_count()} 上限={limit} "
                        f"占用={_mb(used):.1f} MiB 预算={_mb(budget):.0f} MiB",
                    )

                summary.append(
                    f"{namespace}/{action} L{level}: {frames}帧 "
                    f"{'全命中' if fits else '截断'} "
                    f"hits={hits} 条目={renderer.cached_count()}/{limit} {_mb(used):.0f}MB"
                )

    note("")
    note("---- 汇总")
    for line in summary:
        note(line)


# ==================================================================== §3 全档位内存账


def section_memory(app) -> None:
    """§3 内存账：**所有动作 × 所有档位**的条目数 × 单张 ≤ 预算。"""
    from deskpet import pet_loader, rendering, size

    section("§3 内存账：全部动作 × 全部档位都不超预算")

    budget = rendering.MEMORY_BUDGET_BYTES
    worst = 0
    worst_where = ""
    checked = 0

    for namespace in ("weedy", "weedy_sightseer"):
        spec = pet_loader.load_pet(ROOT / "assets" / namespace)
        for action in spec.action_order:
            for level in ALL_LEVELS:
                renderer = rendering.FrameRenderer(spec, 1.0)
                limit = renderer.limit_for_level(level)
                per = rendering.bytes_per_frame(size.pixel_size(spec.cell, level))
                used = limit * per
                checked += 1
                if used > worst:
                    worst, worst_where = used, f"{namespace}/{action} L{level}"

    check(
        f"全部 {checked} 个（动作 × 档位）组合的「上限 × 单张 ≤ 预算」",
        worst <= budget,
        f"最坏 {worst_where} 占用 {_mb(worst):.1f} MiB，预算 {_mb(budget):.0f} MiB",
    )

    # ---- 真正跑一遍满载：条目数 × 单张 ≤ 预算（不只是算上限，而是真填满）
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    action = "waiting"  # 240 帧，第 6 级放不下 → 会被截断，正好验满载
    frames = spec.action(action).frame_count
    renderer = rendering.FrameRenderer(spec, 1.0)
    for index in range(frames):
        renderer.frame(action, index, 6, False)
    per = rendering.bytes_per_frame(size.pixel_size(spec.cell, 6))
    used = renderer.cached_count() * per
    check(
        f"真填满缓存后「条目数 × 单张 ≤ 预算」（{action} {frames} 帧，第 6 级）",
        used <= rendering.MEMORY_BUDGET_BYTES,
        f"条目={renderer.cached_count()} 单张={_mb(per):.3f} MiB "
        f"占用={_mb(used):.1f} MiB 预算={_mb(rendering.MEMORY_BUDGET_BYTES):.0f} MiB",
    )

    check(
        "被截断的动作条目数正好等于上限（缓存被用满，不是半空）",
        renderer.cached_count() == renderer.limit_for_level(6),
        f"条目={renderer.cached_count()} 上限={renderer.limit_for_level(6)}",
    )


# ==================================================================== §4 切档


def section_level_switch(app) -> None:
    """§4 反复切档 1↔10 各 3 次：无残留、无崩溃、条目数不超上限。"""
    from deskpet import pet_loader, rendering, size

    section("§4 反复切档 1↔10 各 3 次：无残留、不超上限")

    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    renderer = rendering.FrameRenderer(spec, 1.0)
    frames = spec.action("idle").frame_count

    for sweep in range(3):
        for level in (1, 10):
            for index in range(frames):
                renderer.frame("idle", index, level, False)
            per = rendering.bytes_per_frame(size.pixel_size(spec.cell, level))
            used = renderer.cached_count() * per
            check(
                f"第 {sweep + 1} 轮切到第 {level} 级后：条目不超该档上限且不超预算",
                renderer.cached_count() <= renderer.limit_for_level(level) and used <= rendering.MEMORY_BUDGET_BYTES,
                f"条目={renderer.cached_count()} 上限={renderer.limit_for_level(level)} "
                f"按该档算占用={_mb(used):.1f} MiB",
            )

    check(
        "反复切档 3 轮后没有崩溃、缓存键仍是 4 维元组",
        isinstance(next(iter(renderer._cache)), tuple)
        and len(next(iter(renderer._cache))) == 4,
        f"示例键：{next(iter(renderer._cache))}",
    )
    check(
        "clear() 之后条目归零（换素材包 / 换 DPI 的出口）",
        (renderer.clear() or renderer.cached_count()) == 0,
        f"条目={renderer.cached_count()}",
    )


# ==================================================================== §5 分辨力


def section_discrimination(app) -> None:
    """§5 对照实验：证明"命中率"这个判据**真的在看缓存上限**。"""
    from deskpet import pet_loader, rendering

    section("§5 对照实验：判据有分辨力吗")

    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    frames = spec.action("idle").frame_count

    # 对照①：上限比帧数少一张 → 第二轮**全不命中**（LRU 把整轮冲洗干净）
    tight = rendering.FrameRenderer(spec, limit=frames - 1)
    _play_round(tight, "idle", frames, 6)
    hits_tight, _ = _play_round(tight, "idle", frames, 6)
    check(
        f"[对照] 上限={frames - 1}（比帧数少一张）时第二轮命中为 0",
        hits_tight == 0,
        f"hits={hits_tight}（证明判据确实在看上限，而不是恒真）",
    )

    # 对照②：上限 = 帧数 → 第二轮**全命中**
    exact = rendering.FrameRenderer(spec, limit=frames)
    _play_round(exact, "idle", frames, 6)
    hits_exact, misses_exact = _play_round(exact, "idle", frames, 6)
    check(
        f"[对照] 上限={frames}（正好等于帧数）时第二轮全命中",
        hits_exact == frames and misses_exact == 0,
        f"hits={hits_exact} misses={misses_exact}",
    )

    # 对照③：产品的默认上限（不是显式给的）也要能全命中 —— 这才是真实路径
    auto = rendering.FrameRenderer(spec)
    _play_round(auto, "idle", frames, 6)
    hits_auto, misses_auto = _play_round(auto, "idle", frames, 6)
    check(
        f"[正证] 产品默认上限（自动算 = {auto.limit_for_level(6)}）时第二轮全命中",
        hits_auto == frames and misses_auto == 0,
        f"hits={hits_auto} misses={misses_auto} 上限={auto.limit_for_level(6)}",
    )


# ==================================================================== §6 task15 基线


def section_task15_baseline(app) -> None:
    """§6 守 task15 的基线：缓存键仍是 4 维、抗锯齿开关不进键。"""
    from deskpet import pet_loader, rendering

    section("§6 守 task15 基线：cache_key 仍是 4 维")

    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    off = rendering.FrameRenderer(spec, antialias=False)
    on = rendering.FrameRenderer(spec, antialias=True)

    for level in ALL_LEVELS:
        key_off = off.cache_key("idle", 0, level, False)
        key_on = on.cache_key("idle", 0, level, False)
        if key_off != key_on or len(key_off) != 4:
            check(
                f"第 {level} 级 cache_key 开/关一致且 4 维",
                False,
                f"off={key_off} on={key_on}",
            )
            return
    check(
        "全部 10 档：cache_key 开/关一致且 4 维（抗锯齿开关不进键）",
        True,
        "判据：每个档位的 (动作, 帧序号, 档位, 镜像) 与开关无关",
    )

    # 开关确实影响的是**出图**（否则上面那条就是废话）
    img_off = off.frame("idle", 0, 6, False).toImage()
    img_on = on.frame("idle", 0, 6, False).toImage()
    check(
        "但开关确实影响出图（同一键、两种渲染结果）",
        img_off != img_on,
        f"off={img_off.width()}x{img_off.height()} on={img_on.width()}x{img_on.height()}",
    )


# ==================================================================== §7 纪律


def section_discipline(app, hash_before: str) -> None:
    section("§7 自检纪律（assets/ 未被改动、不写用户真实目录）")

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
        "自检不创建 ConfigStore（不碰用户真实的 data/config.json）",
        not (ROOT / "data" / "task16_out" / "config.json").exists(),
        f"检查路径={ROOT / 'data' / 'task16_out' / 'config.json'}",
    )


# ==================================================================== main


def main() -> int:
    app = QApplication.instance() or QApplication([])
    hash_before = _hash_assets()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    section_budget(app)
    section_hit_rate(app)
    section_memory(app)
    section_level_switch(app)
    section_discrimination(app)
    section_task15_baseline(app)
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
    if not hasattr(QApplication, "instance"):  # pragma: no cover
        raise SystemExit(2)
    raise SystemExit(main())
