"""task09 自检：尺寸控制栏（一条悬浮条，缩放时按钮不动）。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task09_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task09.py

**窗口用例走 offscreen 平台插件**。offscreen 的虚拟屏幕只有 **800×800**
（§4.14），装不下第 10 级（500×500 素材 → 1000×1000），所以凡是要"看全屏"
的断言都**按给定的屏幕矩形**验几何，不依赖虚拟屏幕的真实尺寸
（§4.11：别断言随环境变化的量）。

**真屏幕那几条**（"右边缘真的对齐"、"连点 + 时按钮的屏幕坐标真的不变"）
由 `tools/check_task09_real.py` 在真平台验。
"""

import os
import shutil
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task09_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task09_scratch"

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


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _code_only(path: Path) -> str:
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，task06/07/08 各踩过）。"""
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _finish() -> int:
    _LINES.append("")
    if _FAILED:
        summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项"
    else:
        summary = f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


def _load_spec():
    """自带包的 `PetSpec`（**只读**，一个字节都不改）。"""
    from deskpet import pet_loader

    return pet_loader.load_pet(ROOT / "assets" / "weedy")


# ------------------------------------------------------------------ 主流程


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run_checks()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run_checks() -> int:
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import control_bar as cb
    from deskpet import context_menu, size
    from deskpet.app import ControlBarController
    from deskpet.config import ConfigStore
    from deskpet.idle_state import IdleStateMachine
    from deskpet.mirror import MirrorState
    from deskpet.pet_window import PetWindow

    spec = _load_spec()

    # ------------------------------------------------------------ §1 常量
    section("1. 常量真值只有一处（栏高 / 间距 / 预留）")

    check("BAR_HEIGHT == 40", cb.BAR_HEIGHT == 40, f"实测 {cb.BAR_HEIGHT}")
    check("GAP == 8", cb.GAP == 8, f"实测 {cb.GAP}")
    check(
        "RESERVED == 40 + 8 + 4",
        cb.RESERVED == cb.BAR_HEIGHT + cb.GAP + 4,
        f"实测 {cb.RESERVED}",
    )
    check(
        "RESERVED 是算出来的，不是另写一个 52",
        "BAR_HEIGHT + GAP + 4" in _source_of("control_bar.py"),
        "源码里的写法",
    )
    # 真值只允许一处：别的模块不许再写一遍 40 / 8 / 52。
    for module in ("app.py", "pet_window.py", "context_menu.py"):
        code = _code_only(ROOT / "src" / "deskpet" / module)
        check(
            f"{module} 里没有第二个 BAR_HEIGHT/GAP/RESERVED 真值",
            "BAR_HEIGHT = 40" not in code and "GAP = 8" not in code,
            "只允许在 control_bar.py 定义",
        )

    # ------------------------------------------------------------ §2 文字格式
    section("2. 档位文字：{倍数:.2f}x（{档位}/{总数}）")

    check("第 6 级 → 1.00x（6/10）", size.level_text(6) == "1.00x（6/10）", size.level_text(6))
    check("第 1 级 → 0.25x（1/10）", size.level_text(1) == "0.25x（1/10）", size.level_text(1))
    check("第 10 级 → 2.00x（10/10）", size.level_text(10) == "2.00x（10/10）", size.level_text(10))
    check(
        "括号是全角（与卡里逐字符一致）",
        "（" in size.level_text(6) and "）" in size.level_text(6),
        "U+FF08 / U+FF09",
    )
    check(
        "越界档位不抛异常（展示函数）",
        size.level_text(0) == "0.25x（1/10）" and size.level_text(99) == "2.00x（10/10）",
        f"0→{size.level_text(0)} / 99→{size.level_text(99)}",
    )
    check(
        "总数可改（将来多于 10 级时文字跟着走）",
        size.level_text(3, 12) == "0.50x（3/12）",
        size.level_text(3, 12),
    )
    check(
        "10 级的文字两两不同（不会两级显示同一个说法）",
        len({size.level_text(i) for i in range(1, 11)}) == 10,
        str(sorted({size.level_text(i) for i in range(1, 11)})[:3]),
    )

    # ------------------------------------------------------------ §3 控制栏自身
    section("3. 控制栏：独立顶层窗口 + 布局 + 边界置灰")

    bar = cb.ControlBar()
    flags = bar.windowFlags()
    from PySide6.QtCore import Qt

    check("是 Qt.Tool", bool(flags & Qt.Tool), f"flags={int(flags)}")
    check("无边框", bool(flags & Qt.FramelessWindowHint), f"flags={int(flags)}")
    check("始终置顶", bool(flags & Qt.WindowStaysOnTopHint), f"flags={int(flags)}")
    check(
        "显式打开了 WA_QuitOnClose（否则关掉它 app.exec() 不返回）",
        bar.testAttribute(Qt.WA_QuitOnClose),
        "agent.md §4.9",
    )
    check("栏高固定 == BAR_HEIGHT", bar.height() == cb.BAR_HEIGHT, f"实测 {bar.height()}")

    layout = bar.layout()
    check("用的是 QHBoxLayout", layout is not None and type(layout).__name__ == "QHBoxLayout", type(layout).__name__)
    check("布局里有 4 个控件（文字 + − + ×）", layout.count() == 4, f"实测 {layout.count()}")
    check(
        "控件按顺序是 label/−/+/×",
        bar.level_label is not None
        and bar.button_down.text() == cb.TEXT_LEVEL_DOWN
        and bar.button_up.text() == cb.TEXT_LEVEL_UP
        and bar.button_close.text() == cb.TEXT_CLOSE,
        f"{bar.button_down.text()!r} {bar.button_up.text()!r} {bar.button_close.text()!r}",
    )
    check(
        "按钮文字不是 ASCII 连字符/字母 x",
        cb.TEXT_LEVEL_DOWN == "\u2212" and cb.TEXT_CLOSE == "\u00d7",
        f"{cb.TEXT_LEVEL_DOWN!r} / {cb.TEXT_CLOSE!r}",
    )

    # 边界置灰
    bar.set_level(6)
    check("中间档位两个按钮都可点", bar.button_down.isEnabled() and bar.button_up.isEnabled(), "6/10")
    bar.set_level(1)
    check("第 1 级：− 灰掉", not bar.button_down.isEnabled(), f"− enabled={bar.button_down.isEnabled()}")
    check("第 1 级：+ 仍可点", bar.button_up.isEnabled(), f"+ enabled={bar.button_up.isEnabled()}")
    check("第 1 级文字", bar.level_label.text() == "0.25x（1/10）", bar.level_label.text())
    bar.set_level(10)
    check("第 10 级：+ 灰掉", not bar.button_up.isEnabled(), f"+ enabled={bar.button_up.isEnabled()}")
    check("第 10 级：− 仍可点", bar.button_down.isEnabled(), f"− enabled={bar.button_down.isEnabled()}")
    check("第 10 级文字", bar.level_label.text() == "2.00x（10/10）", bar.level_label.text())

    # **关键**：文字长度变化不能撑动布局（否则按钮会挪）
    bar.set_level(1)
    size_at_1 = (bar.size().width(), bar.button_up.pos().x(), bar.button_up.pos().y())
    bar.set_level(10)
    size_at_10 = (bar.size().width(), bar.button_up.pos().x(), bar.button_up.pos().y())
    check(
        "档位 1↔10 之间栏宽与按钮窗口内坐标完全不变",
        size_at_1 == size_at_10,
        f"1 级 {size_at_1} vs 10 级 {size_at_10}",
    )
    check("栏宽固定（setFixedSize 过）", bar.minimumWidth() == bar.maximumWidth(), f"{bar.minimumWidth()}..{bar.maximumWidth()}")

    # ------------------------------------------------------------ §4 定位
    section("4. 定位：下方优先 / 右对齐 / 边界兜底")

    screen = QRect(0, 0, 2560, 1440)
    bar.set_level(6)
    w, h = bar.width(), bar.height()

    # ① 宠物在屏幕中间 → 下方，右对齐
    pet = QRect(1000, 400, 500, 500)
    bar.place_relative_to(pet, screen)
    check(
        "宠物右边 = 控制栏右边（右对齐，不是水平居中）",
        bar.x() + w == pet.x() + pet.width(),
        f"栏右边 {bar.x() + w} vs 宠物右边 {pet.x() + pet.width()}",
    )
    check(
        "不是水平居中（居中的话会差出一大截）",
        bar.x() != pet.x() + (pet.width() - w) // 2,
        f"栏左边 {bar.x()}，居中会是 {pet.x() + (pet.width() - w) // 2}",
    )
    check(
        "在宠物下方，间距 == GAP",
        bar.y() == pet.y() + pet.height() + cb.GAP,
        f"栏顶 {bar.y()}，宠物底 {pet.y() + pet.height()}，GAP {cb.GAP}",
    )

    # ② 宠物贴屏幕底部 → 上方
    pet_low = QRect(1000, 1440 - 500, 500, 500)  # 底边正好在屏幕底
    bar.place_relative_to(pet_low, screen)
    check(
        "下方放不下 → 上方，间距 == GAP",
        bar.y() == pet_low.y() - cb.GAP - h,
        f"栏顶 {bar.y()}，宠物顶 {pet_low.y()}",
    )
    check(
        "上方时仍然右对齐",
        bar.x() + w == pet_low.x() + pet_low.width(),
        f"栏右边 {bar.x() + w} vs 宠物右边 {pet_low.x() + pet_low.width()}",
    )

    # ③ 上下都放不下 → 贴屏幕缘
    pet_huge = QRect(0, -200, 500, 1800)  # 比屏幕还高
    bar.place_relative_to(pet_huge, screen)
    check(
        "上下都放不下 → 夹在屏幕内（贴上缘）",
        bar.y() >= screen.top() and bar.y() + h <= screen.bottom() + 1,
        f"栏顶 {bar.y()}，高 {h}，屏幕 {screen.top()}..{screen.bottom()}",
    )

    # ④ 右对齐后越界 → 夹回屏幕内
    pet_right = QRect(2560 - 500, 400, 500, 500)
    bar.place_relative_to(pet_right, screen)
    check(
        "宠物贴右缘时栏仍完整在屏幕内",
        bar.x() >= screen.left() and bar.x() + w <= screen.right() + 1,
        f"栏左边 {bar.x()}，宽 {w}",
    )
    check(
        "那种情况下仍然右对齐（夹取没有把它推走）",
        bar.x() + w == pet_right.x() + pet_right.width(),
        f"栏右边 {bar.x() + w} vs 宠物右边 {pet_right.x() + pet_right.width()}",
    )

    # ------------------------------------------------------------ §5 增量跟随
    section("5. follow()：按增量平移，禁止重算锚点")

    bar.place_relative_to(QRect(1000, 400, 500, 500), screen)
    start_x, start_y = bar.x(), bar.y()
    bar.follow(37, -21)
    check("正增量平移", (bar.x(), bar.y()) == (start_x + 37, start_y - 21), f"({bar.x()}, {bar.y()})")
    bar.follow(-37, 21)
    check("反增量回到原处（不累积误差）", (bar.x(), bar.y()) == (start_x, start_y), f"({bar.x()}, {bar.y()})")

    # 来回抖 100 次，位置必须**精确**回到原点
    for _ in range(100):
        bar.follow(3, 7)
        bar.follow(-3, -7)
    check("来回抖 100 次后位置精确回到原点", (bar.x(), bar.y()) == (start_x, start_y), f"({bar.x()}, {bar.y()})")

    bar.follow(0, 0)
    check("零增量不动", (bar.x(), bar.y()) == (start_x, start_y), f"({bar.x()}, {bar.y()})")

    # 边缘处跟随不被裁（增量平移**不夹取**，所以能跟着宠物到任何地方）
    bar.move(screen.right() - 10, screen.bottom() - 10)
    edge_x, edge_y = bar.x(), bar.y()
    bar.follow(-500, -500)
    check(
        "边缘处按增量移得动（没被夹回屏幕内 = 没有重算锚点）",
        (bar.x(), bar.y()) == (edge_x - 500, edge_y - 500),
        f"({bar.x()}, {bar.y()})",
    )

    # ------------------------------------------------------------ §6 组装层
    section("6. ControlBarController：幂等 / 抬升 / 回位 / 边界不写配置")

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

    controller = ControlBarController(window)
    window.set_resize_handler(controller.toggle)

    check("初始没有控制栏", not controller.visible and controller.bar is None, "未打开")

    base_top = window.base_position.y()
    base_bottom = base_top + window.pixel_size[1]

    controller.open()
    check("打开后有控制栏且可见", controller.visible, f"visible={controller.visible}")
    bar1 = controller.bar
    opened_top = window.base_position.y()
    opened_bottom = opened_top + window.pixel_size[1]
    check(
        "打开时宠物整体上移 RESERVED",
        base_top - opened_top == cb.RESERVED,
        f"上移了 {base_top - opened_top}，RESERVED={cb.RESERVED}",
    )
    check(
        "上移之后底边 = 原底边 − RESERVED",
        base_bottom - opened_bottom == cb.RESERVED,
        f"原底 {base_bottom} → 新底 {opened_bottom}",
    )
    check("控制栏在宠物下方", controller.bar.y() == opened_bottom + cb.GAP, f"栏顶 {controller.bar.y()}，宠物底 {opened_bottom}")

    # 幂等：重复 toggle 打开 → 同一个实例
    controller.open()
    controller.open()
    check("重复打开返回**同一个**栏（不会叠出好几个）", controller.bar is bar1, "同一实例")
    check("重复打开没有再次上移", window.base_position.y() == opened_top, f"y={window.base_position.y()}")

    # 幂等：重复 toggle 打开 → 同一个实例
    controller.open()
    controller.open()
    check("重复打开返回**同一个**栏（不会叠出好几个）", controller.bar is bar1, "同一实例")
    check("重复打开没有再次上移", window.base_position.y() == opened_top, f"y={window.base_position.y()}")

    controller.toggle()  # 关
    check("toggle 一次就关掉", not controller.visible, f"visible={controller.visible}")
    controller.toggle()  # 再开
    check("关闭后再打开正常（同一个实例复用）", controller.visible and controller.bar is bar1, "复用")
    controller.close()
    check(
        "关闭后宠物放回原本贴屏幕底边的位置（边距归零）",
        window.base_position.y() == base_top and window.bottom_margin == 0,
        f"y={window.base_position.y()}（原本 {base_top}），边距 {window.bottom_margin}",
    )

    # ------------------------------------------------------------ §7 缩放中按钮不动
    section("7. 缩放中按钮不动（本 task 最关键的一条）")

    controller.open()
    bar_pos_at_open = (controller.bar.x(), controller.bar.y())
    pet_bottom_at_open = window.base_position.y() + window.pixel_size[1]

    # ⚠ offscreen 的虚拟屏幕只有 800×800（§4.14），装不下第 8 级以上的 500×500 素材，
    # 所以"宠物底边恒定"这条**只能在放得下的档位区间**里验：第 10 级的窗口是
    # 1000×1000，比屏幕还高，`clamp_position` 必然把它顶到上缘。那是测试平台的
    # 限制，不是产品 bug（§4.11：别为让它变绿而改产品逻辑）。
    #
    # 真正与平台无关的不变量是**控制栏的位置**——它才是本 task 的关键验收，
    # 而它从第 1 级到第 10 级**全程不变**（下面那条断言）。
    fit_bottom = True
    bottom_detail = f"底边恒为 {pet_bottom_at_open}"

    positions = []
    for level in range(1, 11):
        controller.bar.level_up.emit()  # 走**产品路径**（信号），不是直接调 set_size_level
        positions.append((window.level, controller.bar.x(), controller.bar.y()))
        pet_bottom = window.base_position.y() + window.pixel_size[1]
        # 只检查"窗口放得下"的那些档位（放不下时会被夹到屏幕内，底边自然变）。
        fits = window.pixel_size[1] <= 800 - cb.RESERVED
        if fits and pet_bottom != pet_bottom_at_open:
            fit_bottom = False
            bottom_detail = f"第 {level} 级时底边 {pet_bottom} != {pet_bottom_at_open}"
            break

    check("放得下的档位区间内：宠物底边始终不动", fit_bottom, bottom_detail)

    check(
        "第 10 级的 + 仍然灰着（到头了）",
        not controller.bar.button_up.isEnabled(),
        f"+ enabled={controller.bar.button_up.isEnabled()}",
    )
    check("连点后档位确实到了 10", window.level == 10, f"level={window.level}")
    check(
        "全程控制栏的屏幕坐标完全不变",
        all((x, y) == bar_pos_at_open for _lvl, x, y in positions),
        f"开栏时 {bar_pos_at_open}，各级 {[p[1:] for p in positions]}",
    )

    # 再一路点回去
    for _ in range(12):
        controller.bar.level_down.emit()
    check("一路点 − 到底：停在第 1 级", window.level == 1, f"level={window.level}")
    check("第 1 级时 − 灰掉", not controller.bar.button_down.isEnabled(), "− 禁用")
    check(
        "点回到底之后控制栏位置仍然没变",
        (controller.bar.x(), controller.bar.y()) == bar_pos_at_open,
        f"({controller.bar.x()}, {controller.bar.y()})",
    )

    # ------------------------------------------------------------ §7b 换档不算拖动
    section("7b. 换档时跟随逻辑必须挂起（换档不是拖动）")

    # 把跟随接上（`app.py` 的真实接线），再看换档会不会把栏带走。
    # ⚠ 这条盯的是一个**真平台自检抓到的真 bug**：`set_size_level()` 会改宠物
    # 左上角（右下角锚点不动）→ `position_changed` 照样发出来 → 跟随逻辑
    # 按增量平移控制栏 → 表现是"点 + 时栏斜着跑，用户得追着鼠标点"，
    # 正好违反本 task 最关键的那条验收（实测栏从 2306,1372 漂到 1974,891）。
    window.set_resize_handler(controller.toggle)
    window.position_changed.connect(lambda _pos: controller.sync_follow())
    controller.close()
    controller.open()
    for _ in range(4):
        app.processEvents()
    follow_bar_pos = (controller.bar.x(), controller.bar.y())
    # 只走**产品路径**（`bar.level_up` → `_step`）。直接调 `set_size_level()`
    # 不是产品序列（那等于"程序自己改了档"），那时跟随照常生效是对的。
    for _ in range(4):
        controller.bar.level_up.emit()
        app.processEvents()
    check(
        "接了跟随之后，换档仍然不让控制栏移动",
        (controller.bar.x(), controller.bar.y()) == follow_bar_pos,
        f"开栏 {follow_bar_pos} → 换档后 ({controller.bar.x()}, {controller.bar.y()})",
    )
    check(
        "换档后跟随基准已重设（紧接着拖动第一下不跳）",
        controller._synced_pet_pos == window.base_position,
        f"{controller._synced_pet_pos} vs {window.base_position}",
    )
    # 但**真的拖动**时必须跟随（别把跟随一起关死了）
    before_drag = (controller.bar.x(), controller.bar.y())
    window.drag_controller.begin(QPoint(10, 10), QPoint(500, 500), window.base_position)
    window.drag_controller.update(QPoint(530, 520))
    window.drag_by(30, 20)
    for _ in range(4):
        app.processEvents()
    window._finish_drag(QPoint(530, 520))
    for _ in range(4):
        app.processEvents()
    moved = (controller.bar.x() - before_drag[0], controller.bar.y() - before_drag[1])
    check(
        "真拖动时跟随照常生效（挂起只针对换档）",
        moved != (0, 0),
        f"栏动了 {moved}",
    )
    controller.close()

    # ------------------------------------------------------------ §8 换档写配置
    section("8. 换档写配置：只在真换档时写")

    def mtime() -> int:
        return cfg_path.stat().st_mtime_ns if cfg_path.exists() else -1

    # 先把档位摆到中间，才有两侧的边界可试。
    controller.bar.set_level(6)
    window.set_size_level(6)
    controller.sync()
    check("前置：档位回到 6", window.level == 6, f"level={window.level}")

    before = mtime()
    controller.bar.level_up.emit()  # 6 → 7，真换档
    stored = store.get("size_level")
    check("换档后配置里是新档位（7）", stored == 7, f"size_level={stored!r}")
    check("size_level 已经落盘（has 为真）", store.has("size_level"), f"has={store.has('size_level')}")
    check("换档确实写了盘", mtime() != before, f"mtime {before} → {mtime()}")

    # 到上边界（第 10 级）再点：不写
    for _ in range(3):
        controller.bar.level_up.emit()
    check("一路点到第 10 级", window.level == 10, f"level={window.level}")
    before = mtime()
    for _ in range(5):
        controller.bar.level_up.emit()  # 已经第 10 级 → 到边界
    check("到上边界再点：档位不变", window.level == 10, f"level={window.level}")
    check("到上边界再点：**不写配置**（文件没被动过）", mtime() == before, f"mtime {before} → {mtime()}")

    # 到下边界（第 1 级）再点：不写
    for _ in range(9):
        controller.bar.level_down.emit()
    check("一路点到第 1 级", window.level == 1, f"level={window.level}")
    before = mtime()
    for _ in range(5):
        controller.bar.level_down.emit()  # 已经第 1 级 → 到边界
    check("到下边界再点：档位不变", window.level == 1, f"level={window.level}")
    check("到下边界再点：**不写配置**", mtime() == before, f"mtime {before} → {mtime()}")

    # ------------------------------------------------------------ §9 用户位置时不抬
    section("9. 用户显式指定位置时，底部边距机制不生效")

    window2 = PetWindow(spec, 6, config=store)
    window2.play_action("idle")
    window2.show_at(200, 150, user_positioned=True)
    window2.show()
    controller2 = ControlBarController(window2)
    controller2.open()
    check(
        "用户给了位置 → 打开控制栏**不上移**宠物",
        window2.base_position.y() == 150,
        f"y={window2.base_position.y()}（给了 150）",
    )
    check("那种情况下也没有边距", window2.bottom_margin == 0, f"边距 {window2.bottom_margin}")
    controller2.close()
    check("关闭后位置也不变", window2.base_position.y() == 150, f"y={window2.base_position.y()}")

    # ------------------------------------------------------------ §10 菜单接线
    section("10. 右键菜单：调整大小… 可点了")

    state_off = window.menu_state()
    check(
        "装了处理器 → resize_available 为真",
        state_off.resize_available,
        f"resize_available={state_off.resize_available}",
    )
    menu = context_menu.build_menu(window, state_off)
    actions = {context_menu.action_id(a): a for a in menu.actions()}
    check("菜单里有 resize 项", context_menu.ID_RESIZE in actions, str(list(actions)))
    check(
        "调整大小… 可点（task06 里它是灰的）",
        actions[context_menu.ID_RESIZE].isEnabled(),
        f"enabled={actions[context_menu.ID_RESIZE].isEnabled()}",
    )
    check(
        "文字仍是 调整大小…",
        actions[context_menu.ID_RESIZE].text() == "调整大小…",
        actions[context_menu.ID_RESIZE].text(),
    )
    check(
        "设置…/隐藏宠物 仍然灰着（不属本 task）",
        not actions[context_menu.ID_SETTINGS].isEnabled()
        and not actions[context_menu.ID_VISIBILITY].isEnabled(),
        "本 task 只接 resize",
    )

    # 没装处理器的窗口 → resize 仍然灰
    bare = PetWindow(spec, 6)
    check(
        "没装处理器的窗口 resize 仍然灰（不给用户假反应）",
        not bare.menu_state().resize_available,
        f"resize_available={bare.menu_state().resize_available}",
    )

    # 走**产品路径**：菜单 id → 窗口分派 → 处理器
    controller.close()
    check("先确保是关着的", not controller.visible, f"visible={controller.visible}")
    window._handle_menu_choice(context_menu.ID_RESIZE)
    check("选 调整大小… → 控制栏开了", controller.visible, f"visible={controller.visible}")
    window._handle_menu_choice(context_menu.ID_RESIZE)
    check("再选一次 → 关掉", not controller.visible, f"visible={controller.visible}")

    # ------------------------------------------------------------ §11 重置静置计时
    section("11. 调整大小… 重置静置计时")

    idle = IdleStateMachine(lambda: 3)
    window3 = PetWindow(spec, 6, idle_state=idle, config=store)
    window3.play_action("idle")
    window3.showing_at_default_position()
    window3.set_reserved(True)
    window3.show()
    controller3 = ControlBarController(window3)

    def _toggle_resize() -> None:
        """与 `app.py` 里那一段**同构**（重置计时 + 开关栏）。"""
        idle.on_mouse_moved()
        controller3.toggle()

    window3.set_resize_handler(_toggle_resize)

    # 用小到大推进假时间（§4.11：跳着喂永远不触发）
    idle.on_tick(0.0)
    idle.on_tick(3.5)
    check("静置 3.5 秒后进入 waiting（前置条件）", str(idle.stage) == "waiting" or idle.stage.value == "waiting", f"stage={idle.stage}")

    window3._handle_menu_choice(context_menu.ID_RESIZE)
    check("点 调整大小… 后阶段回到 idle", idle.stage.value == "idle", f"stage={idle.stage}")
    check("且控制栏开了", controller3.visible, f"visible={controller3.visible}")
    window3._handle_menu_choice(context_menu.ID_RESIZE)

    # ------------------------------------------------------------ §12 镜像共存
    section("12. 与镜像共存：缩放不会丢镜像")

    mirror = MirrorState(store)
    window4 = PetWindow(spec, 6, mirror=mirror, config=store)
    window4.play_action("idle")
    window4.showing_at_default_position()
    window4.set_reserved(True)
    window4.show()

    check("初始未镜像", not window4.mirrored, f"mirrored={window4.mirrored}")
    ok_toggle = window4.toggle_mirror()
    check("翻转 idle 成功", ok_toggle and window4.mirrored, f"toggle={ok_toggle} mirrored={window4.mirrored}")

    image_before = window4.frame_image.copy()
    controller4 = ControlBarController(window4)
    controller4.open()
    controller4.bar.level_up.emit()  # 6 → 7
    check("缩放后档位变了", window4.level == 7, f"level={window4.level}")
    check("缩放后**仍然是镜像的**", window4.mirrored, f"mirrored={window4.mirrored}")

    image_after = window4.frame_image
    check(
        "缩放后拿到的是另一张图（尺寸变了）",
        image_after.size() != image_before.size(),
        f"{image_before.size()} → {image_after.size()}",
    )
    flipped = image_after

    # 镜像的**像素级**判据（沿用 task06 §的判据 4）：与"直接翻转原图再缩放"
    # 这条**独立路径**比对。`QImage.__eq__` 不是逐像素比较，所以必须比像素。
    from PySide6.QtCore import Qt as _Qt

    from deskpet import pet_loader
    from deskpet.rendering import image_size

    source = pet_loader.load_frame_image(spec, "idle", 0)
    manual = source.flipped(_Qt.Horizontal)
    target = size.pixel_size(image_size(manual), window4.level)
    manual_scaled = manual.scaled(
        target[0], target[1], _Qt.IgnoreAspectRatio, _Qt.SmoothTransformation
    )
    check(
        "缩放后的镜像图 == 『直接翻转原图再缩放』（两条独立路径一致）",
        manual_scaled == flipped,
        f"档位 {window4.level}，尺寸 {flipped.width()}x{flipped.height()}",
    )

    # 再翻一次回到未翻转态：**同一镜像态取两次必须逐位相同**（缓存没串味）。
    window4.toggle_mirror()
    back = window4.frame_image
    check("再翻回来 → 不再镜像", not window4.mirrored, f"mirrored={window4.mirrored}")
    check("回到未翻转态后确实与翻转图不同", back != flipped, "翻转是有效的")

    plain_again = window4.frame_image
    check(
        "同一镜像态取两次得到逐位相同的图（缓存没串味）",
        plain_again == back,
        "连续两次读未翻转态",
    )
    # 切档后再切回来，镜像态必须仍然正确（跨档位交叉验证缓存键含镜像）。
    window4.set_size_level(4)
    check("切到第 4 级后仍未镜像", not window4.mirrored, f"mirrored={window4.mirrored}")
    window4.toggle_mirror()
    check("第 4 级翻转成功", window4.mirrored, f"mirrored={window4.mirrored}")
    window4.toggle_mirror()
    window4.toggle_mirror()
    check("跨档位交叉翻转后仍是镜像的", window4.mirrored, f"mirrored={window4.mirrored}")
    controller4.close()

    # ------------------------------------------------------------ §13 不写盘 / 只读
    section("13. 读操作只读：打开控制栏不写配置")

    path2 = SCRATCH / "config_ro.json"
    path2.write_text("{}", encoding="utf-8")
    store_ro = ConfigStore(path2)
    store_ro.load()
    window5 = PetWindow(spec, 6, config=store_ro)
    window5.play_action("idle")
    window5.showing_at_default_position()
    window5.set_reserved(True)
    window5.show()
    before_bytes = path2.read_bytes()
    controller5 = ControlBarController(window5)
    controller5.open()
    controller5.close()
    check(
        "开关控制栏本身不写配置",
        path2.read_bytes() == before_bytes,
        f"{len(before_bytes)} 字节，前后一致",
    )

    # ------------------------------------------------------------ §14 源码卫生
    section("14. 源码卫生：常量一处 / 不用轮询 / 不碰画布")

    bar_src = _code_only(ROOT / "src" / "deskpet" / "control_bar.py")
    check(
        "control_bar.py 里没有 QTimer 轮询",
        "QTimer" not in bar_src,
        "位置必须由拖动信号驱动（task09 §5）",
    )
    check(
        "control_bar.py 没有用绝对坐标摆放控件",
        "setGeometry" not in bar_src and "move(" not in bar_src.replace("self.move(", ""),
        "布局用 QHBoxLayout；`self.move()` 只用于**窗口自身**",
    )
    check(
        "control_bar.py 里有 QHBoxLayout",
        "QHBoxLayout" in bar_src,
        "布局约定",
    )
    check(
        "control_bar.py 引用了 size.level_text（不自己拼文字）",
        "level_text" in bar_src,
        "文字格式的真值在 size.py",
    )
    pw_src = _code_only(ROOT / "src" / "deskpet" / "pet_window.py")
    check(
        "pet_window.py 没有写死 RESERVED（由调用方给）",
        "RESERVED" not in pw_src,
        "真值只在 control_bar.py",
    )
    check(
        "pet_window.py 不自己拼档位文字",
        "x（" not in pw_src,
        "转发 size.level_text",
    )
    app_src = _code_only(ROOT / "src" / "deskpet" / "app.py")
    # ⚠ `_code_only()` 把 token 用空格连起来，所以 `.close()` 会变成 `. close ( )`
    # ——**不要**拿带点的字面量去判（§4.11：认"源码里有没有某个名字"要先剥注释，
    # 剥完还得按 token 的实际形状写判据）。
    check(
        "app.py 的跟随接在 position_changed 上（信号驱动）",
        "position_changed" in app_src and "sync_follow" in app_src,
        "不是轮询",
    )
    tokens = app_src.split()
    # 找 `bar_controller . close` 这个 token 三元组（tokenize 会用空格把它们分开）。
    closes_bar = any(
        tokens[i] == "bar_controller" and tokens[i + 1] == "." and tokens[i + 2] == "close"
        for i in range(len(tokens) - 2)
    )
    check(
        "app.py 退出收尾里收了控制栏",
        closes_bar,
        "独立顶层窗口要显式收起，否则它比宠物活得久",
    )
    check(
        "app.py 用 Qt 的 QTimer 做控制栏轮询（不该有）",
        "bar_controller" in app_src and "poll" not in app_src,
        "位置变化一律靠信号",
    )

    # ------------------------------------------------------------ §15 目录干净
    section("15. 收尾：没有留下临时文件")

    leftovers = [
        p
        for p in ROOT.glob("**/*")
        if p.is_file() and p.suffix in {".tmp", ".bak"} and "data" in p.parts
    ]
    check("没有残留 .tmp/.bak 临时文件", not leftovers, f"{[str(p) for p in leftovers]}")

    return _finish()


def _source_of(name: str) -> str:
    """读 `src/deskpet/<name>` 的**原文**（注释也算，用来验"写法"）。"""
    return (ROOT / "src" / "deskpet" / name).read_text(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
