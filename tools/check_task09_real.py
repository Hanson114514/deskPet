"""task09 真平台验收：**右边缘真的对齐**、**缩放中按钮的屏幕坐标真的不变**。

自检主体（`check_task09.py`）跑在 offscreen 上，而 offscreen 的虚拟屏幕只有
**800×800**（agent.md §4.14）：第 8 级以上就是 500×500 素材的 1000×1000，
**根本放不下**。于是"宠物放得下时底边固定""栏在屏幕内的实际观感"这几条
只能在真平台验。

跑法（**真平台，会短暂弹出真窗口**）：

    python tools/check_task09_real.py

结果写 `data/task09_real.txt`（UTF-8）。

## 本脚本最看重的一条

**"从第 1 级连点到第 10 级，`+` 按钮的屏幕坐标完全不变"**——这是 task09 §6
里明写的"本 task 最关键的一条"，而它的判据必须是**屏幕坐标**
（`mapToGlobal`），不是窗口内坐标：窗口内坐标不变是布局的事，
屏幕坐标不变才是"用户不用追鼠标"。

## 这一份**不动用户真实配置**

自检的 `config.json` 落在 `data/task09_real_scratch/` 里，跑完整个 scratch
目录被删掉。**用户真实的 `data/config.json` 一个字节都不动。**
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task09_real.txt"
SCRATCH = ROOT / "data" / "task09_real_scratch"

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


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run() -> int:
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import control_bar as cb
    from deskpet import pet_loader
    from deskpet.app import ControlBarController
    from deskpet.config import ConfigStore
    from deskpet.control_bar import screen_rect_for
    from deskpet.pet_window import PetWindow

    spec = pet_loader.load_pet(ROOT / "assets" / "weedy")

    SCRATCH.mkdir(parents=True, exist_ok=True)
    cfg_path = SCRATCH / "config.json"
    cfg_path.write_text("{}", encoding="utf-8")
    store = ConfigStore(cfg_path)
    store.load()

    window = PetWindow(spec, 6, config=store)
    window.play_action("idle")
    window.showing_at_default_position()
    window.set_reserved(True)
    window.show()
    for _ in range(15):
        app.processEvents()

    controller = ControlBarController(window)
    window.set_resize_handler(controller.toggle)
    window.position_changed.connect(lambda _p: controller.sync_follow())

    screen = screen_rect_for(window)
    section("0. 环境")
    check(
        "屏幕装得下第 10 级（否则要在真机上换小一点的素材/屏幕）",
        screen.height() >= 1000 + cb.RESERVED,
        f"屏幕逻辑高度 {screen.height()}，需要 ≥ {1000 + cb.RESERVED}",
    )
    check(
        "缺省落点算出来在屏幕内",
        screen.contains(QRect(window.base_position.x(), window.base_position.y(), *window.pixel_size)),
        f"base={window.base_position.x()},{window.base_position.y()} size={window.pixel_size}",
    )

    # ------------------------------------------------------------ §1 打开与对齐
    section("1. 打开控制栏：右边缘与宠物右边缘对齐，栏在宠物下方")

    controller.open()
    for _ in range(10):
        app.processEvents()
    check("控制栏可见", controller.visible, f"visible={controller.visible}")

    bar = controller.bar
    bar_left_global = bar.mapToGlobal(bar.rect().topLeft())
    pet_w, pet_h = window.pixel_size
    pet_pos = window.base_position
    pet_right = pet_pos.x() + pet_w
    bar_right = bar_left_global.x() + bar.width()

    check(
        "右边缘与宠物右边缘对齐（不是水平居中）",
        bar_right == pet_right,
        f"栏右边 {bar_right} vs 宠物右边 {pet_right}",
    )
    centered_would_be = pet_pos.x() + (pet_w - bar.width()) // 2
    check(
        "确实不是水平居中",
        bar_left_global.x() != centered_would_be,
        f"栏左边 {bar_left_global.x()}，居中会是 {centered_would_be}",
    )
    check(
        "栏在宠物下方，间距 == GAP",
        bar_left_global.y() == pet_pos.y() + pet_h + cb.GAP,
        f"栏顶 {bar_left_global.y()}，宠物底 {pet_pos.y() + pet_h}",
    )
    check("栏高 == BAR_HEIGHT", bar.height() == cb.BAR_HEIGHT, f"{bar.height()}")
    check(
        "栏完整落在屏幕内",
        screen.contains(QRect(bar_left_global.x(), bar_left_global.y(), bar.width(), bar.height())),
        f"({bar_left_global.x()}, {bar_left_global.y()}) {bar.width()}x{bar.height()}，屏幕 {screen}",
    )
    check(
        "文字是 1.00x（6/10）",
        bar.level_label.text() == "1.00x（6/10）",
        bar.level_label.text(),
    )

    # ------------------------------------------------------------ §2 关键：按钮不动
    section("2. **最关键的一条**：连点 + 到 10 级，按钮屏幕坐标完全不变")

    window.set_size_level(1)
    controller.sync()
    for _ in range(8):
        app.processEvents()
    # 先把档位定在第 1 级，**再**摆一次栏：栏的位置是"打开那一刻按宠物摆的"，
    # 而这次 `set_size_level()` 让宠物变小了（底边固定，但左上角变了）。
    # 产品里不会出现这个序列（用户是先开栏再点按钮），所以这里补摆一次。
    controller.reposition()
    for _ in range(8):
        app.processEvents()

    up_global = bar.button_up.mapToGlobal(bar.button_up.rect().center())
    down_global = bar.button_down.mapToGlobal(bar.button_down.rect().center())
    check("起点是第 1 级", window.level == 1, f"level={window.level}")
    check(
        "起点时栏在屏幕内、在宠物下方",
        bar.mapToGlobal(bar.rect().topLeft()).y() == window.base_position.y() + window.pixel_size[1] + cb.GAP,
        f"栏顶 {bar.mapToGlobal(bar.rect().topLeft()).y()}，宠物底 {window.base_position.y() + window.pixel_size[1]}",
    )

    samples = []
    for _ in range(9):  # 1 → 10
        bar.level_up.emit()
        for _ in range(4):
            app.processEvents()
        samples.append(
            (
                window.level,
                bar.button_up.mapToGlobal(bar.button_up.rect().center()),
                bar.mapToGlobal(bar.rect().topLeft()),
            )
        )

    check("连点 9 次后到第 10 级", window.level == 10, f"level={window.level}")
    check(
        "**`+` 按钮的屏幕坐标全程完全不变**",
        all(p == up_global for _lvl, p, _o in samples),
        f"起点 {up_global.x()},{up_global.y()}；各级 "
        + ", ".join(f"L{l}:{p.x()},{p.y()}" for l, p, _o in samples),
    )
    check(
        "**`−` 按钮的屏幕坐标也完全不变**",
        bar.button_down.mapToGlobal(bar.button_down.rect().center()) == down_global,
        f"{down_global.x()},{down_global.y()}",
    )
    check(
        "控制栏左上角的屏幕坐标也完全不变",
        all(o == samples[0][2] for _l, _p, o in samples),
        f"{samples[0][2].x()},{samples[0][2].y()}",
    )
    check(
        "第 10 级时 + 灰掉",
        not bar.button_up.isEnabled(),
        f"+ enabled={bar.button_up.isEnabled()}",
    )
    check(
        "第 10 级时宠物明显比第 1 级大（缩放真的生效了）",
        window.pixel_size[1] > 500,
        f"第 10 级像素高 {window.pixel_size[1]}",
    )

    # 一路点回去也不动
    back_samples = []
    for _ in range(9):
        bar.level_down.emit()
        for _ in range(4):
            app.processEvents()
        back_samples.append(bar.mapToGlobal(bar.rect().topLeft()))
    check("一路点 − 回到第 1 级", window.level == 1, f"level={window.level}")
    check(
        "点回去的过程里控制栏坐标也不变",
        all(o == samples[0][2] for o in back_samples),
        f"起点 {samples[0][2].x()},{samples[0][2].y()}",
    )
    check(
        "第 1 级时 − 灰掉",
        not bar.button_down.isEnabled(),
        f"− enabled={bar.button_down.isEnabled()}",
    )

    # ------------------------------------------------------------ §3 底边固定
    section("3. 缩放过程中宠物底边固定（放得下的档位区间）")

    window.set_size_level(1)
    controller.sync()
    for _ in range(6):
        app.processEvents()

    bottoms = []
    for _ in range(9):
        bar.level_up.emit()
        for _ in range(4):
            app.processEvents()
        bottoms.append((window.level, window.base_position.y() + window.pixel_size[1]))

    stable = [b for lvl, b in bottoms if lvl * 0 + 500 <= screen.height() - cb.RESERVED or True]
    # 只看"宠物高度 + 预留"放得进屏幕的那些档位。
    fit = [(lvl, b) for lvl, b in bottoms if _height_of(window, lvl) + cb.RESERVED <= screen.height()]
    check(
        "放得下的档位里宠物底边完全一致",
        len({b for _l, b in fit}) <= 1,
        f"各档底边：{sorted({b for _l, b in fit})}（放得下的档位 {[l for l, _b in fit]}）",
    )
    check("有档位满足'放得下'（否则这条没验到）", len(fit) >= 3, f"{len(fit)} 个档位")

    # ------------------------------------------------------------ §4 拖动跟随
    section("4. 拖动宠物：控制栏按增量跟随")

    window.set_size_level(4)
    controller.sync()
    for _ in range(6):
        app.processEvents()
    bar_before = bar.mapToGlobal(bar.rect().topLeft())
    pet_before = window.base_position

    # 走**产品路径**：拖 3 次，每次 20 px（用真实的拖动控制器 + 信号）
    from PySide6.QtCore import QPoint

    window.drag_controller.begin(QPoint(10, 10), QPoint(500, 500), window.base_position)
    for step in range(1, 4):
        window.drag_controller.update(QPoint(500 + step * 20, 500 + step * 10))
        window.drag_by(step * 20, step * 10)
        for _ in range(3):
            app.processEvents()
    window._finish_drag(QPoint(560, 530))
    for _ in range(6):
        app.processEvents()

    bar_after = bar.mapToGlobal(bar.rect().topLeft())
    pet_delta_x = window.base_position.x() - pet_before.x()
    pet_delta_y = window.base_position.y() - pet_before.y()
    check(
        "控制栏按**增量**跟着走（位移与宠物完全一致）",
        (bar_after.x() - bar_before.x(), bar_after.y() - bar_before.y())
        == (pet_delta_x, pet_delta_y),
        f"宠物动了 ({pet_delta_x}, {pet_delta_y})，栏动了 "
        f"({bar_after.x() - bar_before.x()}, {bar_after.y() - bar_before.y()})",
    )

    # 来回拖不抖：拖回去，栏必须精确回到原处
    window.drag_controller.begin(QPoint(10, 10), QPoint(500, 500), window.base_position)
    window.drag_controller.update(QPoint(500 - pet_delta_x, 500 - pet_delta_y))
    window.drag_by(-pet_delta_x, -pet_delta_y)
    for _ in range(4):
        app.processEvents()
    window._finish_drag(QPoint(500 - pet_delta_x, 500 - pet_delta_y))
    for _ in range(6):
        app.processEvents()
    bar_back = bar.mapToGlobal(bar.rect().topLeft())
    check(
        "来回拖之后控制栏精确回到原处（不累积误差、不抖）",
        (bar_back.x(), bar_back.y()) == (bar_before.x(), bar_before.y()),
        f"{bar_before.x()},{bar_before.y()} vs {bar_back.x()},{bar_back.y()}",
    )

    # ------------------------------------------------------------ §5 关闭回位
    section("5. 点 × 关闭：栏消失、宠物回位")

    window.showing_at_default_position()
    window.set_reserved(True)
    for _ in range(6):
        app.processEvents()
    controller.close()
    controller.open()
    for _ in range(8):
        app.processEvents()
    opened_y = window.base_position.y()

    bar.closed.emit()  # 走**产品路径**：× 按钮的信号
    for _ in range(8):
        app.processEvents()
    check("点 × 后控制栏隐藏", not controller.visible, f"visible={controller.visible}")
    check(
        "点 × 后宠物放回贴屏幕底边（y 变大 = 放下去了）",
        window.base_position.y() > opened_y and window.bottom_margin == 0,
        f"{opened_y} → {window.base_position.y()}，边距 {window.bottom_margin}",
    )
    expected = screen.bottom() + 1 - window.pixel_size[1] - 24
    check(
        "回位后就是缺省落点的纵坐标（贴屏幕底边、留 24）",
        abs(window.base_position.y() - expected) <= 1,
        f"实际 {window.base_position.y()}，缺省 {expected}",
    )

    # 再打开正常（幂等 + 复用）
    controller.open()
    for _ in range(8):
        app.processEvents()
    check("关闭后再打开正常", controller.visible, f"visible={controller.visible}")
    controller.open()
    check("重复打开还是同一个栏", controller.bar is bar, "同一实例")

    # ------------------------------------------------------------ §6 贴底时放上方
    section("6. 宠物贴屏幕最下方时，控制栏出现在宠物上方")

    # 把宠物拖到屏幕底部（走产品路径：直接摆到夹取后的位置）
    bottom_y = screen.bottom() + 1 - window.pixel_size[1]
    window.set_reserved(False)  # 模拟"用户自己把它拖到底了"
    window.show_at(screen.left() + 100, bottom_y, user_positioned=True)
    for _ in range(6):
        app.processEvents()
    controller.reposition()
    for _ in range(6):
        app.processEvents()

    bar_top = bar.mapToGlobal(bar.rect().topLeft())
    pet_top = window.base_position.y()
    check(
        "下方放不下 → 栏出现在宠物**上方**",
        bar_top.y() + bar.height() == pet_top - cb.GAP,
        f"栏底 {bar_top.y() + bar.height()}，宠物顶 {pet_top}，GAP {cb.GAP}",
    )
    check(
        "仍然右对齐",
        bar_top.x() + bar.width() == window.base_position.x() + window.pixel_size[0],
        f"栏右边 {bar_top.x() + bar.width()} vs 宠物右边 {window.base_position.x() + window.pixel_size[0]}",
    )
    check(
        "栏完整在屏幕内",
        bar_top.y() >= screen.top(),
        f"栏顶 {bar_top.y()}，屏幕顶 {screen.top()}",
    )

    # ------------------------------------------------------------ §7 配置
    section("7. 换档写进配置（重启后仍是这个档位）")

    controller.open()
    for _ in range(6):
        app.processEvents()
    bar.set_level(4)
    window.set_size_level(4)
    controller.sync()
    for _ in range(6):
        app.processEvents()

    check("配置里 size_level 是 4", store.get("size_level") == 4, f"{store.get('size_level')!r}")
    reloaded = ConfigStore(cfg_path)
    reloaded.load()
    check(
        "重新读盘仍是第 4 级（重启后沿用）",
        reloaded.has("size_level") and reloaded.get("size_level") == 4,
        f"has={reloaded.has('size_level')} value={reloaded.get('size_level')!r}",
    )

    # 到边界不写
    def mtime() -> int:
        return cfg_path.stat().st_mtime_ns

    window.set_size_level(10)
    controller.sync()
    for _ in range(4):
        app.processEvents()
    before = mtime()
    for _ in range(5):
        bar.level_up.emit()
        app.processEvents()
    check("到第 10 级再点 + ：不写配置", mtime() == before, f"mtime {before} → {mtime()}")

    # ------------------------------------------------------------ §8 退出收尾
    section("8. 退出收尾：控制栏被收掉")

    controller.close()
    for _ in range(4):
        app.processEvents()
    check("收尾后控制栏不可见", not controller.visible, f"visible={controller.visible}")
    window.close()
    for _ in range(6):
        app.processEvents()
    check("宠物窗口关闭后没有孤儿控制栏", not bar.isVisible(), f"bar visible={bar.isVisible()}")

    return _finish()


def _height_of(window, level: int) -> int:
    """某档位的像素高度（**不回读窗口坐标**，按素材 cell 算）。"""
    from deskpet import size

    return size.pixel_size(window.pet.cell, level)[1]


if __name__ == "__main__":
    raise SystemExit(main())
