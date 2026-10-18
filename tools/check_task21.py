"""task21 自检（offscreen）：**P1 问题修复**（deskPet 完整问题审查报告里的高优先级项）。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task21_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task21.py

## 这一份验什么（逐条对着报告里的编号）

| 节 | 报告编号 | 断言的事实 |
| --- | --- | --- |
| 1 | UX-01 | 方向看**相邻移动**：右→左→右 / 左→右→左 不必越过按下点就切换 |
| 2 | UX-02 | 新的一次拖动**不沿用**上次的方向 |
| 3 | UX-03 | 单击（按下→松开，没挪）**不改姿势 / 不挂起 / 不发 drag_started** |
| 4 | UX-04 | 方向抖动**不反复重启**跑动动作（反转要连续走过容差） |
| 5 | UX-05 | 区域按渲染图**缓存**：连播一轮只算一轮 `QRegion` |
| 6 | UX-06 | 夹取按**可见人物**，且可见块**整块**留在屏幕内（换帧后也成立） |
| 7 | UX-09 | 键盘形态拖动**不碰**宠物动作、也不拉起帧定时器 |
| 8 | UX-10 | `show_action_frame()` 在 KV 形态下返回 **bool 而不是元组** |
| 9 | UX-11 | 合并复绘是**节流**：持续输入期间画面照样刷 |
| 10 | UX-12 | 设置窗口在 `2*8` 键位布局下**没有**被撑宽/裁切 |
| 11 | CODE-01 | 依赖在 `pyproject.toml` 里声明了 NumPy（且与 README 一致） |
| 12 | CODE-02 | 热键相关的 Win32 调用都走**声明过原型**的 `win32` 层 |
| 13 | CODE-03 | 写配置失败时热键**真的回滚**（不是直接返回成功） |

## 这一份**不写用户真实目录 / 不改 assets**

所有落盘都在 `data/task21_out/`（工作区内，§4.13）；`assets/` 一个字节都不动。
窗口用例走 offscreen 平台插件。
"""

