"""task08 自检：拖动与命中（只有按在人物身上才起拖，松手记住位置）。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task08_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task08.py

**窗口用例走 offscreen 平台插件**。offscreen **不支持 `setMask`**（§4.9），
所以"在透明区点击真的穿透到下面的窗口"这条**测不出来**——它必须由
`tools/check_task08_real.py` 在真平台用 Win32 `WindowFromPoint` 验。
本脚本断言的是**其余全部**可在无窗口环境判定的事实：拖动状态机、命中判定、
位置字符串、夹取、目录干净、与静置逻辑的挂起接线。

**拖动事件用 `QMouseEvent` 直接投递**（不是模拟鼠标），因为本任务要验的是
"窗口收到事件后做了什么"，而不是"系统有没有把事件送过来"——
后者归真平台那份。
"""

import json
import os
import shutil
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task08_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task08_scratch"

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
    """只保留**可执行代码**：把注释与字符串去掉（§4.11 的教训，task06/07 各踩一次）。"""
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


def _make_image(width: int, height: int, block: tuple[int, int, int, int] | None = None):
    """造一张透明图，可在指定矩形里填不透明块（`(x, y, w, h)`）。"""
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    if block is not None:
        bx, by, bw, bh = block
        for y in range(by, by + bh):
            for x in range(bx, bx + bw):
                image.setPixelColor(x, y, QColor(255, 0, 0, 255))
    return image


# ------------------------------------------------------------------ 主流程


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run_checks()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run_checks() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    _check_direction_action()
    _check_drag_state()
    _check_hit_test(app)
    _check_region_degradation(app)
    _check_no_app_crash()
    _check_position_text()
    _check_clamping_and_defaults()
    _check_source_contracts()
    _check_position_decision(app)
    _check_drag_events(app)
    _check_drag_writes_config(app)
    _check_manual_and_idle_wiring(app)
    _check_user_position_survives_level_change(app)
    _check_no_temp_files(app)
    return _finish()


# ------------------------------------------------------------ §1 方向 → 动作


def _check_direction_action() -> None:
    from deskpet import drag

    section("1. direction_action：只看符号、不设速度阈值")

    check("dx>0 → running_right", drag.direction_action(1, None) == "running_right")
    check("dx>0（很小，1px）也切", drag.direction_action(1, "running_left") == "running_right")
    check("dx<0 → running_left", drag.direction_action(-1, None) == "running_left")
    check("dx<0（-1px）也切", drag.direction_action(-1, "running_right") == "running_left")
    check(
        "慢速（dx=+2）与快速（dx=+500）结果相同 → 没有速度阈值",
        drag.direction_action(2, None) == drag.direction_action(500, None),
        f"{drag.direction_action(2, None)} == {drag.direction_action(500, None)}",
    )
    check(
        "纯竖直（dx=0）沿用上次方向",
        drag.direction_action(0, "running_left") == "running_left",
    )
    check(
        "dx=0 且没有上次方向 → None（让调用方按缺省起跑）",
        drag.direction_action(0, None) is None,
    )
    check(
        "缺省起跑方向是 running_right（agent.md §4.6）",
        drag.DEFAULT_DIRECTION == "running_right",
        f"{drag.DEFAULT_DIRECTION}",
    )
    check(
        "dx=0 不返回 idle（那会把'沿用'误当成'停下'）",
        drag.direction_action(0, "running_right") != "idle",
    )


# ------------------------------------------------------------ §2 拖动状态机


def _check_drag_state() -> None:
    from PySide6.QtCore import QPoint

    from deskpet import drag

    section("2. DragState：基准位置 + 累计位移（不回读窗口坐标）")

    state = drag.DragState()
    state.begin(QPoint(50, 60), QPoint(1000, 500), QPoint(950, 440))
    check("base_pos = 按下时的权威位置", state.base_pos == QPoint(950, 440), f"{state.base_pos}")
    check(
        "grab_offset = 按下点 − 窗口左上角",
        state.grab_offset == QPoint(50 - 950, 60 - 440),
        f"{state.grab_offset}",
    )
    check("press_global 记的是全局坐标", state.press_global == QPoint(1000, 500))

    target, action = state.update(QPoint(1300, 400))
    check(
        "update：位置 = base + 累计位移（+300, −100）",
        target == QPoint(1250, 340),
        f"{target}",
    )
    check("update：动作按 dx 符号", action == "running_right", f"{action}")

    # **累计**而不是增量：第二次移动仍然以"按下点"为基准。
    target2, _ = state.update(QPoint(1200, 400))
    check(
        "第二次 update 仍以按下点为基准（累计位移，不是增量累加）",
        target2 == QPoint(1150, 340),
        f"{target2}",
    )
    check("finish 返回最后一次的目标位置", state.finish() == QPoint(1150, 340), f"{state.finish()}")

    # "只点了一下没拖动"：update 一次都没调过，finish 就是基准位置。
    still = drag.DragState()
    still.begin(QPoint(10, 10), QPoint(500, 500), QPoint(490, 490))
    check(
        "只点一下没拖动：finish = 基准位置（仍要发完整的位置校正通知）",
        still.finish() == QPoint(490, 490),
        f"{still.finish()}",
    )

    # 竖直移动不切换动作。
    vertical = drag.DragState()
    vertical.begin(QPoint(10, 10), QPoint(500, 500), QPoint(490, 490))
    _, first = vertical.update(QPoint(600, 500))
    _, second = vertical.update(QPoint(600, 700))
    check(
        "竖直移动：动作一直是第一次定下的方向（不闪）",
        first == "running_right" and second == "running_right",
        f"{first!r} → {second!r}",
    )

    # 禁止回读窗口坐标：源码里不许出现 pos() / geometry()。
    source = _code_only(ROOT / "src" / "deskpet" / "drag.py")
    check(
        "drag.py 不回读窗口实时坐标（搜不到 .pos() / .geometry()）",
        ".pos()" not in source and ".geometry()" not in source,
        "代码里搜不到窗口坐标回读",
    )


