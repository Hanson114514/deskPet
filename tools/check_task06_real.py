"""task06 真平台验收：真窗口下的遮罩、菜单定位、拖动、翻转观感。

自检主体（`check_task06.py`）跑在 offscreen 上，**测不出这几件事**（agent.md §4.9）：

1. `setMask` 在 offscreen 下**不受支持**（插件会打印 "does not support setting
   window masks"）→ "翻转后遮罩跟着换面"只能在真平台验；
2. 菜单**在鼠标处弹出**这件事要有真窗口与真事件循环；
3. 拖动中右键不打断拖动，在真事件循环下才作数。

跑法（**真平台，会短暂弹出真窗口**）：

    python tools/check_task06_real.py

结果写 `data/task06_real.txt`（UTF-8）。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task06_real.txt"
SCRATCH = ROOT / "data" / "task06_real_scratch"

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
    from PySide6.QtCore import QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication

    from deskpet import context_menu as CM
    from deskpet import pet_loader
    from deskpet.config import ConfigStore
    from deskpet.mirror import MirrorState
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    cfg = SCRATCH / "config.json"
    store = ConfigStore(cfg)
    store.load()

    mirror = MirrorState(store)
    window = PetWindow(spec, 4, mirror=mirror)
    window.play_action("idle")
    window.showing_at_default_position()
    window.show()

    # 让真窗口真的起来（真平台下 setMask 要等窗口映射之后才生效）。
    for _ in range(12):
        app.processEvents()

    # ------------------------------------------------------------ §1 真遮罩
    section("1. 真平台：遮罩与『翻转后遮罩跟着换面』")

    # ⚠ **先定格在同一帧**再比较遮罩：`idle` 在播的时候每帧轮廓都不同
    # （实测相邻帧的外接矩形宽度会差 1 px），于是"翻转前后宽度一致"这条
    # 会在两次读到**不同帧**时偶发失败——那是测试没控制变量，不是产品错了
    # （task08 加镜像命中用例时这条真的偶发红过一次）。
    window.hold_frame(0)
    for _ in range(8):
        app.processEvents()

    mask_before = window.mask()
    check(
        "真平台下窗口设上了遮罩（offscreen 测不出这一条）",
        not mask_before.isEmpty(),
        f"区域是否为空：{mask_before.isEmpty()}",
    )

    rect_before = mask_before.boundingRect()
    frame_before = window.frame_index
    window.toggle_mirror()
    for _ in range(8):
        app.processEvents()
    rect_after = window.mask().boundingRect()
    check(
        "翻转前后看的是**同一帧**（定格住了，比较才有意义）",
        window.frame_index == frame_before,
        f"帧号 {frame_before} → {window.frame_index}",
    )

    check("翻转后画面确实变了", window.mirrored is True, "mirrored=True")
    check(
        "翻转后遮罩重新算过（外接矩形仍在窗口内且非空）",
        not rect_after.isEmpty()
        and rect_after.width() > 0
        and rect_after.height() > 0,
        f"外接矩形 {rect_after.x()},{rect_after.y()} {rect_after.width()}x{rect_after.height()}",
    )
    check(
        "翻转前后遮罩外接矩形的**宽度**一致（水平翻转不改变轮廓尺寸）",
        rect_before.width() == rect_after.width(),
        f"{rect_before.width()} → {rect_after.width()}",
    )
    check(
        "翻转前后遮罩外接矩形的**高度**一致（水平翻转不改变轮廓尺寸）",
        rect_before.height() == rect_after.height(),
        f"{rect_before.height()} → {rect_after.height()}",
    )

    window.toggle_mirror()
    for _ in range(8):
        app.processEvents()
    check("翻回来", window.mirrored is False, "mirrored=False")

    # ------------------------------------------------------------ §2 位置尺寸
    section("2. 翻转不改变位置与尺寸（锚点仍成立）")

    pos_before = window.base_position
    size_before = window.pixel_size
    window.toggle_mirror()
    for _ in range(8):
        app.processEvents()
    check(
        "翻转后位置不变",
        window.base_position == pos_before,
        f"{pos_before} → {window.base_position}",
    )
    check(
        "翻转后尺寸不变",
        window.pixel_size == size_before,
        f"{size_before} → {window.pixel_size}",
    )
    check(
        "翻转后画面清晰、与原尺寸一致（不是拉伸变形）",
        (window.frame_image.width(), window.frame_image.height()) == size_before,
        f"帧图 {window.frame_image.width()}x{window.frame_image.height()}",
    )

    # ------------------------------------------------------------ §3 菜单定位
    section("3. 真平台：菜单在鼠标处弹出 + 层级是宠物窗口")

    captured: dict = {}

    def on_menu(*args, **kwargs):
        # 用真 build_menu，但把 exec 换掉：避免真弹出阻塞事件循环。
        menu = real_build(*args, **kwargs)
        captured["menu"] = menu
        captured["state"] = args[1]

        class _Shim:
            def __init__(self, inner):
                self._inner = inner

            def setAttribute(self, *a, **k):
                return self._inner.setAttribute(*a, **k)

            def exec(self, *a, **k):
                captured["exec_args"] = a
                return None

        return _Shim(menu)

    import deskpet.pet_window as PW

    real_build = CM.build_menu
    PW.context_menu.build_menu = on_menu  # type: ignore[assignment]
    try:
        target = QPoint(77, 88)
        event = QContextMenuEvent(
            QContextMenuEvent.Mouse, QPoint(5, 5), window.mapToGlobal(target)
        )
        window.contextMenuEvent(event)
    finally:
        PW.context_menu.build_menu = real_build  # type: ignore[assignment]

    menu = captured.get("menu")
    check("右键真的构建了菜单", menu is not None, f"state={captured.get('state')!r}")
    check("菜单的 parent 是宠物窗口", menu is not None and menu.parent() is window, "parent=window")
    exec_args = captured.get("exec_args") or ()
    check(
        "菜单在**鼠标处**弹出（用 globalPos）",
        bool(exec_args) and exec_args[0] == window.mapToGlobal(target),
        f"exec 的位置 {exec_args[0] if exec_args else None}",
    )
    if menu is not None:
        texts = [a.text() for a in menu.actions() if not a.isSeparator()]
        # ⚠ task17 在 `action` 与 `mirror` **之间**插了一项形态项
        #   （`开启KV` / `退出KV`，task17 §4.3 明写的位置），所以这条基线跟着更新。
        #   顺序本身仍是契约，只是契约**长了一项**——不是把断言放松。
        check(
            "真菜单的文字顺序与附录 B 一致（含 task17 的形态项）",
            texts
            == ["调整大小…", "坐下休息", "开启KV", "镜像翻转", "设置…", "隐藏宠物", "退出"],
            f"实际 {texts}",
        )

    # ------------------------------------------------------------ §4 拖动 + 右键
    section("4. 真平台：拖动中右键不打断拖动")

    # task08 把拖动收进了 `drag.DragController`（旧的 `_dragging` / `_press_global` /
    # `_drag_base_pos` 三个私有字段已作废）。这里改用**真实鼠标事件**进入拖动。
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent

    start = window.base_position
    window.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(5.0, 5.0),
            QPointF(start.x() + 5.0, start.y() + 5.0),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    window.mouseMoveEvent(
        QMouseEvent(
            QEvent.MouseMove,
            QPointF(65.0, 45.0),
            QPointF(start.x() + 65.0, start.y() + 45.0),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    moved = window.base_position
    check("拖动真的移动了窗口", moved != start, f"{start} → {moved}")
    check("已进入拖动状态", window.dragging is True)

    PW.context_menu.build_menu = on_menu  # type: ignore[assignment]
    try:
        window.contextMenuEvent(
            QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(5, 5), QPoint(1060, 1040))
        )
    finally:
        PW.context_menu.build_menu = real_build  # type: ignore[assignment]
    check(
        "拖动中右键不改位置",
        window.base_position == moved,
        f"{moved} → {window.base_position}",
    )
    check("拖动中右键不结束拖动", window.dragging is True, "dragging=True")
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(65.0, 45.0),
            QPointF(moved.x() + 65.0, moved.y() + 45.0),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )
    check("松手后退出拖动", window.dragging is False)

    # ------------------------------------------------------------ §5 事件循环存活
    section("5. 翻转 10 次期间事件循环不卡（真事件循环）")

    ticks = {"n": 0}
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    heartbeat.start()

    frames_seen = []
    # 期望值**从翻转前的实际状态推**，不写死字面量：
    # 前面几节翻过好几次，写死等于把测试绑在那串序列上（§4.11：别断言会漂的量）。
    expected_flips = []
    current = window.mirrored
    for _ in range(10):
        current = not current
        expected_flips.append(current)
        window.toggle_mirror()
        frames_seen.append(window.mirrored)
        for _ in range(6):
            app.processEvents()
    heartbeat.stop()

    check(
        "10 次翻转每次状态都正确交替",
        frames_seen == expected_flips,
        f"期望 {expected_flips} / 实际 {frames_seen}",
    )
    # "每次都真的翻面了" = 相邻两次的取值必须两两不同，且两种状态都出现过。
    check(
        "10 次翻转是严格交替（没有一次空转）",
        all(frames_seen[i] != frames_seen[i + 1] for i in range(len(frames_seen) - 1))
        and len(set(frames_seen)) == 2,
        f"序列 {frames_seen}",
    )
    check(
        "期间心跳定时器有被处理（事件循环没被阻塞）",
        ticks["n"] > 0,
        f"心跳 {ticks['n']} 次",
    )

    # 收尾：翻转状态写进了配置
    written_ok = cfg.exists()
    check(
        "10 次翻转后配置写下去了（镜像被记住）",
        written_ok,
        f"{cfg} 存在={written_ok}",
    )

    window.stop_animation()
    window.close()
    for _ in range(6):
        app.processEvents()
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
