"""task22 自检（offscreen）：**反复调档之后，拖动与缩放必须仍然正确**。

用户 2026 报的两个问题（都由 task21 UX-06 引入）：

1. **卡住不能往下**：反复调整大小之后，宠物卡在一个位置，其它方向正常、
   只有「往下」永远不动；
2. **放大缩小会乱飞**：切档没有严格以右下角为锚点。

两个问题的根因是同一个函数 —— PetWindow._clamp_box() 当时把
「上一次夹取用的那一块」也并进了夹取框：

- 那个矩形是**历史**不是**事实**，且**跨档位不清空**；
- 于是夹取框会长成 708x879，而窗口只有 500x500；
- 框比窗口还大时，「把框夹进屏幕」对任何位置都判越界 -> 位置原地不动（问题 1），
  并且越界夹取把左上角推成负值 -> 右下角锚点漂移（问题 2）。

本脚本把这两条钉成回归：**无论之前怎么来回调档**，都必须满足

- _clamp_box() 永远是窗口的**子矩形**（宽高都不超过窗口）；
- 往下能真的往下走、往上能真的往上走；
- 连续切档时窗口右下角**逐次严格相等**；
- UX-06 的贴边能力不被改回去。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task22.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task22_check.txt"

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


#: 用户「反复调整大小」时真实会出现的各种来回顺序（含跨满 10 档、往返、跳跃）。
CHURN_SEQUENCES = (
    (10, 9, 8, 7, 6, 5, 4, 3, 2, 1),
    (1, 2, 3, 4, 5, 6, 7, 8, 9, 10),
    (6, 10, 1, 6, 10, 1, 6),
    (10, 10, 1, 1, 6, 8, 3, 7, 2, 9, 4, 5, 6),
    (7, 9, 5, 10, 3, 8, 6, 2, 6),
    # ⚠ 这一条**不含 10 档**：offscreen 虚拟屏只有 800x800，而 10 档是 1000x1000
    # （比屏幕还大，本就无处可移）。用它验“窗口装得下时四个方向都能动”。
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 4, 6, 8, 6),
)


def _load_spec():
    from deskpet import pet_loader

    return pet_loader.load_pet(ROOT / "assets" / "weedy")


def _window(spec, level: int, at=(200, 200)):
    from deskpet.pet_window import PetWindow

    window = PetWindow(spec, level)
    window.show_at(*at)
    return window


def _inside_window(box, width, height) -> bool:
    x, y, w, h = box
    return 0 <= x and 0 <= y and w <= width and h <= height and x + w <= width and y + h <= height


def section_box_fits_window(app) -> None:
    section("1. 夹取框永远是窗口的子矩形（不管之前怎么调档）")

    spec = _load_spec()
    offenders = []
    for start in (1, 3, 6, 10):
        for seq in CHURN_SEQUENCES:
            window = _window(spec, start)
            for level in seq:
                window.set_size_level(level)
            width, height = window.pixel_size
            box = window._clamp_box()
            if not _inside_window(box, width, height):
                offenders.append((start, seq, (width, height), box))
            window.close()
    check(
        "各种来回调档之后，夹取框都落在窗口内",
        not offenders,
        f"越界的组合：{offenders[:3]}",
    )

    # 基准必须取**同一档位**的全新窗口：夹取框本来就随档位变（这是设计），
    # 要验的是"同档位下，来回调过档与从没调过档结果一致"。
    mismatched = []
    for seq in CHURN_SEQUENCES:
        end_level = seq[-1]
        fresh = _window(spec, end_level)
        baseline = fresh._clamp_box()
        fresh.close()
        window = _window(spec, 6)
        for level in seq:
            window.set_size_level(level)
        got = window._clamp_box()
        if got != baseline:
            mismatched.append((seq, end_level, baseline, got))
        window.close()
    check(
        "同一档位下，来回调过档与全新窗口的夹取框一致（不残留历史）",
        not mismatched,
        f"不一致：{mismatched[:3]}",
    )


def section_can_move(app) -> None:
    section("2. 反复调档之后，四个方向都还能动")

    spec = _load_spec()

    # ⚠ offscreen 的虚拟屏幕只有 800x800（§4.14），而最高档窗口是 1000x1000——
    # **比屏幕还大**。那种情况下本来就"无处可移"（夹取必然原地不动），
    # 不是缺陷。所以只挑"窗口装得进屏幕"的档位序列来验移动。
    probe_window = _window(spec, 6)
    area = probe_window._screen_area()
    probe_window.close()

    def _levels_fit(seq) -> bool:
        window = _window(spec, seq[0])
        ok = True
        for level in seq:
            window.set_size_level(level)
            width, height = window.pixel_size
            if width > area[2] or height > area[3]:
                ok = False
                break
        window.close()
        return ok

    usable = [seq for seq in CHURN_SEQUENCES if _levels_fit(seq)]
    note(f"可用于移动验证的调档序列：{len(usable)} / {len(CHURN_SEQUENCES)}（其余含超大档位）")
    check("至少有一条序列可用于验证", bool(usable), f"{usable[:2]}")

    stuck = []
    for seq in usable:
        window = _window(spec, 6)
        for level in seq:
            window.set_size_level(level)
        width, height = window.pixel_size
        window._move_clamped(
            area[0] + (area[2] - width) // 2, area[1] + (area[3] - height) // 2
        )
        results = {}
        for name, dx, dy in (("下", 0, 300), ("上", 0, -300), ("左", -300, 0), ("右", 300, 0)):
            before = window.base_position
            window._move_clamped(before.x() + dx, before.y() + dy)
            results[name] = window.base_position != before
        if not all(results.values()):
            stuck.append((seq, results))
        window.close()
    check(
        "四个方向都能移动（尤其往下）",
        not stuck,
        f"卡住的组合：{stuck[:3]}",
    )

    # 用户报的现象原样复现：反复调大小 → 只往下，必须真的往下。
    window = _window(spec, 6)
    for level in (10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 10, 1, 6):
        window.set_size_level(level)
    window._move_clamped(window.base_position.x(), area[1] - 9999)
    ys = []
    for _ in range(3):
        window._move_clamped(window.base_position.x(), window.base_position.y() + 120)
        ys.append(window.base_position.y())
    check(
        "反复调档后，连续三次往下都能真的往下",
        ys[0] < ys[1] <= ys[2],
        f"y 依次为 {ys}",
    )
    window.close()

def _corner(window):
    width, height = window.pixel_size
    return (window.base_position.x() + width, window.base_position.y() + height)


def section_anchor(app) -> None:
    section("3. 切档严格以右下角为锚点（右下角逐次相等）")

    spec = _load_spec()
    window = _window(spec, 6, at=(150, 120))
    anchor = _corner(window)
    check("取初始右下角作为锚点", True, f"{anchor}")

    moved = []
    for level in (5, 4, 3, 2, 1, 2, 3, 4, 5, 6):
        window.set_size_level(level)
        corner = _corner(window)
        moved.append((level, corner, corner == anchor))
    bad = [m for m in moved if not m[2]]
    check(
        "连续切档时右下角始终等于锚点",
        not bad,
        f"漂移的：{bad[:3]}",
    )
    window.close()

    # 已经贴着屏幕右下角（最常见的摆放位置）时，缩小也必须守住右下角。
    window = _window(spec, 6, at=(300, 300))
    area = window._screen_area()
    width, height = window.pixel_size
    window.move_to(area[0] + area[2] - width, area[1] + area[3] - height)
    anchor = _corner(window)
    drift = []
    for level in (5, 4, 3, 2, 1):
        window.set_size_level(level)
        corner = _corner(window)
        if corner != anchor:
            drift.append((level, corner))
    check(
        "贴着屏幕右下角时，缩小仍然守得住右下角",
        not drift,
        f"漂移的：{drift[:3]}（锚点 {anchor}）",
    )
    window.close()


def section_still_hugs_edge(app) -> None:
    section("4. UX-06 的贴边能力不能被这次修复弄回去")

    spec = _load_spec()
    window = _window(spec, 6)
    area = window._screen_area()
    window._move_clamped(area[0] + area[2] + 5000, area[1] + area[3] + 5000)
    box = window._clamp_box()
    b = window.base_position
    vis_right = b.x() + box[0] + box[2]
    vis_bottom = b.y() + box[1] + box[3]
    check(
        "往右下拖到底：可见块右下角贴住屏幕右下角",
        (vis_right, vis_bottom) == (area[0] + area[2], area[1] + area[3]),
        f"可见块右下 {(vis_right, vis_bottom)}",
    )
    width, height = window.pixel_size
    check(
        "透明留白仍然允许出屏（可见块才是贴边依据）",
        b.x() + width > area[0] + area[2] or b.y() + height > area[1] + area[3],
        f"窗口右下 {(b.x() + width, b.y() + height)}",
    )
    window.close()


def section_churn_stays_on_screen(app) -> None:
    section("5. 反复调档时每一次都在屏内")

    spec = _load_spec()
    window = _window(spec, 3, at=(400, 400))
    area = window._screen_area()
    out = []
    for level in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9, 8, 7, 6, 5, 4, 3):
        window.set_size_level(level)
        width, height = window.pixel_size
        if width > area[2] or height > area[3]:
            # 窗口本身比屏幕还大：可见块出屏是**几何必然**，跳过这一档。
            continue
        x, y = window.base_position.x(), window.base_position.y()
        box = window._clamp_box()
        if not (
            area[0] <= x + box[0]
            and area[1] <= y + box[1]
            and x + box[0] + box[2] <= area[0] + area[2]
            and y + box[1] + box[3] <= area[1] + area[3]
        ):
            out.append((level, (x, y), box))
    check(
        "每一次切档后可见块都整块在屏内",
        not out,
        f"出屏的：{out[:3]}",
    )
    window.close()


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        _LINES.append("task22 自检 —— 反复调档后拖动与缩放仍然正确")
        _LINES.append(f"工作区根：{ROOT}")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        section_box_fits_window(app)
        section_can_move(app)
        section_anchor(app)
        section_still_hugs_edge(app)
        section_churn_stays_on_screen(app)

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