# ------------------------------------------------------------ §3 命中判定


def _check_hit_test(app) -> None:
    from PySide6.QtCore import QPoint

    from deskpet import hit_test

    section("3. hit_test：按不透明像素判定 + 镜像坐标翻转")

    # 40×40 的图，不透明块在 x=5..14, y=10..29（**故意不对称**，镜像才验得出来）。
    image = _make_image(40, 40, (5, 10, 10, 20))

    check("块内 → 命中", hit_test.hit_test(image, QPoint(10, 20)) is True)
    check("块外（同一行右侧）→ 不命中", hit_test.hit_test(image, QPoint(30, 20)) is False)
    check("块外（左上角）→ 不命中", hit_test.hit_test(image, QPoint(0, 0)) is False)
    check("块外（同一列下方）→ 不命中", hit_test.hit_test(image, QPoint(10, 35)) is False)

    # 镜像：x' = width - 1 - x。原块 x∈[5,14]（40 宽）→ 镜像后 x∈[25,34]。
    check(
        "镜像后原位置（左侧）按不到",
        hit_test.hit_test(image, QPoint(10, 20), mirrored=True) is False,
    )
    check(
        "镜像后翻转位置（右侧）能按到",
        hit_test.hit_test(image, QPoint(30, 20), mirrored=True) is True,
    )
    check(
        "镜像坐标公式 x' = width - 1 - x：x=5 翻到 34",
        hit_test.hit_test(image, QPoint(34, 20), mirrored=True) is True,
    )
    check(
        "镜像坐标公式：x=35 翻到 4（原块的左边外侧）",
        hit_test.hit_test(image, QPoint(35, 20), mirrored=True) is False,
    )
    check(
        "镜像**不影响** y（竖直方向不翻）",
        hit_test.hit_test(image, QPoint(30, 35), mirrored=True) is False,
    )

    # rendered_size：判定跟着"看到的画面"走（图可能还是原尺寸）。
    # ⚠ offscreen 的虚拟屏幕是 800×800（§4.14），但这里与屏幕无关，只看图。
    check(
        "rendered_size 生效：把 40×40 的图当 20×20 看时，(10,20) 已在图外",
        hit_test.hit_test(image, QPoint(10, 20), rendered_size=(20, 20)) is False,
    )
    # 缩到 20×20 后，原块 x∈[5,14] → x∈[2,7]，y∈[10,29] → y∈[5,14]。
    check(
        "rendered_size 生效：缩到 20×20 后 (3,10) 仍在块内",
        hit_test.hit_test(image, QPoint(3, 10), rendered_size=(20, 20)) is True,
        "缩放后块在 x∈[2,7], y∈[5,14]",
    )
    check(
        "rendered_size 生效：缩到 20×20 后 (0,0) 在块外",
        hit_test.hit_test(image, QPoint(0, 0), rendered_size=(20, 20)) is False,
    )


# ------------------------------------------------------------ §4 降级语义


def _check_region_degradation(app) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QImage

    from deskpet import hit_test

    section("4. 降级：算不出来 → 整帧可点，永不抛异常")

    check("空图 → 整帧可点", hit_test.hit_test(QImage(), QPoint(0, 0)) is True)
    check("None → 整帧可点", hit_test.hit_test(None, QPoint(0, 0)) is True)
    check("空图的区域是 None", hit_test.region_for_frame(QImage()) is None)
    check("None 的区域是 None", hit_test.region_for_frame(None) is None)

    # **全透明**的图：区域为空 → 退化成整帧可点（不是"哪儿都点不到"）。
    blank = _make_image(20, 20, None)
    check(
        "全透明图 → 整帧可点（区域空是降级，不是'判不中'）",
        hit_test.hit_test(blank, QPoint(10, 10)) is True,
    )

    # 没有 alpha 通道 = 整块不透明。
    opaque = QImage(20, 20, QImage.Format_RGB32)
    opaque.fill(0x00FF0000)
    check(
        "没有 alpha 通道 → 整块不透明（任意点都命中）",
        hit_test.hit_test(opaque, QPoint(0, 0)) is True,
    )

    check(
        "图外的点不按降级处理 → False",
        hit_test.hit_test(_make_image(20, 20, (0, 0, 10, 10)), QPoint(100, 100)) is False,
    )

    # **没有 QGuiApplication 时不许崩**（task08 实测到的真问题）。
    check(
        "can_build_region() 在自检里有 app → True",
        hit_test.can_build_region() is True,
    )


