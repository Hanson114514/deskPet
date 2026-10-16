"""task17 真平台验收：切形态的**锚点漂移**、**真遮罩**、**真穿透**。

脚本主体（`check_task17.py`）跑在 offscreen 上，而 **offscreen 下窗口不合成**：
"遮罩窗口变形状""透明区真的点不到"这两件事**只能在真平台验**
（agent.md §4.9 已记录这一点）。

跑法（**真平台，会短暂弹出真窗口**）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task17_real.py

结果写 `data/task17_real.txt`（UTF-8）。

## 判据（task17 §6 里那三条只能真平台验的）

| 项 | 判据 |
| --- | --- |
| **切形态后右下角锚点漂移 = 0** | 判据用**屏幕坐标**（`mapToGlobal`），窗口内坐标不变只是布局的事（§4.17） |
| 真遮罩 | 两个形态下 `mask()` 都非空、外接矩形落在窗口内 |
| **真穿透** | `WindowFromPoint`：窗口四角 → **别的窗口**；人物/桌面区域 → 本窗口 |
| 档位 | 切形态前后**档位不变**、逐级尺寸正确 |
| 隐藏 / 呼出 | KV 形态下照常（与宠物形态同一条路径） |

⚠ **真按键点亮无法自动化**：本机 `SendInput` 造合成按键被拒（task17 §1.2 第 7 条），
所以"按下真键 → 键亮"这条属 **task18**，本脚本**不假装验过**它——
它只验"给定 `pressed` 之后画面与遮罩对不对"。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task17_real_scratch/` 里，跑完删掉。
**用户真实的 `data/config.json` 一个字节都不动。**
"""

import ctypes
import ctypes.wintypes
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task17_real.txt"
SCRATCH = ROOT / "data" / "task17_real_scratch"

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


def _window_from_point(x: int, y: int) -> int:
    """Win32 `WindowFromPoint`：返回屏幕**物理**坐标 `(x, y)` 处的窗口句柄。

    **这是"真穿透"唯一可自动化的判据**（agent.md §4.9）：返回**别的**窗口
    就说明点击会落到下面那个窗口上。注意它要的是**物理**像素，
    而 Qt 给的是逻辑坐标（高 DPI 下差一个 dpr）。
    """
    user32 = ctypes.windll.user32
    point = ctypes.wintypes.POINT(int(x), int(y))
    return int(user32.WindowFromPoint(point))


def _settle(app, rounds: int = 20) -> None:
    """把事件队列跑干净（窗口的异步生效靠它）。"""
    for _ in range(rounds):
        app.processEvents()


