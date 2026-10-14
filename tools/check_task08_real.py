"""task08 真平台验收：**真穿透**、跟手拖动、位置持久化、拖动落点夹取。

自检主体（`check_task08.py`）跑在 offscreen 上，**测不出这几件事**（agent.md §4.9）：

1. **`setMask` 在 offscreen 下不受支持**（插件会打印 "does not support setting
   window masks"）→ "点透明处真的穿透到下面的窗口"只能在真平台验；
2. 穿透的**可自动化判据**是 Win32 `WindowFromPoint`：在人物身上取点应返回
   **本进程的窗口**，在透明角落取点应返回**别的窗口**；
3. 真窗口下拖动跟手、拖动结束把位置写进 `config.json`、重启后还在原位。

跑法（**真平台，会短暂弹出真窗口**）：

    python tools/check_task08_real.py

结果写 `data/task08_real.txt`（UTF-8）。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task08_real_scratch/` 里（`paths.override_dirs()`
把程序目录指到仓库根，但配置路径由 `ConfigStore(path)` 显式给出），
跑完整个 scratch 目录被删掉。**用户真实的 `data/config.json` 一个字节都不动。**
"""

import ctypes
import ctypes.wintypes
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task08_real.txt"
SCRATCH = ROOT / "data" / "task08_real_scratch"

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