def _check_no_app_crash() -> None:
    """**子进程**里不带 `QApplication` 调一次 `region_for_frame()`，验证不崩。

    task08 实测：`QBitmap.fromImage()` 在没有 `QGuiApplication` 实例时
    会**直接段错误**（Windows `0xC0000005`），`try/except` 拦不住。
    所以这条必须用**子进程**验——真崩了只会带走子进程，不会带走自检。
    """
    import subprocess

    section("5. 无 QApplication 时不许段错误（子进程验证）")

    snippet = (
        "import os, sys;"
        f"sys.path.insert(0, {str(ROOT / 'src')!r});"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "from PySide6.QtGui import QImage, QColor;"
        "from deskpet import hit_test;"
        "img = QImage(20, 20, QImage.Format_ARGB32_Premultiplied);"
        "img.fill(QColor(0, 0, 0, 0));"
        "print('region=', hit_test.region_for_frame(img));"
        "print('hit=', hit_test.hit_test(img, __import__('PySide6.QtCore', fromlist=['QPoint']).QPoint(1, 1)))"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=str(ROOT),
    )
    check(
        "无 app 的子进程退出码为 0（不是 0xC0000005 段错误）",
        completed.returncode == 0,
        f"returncode={completed.returncode}",
    )
    check(
        "无 app 时 region 退化为 None",
        "region= None" in (completed.stdout or ""),
        (completed.stdout or "").strip().splitlines()[-2:] and (completed.stdout or "").strip(),
    )
    check(
        "无 app 时 hit_test 退化为整帧可点",
        "hit= True" in (completed.stdout or ""),
        (completed.stdout or "").strip().splitlines()[-1] if completed.stdout else "(无输出)",
    )


# ------------------------------------------------------------ §6 位置字符串


def _check_position_text() -> None:
    from deskpet import geometry

    section("6. position 字符串：\"x,y\" 的解析与拼写")

    check("格式：\"1200,600\"", geometry.format_position(1200, 600) == "1200,600")
    check("格式：负数也对", geometry.format_position(-5, -6) == "-5,-6")
    check("解析：\"1200,600\" → (1200, 600)", geometry.parse_position("1200,600") == (1200, 600))
    check("解析：带空格也认（同一件事的另一种写法）", geometry.parse_position(" 1200 , 600 ") == (1200, 600))
    check("解析：负数", geometry.parse_position("-500,-500") == (-500, -500))
    check("解析：\"\" → None（回缺省落点）", geometry.parse_position("") is None)
    check("解析：\"abc\" → None", geometry.parse_position("abc") is None)
    check("解析：\"1,2,3\" → None（段数不对）", geometry.parse_position("1,2,3") is None)
    check("解析：\"1\" → None", geometry.parse_position("1") is None)
    check("解析：\"1,\" → None", geometry.parse_position("1,") is None)
    check("解析：\"1.5,2\" → None（不是整数）", geometry.parse_position("1.5,2") is None)
    check("解析：非字符串 → None（不崩）", geometry.parse_position(123) is None)
    check("解析：None → None（不崩）", geometry.parse_position(None) is None)
    check(
        "往返：format → parse 得到原值",
        all(
            geometry.parse_position(geometry.format_position(x, y)) == (x, y)
            for x, y in ((0, 0), (1200, 600), (-500, -500), (99999, 99999))
        ),
    )


# ------------------------------------------------------------ §7 夹取与缺省落点


