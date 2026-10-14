"""task04 自检：尺寸档位与缩放。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task04_check.txt`，UTF-8），不靠重定向——进程会在文件可读之前退出（§4.3）。

跑法（必须带 UTF-8 环境变量，否则中文输出在 GBK 控制台会炸，§4.11）：

    python tools/check_task04.py

**不写用户真实目录**（§4.11）：需要配置的用例把 `ConfigStore` 指向自检根下的临时路径，
窗口相关的用例全部走 offscreen 平台插件，不弹真窗口。
"""

import os
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写，
# 在 %TEMP% 下建目录会被拒绝（agent.md §4.13 最后一条）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# offscreen 平台插件：不弹窗口。注意它**不支持 setMask**（§4.9），
# 所以本脚本**不断言"遮罩生效"**，只断言区域算得出来。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**），别把自检产物留在 `tools/` 里当垃圾。
RESULT_FILE = ROOT / "data" / "task04_check.txt"

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


def _check_real_screen() -> int:
    """真平台上验"每一级都完整可见"——**必须用真屏幕**。

    offscreen 的虚拟屏幕是 800×800，装不下第 10 级（1000×1000），
    所以这条验收只能在真平台上跑（`python tools/check_task04.py --real`）。
    它会真的建一个 `PetWindow` 并逐级 `set_size_level()` 读回**权威位置**，
    不弹可见窗口（不 `show()`）。
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from deskpet import paths, pet_loader, size
    from deskpet.pet_window import PetWindow

    with paths.override_dirs(program=ROOT, resource=ROOT):
        app = QApplication.instance() or QApplication([])
        spec = pet_loader.resolve_pet(None, None).spec
        if spec is None:
            check("真平台：解出默认素材包", False, "默认形象不可用")
            return _finish()

        window = PetWindow(spec, size.DEFAULT_LEVEL)
        window.showing_at_default_position()
        screen = QGuiApplication.primaryScreen().geometry()
        area = (screen.x(), screen.y(), screen.width(), screen.height())

        rows = []
        ok_all = True
        for level in range(1, size.LEVEL_COUNT + 1):
            window.set_size_level(level)
            pos = window.base_position
            width, height = window.pixel_size
            inside = (
                pos.x() >= area[0]
                and pos.y() >= area[1]
                and pos.x() + width <= area[0] + area[2]
                and pos.y() + height <= area[1] + area[3]
            )
            ok_all = ok_all and inside
            rows.append(f"L{level}:({pos.x()},{pos.y()}) {width}x{height} {'in' if inside else 'OUT'}")

        check(
            f"真平台 {area[2]}x{area[3]}：1..10 级全部完整可见",
            ok_all,
            " / ".join(rows),
        )
        return _finish()


def main() -> int:
    from deskpet import paths
    from PySide6.QtWidgets import QApplication

    if "--real" in sys.argv:
        # 真平台：**不要**设 offscreen（本模块开头只 setdefault，
        # 显式 --real 时把已设的 offscreen 撤掉）。
        os.environ.pop("QT_QPA_PLATFORM", None)
        return _check_real_screen()

    # "程序目录 = 被启动的那个脚本所在目录"（agent.md §4.13）：本脚本住在 `tools/`，
    # 不修正的话 `resource_dir()` 会算出 `<仓库>/tools`，`assets/` 自然找不到。
    # 用 `paths.override_dirs()` 把两个目录钉在仓库根——**这正是它存在的用途**，
    # 不要去改路径规则本身。
    with paths.override_dirs(program=ROOT, resource=ROOT):
        return _run_checks(QApplication)


def _run_checks(QApplication) -> int:
    from PySide6.QtGui import QGuiApplication

    from deskpet import pet_loader, rendering, size
    from deskpet.config import DEFAULT_SIZE_LEVEL, DEFAULTS, ConfigStore
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])

    # ------------------------------------------------------------ §1 档位表
    section("1. 档位表（10 级 / 两两不同 / 严格递增）")
    cell = (500, 500)
    sizes = [size.pixel_size(cell, level) for level in range(1, size.LEVEL_COUNT + 1)]
    heights = [item[1] for item in sizes]

    check("LEVEL_COUNT 是 10", size.LEVEL_COUNT == 10, f"实际 {size.LEVEL_COUNT}")
    check("档位表有 10 项", len(size.LEVEL_RATIOS) == 10, f"实际 {len(size.LEVEL_RATIOS)}")
    check(
        "10 级像素尺寸两两不同",
        len(set(sizes)) == size.LEVEL_COUNT,
        f"去重后剩 {len(set(sizes))} 个",
    )
    check(
        "10 级像素高度严格递增",
        heights == sorted(heights) and len(set(heights)) == len(heights),
        f"高度表 {heights}",
    )
    check(
        "倍数显示是 0.25 … 2.00",
        [size.ratio_text(lv) for lv in range(1, 11)]
        == ["0.25", "0.33", "0.50", "0.67", "0.75", "1.00", "1.25", "1.33", "1.50", "2.00"],
        f"实际 {[size.ratio_text(lv) for lv in range(1, 11)]}",
    )

    # ------------------------------------------------------------ §2 标定基准
    section("2. 标定基准（只读常量，不读当前屏幕）")
    cal = size.CALIBRATION
    logical = size.logical_screen_height(int(cal["screen_height"]), float(cal["dpi_scale"]))
    calibrated = size.level_for_screen(logical, (int(cal["cell"]), int(cal["cell"])))
    check(
        "CALIBRATION 选出它自己声明的档位",
        calibrated == cal["level"],
        f"{cal['screen_width']}x{cal['screen_height']} @dpi{cal['dpi_scale']} "
        f"→ 逻辑高 {logical} → 第 {calibrated} 级（声明第 {cal['level']} 级）",
    )
    check(
        "DEFAULT_LEVEL 与 config 的默认值同源",
        DEFAULT_SIZE_LEVEL == size.DEFAULT_LEVEL == DEFAULTS["size_level"],
        f"size.DEFAULT_LEVEL={size.DEFAULT_LEVEL} config={DEFAULT_SIZE_LEVEL} "
        f"DEFAULTS={DEFAULTS['size_level']}",
    )

    # ------------------------------------------------------------ §3 单调性 / 夹取
    section("3. 屏幕自适应单调性 + 档位夹取")
    heights_seq = [size.level_for_screen(h, cell) for h in (720, 768, 864, 900, 1080, 1200, 1440, 1600, 2160)]
    check(
        "屏幕越高，选出的档位不得变小",
        heights_seq == sorted(heights_seq),
        f"720→2160 依次得到 {heights_seq}",
    )
    check(
        "clamp_level 夹到 1..10",
        [size.clamp_level(v) for v in (0, -5, 1, 10, 11, 99)] == [1, 1, 1, 10, 10, 10],
        f"实际 {[size.clamp_level(v) for v in (0, -5, 1, 10, 11, 99)]}",
    )
    check(
        "is_valid_level 拒绝边界外的值",
        not size.is_valid_level(0)
        and not size.is_valid_level(11)
        and not size.is_valid_level(True)
        and size.is_valid_level(1)
        and size.is_valid_level(10),
        "0/11/bool 拒，1/10 收",
    )

    # ------------------------------------------------------------ §4 锚点与夹取
    section("4. 右下角锚点 + 越界夹取")
    anchor = size.anchor_of(2036, 916, 500, 500)
    check(
        "anchor_of 算出右下角",
        (anchor.x, anchor.y) == (2536, 1416),
        f"左上(2036,916)+500x500 → ({anchor.x},{anchor.y})",
    )
    check(
        "anchor.top_left_for 反推左上角",
        anchor.top_left_for(1000, 1000) == (1536, 416),
        f"实际 {anchor.top_left_for(1000, 1000)}",
    )
    check(
        "越界位置被夹回屏幕内",
        size.clamp_position(99999, 99999, 250, 250, (0, 0, 2560, 1440)) == (2310, 1190),
        f"实际 {size.clamp_position(99999, 99999, 250, 250, (0, 0, 2560, 1440))}",
    )
    check(
        "负的越界位置被夹回左上角",
        size.clamp_position(-500, -500, 250, 250, (0, 0, 2560, 1440)) == (0, 0),
        f"实际 {size.clamp_position(-500, -500, 250, 250, (0, 0, 2560, 1440))}",
    )

    # ------------------------------------------------------------ §5 渲染与缓存
    section("5. 渲染器：尺寸对齐 + 缓存键 + 上限")
    spec = pet_loader.resolve_pet(None, None).spec
    if spec is None:
        check("解出默认素材包", False, "连默认形象都不可用，后续用例跳过")
        return _finish()

    renderer = rendering.FrameRenderer(spec, 1.0)
    frame0 = renderer.frame("idle", 0, 1)
    check(
        "第 1 级渲染出的尺寸 == pixel_size(cell, 1)",
        (frame0.width(), frame0.height()) == size.pixel_size(spec.cell, 1),
        f"图 {frame0.width()}x{frame0.height()}，期望 {size.pixel_size(spec.cell, 1)}",
    )
    frame10 = renderer.frame("idle", 0, 10)
    check(
        "第 10 级渲染出的尺寸 == pixel_size(cell, 10)",
        (frame10.width(), frame10.height()) == size.pixel_size(spec.cell, 10),
        f"图 {frame10.width()}x{frame10.height()}，期望 {size.pixel_size(spec.cell, 10)}",
    )
    check(
        "缓存键含档位（不同档位是不同键）",
        renderer.cache_key("idle", 0, 1) != renderer.cache_key("idle", 0, 10),
        f"{renderer.cache_key('idle', 0, 1)} vs {renderer.cache_key('idle', 0, 10)}",
    )
    check(
        "缓存键含动作与帧号",
        len({renderer.cache_key("idle", 0, 6), renderer.cache_key("rest", 0, 6),
             renderer.cache_key("idle", 1, 6)}) == 3,
        "动作 / 帧号 / 档位三个维度都进键",
    )
    before = renderer.hits
    renderer.frame("idle", 0, 1)
    check("同一键第二次取命中缓存", renderer.hits == before + 1, f"hits {before}→{renderer.hits}")

    # 缓存上限：来回切档 20 次（1↔10），缓存不得无限增长。
    from deskpet.rendering import DEFAULT_CACHE_LIMIT

    small = rendering.FrameRenderer(spec, 1.0, limit=4)
    for i in range(20):
        small.frame("idle", i % 7, 1 if i % 2 else 10)
    check(
        "缓存条数不超过上限",
        small.cached_count() <= 4,
        f"上限 4，实际 {small.cached_count()}",
    )
    check("DEFAULT_CACHE_LIMIT 是个正数", DEFAULT_CACHE_LIMIT > 0, f"实际 {DEFAULT_CACHE_LIMIT}")

    # ------------------------------------------------------------ §6 窗口
    section("6. 窗口：窗口尺寸 == 内容尺寸 + 切档锚点漂移为 0")
    window = PetWindow(spec, 6)
    check(
        "窗口尺寸 == 档位像素尺寸（没有留白）",
        (window.width(), window.height()) == window.pixel_size,
        f"窗口 {window.width()}x{window.height()} 档位尺寸 {window.pixel_size}",
    )
    check(
        "帧图尺寸 == 窗口尺寸（没有裁边）",
        (window.frame_image.width(), window.frame_image.height())
        == (window.width(), window.height()),
        f"帧 {window.frame_image.width()}x{window.frame_image.height()} "
        f"窗口 {window.width()}x{window.height()}",
    )

    # 摆到默认落点，再逐级切档，比对右下角漂移。
    #
    # ⚠ offscreen 平台插件给的虚拟屏幕只有 800×800，而第 10 级是 1000×1000 ——
    # **物理上放不下**。所以：
    #   ① "锚点漂移为 0" 必须在一个**装得下所有档位**的屏幕矩形上验，
    #      这里直接喂给 `window._move_clamped` 用的同一套 `size.clamp_position`；
    #   ② "每一级都完整可见" 在 offscreen 下**没有意义**，挪到真平台跑（见 `--real`）。
    screen_area = window._screen_area()
    big_area = (0, 0, 2560, 1440)  # 足够容纳第 10 级的 1000×1000
    window.move_to(0, 0)
    anchors: list[tuple[int, int]] = []
    for level in range(1, size.LEVEL_COUNT + 1):
        # 用与产品同一条路径摆放：锚点在切档前取，切完档夹到屏幕内。
        window.move_to(0, 0)
        window.set_size_level(6)
        width, height = window.pixel_size
        window.move_to(*size.clamp_position(1000, 800, width, height, big_area))
        anchor_before = size.anchor_of(
            window.base_position.x(), window.base_position.y(), width, height
        )

        window.set_size_level(level)
        width, height = window.pixel_size
        left, top = size.clamp_position(
            *anchor_before.top_left_for(width, height), width, height, big_area
        )
        window.move_to(left, top)
        anchors.append((left + width, top + height))

    drift = len(set(anchors))
    check(
        "1..10 逐级切档，右下角漂移为 0",
        drift == 1,
        f"10 级得到 {drift} 个不同的右下角：{anchors[0]} … {anchors[-1]}",
    )
    check(
        "在装得下的屏幕上，每一级都完整落在屏幕内",
        all(0 <= a[0] <= big_area[2] and 0 <= a[1] <= big_area[3] for a in anchors),
        f"屏幕 {big_area[2]}x{big_area[3]}，右下角 {anchors[0]} … {anchors[-1]}",
    )
    check(
        "档位变大时左上角向『左上』移动（脚底与右边不动）",
        all(
            anchors[0][0] - size.pixel_size(cell, lv)[0]
            > anchors[0][0] - size.pixel_size(cell, lv + 1)[0]
            for lv in range(1, size.LEVEL_COUNT)
        ),
        f"第 1 级左上角 x={anchors[0][0] - size.pixel_size(cell, 1)[0]}，"
        f"第 10 级 x={anchors[0][0] - size.pixel_size(cell, 10)[0]}",
    )
    if screen_area[2] > 0:
        _LINES.append(
            f"    （注：本平台虚拟屏幕 {screen_area[2]}×{screen_area[3]}；"
            f"offscreen 插件给的是 800×800，装不下第 10 级，故上面的『完整可见』"
            f"在 2560×1440 的矩形上验，真平台另见 --real）"
        )

    # 反复切 20 次：不抛异常，且仍锚在同一处。
    try:
        for i in range(20):
            window.set_size_level(1 if i % 2 else 10)
        repeated = True
    except Exception as exc:  # pragma: no cover
        repeated = False
        _LINES.append(f"    切档抛异常：{type(exc).__name__}: {exc}")
    check("反复切档 20 次不抛异常", repeated)

    # ------------------------------------------------------------ §7 配置判定
    section("7. 档位来源：命令行 > 沿用上次 > 按屏幕自适应")
    from deskpet.app import (
        LEVEL_SOURCE_CLI,
        LEVEL_SOURCE_CONFIG,
        LEVEL_SOURCE_SCREEN,
        _resolve_size_level,
    )

    store = ConfigStore(ROOT / "tools" / "_task04_config.json")
    store.load()  # 文件不存在 → 全部默认值，且 has() 全假
    level, source = _resolve_size_level(None, store, cell)
    check(
        "没设过 → 按屏幕自适应",
        source == LEVEL_SOURCE_SCREEN and level == size.level_for_screen(
            int(QGuiApplication.primaryScreen().geometry().height()), cell
        ),
        f"得到第 {level} 级，来源 {source}",
    )

    # 设成**默认值**也必须显示"沿用上次"（§2.4 规则 5 的用处）。
    store.set("size_level", size.DEFAULT_LEVEL)
    level, source = _resolve_size_level(None, store, cell)
    check(
        "设成默认档位 → 仍是『沿用上次』而非『自适应』",
        source == LEVEL_SOURCE_CONFIG and level == size.DEFAULT_LEVEL,
        f"得到第 {level} 级，来源 {source}",
    )

    level, source = _resolve_size_level(10, store, cell)
    check(
        "命令行优先于配置",
        source == LEVEL_SOURCE_CLI and level == 10,
        f"得到第 {level} 级，来源 {source}",
    )

    # 清理自检自己造的配置文件（不留垃圾，§2.3）。
    for leftover in (
        ROOT / "tools" / "_task04_config.json",
        ROOT / "tools" / ".task04_config.json.tmp",
    ):
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass

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
    except OSError as exc:  # pragma: no cover - 写不出结果不该让自检本身失败
        shown = f"（结果写不出去：{exc}）"
    print(_LINES[-1])
    print(f"结果已写入：{shown}")
    # 退出码：有失败才非 0（§4.11：非 0 不等于有断言失败，先看最后一行）。
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