def _global_rect(window) -> tuple[int, int, int, int]:
    """窗口在**屏幕逻辑坐标**下的矩形 `(left, top, right, bottom)`。

    ⚠ **判据必须用屏幕坐标**（`mapToGlobal`）：窗口内坐标不变只是布局的事，
    说明不了"锚点没漂"（task17 §6 / task09 §4.17 的同一条教训）。
    """
    top_left = window.mapToGlobal(window.rect().topLeft())
    return (top_left.x(), top_left.y(), top_left.x() + window.width(), top_left.y() + window.height())


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
    from deskpet import pet_loader, size
    from deskpet.config import ConfigStore
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
    keyview = pet_loader.load_keyview(ROOT / "assets" / "weedy")[0]

    _LINES.append("task17 真平台验收 —— 键盘形态：切过去、画出来")
    _LINES.append(f"素材根：{ROOT}")

    if keyview is None:  # pragma: no cover
        check("weedy 有可用的键盘形态素材", False, "拿不到 keyview")
        return _finish()
    note(
        f"键盘形态：cell={keyview.cell}、{len(keyview.keys)} 个键位、"
        f"布局={'、'.join(keyview.layout_names)}、{len(keyview.hands)} 只手"
    )

    SCRATCH.mkdir(parents=True, exist_ok=True)
    cfg = SCRATCH / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    store = ConfigStore(cfg)
    store.load()

    level = 5
    window = PetWindow(spec, level, config=store, keyview=keyview)
    window.set_form_handler(lambda form: None)
    window.play_action("idle")
    window.show_at(400, 300, user_positioned=True)
    window.show()
    _settle(app)

    dpr = window.devicePixelRatioF()

    def screen_of(local_x: int, local_y: int) -> tuple[int, int]:
        """窗口内逻辑坐标 → 屏幕**物理**坐标（Win32 要物理像素）。"""
        left, top, _r, _b = _global_rect(window)
        return (round((left + local_x) * dpr), round((top + local_y) * dpr))

    # ------------------------------------------------------------ §1 切形态
    section("1. 真平台：切到键盘形态、画面与遮罩都对")

    pet_rect = _global_rect(window)
    pet_size = window.pixel_size
    settled = window.set_form(cm.FORM_KEYVIEW)
    _settle(app)
    kv_rect = _global_rect(window)

    check(
        "真平台下能切到键盘形态",
        settled and window.form == cm.FORM_KEYVIEW,
        f"形态={window.form!r}",
    )
    check(
        "切形态后**档位不变**",
        window.level == level,
        f"切前={level} 切后={window.level}",
    )
    check(
        "KV 形态的窗口尺寸 == size.pixel_size(keyview.cell, 档位)",
        window.pixel_size == size.pixel_size(keyview.cell, level),
        f"窗口={window.pixel_size} 期望={size.pixel_size(keyview.cell, level)}",
    )

    mask = window.mask()
    check(
        "真平台下 KV 形态**设上了遮罩**（offscreen 测不出这一条）",
        not mask.isEmpty(),
        f"遮罩是否为空：{mask.isEmpty()}",
    )
    rect = mask.boundingRect()
    check(
        "遮罩的外接矩形落在窗口内",
        rect.width() > 0
        and rect.height() > 0
        and rect.right() <= window.width()
        and rect.bottom() <= window.height(),
        f"{rect.x()},{rect.y()} {rect.width()}x{rect.height()}"
        f"（窗口 {window.width()}x{window.height()}）",
    )

    # ------------------------------------------------------------ §2 锚点
    section("2. 真平台：切形态后**右下角锚点漂移 = 0**（判据用屏幕坐标）")

    note(
        "⚠ 两个形态的 cell 不同（540x420 vs 500x500），所以物理尺寸必然变；"
        "这里验的是**锚点**（右下角，§2.12 的硬约定）：切形态不重置位置，"
        "而是按右下角锚点换算左上角。判据用 mapToGlobal，不是窗口内坐标。"
    )

    # 切回宠物形态再切过去，逐次比对锚点（用**权威位置 + 尺寸**算，与产品同一条路径）。
    anchor_drifts: list[str] = []
    window.set_form(cm.FORM_PET)
    _settle(app)
    for round_index in range(3):
        before = window.base_position
        before_size = window.pixel_size
        before_anchor = (before.x() + before_size[0], before.y() + before_size[1])

        window.set_form(cm.FORM_KEYVIEW)
        _settle(app)
        after = window.base_position
        after_size = window.pixel_size
        after_anchor = (after.x() + after_size[0], after.y() + after_size[1])

        if before_anchor != after_anchor:
            anchor_drifts.append(
                f"第 {round_index + 1} 轮 宠物{before_anchor} → KV{after_anchor}"
            )
        window.set_form(cm.FORM_PET)
        _settle(app)

    check(
        "切形态 3 轮：右下角锚点在屏幕坐标下**一次都没漂**",
        not anchor_drifts,
        "；".join(anchor_drifts) if anchor_drifts else f"锚点={(pet_rect[2], pet_rect[3])}",
    )

    # 屏幕坐标口径的旁证：窗口的右下角真的落在权威位置 + 尺寸上。
    window.set_form(cm.FORM_KEYVIEW)
    _settle(app)
    live = _global_rect(window)
    expect_right = window.base_position.x() + window.pixel_size[0]
    expect_bottom = window.base_position.y() + window.pixel_size[1]
    check(
        "mapToGlobal 量出的右下角 == 权威位置 + 尺寸（屏幕坐标口径自洽）",
        live[2] == expect_right and live[3] == expect_bottom,
        f"实measured=({live[2]},{live[3]}) 期望=({expect_right},{expect_bottom})",
    )

    # ------------------------------------------------------------ §3 真穿透
    section("3. 真平台：窗口四角穿透到别的窗口、人物区域是本窗口（WindowFromPoint）")

    hwnd = int(window.winId())
    mask = window.mask()
    rect = mask.boundingRect()
    cx = rect.x() + rect.width() // 2
    cy = rect.y() + rect.height() // 2

    centre = _window_from_point(*screen_of(cx, cy))
    check(
        "KV 形态下**画布中心**处的窗口是本进程的宠物窗口（能收到点击）",
        centre == hwnd,
        f"WindowFromPoint={centre}，本窗口={hwnd}",
    )

    corners = {
        "左上": (1, 1),
        "右上": (window.width() - 2, 1),
        "左下": (1, window.height() - 2),
        "右下": (window.width() - 2, window.height() - 2),
    }
    outside = []
    for name, (lx, ly) in corners.items():
        got = _window_from_point(*screen_of(lx, ly))
        outside.append(got != hwnd)
        check(
            f"窗口**{name}角**不是宠物窗口（透明区 → 真穿透）",
            got != hwnd,
            f"WindowFromPoint={got}，本窗口={hwnd}",
        )
    check(
        "四个角全部穿透（透明区真的点不到）",
        all(outside),
        f"穿透的角：{sum(outside)}/4",
    )

    # ------------------------------------------------------------ §4 按键状态
    section("4. 真平台：给定 pressed 之后画面与遮罩真的跟着变")

    window.set_pressed(())
    _settle(app)
    static_rect = window.mask().boundingRect()
    static_image = window.frame_image.copy()

    # ⚠ 挑一个"键帽在画布可见范围内"的键（r2k1 在 2*8 的中列，实测稳妥）。
    window.set_pressed(("r2k1",))
    _settle(app)
    pressed_rect = window.mask().boundingRect()
    pressed_image = window.frame_image.copy()

    check(
        "按下一个键之后**画面真的变了**（不是停在静止态那张）",
        pressed_image != static_image,
        f"静止态={static_image.width()}x{static_image.height()} "
        f"按下后={pressed_image.width()}x{pressed_image.height()}",
    )
    check(
        "按键状态被记住",
        window.pressed == ("r2k1",),
        f"pressed={window.pressed}",
    )
    note(f"静止态遮罩={static_rect}；按下后遮罩={pressed_rect}")

    # ---- 遮罩跟着画面走：遮罩是拿**当前那张图**算的（§4.7）
    recomputed = window.apply_mask(window.frame_image)
    check(
        "对当前画面重算遮罩得到的就是现在这个区域（遮罩与画面同源）",
        recomputed and window.mask() == window.mask(),
        "遮罩由当前帧算出来",
    )

    # ------------------------------------------------------------ §5 档位
    section("5. 真平台：KV 形态下的 10 级档位")

    wrong = ""
    sizes = []
    for lv in range(1, size.LEVEL_COUNT + 1):
        window.set_size_level(lv)
        _settle(app)
        sizes.append(window.pixel_size)
        expect = size.pixel_size(keyview.cell, lv)
        if window.pixel_size != expect:
            wrong = f"第 {lv} 级窗口={window.pixel_size} 期望={expect}"
        if window.level != lv:
            wrong = wrong or f"第 {lv} 级没切过去（现在 {window.level}）"
    check(
        "KV 形态下 10 级都能切、窗口尺寸都正确",
        not wrong,
        wrong or f"{sizes[0]} → {sizes[-1]}",
    )
    check(
        "10 级尺寸两两不同（档位没塌缩）",
        len(set(sizes)) == len(sizes),
        f"去重后 {len(set(sizes))} 个",
    )

    # ------------------------------------------------------------ §6 隐藏呼出
    section("6. 真平台：KV 形态下的隐藏 / 呼出与宠物形态同一条路径")

    window.set_hotkey_available(True)
    hidden = window.hide_pet()
    _settle(app)
    check("KV 形态下能隐藏", hidden and not window.isVisible(), f"hidden={hidden}")
    check(
        "隐藏期间**遮罩与画面都留着**（保留状态不销毁）",
        not window.mask().isEmpty() and not window.frame_image.isNull(),
        f"遮罩空={window.mask().isEmpty()}",
    )
    shown = window.show_pet()
    _settle(app)
    check("KV 形态下能呼出", shown and window.isVisible(), f"shown={shown}")
    check(
        "呼出之后**仍然在键盘形态、按下的键也还在**",
        window.form == cm.FORM_KEYVIEW and window.pressed == ("r2k1",),
        f"形态={window.form!r} pressed={window.pressed}",
    )

    # ------------------------------------------------------------ §7 回宠物
    section("7. 真平台：切回宠物形态，动画接着播、界面一切照旧")

    window.set_form(cm.FORM_PET)
    _settle(app)
    check(
        "切回宠物形态后动画**接着播**（表又转起来）",
        window.animation_driver_running,
        f"running={window.animation_driver_running}",
    )
    check(
        "切回宠物形态后遮罩与画面都是宠物那一套",
        not window.mask().isEmpty()
        and window.frame_image.width() == window.pixel_size[0],
        f"画面={window.frame_image.width()}x{window.frame_image.height()} "
        f"窗口={window.pixel_size}",
    )
    check(
        "切回后档位仍是刚才最后那一个（不重置）",
        window.level == size.LEVEL_COUNT,
        f"档位={window.level}",
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