def _check_clamping_and_defaults() -> None:
    from deskpet import geometry, size

    section("7. 夹取与缺省落点（越界值必须夹在写权威位置之前）")

    area = (0, 0, 2560, 1440)
    check("窗口内不动", geometry.clamp_position(100, 100, 500, 500, area) == (100, 100))
    check(
        "(99999, 99999) 夹回屏内",
        geometry.clamp_position(99999, 99999, 500, 500, area) == (2060, 940),
        f"{geometry.clamp_position(99999, 99999, 500, 500, area)}",
    )
    check(
        "(-500, -500) 夹回屏内",
        geometry.clamp_position(-500, -500, 500, 500, area) == (0, 0),
        f"{geometry.clamp_position(-500, -500, 500, 500, area)}",
    )
    check(
        "窗口比屏幕还大时贴左上角（不塞到负数）",
        geometry.clamp_position(10, 10, 3000, 3000, area) == (0, 0),
    )
    check(
        "拿不到屏幕信息时原样返回（不猜）",
        geometry.clamp_position(10, 20, 500, 500, (0, 0, 0, 0)) == (10, 20),
    )

    # 缺省落点：右下各留 24 逻辑单位。
    check(
        "缺省落点 = 右下角留 24（2560−500−24, 1440−500−24）",
        geometry.default_position(500, 500, area) == (2036, 916),
        f"{geometry.default_position(500, 500, area)}",
    )
    check(
        "缺省落点用的是**整块屏幕**（边距从屏幕边缘量起）",
        geometry.default_position(500, 500, area)[0] == 2560 - 500 - size.DEFAULT_MARGIN,
        f"右边距 = {size.DEFAULT_MARGIN}",
    )
    check(
        "屏幕信息拿不到 → (0, 0)",
        geometry.default_position(500, 500, (0, 0, 0, 0)) == (0, 0),
    )
    check(
        "夹取与锚点的真值仍在 size（geometry 只转发，没有第二份实现）",
        geometry.clamp_position is not size.clamp_position
        and geometry.clamp_position(1, 2, 3, 4, area) == size.clamp_position(1, 2, 3, 4, area),
        "转发结果与 size 一致",
    )


# ------------------------------------------------------------ §8 源码契约


def _check_source_contracts() -> None:
    section("8. 源码契约（只做要求的事）")

    window_src = _code_only(ROOT / "src" / "deskpet" / "pet_window.py")
    hit_src = _code_only(ROOT / "src" / "deskpet" / "hit_test.py")
    # ⚠ 认"某个**字符串字面量**在不在"时不能用 `_code_only()`（它把字符串都剥掉了）。
    # 那种断言要看**原文**——两条规则各管一半，别混（§4.11）。
    window_text = (ROOT / "src" / "deskpet" / "pet_window.py").read_text(encoding="utf-8")
    app_src = _code_only(ROOT / "src" / "deskpet" / "app.py")
    app_text = (ROOT / "src" / "deskpet" / "app.py").read_text(encoding="utf-8")

    check(
        "区域生成只有一处实现（pet_window 转发，不再自己算）",
        "createAlphaMask" in hit_src and "createAlphaMask" not in window_src,
        "createAlphaMask 只在 hit_test.py",
    )
    check(
        "pet_window 不再 import QBitmap（区域生成搬走了）",
        "QBitmap" not in window_src,
        "代码里搜不到 QBitmap",
    )
    check(
        "setMask 仍然只有一处调用（真穿透靠它）",
        window_src.count("setMask") == 1,
        f"setMask 出现 {window_src.count('setMask')} 次",
    )
    check(
        "拖动**不逐帧写配置**：写 position 的字面量只出现在持久化那两行（set + last_error）",
        window_text.count('"position"') == 2,
        f'"position" 字面量出现 {window_text.count(chr(34) + "position" + chr(34))} 次',
    )
    check(
        "拖动过程中**没有**任何写盘调用（写盘只经 `_persist_position`，事件处理器不直接调）",
        window_src.count("_persist_position") == 4,
        f"_persist_position 出现 {window_src.count('_persist_position')} 次"
        "（定义 1 + 松手 1 + 换档 1 + persist_position 转发 1）",
    )
    check(
        "命令行 --position 已接上",
        '"--position"' in app_text and "_position_arg" in app_src,
        "app.py 里有 --position 与其解析器",
    )
    check(
        "位置决定顺序的两条分支都在 app.py（命令行 > 上次 > 缺省）",
        "cli_position is not None" in app_src and "has" in app_src,
        "两条分支都在 _resolve_position() 里（位置决定顺序已由 §9 逐条实测）",
    )
    from deskpet import geometry

    check(
        "位置来源文字的真值在 geometry 一处",
        (geometry.POSITION_SOURCE_CLI, geometry.POSITION_SOURCE_CONFIG, geometry.POSITION_SOURCE_DEFAULT)
        == ("命令行指定", "沿用上次", "默认落点"),
        f"{geometry.POSITION_SOURCE_CLI} / {geometry.POSITION_SOURCE_CONFIG} / {geometry.POSITION_SOURCE_DEFAULT}",
    )


# ------------------------------------------------------------ §9 位置决定顺序