def _window_from_point(x: int, y: int) -> int:
    """Win32 `WindowFromPoint`：返回屏幕物理坐标 `(x, y)` 处的窗口句柄。

    **这是"真穿透"唯一可自动化的判据**（agent.md §4.9）：
    返回**别的**窗口就说明点击会落到下面那个窗口上。
    注意它要的是**物理**像素，而 Qt 给的是逻辑坐标（高 DPI 下差一个 dpr）。
    """
    user32 = ctypes.windll.user32
    point = ctypes.wintypes.POINT(int(x), int(y))
    return int(user32.WindowFromPoint(point))


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
    from PySide6.QtGui import QGuiApplication, QMouseEvent
    from PySide6.QtWidgets import QApplication

    from deskpet import pet_loader
    from deskpet.config import ConfigStore
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])
    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    cfg = SCRATCH / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    store = ConfigStore(cfg)
    store.load()

    window = PetWindow(spec, 4, config=store)
    window.play_action("idle")
    window.show_at(400, 300, user_positioned=True)
    window.show()
    for _ in range(12):
        app.processEvents()

    dpr = window.devicePixelRatioF()

    # ------------------------------------------------------------ §1 真穿透
    section("1. 真平台：人物身上是本窗口，透明角落是别的窗口（WindowFromPoint）")

    mask = window.mask()
    check(
        "真平台下窗口设上了遮罩（offscreen 测不出这一条）",
        not mask.isEmpty(),
        f"区域是否为空：{mask.isEmpty()}",
    )

    rect = mask.boundingRect()
    check(
        "遮罩的外接矩形落在窗口内",
        rect.width() > 0
        and rect.height() > 0
        and rect.right() <= window.width()
        and rect.bottom() <= window.height(),
        f"{rect.x()},{rect.y()} {rect.width()}x{rect.height()}（窗口 {window.width()}x{window.height()}）",
    )

    hwnd = int(window.winId())
    origin = window.base_position
    cx = rect.x() + rect.width() // 2
    cy = rect.y() + rect.height() // 2

    def screen_of(local_x: int, local_y: int) -> tuple[int, int]:
        """窗口内逻辑坐标 → 屏幕**物理**坐标（Win32 要物理像素）。"""
        return (
            round((origin.x() + local_x) * dpr),
            round((origin.y() + local_y) * dpr),
        )

    centre_hwnd = _window_from_point(*screen_of(cx, cy))
    check(
        "人物**中心**处的窗口是本进程的宠物窗口（能收到点击）",
        centre_hwnd == hwnd,
        f"WindowFromPoint={centre_hwnd}，本窗口={hwnd}",
    )

    # 窗口四角：素材是 500×500 画布上的人物，四角必然在外面。
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
        "四个角全部穿透（透明区真的点不到宠物）",
        all(outside),
        f"穿透的角：{sum(outside)}/4",
    )

    # ------------------------------------------------------------ §2 拖动跟手
    section("2. 真平台：拖动跟手 + 按水平方向切跑动动作 + 松手回 idle")

    def press(local, glob, buttons=None):
        window.mousePressEvent(
            QMouseEvent(
                QEvent.MouseButtonPress,
                QPointF(*local),
                QPointF(*glob),
                Qt.LeftButton,
                Qt.LeftButton if buttons is None else buttons,
                Qt.NoModifier,
            )
        )

    def move(local, glob):
        window.mouseMoveEvent(
            QMouseEvent(
                QEvent.MouseMove,
                QPointF(*local),
                QPointF(*glob),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )

    def release(local, glob):
        window.mouseReleaseEvent(
            QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(*local),
                QPointF(*glob),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )

    start = window.base_position
    press((cx, cy), (start.x() + cx, start.y() + cy))
    check("按下后进入拖动", window.dragging is True)

    move((cx + 120, cy + 40), (start.x() + cx + 120, start.y() + cy + 40))
    moved = window.base_position
    check(
        "向右拖：位置 = 基准 + 位移（跟手，不丢偏移）",
        moved == QPoint(start.x() + 120, start.y() + 40),
        f"{start} → {moved}",
    )
    check(
        "向右拖 → running_right",
        window.animation.action == "running_right",
        f"{window.animation.action}",
    )

    move((cx - 100, cy + 40), (start.x() + cx - 100, start.y() + cy + 40))
    check(
        "向左拖 → running_left",
        window.animation.action == "running_left",
        f"{window.animation.action}",
    )

    # **快速来回拖**：每一步都按"按下点 + 累计位移"算，不应该越拖越偏。
    for step in range(12):
        offset = 200 if step % 2 == 0 else -200
        move((cx + offset, cy), (start.x() + cx + offset, start.y() + cy))
        expected = QPoint(start.x() + offset, start.y())
        if window.base_position != expected:
            check(
                "快速来回拖不丢偏移（每步都回到同一个理论位置）",
                False,
                f"第 {step} 步：实际 {window.base_position}，期望 {expected}",
            )
            break
    else:
        check(
            "快速来回拖不丢偏移（每步都回到同一个理论位置）",
            True,
            "12 步来回都精确命中理论位置",
        )

    release((cx, cy), (start.x() + cx, start.y() + cy))
    check("松手后退出拖动", window.dragging is False)
    check("松手回 idle", window.animation.action == "idle", f"{window.animation.action}")

    # ------------------------------------------------------------ §3 写配置
    section("3. 松手把落脚点写进 config.json（\"x,y\"，夹取后的值）")

    window.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(cx, cy),
            QPointF(window.base_position.x() + cx, window.base_position.y() + cy),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    base_now = window.base_position
    window.mouseMoveEvent(
        QMouseEvent(
            QEvent.MouseMove,
            QPointF(cx + 60, cy + 30),
            QPointF(base_now.x() + cx + 60, base_now.y() + cy + 30),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(cx + 60, cy + 30),
            QPointF(base_now.x() + cx + 60, base_now.y() + cy + 30),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )
    for _ in range(6):
        app.processEvents()

    landed = window.base_position
    payload = json.loads(cfg.read_text(encoding="utf-8"))
    check(
        "config.json 里有 position",
        "position" in payload,
        f"position={payload.get('position')!r}",
    )
    check(
        "写入的正是**权威位置**（夹取后）",
        payload.get("position") == f"{landed.x()},{landed.y()}",
        f"{payload.get('position')!r} vs {landed.x()},{landed.y()}",
    )
    check(
        "格式是 \"x,y\"",
        isinstance(payload.get("position"), str) and payload["position"].count(",") == 1,
        f"{payload.get('position')!r}",
    )

    # 目录干净：没有 .tmp 残留。
    leftovers = sorted(p.name for p in SCRATCH.iterdir())
    check(
        "松手后 scratch 目录里只有 config.json（没有临时文件）",
        leftovers == ["config.json"],
        f"{leftovers}",
    )

    # ------------------------------------------------------------ §4 拖出屏外
    section("4. 拖到屏幕外 → 夹回屏内，且**配置里存的是夹取后的值**")

    screen = QGuiApplication.primaryScreen()
    area = (
        screen.geometry().x(),
        screen.geometry().y(),
        screen.geometry().width(),
        screen.geometry().height(),
    )
    window.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(cx, cy),
            QPointF(window.base_position.x() + cx, window.base_position.y() + cy),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    base_now = window.base_position
    far = (area[0] + area[2] + 5000, area[1] + area[3] + 5000)
    window.mouseMoveEvent(
        QMouseEvent(
            QEvent.MouseMove,
            QPointF(cx, cy),
            QPointF(*far),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    window.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(cx, cy),
            QPointF(*far),
            Qt.LeftButton,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )
    for _ in range(6):
        app.processEvents()

    clamped = window.base_position
    width, height = window.pixel_size
    check(
        "拖出右下角后位置被夹回屏内",
        area[0] <= clamped.x() <= area[0] + area[2] - width
        and area[1] <= clamped.y() <= area[1] + area[3] - height,
        f"({clamped.x()}, {clamped.y()})，屏幕 {area}，窗口 {width}x{height}",
    )
    payload = json.loads(cfg.read_text(encoding="utf-8"))
    check(
        "配置里存的是**夹取后**的值（重启后仍在屏内）",
        payload.get("position") == f"{clamped.x()},{clamped.y()}",
        f"{payload.get('position')!r}",
    )
    check(
        "夹取没把窗口塞到屏幕外（左上角也 ≥ 屏幕原点）",
        clamped.x() >= area[0] and clamped.y() >= area[1],
        f"({clamped.x()}, {clamped.y()})",
    )

    # ------------------------------------------------------------ §5 重启复现
    section("5. \"重启\"后出现在上次的位置（读配置 + 夹取）")

    window.stop_animation()
    window.close()
    for _ in range(6):
        app.processEvents()

    fresh = ConfigStore(cfg)
    fresh.load()
    from deskpet import app as APP

    parsed, source = APP._resolve_position(None, fresh)
    check(
        "位置来源显示『沿用上次』",
        source == "沿用上次",
        f"source={source}",
    )
    check(
        "读出来的位置与写入的一致",
        parsed == (clamped.x(), clamped.y()),
        f"{parsed} vs ({clamped.x()}, {clamped.y()})",
    )

    window2 = PetWindow(spec, 4, config=fresh)
    window2.play_action("idle")
    window2.show_at(parsed[0], parsed[1], user_positioned=True)
    window2.show()
    for _ in range(10):
        app.processEvents()
    check(
        "重启后窗口落在上次的位置",
        window2.base_position == clamped,
        f"{window2.base_position} vs {clamped}",
    )

    # ------------------------------------------------------------ §6 换档
    section("6. 拖动过之后切档：右下角锚点不动、**不弹回缺省落点**")

    from deskpet import geometry

    before = window2.base_position
    wb, hb = window2.pixel_size
    anchor = (before.x() + wb, before.y() + hb)
    window2.set_size_level(6)
    for _ in range(6):
        app.processEvents()
    after = window2.base_position
    wa, ha = window2.pixel_size
    check(
        "切档后右下角锚点不动",
        (after.x() + wa, after.y() + ha) == anchor,
        f"{anchor} → ({after.x() + wa}, {after.y() + ha})",
    )
    default_xy = geometry.default_position(wa, ha, area)
    check(
        "切档后**没有**弹回缺省落点",
        after != QPoint(*default_xy),
        f"{after}（缺省落点 {QPoint(*default_xy)}）",
    )
    check(
        "切档后位置仍被夹在屏内",
        area[0] <= after.x() <= area[0] + area[2] - wa
        and area[1] <= after.y() <= area[1] + area[3] - ha,
        f"{after}",
    )

    # ------------------------------------------------------------ §7 事件循环
    section("7. 反复拖动 1 分钟的量级：事件循环不被阻塞")

    ticks = {"n": 0}
    heartbeat = QTimer()
    heartbeat.setInterval(20)
    heartbeat.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
    heartbeat.start()

    for step in range(120):
        base = window2.base_position
        window2.mousePressEvent(
            QMouseEvent(
                QEvent.MouseButtonPress,
                QPointF(5.0, 5.0),
                QPointF(base.x() + 5.0, base.y() + 5.0),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        for sub in range(10):
            dx = (sub - 5) * 8
            window2.mouseMoveEvent(
                QMouseEvent(
                    QEvent.MouseMove,
                    QPointF(5.0 + dx, 5.0),
                    QPointF(base.x() + 5.0 + dx, base.y() + 5.0),
                    Qt.LeftButton,
                    Qt.LeftButton,
                    Qt.NoModifier,
                )
            )
        window2.mouseReleaseEvent(
            QMouseEvent(
                QEvent.MouseButtonRelease,
                QPointF(5.0, 5.0),
                QPointF(base.x() + 5.0, base.y() + 5.0),
                Qt.LeftButton,
                Qt.NoButton,
                Qt.NoModifier,
            )
        )
        app.processEvents()
    heartbeat.stop()

    check(
        "120 次完整拖动（每次 10 个移动事件）期间事件循环仍在跑",
        ticks["n"] > 0,
        f"心跳 {ticks['n']} 次",
    )
    check(
        "反复拖动后位置仍在屏内",
        area[0] <= window2.base_position.x() <= area[0] + area[2] - window2.pixel_size[0]
        and area[1] <= window2.base_position.y() <= area[1] + area[3] - window2.pixel_size[1],
        f"{window2.base_position}",
    )

    # ------------------------------------------------------------ §8 镜像命中
    section("8. 镜像开启时：判定跟着镜像翻（本次新增的必测项）")

    from deskpet.mirror import MirrorState

    mcfg = SCRATCH / "mirror_config.json"
    mcfg.write_text("{}", encoding="utf-8")
    mstore = ConfigStore(mcfg)
    mstore.load()
    mirror = MirrorState(mstore)
    window3 = PetWindow(spec, 4, mirror=mirror)
    # **定格在第 0 帧**：动画在播的话每帧轮廓都不同，两次扫描本来就不该相等
    # （那不是"判定没跟着镜像翻"，而是"看的不是同一张画"）。
    window3.play_action("idle", auto_start=False)
    window3.hold_frame(0)
    window3.show_at(400, 300, user_positioned=True)
    window3.show()
    for _ in range(12):
        app.processEvents()

    hwnd3 = int(window3.winId())
    origin3 = window3.base_position
    dpr3 = window3.devicePixelRatioF()

    def screen_of3(lx: int, ly: int) -> tuple[int, int]:
        return (
            round((origin3.x() + lx) * dpr3),
            round((origin3.y() + ly) * dpr3),
        )

    def hit_columns() -> tuple[tuple[int, int], int]:
        """扫一遍当前遮罩的外接矩形，返回 `((最左命中 x, 最右命中 x), 命中点数)`。

        **帧必须定格**：动画在播的话每一帧的人物轮廓都不同，两次扫描本来就
        不该相等（那不是"判定没跟着镜像翻"，是"看的不是同一张画"）。
        """
        rect = window3.mask().boundingRect()
        xs: list[int] = []
        for ly in range(rect.y(), rect.y() + rect.height(), 2):
            for lx in range(rect.x(), rect.x() + rect.width(), 2):
                if _window_from_point(*screen_of3(lx, ly)) == hwnd3:
                    xs.append(lx)
        return (min(xs), max(xs)), len(xs)

    window3.hold_frame(0)
    for _ in range(8):
        app.processEvents()
    bbox_before = window3.mask().boundingRect()
    if bbox_before.isEmpty():
        check("镜像前有非空遮罩（镜像命中用例的前提）", False, f"{bbox_before}")
    else:
        (left_before, right_before), count_before = hit_columns()
        check(
            "镜像**关闭**时：人物身上取样能命中本窗口（对照基线）",
            count_before > 0,
            f"命中 {count_before} 个采样点，x∈[{left_before}, {right_before}]",
        )

        # 开启镜像：定格同一帧，遮罩必须跟着翻（外接矩形宽度不变、位置左右对调）。
        window3.toggle_mirror()
        for _ in range(10):
            app.processEvents()
        check("镜像已开启", window3.mirrored is True, f"mirrored={window3.mirrored}")

        bbox_after = window3.mask().boundingRect()
        check(
            "镜像后遮罩**宽度**不变（水平翻转不改轮廓宽度）",
            bbox_after.width() == bbox_before.width(),
            f"{bbox_before.width()} → {bbox_after.width()}",
        )
        check(
            "镜像后遮罩的**左右位置对调了**（判定真的跟着翻）",
            bbox_after.x() != bbox_before.x(),
            f"外接矩形 x：{bbox_before.x()} → {bbox_after.x()}",
        )

        (left_after, right_after), count_after = hit_columns()
        check(
            "镜像后：人物身上仍然能命中（不是整块穿透）",
            count_after > 0,
            f"命中 {count_after} 个采样点，x∈[{left_after}, {right_after}]",
        )
        check(
            "镜像后『能点到的范围』确实变了（不是同一片区域）",
            (left_after, right_after) != (left_before, right_before),
            f"x 范围 [{left_before}, {right_before}] → [{left_after}, {right_after}]",
        )

        # 镜像开启时在人物身上按下 → 仍然起拖（判定没坏）。
        press_at = None
        rect = window3.mask().boundingRect()
        for ly in range(rect.y(), rect.y() + rect.height(), 3):
            for lx in range(rect.x(), rect.x() + rect.width(), 3):
                if _window_from_point(*screen_of3(lx, ly)) == hwnd3:
                    press_at = (lx, ly)
                    break
            if press_at:
                break
        if press_at is None:  # pragma: no cover - 上面已经断言过 count > 0
            check("镜像开启时能找到可按下的点", False, "扫描没找到命中点")
        else:
            gx, gy = origin3.x() + press_at[0], origin3.y() + press_at[1]
            window3.mousePressEvent(
                QMouseEvent(
                    QEvent.MouseButtonPress,
                    QPointF(*press_at),
                    QPointF(gx, gy),
                    Qt.LeftButton,
                    Qt.LeftButton,
                    Qt.NoModifier,
                )
            )
            check(
                "镜像开启时在**人物身上**按下能起拖",
                window3.dragging is True,
                f"按下点 {press_at}，dragging={window3.dragging}",
            )
            window3.mouseReleaseEvent(
                QMouseEvent(
                    QEvent.MouseButtonRelease,
                    QPointF(*press_at),
                    QPointF(gx, gy),
                    Qt.LeftButton,
                    Qt.NoButton,
                    Qt.NoModifier,
                )
            )

    window3.stop_animation()
    window3.close()
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