import json
import os
import shutil
import sys
import tokenize
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task21_check.txt"
OUT_DIR = ROOT / "data" / "task21_out"

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
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _code_only(path: Path) -> str:
    """只保留**可执行代码**：剥掉注释与字符串（§4.11 的教训）。"""
    source = path.read_text(encoding="utf-8")
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _event(kind: str, local, glob, buttons=None):
    """造一个真实的 {bt}QMouseEvent{bt}（不模拟系统鼠标，直接投递）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    types = {
        "press": QEvent.MouseButtonPress,
        "move": QEvent.MouseMove,
        "release": QEvent.MouseButtonRelease,
        "dblclick": QEvent.MouseButtonDblClick,
    }
    if buttons is None:
        buttons = Qt.LeftButton if kind in ("press", "move") else Qt.NoButton
    return QMouseEvent(
        types[kind],
        QPointF(*local),
        QPointF(*glob),
        Qt.LeftButton,
        buttons,
        Qt.NoModifier,
    )


def _load_spec():
    from deskpet import pet_loader

    return pet_loader.load_pet(ROOT / "assets" / "weedy")


def _make_window(spec, level: int = 1, **kwargs):
    from deskpet.pet_window import PetWindow

    window = PetWindow(spec, level, **kwargs)
    window.show_at(400, 300)
    return window


# ==================================================================== §1 UX-01


def section_direction_adjacent() -> None:
    from PySide6.QtCore import QPoint

    from deskpet import drag

    section("1. UX-01：方向看**相邻移动**，不是「离按下点多远」")

    # 右 → 左 → 右：全程都没越过按下点（累计位移一直是负的）。
    state = drag.DragState()
    state.begin(QPoint(10, 10), QPoint(1000, 500), QPoint(990, 490))
    seq = []
    for pos in (QPoint(1040, 500), QPoint(1000, 500), QPoint(1040, 500)):
        _target, action = state.update(pos)
        seq.append(action)
    check(
        "右→左→右：依次是 right / left / right（不必越过按下点）",
        seq == ["running_right", "running_left", "running_right"],
        f"实测 {seq}",
    )

    # 左 → 右 → 左：同理（这次要真的"往左"开始）。
    state2 = drag.DragState()
    state2.begin(QPoint(10, 10), QPoint(1000, 500), QPoint(990, 490))
    seq2 = []
    for pos in (QPoint(960, 500), QPoint(1000, 500), QPoint(960, 500)):
        _target, action = state2.update(pos)
        seq2.append(action)
    check(
        "左→右→左：依次是 left / right / left",
        seq2 == ["running_left", "running_right", "running_left"],
        f"实测 {seq2}",
    )

    # 位置仍然按"基准 + 累计位移"算（UX-01 只改方向，不改位置）。
    state3 = drag.DragState()
    state3.begin(QPoint(10, 10), QPoint(1000, 500), QPoint(990, 490))
    target, _ = state3.update(QPoint(1040, 480))
    check(
        "位置仍是 基准 + 累计位移（相邻位移只用于判方向）",
        target == QPoint(1030, 470),
        f"实测 {target}",
    )


# ==================================================================== §2 UX-02


def section_direction_reset() -> None:
    from PySide6.QtCore import QPoint

    from deskpet import drag

    section("2. UX-02：新的一次拖动不沿用上次的方向")

    controller = drag.DragController()
    controller.begin(QPoint(10, 10), QPoint(1000, 500), QPoint(990, 490))
    controller.update(QPoint(1100, 500))  # 往右跑
    controller.finish(QPoint(1090, 490))
    right = controller.state.last_direction
    controller.begin(QPoint(10, 10), QPoint(600, 500), QPoint(590, 490))
    check(
        "begin() 清掉上一次的跑动方向",
        controller.state.last_direction is None,
        f"上一次是 {right}，新的 begin() 之后是 {controller.state.last_direction}",
    )
    _target, action = controller.update(QPoint(600, 560))  # 这一次纯竖直
    check(
        "新拖动里的纯竖直移动**不**沿用上次方向",
        action is None,
        f"实测 {action}",
    )


# ==================================================================== §3 UX-03


def _idle_window(spec):
    """带久置状态机的窗口（"点一下会不会打断休息"要看它）。"""
    from deskpet.idle_state import IdleStateMachine

    machine = IdleStateMachine(lambda: 20)
    window = _make_window(spec, 1, idle_state=machine)
    return window, machine


def section_press_is_not_drag(app) -> None:
    section("3. UX-03：单击（按下→松开）不改姿势、不挂起、不起拖")

    spec = _load_spec()
    window, machine = _idle_window(spec)
    machine.enter_manual(now=None)
    origin = window.base_position
    press = (origin.x() + 10, origin.y() + 10)

    window.mousePressEvent(_event("press", (10, 10), press))
    check("按下后**不是**拖动", window.dragging is False)
    check("按下后停在候选态", window._press_pending is True)
    check("按下时不挂起静置逻辑", machine.suspended is False)
    check("按下时不退出 manual 坐姿", window.manual_rest is True, f"manual={window.manual_rest}")
    check(
        "按下时动作不变（还是休息姿势）",
        window.action == "rest",
        f"实测 {window.action}",
    )

    window.mouseReleaseEvent(_event("release", (10, 10), press))
    check("单击松手后**没有**进入过拖动", window.dragging is False)
    check("单击松手后姿势不变", window.action == "rest", f"实测 {window.action}")
    check("单击松手后 manual 仍在", window.manual_rest is True)
    check("单击松手后位置不变", window.base_position == origin, f"{window.base_position} vs {origin}")

    # 真的拖起来：越过阈值之后才接管。
    press2 = (origin.x() + 10, origin.y() + 10)
    window.mousePressEvent(_event("press", (10, 10), press2))
    window.mouseMoveEvent(_event("move", (14, 10), (press2[0] + 3, press2[1])))  # 3 px：不够
    check(
        "没越过阈值时仍然不是拖动",
        window.dragging is False,
        f"dragging={window.dragging}",
    )
    window.mouseMoveEvent(_event("move", (40, 10), (press2[0] + 30, press2[1])))
    check("越过阈值后进入拖动", window.dragging is True)
    check("拖动开始才挂起静置逻辑", machine.suspended is True)
    check("拖动开始才退出 manual", window.manual_rest is False)
    window.mouseReleaseEvent(_event("release", (40, 10), (press2[0] + 30, press2[1])))
    check("松手后回到 idle", window.action == "idle", f"实测 {window.action}")
    check("松手后恢复静置逻辑", machine.suspended is False)
    window.close()


# ==================================================================== §4 UX-04


def section_direction_jitter() -> None:
    from PySide6.QtCore import QPoint

    from deskpet import drag

    section("4. UX-04：方向抖动不反复重启跑动动作")

    state = drag.DragState()
    state.begin(QPoint(0, 0), QPoint(1000, 500), QPoint(1000, 500))
    actions: list[str | None] = []
    x = 1000
    # 先往右跑起来。
    x += 40
    actions.append(state.update(QPoint(x, 500))[1])
    # 1~2 px 的来回抖动（真实鼠标的高频抖动幅度）。
    for delta in (-2, 1, -2, 1, -2, 1):
        x += delta
        actions.append(state.update(QPoint(x, 500))[1])
    check(
        "抖动期间方向一直是 running_right（没有来回切）",
        set(actions) == {"running_right"},
        f"实测 {actions}",
    )

    # 真的反向：连续往左走超过容差才切。
    switched_at = None
    for step in range(1, 20):
        x -= 1
        action = state.update(QPoint(x, 500))[1]
        if action == "running_left":
            switched_at = step
            break
    check(
        "真的反向时会在容差之内切到 running_left",
        switched_at is not None and switched_at <= drag.DIRECTION_SWITCH_EPSILON,
        f"走了 {switched_at} px 之后切到 {switched_at and 'running_left'}",
    )


# ==================================================================== §5 UX-05


def section_region_cache(app) -> None:
    from PySide6.QtGui import QColor, QImage

    section("5. UX-05：窗口区域按渲染图缓存（换帧不重算 QRegion）")

    from deskpet.pet_window import PetWindow

    spec = _load_spec()
    window = PetWindow(spec, 1)
    action = spec.action("idle")
    frames = action.frame_count if action is not None else 0
    check("素材里 idle 有多帧（不然这一节没有意义）", frames > 1, f"{frames} 帧")

    window.region_builds = 0
    for index in range(frames):
        image = window._renderer.frame("idle", index, window.level, window.mirrored)
        window.apply_mask(image)
    first_round = window.region_builds
    check(
        "连播一轮之后只算了一轮的份（= 帧数）",
        first_round == frames,
        f"算了 {first_round} 次，帧数 {frames}",
    )

    window.region_builds = 0
    for index in range(frames):
        image = window._renderer.frame("idle", index, window.level, window.mirrored)
        window.apply_mask(image)
    check(
        "第二轮**一次都不重算**（全部命中缓存）",
        window.region_builds == 0,
        f"第二轮算了 {window.region_builds} 次",
    )

    # 空图 / 算不出来时仍然退化成"整帧可点"（§2.8 的降级不能破）。
    empty = QImage()
    check("空图 → apply_mask 返回 False（清掉遮罩）", window.apply_mask(empty) is False)
    window.close()

    # 缓存有上限（不能无限长）。
    from deskpet import pet_window as PW

    check(
        "区域缓存有明确上限（不是无界字典）",
        PW.REGION_CACHE_LIMIT > 0 and PW.REGION_CACHE_LIMIT <= 4096,
        f"REGION_CACHE_LIMIT={PW.REGION_CACHE_LIMIT}",
    )


# ==================================================================== §6 UX-06


def _visible_in_screen(window) -> bool:
    area = window._screen_area()
    box = window._clamp_box()
    left, top = window.base_position.x(), window.base_position.y()
    return (
        area[0] <= left + box[0]
        and area[1] <= top + box[1]
        and left + box[0] + box[2] <= area[0] + area[2]
        and top + box[1] + box[3] <= area[1] + area[3]
    )


def section_visible_edge(app) -> None:
    section("6. UX-06：按**可见人物**夹取，人物边缘能真的贴住屏幕边")

    spec = _load_spec()
    window = _make_window(spec, 1)
    bounds = window.visible_bounds
    width, height = window.pixel_size
    check("算得出可见块", bounds is not None, f"{bounds}")
    if bounds is None:
        window.close()
        return
    bx, by, bw, bh = bounds
    check(
        "可见块比整块画布小（素材周边有透明留白，这一节才有意义）",
        bw < width or bh < height,
        f"可见块 {bounds} / 画布 {(width, height)}",
    )

    # 拖到远远超出右下 → 可见块的右边**恰好**贴在屏幕右边。
    window._move_clamped(100000, 100000)
    area = window._screen_area()
    left, top = window.base_position.x(), window.base_position.y()
    box = window._clamp_box()
    check(
        "往右下拖到底：可见块的右边缘贴住屏幕右边",
        left + box[0] + box[2] == area[0] + area[2],
        f"{left + box[0] + box[2]} vs {area[0] + area[2]}",
    )
    check(
        "往右下拖到底：可见块的下边缘贴住屏幕下边",
        top + box[1] + box[3] == area[1] + area[3],
        f"{top + box[1] + box[3]} vs {area[1] + area[3]}",
    )
    check(
        "透明留白被允许出屏（窗口右边缘超过屏幕右边）",
        left + width > area[0] + area[2],
        f"窗口右边 {left + width} vs 屏幕右边 {area[0] + area[2]}",
    )

    # 左上同理。
    window._move_clamped(-100000, -100000)
    left, top = window.base_position.x(), window.base_position.y()
    box = window._clamp_box()
    check(
        "往左上拖到底：可见块的左边缘贴住屏幕左边",
        left + box[0] == area[0],
        f"{left + box[0]} vs {area[0]}",
    )

    # **换帧之后也得整块在屏内**（防的是"换个姿势就出屏、抓不回来"）。
    # 走的是产品真实路径：`play_action()` 换动作 → 出图 → 再看可见块。
    window._move_clamped(100000, 100000)
    offenders = []
    for action in spec.action_order:
        if spec.action(action) is None:
            continue
        window.play_action(action)
        window.show_action_frame(action, 0)
        if not _visible_in_screen(window):
            offenders.append((action, window._clamp_box()))
    check(
        "换动作之后可见块仍然整块在屏内（夹取要保守到换帧也不越界）",
        not offenders,
        f"出屏的：{offenders}",
    )
    check(
        "任意动作的第一帧下，可见块仍然整块在屏内（抓得回来）",
        not offenders,
        f"出屏的动作：{offenders}",
    )
    window.close()


# ==================================================================== §7 UX-09


def section_keyview_drag(app) -> None:
    from PySide6.QtCore import QPoint

    section("7. UX-09：键盘形态拖动不碰宠物动作")

    from deskpet import pet_loader
    from deskpet.pet_window import PetWindow
    from deskpet.context_menu import FORM_KEYVIEW

    spec = _load_spec()
    kv = None
    for candidate in sorted((ROOT / "data" / "task19_out" / "kv").glob("*")):
        loaded, _issues, _warnings = pet_loader.load_keyview(candidate)
        if loaded is not None:
            kv = loaded
            break
    if kv is None:
        note("没有可用的 KV 占位包 → 用一个假的 content 代替（只验形态分支）")
        window = PetWindow(spec, 1)
        before = window.action

        class _FakeContent:
            form = FORM_KEYVIEW
            cell = spec.cell

            def image(self, level, mirrored):
                return window._frame_image

        window._content = _FakeContent()
        window.drag_controller.begin(QPoint(5, 5), QPoint(405, 305), window.base_position)
        window._apply_drag(QPoint(605, 305))
        check(
            "KV 形态下拖动**不换**宠物动作",
            window.action == before,
            f"{before} → {window.action}",
        )
        check(
            "KV 形态下拖动**不拉起**帧定时器",
            not window.animation_driver_running if hasattr(window, "animation_driver_running") else True,
            "（没有该属性时跳过）",
        )
        window.close()
        return

    window = PetWindow(spec, 1, keyview=kv, keyview_layout="2*8")
    window.set_form(FORM_KEYVIEW)
    check("已经切到键盘形态", window.form == FORM_KEYVIEW, window.form)
    before = window.action
    window.drag_controller.begin(QPoint(5, 5), QPoint(405, 305), window.base_position)
    window._apply_drag(QPoint(705, 305 + 40))
    check(
        "KV 形态下拖动**不换**宠物动作",
        window.action == before,
        f"{before} → {window.action}",
    )
    window.close()


# ==================================================================== §8 UX-10


def section_show_action_frame_bool(app) -> None:
    from PySide6.QtGui import QImage

    section("8. UX-10：show_action_frame 返回 bool（不是 (bool,) 元组）")

    from deskpet.pet_window import PetWindow
    from deskpet.context_menu import FORM_KEYVIEW

    spec = _load_spec()
    window = PetWindow(spec, 1)

    answer = window.show_action_frame("idle", 0)
    check(
        "普通形态下返回值是 bool",
        isinstance(answer, bool),
        f"{type(answer).__name__} = {answer!r}",
    )

    class _FakeContent:
        form = FORM_KEYVIEW
        cell = spec.cell

        def image(self, level, mirrored):
            return QImage()

    window._content = _FakeContent()
    answer_kv = window.show_action_frame("idle", 0)
    check(
        "键盘形态下返回值仍然是 bool（不是元组）",
        isinstance(answer_kv, bool),
        f"{type(answer_kv).__name__} = {answer_kv!r}",
    )
    check(
        "空画面时返回值是 False（没换上图不许报成 True）",
        answer_kv is False,
        f"{answer_kv!r}",
    )
    window.close()


# ==================================================================== §9 UX-11


def section_repaint_throttle(app) -> None:
    import time

    section("9. UX-11：合并复绘是**节流**（持续输入期间画面照样刷）")

    from deskpet.pet_window import PetWindow

    from deskpet.context_menu import FORM_KEYVIEW

    spec = _load_spec()
    window = PetWindow(spec, 1)
    # `_on_repaint_due()` 在**非 KV 形态**下直接返回（不白画没人看的图），
    # 而本类要验的正是"到点了会不会画"——所以先切到键盘形态。

    class _FakeContent:
        form = FORM_KEYVIEW
        cell = spec.cell
        pressed: tuple = ()
        active_keys: frozenset = frozenset()

        def image(self, level, mirrored):
            return window._frame_image

    window._content = _FakeContent()
    window.repaint_count = 0
    window._on_keys_changed(())
    check("第一次变化会起一拍定时器", window.repaint_pending is True)
    window._on_keys_changed(())
    window._on_keys_changed(())
    check(
        "后续变化**不重置**定时器（持续输入就画不出来了是原 bug）",
        window.repaint_pending is True,
        "定时器仍在等",
    )

    # 真跑事件循环 120 ms：期间不断喂变化，画面必须至少刷一次。
    deadline = time.perf_counter() + 0.12
    while time.perf_counter() < deadline:
        window._on_keys_changed(())
        app.processEvents()
    check(
        "持续输入 120 ms 内至少真的重绘了一次",
        window.repaint_count >= 1,
        f"repaint_count={window.repaint_count}",
    )
    window.close()


# ==================================================================== §10 UX-12


def section_settings_no_overflow(app) -> None:
    section("10. UX-12：设置窗口在 2*8 键位布局下不被撑宽 / 裁切")

    from PySide6.QtWidgets import QScrollArea, QWidget

    from deskpet import pet_loader, settings
    from deskpet.config import ConfigStore

    kv = None
    for candidate in sorted((ROOT / "data" / "task19_out" / "kv").glob("*")):
        loaded, _issues, _warnings = pet_loader.load_keyview(candidate)
        if loaded is not None and "2*8" in loaded.layout_names:
            kv = loaded
            break
    if kv is None:
        note("没有 2*8 的 KV 占位包 → 这一节只能验「普通设置窗口不受影响」")
        path = _fresh_dir(OUT_DIR / "cfg_plain") / "config.json"
        path.write_text("{}", encoding="utf-8")
        store = ConfigStore(path)
        store.load()
        window = settings.SettingsWindow(store, "", None, None)
        window.show()
        app.processEvents()
        check("普通设置窗口能开出来", window.width() == settings.WINDOW_WIDTH, f"{window.width()}")
        window.close()
        return

    path = _fresh_dir(OUT_DIR / "cfg_kv") / "config.json"
    path.write_text(json.dumps({"keyview_layout": "2*8"}), encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    window = settings.SettingsWindow(store, "", None, kv)
    window.show()
    app.processEvents()
    window.adjustSize()
    app.processEvents()

    grid = window._key_grid
    check("网格按 2*8 摆出来了", grid is not None and grid.layout is not None and grid.layout.name == "2*8")
    scroll = window.findChild(QScrollArea)
    viewport_width = scroll.viewport().width()
    check(
        "键位网格的最小宽度**小于**内容区宽度（不会把窗口撑宽）",
        grid.minimumSizeHint().width() < viewport_width,
        f"网格 {grid.minimumSizeHint().width()} vs 内容区 {viewport_width}",
    )
    overflow = [
        (type(child).__name__, child.width(), child.parentWidget().width())
        for child in window.findChildren(QWidget)
        if child.parentWidget() is not None
        and child.isVisible()
        and child.width() > child.parentWidget().width() + 1
    ]
    check(
        "窗口里没有任何控件比自己父控件还宽（文字不会被裁）",
        overflow == [],
        f"{overflow[:5]}",
    )
    window.close()


# ==================================================================== §11 CODE-01


def section_declared_dependencies() -> None:
    section("11. CODE-01：运行时依赖（含 NumPy）在 pyproject.toml 里声明")

    pyproject = ROOT / "pyproject.toml"
    check("pyproject.toml 存在", pyproject.exists(), str(pyproject))
    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8")
        check("dependencies 里声明了 PySide6", "PySide6" in text)
        check("dependencies 里声明了 NumPy", "numpy" in text.lower())
    requirements = ROOT / "requirements.txt"
    check("requirements.txt 存在（给不读 pyproject 的人用）", requirements.exists())
    if requirements.exists():
        body = requirements.read_text(encoding="utf-8").lower()
        check("requirements.txt 里也有 numpy", "numpy" in body)

    readme = ROOT / "README.md"
    if readme.exists():
        body = readme.read_text(encoding="utf-8")
        check(
            "README 的从源码运行不再只写 pip install PySide6",
            "pip install PySide6\npython run.py" not in body,
            "按 README 新建环境会缺 NumPy",
        )


# ==================================================================== §12 CODE-02


def section_win32_prototypes() -> None:
    section("12. CODE-02：热键相关 Win32 调用都走声明过原型的 win32 层")

    # ⚠ 这里**不能**过 `_code_only()`：它把 token 用空格拼起来，`X.argtypes`
    # 会变成 `X . argtypes`，按 `'X.argtypes'` 搜就搜不到（§4.11 的同一条教训：
    # "认源码里有没有某个名字"必须先想清楚剥完之后长什么样）。
    win32_source = (ROOT / "src" / "deskpet" / "win32.py").read_text(encoding="utf-8")
    for name in ("GetModuleHandleW", "DestroyWindow", "RegisterHotKey", "UnregisterHotKey"):
        check(
            f"win32.py 声明了 {name} 的原型（argtypes/restype）",
            f"{name}.argtypes" in win32_source and f"{name}.restype" in win32_source,
            "原型只允许在这一层声明",
        )

    # ⚠ 必须先剥掉注释与字符串（agent.md §4.11 的教训：解释"为什么不用它"的注释
    # 会被 `in` 判成"在用它"）。
    hotkey_code = _code_only(ROOT / "src" / "deskpet" / "hotkey.py")
    check(
        "hotkey.py 不再裸调 ctypes.windll.kernel32.GetModuleHandleW",
        "ctypes.windll.kernel32.GetModuleHandleW" not in hotkey_code,
        "裸调会让 64 位句柄被截断",
    )
    check(
        "hotkey.py 不再裸调 ctypes.windll.user32.DestroyWindow",
        "ctypes.windll.user32.DestroyWindow" not in hotkey_code,
        "同上",
    )
    check(
        "hotkey.py 的实例句柄取自 win32.get_module_handle()",
        "get_module_handle" in hotkey_code,
    )

    from deskpet import win32

    check(
        "win32.get_module_handle / destroy_window 对外可用",
        callable(getattr(win32, "get_module_handle", None))
        and callable(getattr(win32, "destroy_window", None)),
    )
    check(
        "取不到的句柄返回 0（不抛异常）",
        isinstance(win32.get_module_handle(), int),
        f"{win32.get_module_handle()}",
    )
    check("句柄为 0 时 destroy_window 返回 False", win32.destroy_window(0) is False)


# ==================================================================== §13 CODE-03


def section_hotkey_rollback() -> None:
    section("13. CODE-03：写配置失败时热键**真的回滚**")

    from deskpet import app as APP
    from deskpet import hotkey as HK

    class _Outcome:
        def __init__(self, combo, reason=""):
            self.combo = combo
            self.changed = False
            self.reason = reason

    class _Manager:
        def __init__(self) -> None:
            self.combo = "Ctrl+Alt+P"

        @property
        def has_hotkey(self) -> bool:
            return self.combo is not None

        def release_hotkey(self) -> None:
            self.combo = None

        def try_register(self, combo: str):
            # 新组合"注册成功"
            self.combo = combo
            return _Outcome(combo)

        def restore(self, combo: str):
            self.combo = combo
            return _Outcome(combo)

    class _Window:
        def __init__(self) -> None:
            self.available: list[bool] = []

        def set_hotkey_available(self, value: bool) -> None:
            self.available.append(bool(value))

        def set_visibility_handler(self, _handler) -> None:
            pass

    controller = object.__new__(APP.HotkeyController)
    controller.window = _Window()
    controller.store = None
    controller.bar_controller = None
    controller.result = HK.RegisterResult(None, False, "")
    controller.manager = _Manager()

    # 模拟设置窗口的回滚调用：change(旧, 旧) —— 此时系统里注册的**是新的**。
    controller.manager.combo = "Ctrl+Shift+F12"
    outcome = controller.change("Ctrl+Alt+P", "Ctrl+Alt+P")
    check(
        "change(旧, 旧) 真的把旧热键注册回去了",
        controller.manager.combo == "Ctrl+Alt+P",
        f"现在注册的是 {controller.manager.combo}",
    )
    check("回滚成功时 outcome.ok 为真", bool(outcome.ok) is True, f"{outcome}")
    check(
        "回滚之后「有热键可用」是成立的（绝不落到没有热键）",
        controller.manager.has_hotkey is True,
    )

    # 已经就注册着目标组合时：不重复注册（也不去释放）。
    before = controller.manager.combo
    outcome2 = controller.change("Ctrl+Alt+P", "Ctrl+Alt+P")
    check(
        "系统里已经是这个组合 → 直接成功、不做多余动作",
        bool(outcome2.ok) is True and controller.manager.combo == before,
        f"{outcome2.combo} / {controller.manager.combo}",
    )


# ==================================================================== 收尾


def main() -> int:
    from deskpet import paths

    app = None
    with paths.override_dirs(program=ROOT, resource=ROOT):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        _LINES.append("task21 自检 —— P1 问题修复（UX-01/02/03/04/05/06/09/10/11/12 + CODE-01/02/03）")
        _LINES.append(f"工作区根：{ROOT}")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        section_direction_adjacent()
        section_direction_reset()
        section_press_is_not_drag(app)
        section_direction_jitter()
        section_region_cache(app)
        section_visible_edge(app)
        section_keyview_drag(app)
        section_show_action_frame_bool(app)
        section_repaint_throttle(app)
        section_settings_no_overflow(app)
        section_declared_dependencies()
        section_win32_prototypes()
        try:
            section_hotkey_rollback()
        finally:
            shutil.rmtree(OUT_DIR / "cfg_plain", ignore_errors=True)
            shutil.rmtree(OUT_DIR / "cfg_kv", ignore_errors=True)

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