def _check_position_decision(app) -> None:
    from deskpet import app as APP
    from deskpet.config import ConfigStore

    section("9. 位置决定顺序：命令行 > 上次保存的 > 缺省落点")

    config_path = _fresh_dir(SCRATCH) / "config.json"

    def store_with(payload: dict | None) -> ConfigStore:
        if payload is None:
            config_path.write_text("{}", encoding="utf-8")
        else:
            config_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        store = ConfigStore(config_path)
        store.load()
        return store

    pos, source = APP._resolve_position((10, 20), store_with({"position": "999,999"}))
    check("命令行优先", pos == (10, 20) and source == "命令行指定", f"{pos} {source}")

    pos, source = APP._resolve_position(None, store_with({"position": "1200,600"}))
    check("其次：上次保存的", pos == (1200, 600) and source == "沿用上次", f"{pos} {source}")

    pos, source = APP._resolve_position(None, store_with(None))
    check("都没有 → None（走缺省落点）", pos is None and source == "默认落点", f"{pos} {source}")

    pos, source = APP._resolve_position(None, store_with({"position": ""}))
    check(
        "position 是空串 → 缺省落点、来源'默认落点'（空串 = 没表态）",
        pos is None and source == "默认落点",
        f"{pos} {source}",
    )
    for bad in ("abc", "1,2,3", "1", "1.5,2"):
        pos, source = APP._resolve_position(None, store_with({"position": bad}))
        check(
            f"配置里 position={bad!r} → 忽略 + 缺省落点，不报错",
            pos is None and source == "默认落点",
            f"{pos} {source}",
        )

    # `has()` 的意义：值**恰好**是缺省落点时也必须显示"沿用上次"。
    # 用真屏幕算一次缺省落点，写进配置，再读回来。
    from PySide6.QtGui import QGuiApplication

    from deskpet import geometry

    screen = QGuiApplication.primaryScreen()
    area = (
        screen.geometry().x(),
        screen.geometry().y(),
        screen.geometry().width(),
        screen.geometry().height(),
    )
    default_xy = geometry.default_position(500, 500, area)
    store = store_with({"position": geometry.format_position(*default_xy)})
    pos, source = APP._resolve_position(None, store)
    check(
        "位置**恰好等于**缺省落点时，来源仍是'沿用上次'（用 has()，不是值比较）",
        pos == default_xy and source == "沿用上次",
        f"{pos} {source}",
    )


# ------------------------------------------------------------ §10 拖动事件


def _event(kind: str, local, glob, button=None, buttons=None):
    """造一个真实的 `QMouseEvent`（不模拟系统鼠标，直接投递）。"""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    types = {
        "press": QEvent.MouseButtonPress,
        "move": QEvent.MouseMove,
        "release": QEvent.MouseButtonRelease,
        "dblclick": QEvent.MouseButtonDblClick,
    }
    if button is None:
        button = Qt.LeftButton
    if buttons is None:
        buttons = Qt.LeftButton if kind in ("press", "move") else Qt.NoButton
    return QMouseEvent(
        types[kind],
        QPointF(*local),
        QPointF(*glob),
        button,
        buttons,
        Qt.NoModifier,
    )


def _make_window(spec, **kwargs):
    from deskpet.pet_window import PetWindow

    window = PetWindow(spec, kwargs.pop("level", 6), **kwargs)
    # 位置是"外部决定"的：权威位置由 `show_at()` 写，**不回读窗口坐标**。
    # ⚠ 给的坐标会被夹取（offscreen 的虚拟屏幕只有 800×800，§4.14），
    # 所以下面的期望值一律读 `window.base_position` 而不是写死输入值。
    window.show_at(400, 300)
    return window


def _clamped(window, x: int, y: int):
    """按**当前窗口尺寸 + 当前屏幕**夹一次——测试里算期望值必须走同一条规则。

    ⚠ offscreen 的虚拟屏幕是 **800×800**（§4.14），比真机小得多，
    所以"拖到 (520, 340)"这种期望值在 offscreen 下会被夹住。
    **别为了让断言变绿去改产品逻辑**：这里用产品自己的夹取函数算期望。
    """
    from deskpet import geometry

    width, height = window.pixel_size
    return geometry.clamp_position(x, y, width, height, window._screen_area())


def _check_drag_events(app) -> None:
    from PySide6.QtCore import QPoint, Qt

    section("10. 拖动事件：跟手 / 切动作 / 松手回 idle / 信号")

    spec = _load_spec()
    # ⚠ 用**第 1 级**（125×125）：offscreen 的虚拟屏幕只有 800×800（§4.14），
    # 第 6 级（500×500）在它上面几乎无处可动（最大左上角就是 (300,300)），
    # "位置变了"会正确地判定"没动"（§4.11 的教训：断言依赖时序/空间时要先留出余地）。
    window = _make_window(spec, level=1)
    started: list[int] = []
    finished: list[QPoint] = []
    moves: list[QPoint] = []
    window.drag_controller.drag_started.connect(lambda: started.append(1))
    window.drag_controller.drag_finished.connect(lambda pos: finished.append(QPoint(pos)))
    window.position_changed.connect(lambda pos: moves.append(QPoint(pos)))

    check("初始不是拖动中", window.dragging is False)

    # 按下：局部 (50,60)，全局 (450,360)。窗口实际位置以权威位置为准
    # （`show_at(400,300)` 在 800×800 的 offscreen 屏上会被夹）。
    origin = window.base_position
    press_global = (origin.x() + 50, origin.y() + 60)
    window.mousePressEvent(_event("press", (50, 60), press_global))
    check("按下后进入拖动", window.dragging is True)
    check("发出 drag_started", len(started) == 1, f"{len(started)}")
    check(
        "按下时的基准位置是权威位置（不是回读的窗口坐标）",
        window.drag_controller.state.base_pos == origin,
        f"{window.drag_controller.state.base_pos} vs {origin}",
    )
    check(
        "指针变成'移动'形状",
        window.cursor().shape() == Qt.SizeAllCursor,
        f"{window.cursor().shape()}",
    )

    # 移动 +120 / +40（用全局坐标算，与按下点的差就是位移）。
    move_global = (press_global[0] + 120, press_global[1] + 40)
    window.mouseMoveEvent(_event("move", (170, 100), move_global))
    check(
        "拖动跟手：位置 = 基准 + 位移（再按屏幕夹取）",
        window.base_position == QPoint(*_clamped(window, origin.x() + 120, origin.y() + 40)),
        f"{window.base_position}（期望 {QPoint(*_clamped(window, origin.x() + 120, origin.y() + 40))}）",
    )
    check(
        "向右拖 → running_right",
        window.animation.action == "running_right",
        f"{window.animation.action}",
    )
    check("位置变了 → 发出 position_changed", len(moves) >= 1, f"{len(moves)}")

    # 向左拖（回到按下点左侧）。
    window.mouseMoveEvent(_event("move", (0, 100), (press_global[0] - 100, press_global[1])))
    check(
        "向左拖 → running_left",
        window.animation.action == "running_left",
        f"{window.animation.action}",
    )

    # 纯竖直：动作不变（沿用上次方向）。
    before = window.animation.action
    window.mouseMoveEvent(_event("move", (0, 200), (press_global[0] - 100, press_global[1] + 100)))
    check(
        "纯竖直移动不切动作（沿用上次方向）",
        window.animation.action == before,
        f"{before} → {window.animation.action}",
    )

    # 松手。
    final_global = (press_global[0] - 100, press_global[1] + 100)
    window.mouseReleaseEvent(_event("release", (0, 200), final_global, buttons=Qt.NoButton))
    check("松手后退出拖动", window.dragging is False)
    check(
        "松手回 idle",
        window.animation.action == "idle",
        f"{window.animation.action}",
    )
    check("发出 drag_finished", len(finished) == 1, f"{len(finished)}")
    check(
        "drag_finished 带的是**夹取后**的最终位置",
        finished[0] == window.base_position,
        f"{finished[0]} vs {window.base_position}",
    )
    check(
        "松手还原指针",
        window.cursor().shape() != Qt.SizeAllCursor,
        f"cursor={window.cursor().shape()}",
    )

    # 拖动途中双击被忽略。
    window.mouseDoubleClickEvent(_event("dblclick", (50, 60), (450, 360)))
    check("不在拖动中时双击不报错", True)


def _check_drag_writes_config(app) -> None:
    from PySide6.QtCore import QPoint, Qt

    from deskpet import geometry
    from deskpet.config import ConfigStore

    section("11. 松手写配置：只在松手/换档写，写的是夹取后的值")

    spec = _load_spec()
    scratch = _fresh_dir(SCRATCH / "cfg1")
    path = scratch / "config.json"
    path.write_text("{}", encoding="utf-8")
    store = ConfigStore(path)
    store.load()

    window = _make_window(spec, config=store, level=1)  # 第 1 级：125×125
    window.mousePressEvent(_event("press", (5, 5), (405, 305)))
    window.mouseMoveEvent(_event("move", (200, 100), (600, 400)))
    check(
        "拖动过程中**没有**写盘（松手前 config.json 里没有 position）",
        "position" not in json.loads(path.read_text(encoding="utf-8")),
        f"{json.loads(path.read_text(encoding='utf-8'))}",
    )
    window.mouseReleaseEvent(_event("release", (200, 100), (600, 400), buttons=Qt.NoButton))

    payload = json.loads(path.read_text(encoding="utf-8"))
    check(
        "松手后 position 落盘",
        "position" in payload,
        f"position={payload.get('position')!r}",
    )
    check(
        "写的是权威位置（夹取后）",
        payload.get("position") == geometry.format_position(*(
            window.base_position.x(), window.base_position.y()
        )),
        f"{payload.get('position')!r} vs {geometry.format_position(window.base_position.x(), window.base_position.y())!r}",
    )
    check(
        "格式是 \"x,y\"",
        isinstance(payload.get("position"), str) and payload["position"].count(",") == 1,
        f"{payload.get('position')!r}",
    )

    # 拖到屏幕外 → 写进配置的必须是**夹取后**的值。
    window2 = _make_window(spec, config=store, level=1)
    window2.mousePressEvent(_event("press", (5, 5), (405, 305)))
    area = window2._screen_area()
    far_x = area[0] + area[2] + 5000
    far_y = area[1] + area[3] + 5000
    window2.mouseMoveEvent(_event("move", (5, 5), (far_x, far_y)))
    window2.mouseReleaseEvent(_event("release", (5, 5), (far_x, far_y), buttons=Qt.NoButton))
    pos = window2.base_position
    width, height = window2.pixel_size
    payload2 = json.loads(path.read_text(encoding="utf-8"))
    check(
        "拖到屏幕外后：权威位置在屏内",
        area[0] <= pos.x() <= area[0] + area[2] - width
        and area[1] <= pos.y() <= area[1] + area[3] - height,
        f"pos={pos} area={area} size={width}x{height}",
    )
    check(
        "配置里存的是**夹取后**的值（重启后仍在屏内）",
        payload2.get("position") == f"{pos.x()},{pos.y()}",
        f"{payload2.get('position')!r} vs {pos.x()},{pos.y()}",
    )

    # (99999, 99999) / (-500, -500) 直接写进配置 → 启动后夹到屏内。
    for raw in ("99999,99999", "-500,-500"):
        path.write_text(json.dumps({"position": raw}), encoding="utf-8")
        fresh = ConfigStore(path)
        fresh.load()
        wx, wy = geometry.parse_position(fresh.get("position"))
        cx, cy = geometry.clamp_position(wx, wy, width, height, area)
        check(
            f"配置里 {raw} → 夹到屏内 ({cx}, {cy})",
            area[0] <= cx <= area[0] + area[2] - width
            and area[1] <= cy <= area[1] + area[3] - height,
            f"({cx}, {cy})",
        )


def _check_manual_and_idle_wiring(app) -> None:
    import time as _time

    from PySide6.QtCore import Qt

    section("12. 与静置逻辑的挂起接口 + manual 坐姿被拖动退出")

    from deskpet.idle_state import IdleStateMachine, Stage

    spec = _load_spec()
    box = {"t": 20}
    machine = IdleStateMachine(lambda: box["t"])
    window = _make_window(spec, idle_state=machine)

    # ⚠ 喂假时间必须"从小往大推进，且不与真实时钟混喂"（§4.11）。
    # 拖动路径上的调用（`set_suspended()`）不传时间 → 状态机读自己的单调时钟，
    # 所以 `enter_manual` 的那个假时刻必须取**真实时刻之后**，
    # 否则它会被单调钳制顶回去、后面的断言就没意义了。
    base = _time.monotonic() + 1.0

    # 进入 manual 坐姿。
    machine.enter_manual(now=base)
    check("manual 已进入", machine.manual is True and window.manual_rest is True)
    check("阶段是 rest", machine.stage is Stage.REST, f"{machine.stage}")
    check("动作切到 rest", window.animation.action == "rest", f"{window.animation.action}")

    # 拖动：manual 必须被退出，动作控制权被挂起。
    window.mousePressEvent(_event("press", (5, 5), (405, 305)))
    check("拖动开始后挂起生效（动作控制权归拖动逻辑）", machine.suspended is True)
    check("manual 被拖动退出", machine.manual is False, f"manual={machine.manual}")
    check(
        "退出 manual 后静置计时归零（不是立刻又坐下）",
        machine.stage is Stage.IDLE,
        f"{machine.stage}",
    )

    # 拖动中静置逻辑不许切动作。
    # ⚠ 拖动期间 `_next_at` 是 `None`（挂起中），所以喂多大的时间都不该升级。
    # 用**真实时刻**喂（拖动路径上的调用本来就不传时间，混喂假时间会把
    # 恢复时钉下的期限污染掉，§4.11）。
    machine.on_tick(now=_time.monotonic())
    machine.on_tick(now=_time.monotonic() + 0.05)
    check(
        "拖动中 tick 再久也不切阶段（挂起生效，动作不会闪）",
        machine.stage is Stage.IDLE,
        f"{machine.stage}",
    )

    release_at = _time.monotonic()
    window.mouseReleaseEvent(_event("release", (5, 5), (405, 305), buttons=Qt.NoButton))
    check("松手后挂起解除", machine.suspended is False)
    check("松手回 idle", window.animation.action == "idle", f"{window.animation.action}")

    # 松手后计时**从头开始**：要重新等满 idle_seconds 才张望。
    # ⚠ `set_suspended(False)` 不传时间 → 它把期限钉在**真实单调时钟**上
    # （`_rearm(current)` 用的是 `_now(None)`）。所以这里**读它钉下来的期限**
    # 当基准，而不是自己猜一个 `now`（猜的那一个可能早于钉期限的时刻，
    # 于是"再等 20 秒"其实还没到）。§4.11："不与真实时钟混喂"。
    deadline = machine.next_change_at
    check(
        "松手后重新排了一个期限（计时从头开始）",
        deadline is not None,
        f"next_change_at={deadline}",
    )
    check(
        "期限 ≈ 松手时刻 + idle_seconds（**不是**从按下时算，也不是从更早的 base 算）",
        abs(deadline - box["t"] - release_at) < 2.0,
        f"deadline − T = {deadline - box['t']:.3f}，松手时刻 = {release_at:.3f}",
    )

    machine.on_tick(now=deadline - box["t"] + 0.1)
    check(
        "刚松手时还没到张望时间",
        machine.stage is Stage.IDLE,
        f"{machine.stage}",
    )
    machine.on_tick(now=deadline - 0.5)
    check(
        "差一点还不够",
        machine.stage is Stage.IDLE,
        f"deadline−0.5",
    )
    machine.on_tick(now=deadline + 0.1)
    check(
        "等满 idle_seconds 后张望（计时从头开始，不是从按下时算）",
        machine.stage is Stage.WAITING,
        f"{machine.stage}",
    )


def _check_user_position_survives_level_change(app) -> None:
    from PySide6.QtCore import QPoint, Qt

    section("13. 用户拖动过之后，切档不弹回右下角")

    spec = _load_spec()
    window = _make_window(spec, level=4)

    # **没有**拖动过：`user_positioned` 为假（位置是外部给的）。
    check("未拖动时 user_positioned 为假", window.user_positioned is False)

    origin = window.base_position
    window.mousePressEvent(_event("press", (5, 5), (origin.x() + 5, origin.y() + 5)))
    # 位移用**全局坐标之差**算：按下点是 (origin+5)，这次移到 (500,350)。
    window.mouseMoveEvent(_event("move", (100, 50), (500, 350)))
    window.mouseReleaseEvent(
        _event("release", (100, 50), (500, 350), buttons=Qt.NoButton)
    )
    check("拖动过之后 user_positioned 为真", window.user_positioned is True)
    dragged_to = window.base_position
    # 期望值 = 基准位置 + 累计位移，再按**当时**的窗口尺寸夹取。
    expect = _clamped(window, origin.x() + (500 - (origin.x() + 5)), origin.y() + (350 - (origin.y() + 5)))
    check(
        "位置确实是用户拖出来的那个（基准 + 位移，按屏幕夹取后）",
        dragged_to == QPoint(*expect),
        f"{dragged_to}（期望 {QPoint(*expect)}）",
    )

    # 切档：只做右下角锚点保护，**绝不重新算缺省落点**。
    before = window.base_position
    width_before, height_before = window.pixel_size
    anchor = (before.x() + width_before, before.y() + height_before)
    window.set_size_level(8)
    after = window.base_position
    width_after, height_after = window.pixel_size
    check(
        "切档后右下角锚点不动（仍以用户位置为准）",
        (after.x() + width_after, after.y() + height_after) == anchor,
        f"锚点 {anchor} → ({after.x() + width_after}, {after.y() + height_after})",
    )
    # 缺省落点在**当前屏幕**上是哪儿——用同一个函数算，别写死。
    from deskpet import geometry

    default_xy = geometry.default_position(width_after, height_after, window._screen_area())
    check(
        "切档后位置**没有**跳回缺省落点",
        after != QPoint(*default_xy),
        f"{after}（缺省落点是 {QPoint(*default_xy)}）",
    )
    check("切档后 user_positioned 仍为真", window.user_positioned is True)
    check(
        "切档后位置在屏内（夹取生效）",
        _clamped(window, after.x(), after.y()) == (after.x(), after.y()),
        f"{after}",
    )


def _check_no_temp_files(app) -> None:
    section("14. 拖动不产生满地临时文件")

    from PySide6.QtCore import Qt

    spec = _load_spec()
    scratch = _fresh_dir(SCRATCH / "tmpcheck")
    from deskpet.config import ConfigStore

    path = scratch / "config.json"
    path.write_text("{}", encoding="utf-8")
    store = ConfigStore(path)
    store.load()

    window = _make_window(spec, config=store)
    window.mousePressEvent(_event("press", (5, 5), (405, 305)))
    # 拖 60 次（模拟"拖动过程中逐帧来事件"）。
    for step in range(1, 61):
        window.mouseMoveEvent(_event("move", (5, 5), (405 + step * 3, 305 + step)))
    window.mouseReleaseEvent(_event("release", (5, 5), (405 + 180, 305 + 60), buttons=Qt.NoButton))

    leftovers = sorted(p.name for p in scratch.iterdir())
    check(
        "松手后目录里只有 config.json（没有 .tmp 残留）",
        leftovers == ["config.json"],
        f"{leftovers}",
    )


if __name__ == "__main__":
    # 无 app 的那条必须在**有** app 之前跑（它自己起子进程）。
    raise SystemExit(main())
