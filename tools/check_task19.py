"""task19 自检（offscreen）：**键盘配置界面**——布局下拉、键位网格、录入与重置。

按 agent.md A.3：**一个脚本、共用一个 `check()、按节输出**。
结果**写文件**（`data/task19_check.txt，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task19.py

## 这一份验什么（对应 task19 §6）

1. **布局下拉只列素材包里真的有的**；没有 `keyview 的包 → 整组禁用 + 提示；
2. **网格行内顺序 == 画面左→右**（2 行 8 列，逐格比对键 id）；
3. **录入**：按 `F → 该键位显示 `F；再点 → `Esc → 值不变；
4. **单独按 Ctrl** → **接受**（task20 改：键位映射支持修饰键单独成键；组合录入那边仍然拒绝）；
5. **同一个键绑到两个键位** → 两个都亮，且不互相解绑；
6. **重置**：确认 → `keyview_keys 变空对象、网格全变「未设置」；取消 → 什么都不变；
7. **空映射时重置按钮禁用**；
8. **写配置失败** → 网格回滚 + 一句提示（注入一个写失败的 store）；
9. **配置里有不存在的键 id** → 被丢弃 + 日志，**且文件没被改写**；
10. **录入期间监听确实停**（轮询计数不增长），退出录入后**恢复**；
11. **切布局** → 窗口尺寸与位置**不变**（共用 `keyview.cell），键集当场变。

## 这一份**不写用户真实目录**

所有落盘都在 `data/task19_out/（工作区内，§4.13）。
`assets/ 一个字节都不动（脚本结尾核对哈希）。
用户真实的 `data/config.json 不碰（全部指向临时目录）。
"""

import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULT_FILE = ROOT / "data" / "task19_check.txt"
OUT_DIR = ROOT / "data" / "task19_out"

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


def _hash_assets() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / "assets").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8", "replace"))
            digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


def _strip_comments(source: str) -> str:
    """去掉注释与文档字符串，只留**可执行的那部分**（源码卫生断言用）。

    为什么要这一步：本项目的注释密度很高，而注释里**恰恰在讨论**那些纪律
    （"界面不许直接持有监听器"这类）。拿整份源码去搜关键字，会让断言
    因为它想证明的纪律**被写下来**而变红——那是**用例脆**，不是回归
    （§4.26 记过同一种毛病：断言要盯意图，不盯字面）。

    做法：先用 `tokenize` 拿 token 流，把 COMMENT 与 STRING 都换成空；
    tokenize 解不开（语法错）时退回原文——那种情况下本来就有更要紧的事。
    """
    import io
    import tokenize

    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except (tokenize.TokenError, IndentationError):  # pragma: no cover
        return source
    return " ".join(pieces)


def _pump(app, ms: int) -> None:
    """跑事件循环 `ms 毫秒（定时器要事件循环才走）。"""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)


# ==================================================================== 测试素材

#: 六个布局**全部**做出来（这样才能验"下拉只列真的有的"与"切布局"）。
ALL_LAYOUTS = ("1*4", "1*6", "1*8", "2*4", "2*6", "2*8")


def _solid(width: int, height: int, color: str):
    from PySide6.QtGui import QColor, QImage

    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


def build_kv_package(namespace: str, layouts=ALL_LAYOUTS, cell=(64, 48)) -> Path:
    """造一个**程序生成的** KV 占位包（落 `data/task19_out/，**不碰 assets/**）。

    `rows 按契约写：**行内顺序 = 画面左→右**，序号**从中间往两边**排
    （4/6 键是 8 键的居中子集，§2.23）。自检就靠"界面第几格 == 这个数组第几项"
    来验顺序，所以这份素材本身就是判据的一半。
    """
    from deskpet import keyview_spec

    root = OUT_DIR / "kv" / namespace
    kvdir = root / "keyview"
    kvdir.mkdir(parents=True, exist_ok=True)
    (root / "frames").mkdir(parents=True, exist_ok=True)

    _solid(cell[0], cell[1], "#204060").save(str(kvdir / "bg.png"), "PNG")
    _solid(cell[0], cell[1], "#3366cc").save(str(root / "frames" / "idle_00.png"), "PNG")

    numbers = keyview_spec.middle_out_numbers(8)
    keys: dict[str, dict] = {}
    for row in (1, 2):
        for column, number in enumerate(numbers):
            key_id = f"r{row}k{number}"
            down = f"key_{key_id}.png"
            _solid(8, 8, "#ffcc00").save(str(kvdir / down), "PNG")
            # "小图 + x/y"写法（另一条路 `overlay 由 task17 验过，这里不重复）。
            keys[key_id] = {"x": 4 + column * 7, "y": 4 + (row - 1) * 10, "down": down}

    layout_index: dict[str, dict] = {}
    for name in layouts:
        rows_count, columns_count = (int(part) for part in name.split("*"))
        # 取**居中子集**：先按 8 列的序号排，再取中间那几个。
        start = (8 - columns_count) // 2
        chosen = numbers[start : start + columns_count]
        rows = []
        for row in range(1, rows_count + 1):
            rows.append([f"r{row}k{number}" for number in chosen])
        layout_index[name] = {"rows": rows}

    index = {
        "name": f"占位{namespace}",
        "description": "task19 自检用的程序生成占位包",
        "cell": [cell[0], cell[1]],
        "actions": {"idle": {"frames": ["idle_00.png"], "fps": 30.0}},
        "keyview": {
            "cell": [cell[0], cell[1]],
            "background": {"normal": ["bg.png"]},
            "keys": keys,
            "layouts": layout_index,
        },
    }
    (root / "asset.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def load_spec(root: Path):
    """按目录读出 KV 规格（`None = 这个包没有可用的 KV）。"""
    from deskpet import pet_loader

    spec, _issues, _warnings = pet_loader.load_keyview(root)
    return spec


def load_pet(root: Path):
    from deskpet import pet_loader

    return pet_loader.load_pet(root)


def make_config(name: str):
    """造一个**指向临时目录**的 `ConfigStore（绝不碰用户真实的 config.json）。"""
    from deskpet.config import ConfigStore

    path = OUT_DIR / "configs" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    store = ConfigStore(path)
    store.load()
    return store


class FailingStore:
    """一个**写一定失败**的 store：验"回滚 + 一句提示"那一条（task19 §6）。

    ⚠ 只包住 `apply()（设置窗口唯一那条写路径），其余照常转发给真 store
    ——这样一个假的写失败**不会**顺带把读、`has()、`last_error() 都弄坏，
    验的仍然是产品那条路径。
    """

    def __init__(self, inner, reason: str = "磁盘假装满了") -> None:
        self._inner = inner
        self._reason = reason
        self.blocked = True

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def apply(self, key, value):
        from deskpet.config import SetResult

        if not self.blocked:
            return self._inner.apply(key, value)
        return SetResult(False, False, self._reason, "OSError")


def press(widget, qt_key: int, vk: int, text: str = "") -> None:
    """给一个控件喂一次按键事件（`nativeVirtualKey 按真平台那样给）。"""
    event = QKeyEvent(QEvent.KeyPress, qt_key, Qt.NoModifier, text)
    event.nativeVirtualKey = lambda: vk  # type: ignore[method-assign]
    widget.keyPressEvent(event)


# ==================================================================== §1 布局下拉


def section_layout_combo(app) -> None:
    """§1：下拉只列素材包里真的有的布局；没有 KV 的包 → 整组禁用 + 提示。"""
    from deskpet import keygrid, settings

    section("§1 布局下拉：只列真的有的；没有 keyview 的包整组禁用")

    root = build_kv_package("kv_all")
    spec = load_spec(root)
    check(
        "占位包解析出六个布局",
        spec is not None and spec.layout_names == ALL_LAYOUTS,
        f"布局={None if spec is None else spec.layout_names}",
    )

    # ---- (1) 只有 2*8 的包：下拉里**只能有** 2*8（不是白名单那六个）
    root2 = build_kv_package("kv_only_28", layouts=("2*8",))
    spec2 = load_spec(root2)
    store = make_config("combo_only")
    window = settings.SettingsWindow(store, current_pet="", keyview=spec2)
    combo = window._widgets["keyview_layout"]
    items = [combo.itemText(index) for index in range(combo.count())]
    check(
        "只有 2*8 的包 → 下拉**只列 2*8**（不是白名单那六个）",
        items == ["2*8"],
        f"下拉项={items}",
    )
    check("下拉可用（这个包有 KV）", combo.isEnabled(), f"enabled={combo.isEnabled()}")
    grid = window._key_grid
    check(
        "网格按这个布局摆出来了（2*8 = 2 行 8 列 = **16** 个键位）",
        grid is not None and len(grid._buttons) == 16,
        f"按钮={0 if grid is None else len(grid._buttons)}",
    )
    window.close()

    # ---- (2) 没有 keyview 的包 → 整组禁用 + 一句说明（与"开机自启"同一种样式）
    store2 = make_config("combo_no_kv")
    window2 = settings.SettingsWindow(store2, current_pet="", keyview=None)
    combo2 = window2._widgets["keyview_layout"]
    grid2 = window2._key_grid
    check(
        "没有 keyview → 下拉**空**且禁用（不是隐藏）",
        combo2.count() == 0 and not combo2.isEnabled(),
        f"项数={combo2.count()} enabled={combo2.isEnabled()}",
    )
    check(
        "没有 keyview → 重置按钮禁用、网格禁用",
        grid2 is not None
        and not grid2._reset_button.isEnabled()
        and not grid2._grid_host.isEnabled(),
        f"重置={None if grid2 is None else grid2._reset_button.isEnabled()}",
    )
    check(
        "没有 keyview → 显示一句「当前素材包没有键盘形态」",
        grid2 is not None and grid2._hint.text() == keygrid.TEXT_NO_KEYVIEW,
        f"提示={None if grid2 is None else grid2._hint.text()!r}",
    )
    check(
        "下拉上挂着同一句说明（鼠标悬停看得到原因）",
        combo2.toolTip() == keygrid.TEXT_NO_KEYVIEW,
        f"tooltip={combo2.toolTip()!r}",
    )
    window2.close()

    # ---- (3) 配置指向一个不存在的布局 → 显示回退后那个 + 一句提示，**不静默改配置**
    store3 = make_config("combo_fallback")
    store3.set("keyview_layout", "1*4")  # 这个包只有 2*8
    window3 = settings.SettingsWindow(store3, current_pet="", keyview=spec2)
    combo3 = window3._widgets["keyview_layout"]
    grid3 = window3._key_grid
    check(
        "配置里的布局不存在 → 下拉显示**回退后**那个（2*8）",
        combo3.currentText() == "2*8",
        f"选中={combo3.currentText()!r}",
    )
    check(
        "回退时显示一句提示（用户要知道自己配的那个没生效）",
        grid3 is not None and "1*4" in grid3._hint.text() and "2*8" in grid3._hint.text(),
        f"提示={None if grid3 is None else grid3._hint.text()!r}",
    )
    check(
        "回退**不静默改配置**（磁盘上还是用户写的那个 1*4）",
        store3.get("keyview_layout") == "1*4",
        f"配置={store3.get('keyview_layout')!r}",
    )
    window3.close()


# ==================================================================== §2 网格顺序


def section_grid_order(app) -> None:
    """§2：网格**行内顺序 == 画面左→右**（逐格比对键 id）。"""
    from deskpet import settings

    section("§2 网格顺序：行内 == 画面左→右（逐格比对键 id）")

    root = build_kv_package("kv_order")
    spec = load_spec(root)
    check("占位包读得出来", spec is not None, "KV 规格")

    store = make_config("order")
    store.set("keyview_layout", "2*8")
    window = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = window._key_grid
    if grid is None or grid.layout is None:
        check("网格建出来了", False, "grid/layout 是 None")
        window.close()
        return

    # ---- 按钮的**界面坐标**（QGridLayout 的行列）必须与 `display_rows` 一致
    # ⚠ task20 起用的是 `display_rows`（= `rows` 旋转 180°）：设置界面是给人看的，
    # 而人的视角与渲染画面**上下左右都相反**（理由见 `LayoutSpec.display_rows`）。
    layout = grid.layout
    mismatches: list[str] = []
    for row_index, row in enumerate(layout.display_rows):
        for column_index, key_id in enumerate(row):
            item = grid._grid.itemAtPosition(row_index, column_index)
            widget = item.widget() if item is not None else None
            if widget is None or grid._buttons.get(key_id) is not widget:
                mismatches.append(f"({row_index},{column_index}) 期望 {key_id}")
    check(
        "**每一格**摆的按钮就是 layout.display_rows 里那个键（2 行 8 列，16 格逐格比对）",
        not mismatches,
        f"不符的格={len(mismatches)}" + (f" 首个={mismatches[0]}" if mismatches else ""),
    )

    check(
        "网格用的是 display_rows（= rows 转 180°），不是 rows 原样",
        tuple(layout.display_rows) != tuple(layout.rows)
        and tuple(layout.display_rows) == tuple(tuple(reversed(r)) for r in reversed(layout.rows)),
        f"display_rows={layout.display_rows}",
    )

    first_row = [grid._buttons[key].toolTip() for key in layout.display_rows[0]]
    check(
        "第一行左→右是 8 6 4 2 1 3 5 7（旋转 180° 后**中间往外**，与 §2.23 一致）",
        first_row == [f"r2k{n}" for n in (8, 6, 4, 2, 1, 3, 5, 7)],
        f"实际={first_row}",
    )

    # ---- 换到 1*4：键位**变少**，且正好是 8 键的居中子集
    store.set("keyview_layout", "1*4")
    window.reload_from_config()
    grid = window._key_grid
    check(
        "换到 1*4 → 网格只剩 4 个键位",
        grid is not None and len(grid._buttons) == 4,
        f"按钮={0 if grid is None else len(grid._buttons)}",
    )
    check(
        "1*4 的键位是 2*8 的**居中子集**（跨布局复用这条前提）",
        grid is not None and set(grid._buttons) == {"r1k3", "r1k1", "r1k2", "r1k4"},
        f"键={sorted(grid._buttons) if grid else None}",
    )
    window.close()


# ==================================================================== §3 录入


def section_capture(app) -> None:
    """§3：录入交互——按 F 录上、Esc 取消、单独 Ctrl 不接受。"""
    from deskpet import settings

    section("§3 录入：按 F / Esc 取消 / 单独按 Ctrl 不接受")

    root = build_kv_package("kv_capture", layouts=("2*8",))
    spec = load_spec(root)
    store = make_config("capture")
    window = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = window._key_grid
    if grid is None:
        check("网格建出来了", False, "grid 是 None")
        window.close()
        return

    written: list[tuple[str, object]] = []
    window.settings_changed.connect(lambda key, value: written.append((key, value)))

    # ---- (1) 按 F → 该键位显示 F + 写进配置
    check(
        "初值：全部是「未设置」",
        all(text == "未设置" for text in grid.button_texts().values()),
        f"文字={sorted(set(grid.button_texts().values()))}",
    )
    check(
        "**空映射时重置按钮禁用**（避免「点了没反应」的困惑）",
        not grid._reset_button.isEnabled(),
        "空映射",
    )
    check("点一个键位进入录入态", grid.begin_capture("r1k1"), "r1k1")
    check(
        "录入态按钮文字变「按下按键…」",
        grid.button("r1k1").display_text == "按下按键…",
        f"文字={grid.button('r1k1').display_text!r}",
    )
    press(grid.button("r1k1"), Qt.Key_F, 0x46, "f")
    check(
        "按 F → 该键位显示 F",
        grid.button("r1k1").display_text == "F",
        f"文字={grid.button('r1k1').display_text!r}",
    )
    check(
        "配置里真的写进去了（键 id -> {vk, name}）",
        store.get("keyview_keys") == {"r1k1": {"vk": 0x46, "name": "F"}},
        f"配置={store.get('keyview_keys')}",
    )
    check(
        "**录完立刻退出录入态**（不用再点一次）",
        not grid.capturing and not grid.button("r1k1").listening,
        f"capturing={grid.capturing}",
    )
    check(
        "settings_changed 发了且带的是 keyview_keys",
        bool(written) and written[-1][0] == "keyview_keys",
        f"信号={written}",
    )
    check("有绑定之后重置按钮**亮起来**", grid._reset_button.isEnabled(), "非空映射")

    # ---- (2) 再点 → Esc → **值不变**
    check("再点同一个键位又进录入态", grid.begin_capture("r1k1"), "r1k1")
    press(grid.button("r1k1"), Qt.Key_Escape, 0x1B)
    check(
        "按 Esc → 退出录入态、**值不变**（还是 F）",
        not grid.capturing and grid.button("r1k1").display_text == "F",
        f"文字={grid.button('r1k1').display_text!r}",
    )
    check(
        "Esc 之后**一个字节都没写**（配置仍是 F）",
        store.get("keyview_keys") == {"r1k1": {"vk": 0x46, "name": "F"}},
        f"配置={store.get('keyview_keys')}",
    )

    # ---- (3) 单独按 Ctrl → **接受**（task20 改；键位映射要它）
    # 用户 2026 明确要求：键位映射支持 Ctrl/Shift/Alt/Win 单独成键。
    # ⚠ 组合录入（`KeyCaptureEdit`，热键那条路）的"单独按修饰键不成立"**不变**，
    # 下面 §3b 那一段专门守它。
    check("进录入态（第二个键位）", grid.begin_capture("r1k2"), "r1k2")
    press(grid.button("r1k2"), Qt.Key_Control, 0x11)
    check(
        "单独按 Ctrl → **录上了**（task20 起支持修饰键单独成键）",
        grid.button("r1k2").display_text == "Ctrl"
        and store.get("keyview_keys").get("r1k2") == {"vk": 0x11, "name": "Ctrl"},
        f"文字={grid.button('r1k2').display_text!r} 配置={store.get('keyview_keys')}",
    )
    check("录完之后**退出录入态**（不是继续等）", not grid.capturing,
          f"capturing={grid.capturing}")

    # Shift 也能单独录（换一个键位）
    check("进录入态（Shift 那个键位）", grid.begin_capture("r1k4"), "r1k4")
    press(grid.button("r1k4"), Qt.Key_Shift, 0x10)
    check(
        "单独按 Shift 也录得上",
        grid.button("r1k4").display_text == "Shift"
        and store.get("keyview_keys").get("r1k4") == {"vk": 0x10, "name": "Shift"},
        f"文字={grid.button('r1k4').display_text!r}",
    )

    # ⚠ 反向：组合录入（热键）那边**必须仍然拒绝**单独修饰键
    # 判据看源码：`KeyCaptureEdit.keyPressEvent` 里仍有"只有修饰键 → rejected"那条。
    cap_src = (ROOT / "src" / "deskpet" / "keycapture.py").read_text(encoding="utf-8")
    edit_body = cap_src.split("class KeyCaptureEdit", 1)[-1]
    check(
        "组合录入（热键）仍然拒绝单独修饰键（两条路要求不同）",
        "REASON_NEED_MODIFIER" in edit_body or "rejected" in edit_body,
        "KeyCaptureEdit 里仍有修饰键拒绝逻辑",
    )
    single_body = cap_src.split("class SingleKeyCaptureButton", 1)[-1].split("class KeyCaptureEdit", 1)[0]
    check(
        "单键录入**不再**把修饰键排除在外（task20）",
        "MODIFIER_QT_KEYS" not in single_body.split("def keyPressEvent", 1)[-1].split("def ", 1)[0],
        "SingleKeyCaptureButton.keyPressEvent 里已无修饰键排除",
    )

    # ---- (4) 没有修饰键的键照样能录（这正是不能复用组合录入的原因）
    check("进录入态（第三个键位）", grid.begin_capture("r1k3"), "r1k3")
    press(grid.button("r1k3"), Qt.Key_Space, 0x20, " ")
    check(
        "没有修饰键的 Space 也录得上（组合录入会拒它，所以不能复用）",
        grid.button("r1k3").display_text == "Space"
        and store.get("keyview_keys").get("r1k3") == {"vk": 0x20, "name": "Space"},
        f"文字={grid.button('r1k3').display_text!r}",
    )

    check(
        "录过的键位都留着（互不覆盖）",
        set(store.get("keyview_keys")) == {"r1k1", "r1k2", "r1k3", "r1k4"},
        f"配置={sorted(store.get('keyview_keys'))}",
    )
    window.close()


# ==================================================================== §4 重复绑定


def section_duplicate(app) -> None:
    """§4：同一个物理键绑到多个键位 → **两个都亮**，不互相解绑（决策 2）。"""
    from deskpet import settings

    section("§4 同一个键绑到两个键位：两个都亮、不互相解绑")

    root = build_kv_package("kv_dup", layouts=("2*8",))
    spec = load_spec(root)
    store = make_config("dup")
    window = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = window._key_grid
    if grid is None:
        check("网格建出来了", False, "grid 是 None")
        window.close()
        return

    grid.begin_capture("r1k1")
    press(grid.button("r1k1"), Qt.Key_J, 0x4A, "j")
    grid.begin_capture("r2k4")
    press(grid.button("r2k4"), Qt.Key_J, 0x4A, "j")

    check(
        "两个键位都显示 J",
        grid.button("r1k1").display_text == "J" and grid.button("r2k4").display_text == "J",
        f"r1k1={grid.button('r1k1').display_text!r} r2k4={grid.button('r2k4').display_text!r}",
    )
    check(
        "**先绑的那个没被解绑**（决策 2：不自动解绑）",
        store.get("keyview_keys").get("r1k1") == {"vk": 0x4A, "name": "J"},
        f"配置={store.get('keyview_keys')}",
    )
    window.close()


# ==================================================================== §5 重置


def section_reset(app, answer_with) -> None:
    """§5：重置——确认 → 清空；取消 → 什么都不变；空映射时禁用。"""
    from deskpet import settings

    section("§5 重置：确认清空 / 取消不动 / 空映射时禁用")

    root = build_kv_package("kv_reset", layouts=("2*8",))
    spec = load_spec(root)
    store = make_config("reset")
    store.set(
        "keyview_keys",
        {"r1k1": {"vk": 0x46, "name": "F"}, "r2k8": {"vk": 0x4A, "name": "J"}},
    )
    window = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = window._key_grid
    if grid is None:
        check("网格建出来了", False, "grid 是 None")
        window.close()
        return

    check(
        "初值：两个键位显示各自的键名、其余「未设置」",
        grid.button("r1k1").display_text == "F"
        and grid.button("r2k8").display_text == "J"
        and grid.button("r1k2").display_text == "未设置",
        f"r1k1={grid.button('r1k1').display_text!r} r2k8={grid.button('r2k8').display_text!r}",
    )

    # ---- (1) **取消** → 什么都不变
    answer_with(NO)
    grid._on_reset_clicked()
    check(
        "取消 → 配置**一个字节都没变**",
        store.get("keyview_keys")
        == {"r1k1": {"vk": 0x46, "name": "F"}, "r2k8": {"vk": 0x4A, "name": "J"}},
        f"配置={store.get('keyview_keys')}",
    )
    check(
        "取消 → 网格照旧（两个键位还是亮着）",
        grid.button("r1k1").display_text == "F" and grid.button("r2k8").display_text == "J",
        "取消不动界面",
    )

    # ---- (2) **确认** → 一次点击释放全部映射
    changed: list[tuple[str, object]] = []
    window.settings_changed.connect(lambda key, value: changed.append((key, value)))
    answer_with(YES)
    grid._on_reset_clicked()
    check(
        "确认 → keyview_keys 变成**空对象**",
        store.get("keyview_keys") == {},
        f"配置={store.get('keyview_keys')}",
    )
    check(
        "确认 → 网格**全部**回到「未设置」",
        all(text == "未设置" for text in grid.button_texts().values()),
        f"文字集合={sorted(set(grid.button_texts().values()))}",
    )
    check(
        "确认 → 重置按钮**又禁用**（现在是空映射了）",
        not grid._reset_button.isEnabled(),
        "空映射",
    )
    check(
        "确认 → 发了 settings_changed（监听层据此**立刻**生效，不用重启）",
        bool(changed) and changed[-1] == ("keyview_keys", {}),
        f"信号={changed}",
    )
    window.close()


# ==================================================================== §6 写失败


def section_write_failure(app, answer_with) -> None:
    """§6：写配置失败 → 网格**回滚** + 一句提示（注入一个写失败的 store）。"""
    from deskpet import settings

    section("§6 写失败：网格回滚到配置里的真值 + 状态栏带原因")

    root = build_kv_package("kv_fail", layouts=("2*8",))
    spec = load_spec(root)
    real = make_config("fail")
    real.set("keyview_keys", {"r1k1": {"vk": 0x46, "name": "F"}})

    failing = FailingStore(real)
    window = settings.SettingsWindow(failing, current_pet="", keyview=spec)
    grid = window._key_grid
    if grid is None:
        check("网格建出来了", False, "grid 是 None")
        window.close()
        return

    # ---- (1) 录入时写失败 → 按钮**回滚**到原值，状态栏说原因
    grid.begin_capture("r2k2")
    press(grid.button("r2k2"), Qt.Key_K, 0x4B, "k")
    check(
        "写失败 → 那个键位**回滚**（不显示刚录的 K）",
        grid.button("r2k2").display_text == "未设置",
        f"文字={grid.button('r2k2').display_text!r}",
    )
    check(
        "写失败 → 状态栏显示**失败原因**（系统原文优先）",
        window._status_label.text().startswith(settings.FAILED_PREFIX)
        and "磁盘假装满了" in window._status_label.text(),
        f"状态栏={window._status_label.text()!r}",
    )
    check(
        "写失败 → 配置里**没**多出 r2k2（磁盘是真值）",
        "r2k2" not in real.get("keyview_keys"),
        f"配置={real.get('keyview_keys')}",
    )

    # ---- (2) 重置时写失败 → 网格**回到原样**（还是 F）
    answer_with(YES)
    grid._on_reset_clicked()
    check(
        "重置写失败 → 网格**回到原样**（r1k1 还是 F）",
        grid.button("r1k1").display_text == "F",
        f"文字={grid.button('r1k1').display_text!r}",
    )
    check(
        "重置写失败 → 配置**没被清空**",
        real.get("keyview_keys") == {"r1k1": {"vk": 0x46, "name": "F"}},
        f"配置={real.get('keyview_keys')}",
    )
    check(
        "重置写失败 → 状态栏也有原因（**绝不显示假的成功**）",
        window._status_label.text().startswith(settings.FAILED_PREFIX),
        f"状态栏={window._status_label.text()!r}",
    )

    # ---- (3) 恢复写盘后再来一次：这次**真的**写进去了
    failing.blocked = False
    grid.begin_capture("r2k2")
    press(grid.button("r2k2"), Qt.Key_K, 0x4B, "k")
    check(
        "[对照] 恢复写盘后同一个动作**真的写进去了**（证明上面是写失败，不是别的原因）",
        (real.get("keyview_keys") or {}).get("r2k2") == {"vk": 0x4B, "name": "K"},
        f"配置={real.get('keyview_keys')}",
    )
    window.close()


# ==================================================================== §7 坏数据


def section_bad_binding(app) -> None:
    """§7：配置里有**当前素材包没有的**键 id → 丢弃 + 日志，**文件不被改写**。"""
    from deskpet import settings

    section("§7 坏数据：不存在的键 id 被丢弃，**文件一个字节都不改**")

    root = build_kv_package("kv_bad", layouts=("2*8",))
    spec = load_spec(root)
    store = make_config("bad")
    store.set(
        "keyview_keys",
        {
            "r1k1": {"vk": 0x46, "name": "F"},
            "r9k9": {"vk": 0x41, "name": "A"},  # 这个包里根本没有这个键位
            "r1k2": {"vk": "坏"},  # vk 非法 → 静默丢弃
            "r1k3": {"name": "没配 vk"},  # 没有 vk → 丢弃
        },
    )
    before = store.path.read_bytes()

    window = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = window._key_grid
    after = store.path.read_bytes()

    check(
        "**打开窗口不改写文件**（读操作只读，§2.10）",
        before == after,
        f"文件变了={before != after}",
    )
    check(
        "不存在的键 id 被丢弃：网格里没有 r9k9 这一格",
        grid is not None and "r9k9" not in grid._buttons,
        f"网格键={sorted(grid._buttons)[:4] if grid else None}…",
    )
    check(
        "被丢掉的绑定**不让整组失败**：合规的那一个照常显示",
        grid is not None and grid.button("r1k1").display_text == "F",
        f"r1k1={None if grid is None else grid.button('r1k1').display_text!r}",
    )
    check(
        "被丢弃的项**仍留在文件里**（没有被顺手写掉）",
        "r9k9" in store.path.read_text(encoding="utf-8"),
        "磁盘上还留着 r9k9",
    )

    # ---- 一次无关的录入也不该把坏的写掉（写路径以"有效绑定"为基础）
    if grid is not None:
        grid.begin_capture("r1k4")
        press(grid.button("r1k4"), Qt.Key_L, 0x4C, "l")
        written = json.loads(store.path.read_text(encoding="utf-8"))["keyview_keys"]
        check(
            "录一个新的键位**不会顺手写掉**那个不存在的键 id（只写有效绑定）",
            "r9k9" not in written and written.get("r1k1", {}).get("vk") == 0x46,
            f"写回后的键={sorted(written)}",
        )
    window.close()


# ==================================================================== §8 挂起


class _SuspendChanger:
    """装配层的换布局处理器**在自检里的替身**：可调用 + 带 set_suspended。

    ⚠ 这个形状**就是产品那个对象的形状**（`KeyviewController`）——
    自检用它才能验"界面只认这两个入口"，而不用把整条装配链拉起来。
    """

    def __init__(self, controller) -> None:
        self.controller = controller
        self.calls: list[str] = []

    def __call__(self, layout):
        from deskpet.settings import KeyviewOutcome

        self.calls.append(layout)
        return KeyviewOutcome(True, layout)

    def set_suspended(self, suspended: bool) -> None:
        self.calls.append(f"suspend={suspended}")
        self.controller.set_suspended(bool(suspended))


def section_suspend(app) -> None:
    """§8：录入期间监听**确实停**（轮询计数不增长），退出录入后**恢复**。"""
    from deskpet import context_menu as cm, settings
    from deskpet.app import KeyListenerController
    from deskpet.pet_window import PetWindow

    section("§8 录入期间挂起监听：轮询次数不增长，退出后恢复")

    root = build_kv_package("kv_susp", layouts=("2*8",))
    spec = load_spec(root)
    pet = load_pet(root)
    store = make_config("susp")
    store.set("keyview_keys", {"r1k1": {"vk": 0x46, "name": "F"}})

    window = PetWindow(pet, 6, config=store, keyview=spec, keyview_layout="2*8")
    window.set_form_handler(lambda form: None)
    window.set_form(cm.FORM_KEYVIEW)
    window.show()
    _pump(app, 20)

    controller = KeyListenerController(window, store)
    controller.sync()
    check(
        "KV 形态 + 可见 → 监听该转起来",
        controller.should_poll() and controller.listener.running,
        f"should_poll={controller.should_poll()} running={controller.listener.running}",
    )
    _pump(app, 40)
    running_polls = controller.listener.polls
    check("轮询次数真的在增长", running_polls > 0, f"轮询={running_polls} 次")

    win = settings.SettingsWindow(store, current_pet="", keyview=spec)
    grid = win._key_grid
    if grid is None:
        check("网格建出来了", False, "grid 是 None")
        win.close()
        window.close()
        return
    win.set_keyview_changer(_SuspendChanger(controller))
    # ⚠ **必须真的 show 出来**：后面那条"关掉窗口要还回挂起"验的是
    # `hideEvent`，而 Qt 对"从没显示过的窗口"不发它（`close()` 只是个空操作）。
    # 用户看到的设置窗口当然是真的显示着的，自检要照这个来。
    win.show()
    _pump(app, 20)

    # ---- (1) 进录入态 → 轮询**停**
    grid.begin_capture("r1k2")
    paused_at = controller.listener.polls
    _pump(app, 40)
    check(
        "**进录入态** → 轮询次数一次都不再增长（CPU 0）",
        controller.listener.polls == paused_at,
        f"进去时={paused_at} 40 ms 后={controller.listener.polls}",
    )
    check(
        "监听器确实被挂起了（suspended 为真、定时器停了）",
        controller.suspended and not controller.listener.running,
        f"suspended={controller.suspended} running={controller.listener.running}",
    )
    check(
        "录入期间 pressed **清空**（回来不带陈旧按下）",
        controller.listener.pressed == (),
        f"pressed={controller.listener.pressed}",
    )

    # ---- (2) Esc 取消 → 恢复
    press(grid.button("r1k2"), Qt.Key_Escape, 0x1B)
    _pump(app, 30)
    check(
        "**Esc 取消** → 监听恢复（轮询次数重新增长）",
        not controller.suspended and controller.listener.polls > paused_at,
        f"suspended={controller.suspended} 轮询 {paused_at} -> {controller.listener.polls}",
    )

    # ---- (3) 录成一个键 → 也要恢复
    resumed_at = controller.listener.polls
    grid.begin_capture("r1k2")
    _pump(app, 20)
    check("录成一个键之前也挂着", controller.suspended, f"suspended={controller.suspended}")
    press(grid.button("r1k2"), Qt.Key_K, 0x4B, "k")
    _pump(app, 30)
    check(
        "**录成一个键** → 监听恢复",
        not controller.suspended and controller.listener.polls > resumed_at,
        f"suspended={controller.suspended}",
    )

    # ---- (4) 窗口关掉也要恢复（单点收尾那一条）
    grid.begin_capture("r1k3")
    check("再进一次录入态", controller.suspended, f"suspended={controller.suspended}")
    win.close()
    _pump(app, 30)
    check(
        "**设置窗口关掉** → 挂起也被还回去（单点收尾，不是只有录成才恢复）",
        not controller.suspended and controller.listener.running,
        f"suspended={controller.suspended} running={controller.listener.running}",
    )

    # ---- (5) 卸掉挂起钩子之后再录入不该炸
    grid.set_suspend_hook(None)
    check(
        "卸掉挂起钩子之后开始录入：**不炸**、只是没人可挂起",
        grid.begin_capture("r1k4") and not grid.suspended,
        f"capturing={grid.capturing}",
    )
    grid.cancel_capture()
    check("取消之后也不炸", not grid.capturing, "cancel_capture 幂等")

    controller.stop()
    window.close()
    _pump(app, 20)


# ==================================================================== §9 切布局


def section_layout_switch(app) -> None:
    """§9：切布局 → 键集当场变；窗口**尺寸与位置不变**（共用 keyview.cell）。"""
    from deskpet import context_menu as cm, settings
    from deskpet.app import FormController, KeyListenerController, KeyviewController
    from deskpet.pet_window import PetWindow

    section("§9 切布局：键集当场变、窗口尺寸与位置**不变**")

    root = build_kv_package("kv_switch")
    spec = load_spec(root)
    pet = load_pet(root)
    store = make_config("switch")
    store.set("keyview_layout", "2*8")
    store.set("keyview_keys", {"r1k1": {"vk": 0x46, "name": "F"}})

    window = PetWindow(pet, 6, config=store, keyview=spec, keyview_layout="2*8")
    window.set_form_handler(lambda form: None)
    window.set_form(cm.FORM_KEYVIEW)
    window.show()
    _pump(app, 20)

    form_controller = FormController(window, store)
    form_controller.layout = "2*8"
    controller = KeyListenerController(window, store)
    keyview_controller = KeyviewController(window, store, controller, form_controller)

    size_before = window.pixel_size
    pos_before = (window.base_position.x(), window.base_position.y())
    keys_before = set(window.keyview_active_keys)
    check(
        "启动时是 2*8（16 个键）",
        len(keys_before) == 16 and window.keyview_layout == "2*8",
        f"布局={window.keyview_layout!r} 键={len(keys_before)}",
    )

    # ---- (1) 切到 1*4
    outcome = keyview_controller("1*4")
    _pump(app, 20)
    check(
        "切布局**成功**（结论 ok + 布局名对）",
        outcome.ok and outcome.layout == "1*4",
        f"结论={outcome}",
    )
    check(
        "键集**当场**变成 1*4 的 4 个（居中子集）",
        set(window.keyview_active_keys) == {"r1k3", "r1k1", "r1k2", "r1k4"},
        f"键={sorted(window.keyview_active_keys)}",
    )
    check(
        "**窗口尺寸不变**（所有布局共用 keyview.cell）",
        window.pixel_size == size_before,
        f"切前={size_before} 切后={window.pixel_size}",
    )
    check(
        "**窗口位置不变**（cell 没变 → 锚点零风险）",
        (window.base_position.x(), window.base_position.y()) == pos_before,
        f"切前={pos_before} 切后={(window.base_position.x(), window.base_position.y())}",
    )

    # ---- (2) 绑定表按新布局**重算**：不在当前布局的键不进绑定
    store.set(
        "keyview_keys",
        {"r1k1": {"vk": 0x46, "name": "F"}, "r1k8": {"vk": 0x4A, "name": "J"}},
    )
    keyview_controller.rebind()
    check(
        "重绑后：**不在当前布局**的键 (r1k8) 不进绑定表",
        0x4A not in controller.listener.bindings,
        f"绑定表={controller.listener.bindings}",
    )
    check(
        "重绑后：**在当前布局**的键 (r1k1) 仍在绑定表里",
        controller.listener.bindings.get(0x46) == ("r1k1",),
        f"绑定表={controller.listener.bindings}",
    )

    # ---- (3) 画面：布局之外的键**不会**被画出来
    window.set_pressed(("r1k8",))
    check(
        "布局之外的键按下 → **滤掉**（不画这个布局没有的键帽）",
        window.pressed == (),
        f"pressed={window.pressed}",
    )

    # ---- (4) 切回 2*8：r1k8 又能用
    keyview_controller("2*8")
    keyview_controller.rebind()
    check(
        "切回 2*8 → r1k8 重新进绑定表",
        controller.listener.bindings.get(0x4A) == ("r1k8",),
        f"绑定表={controller.listener.bindings}",
    )

    # ---- (5) 切到一个**不存在**的布局 → 明确的失败结论（不假装成功）
    before_config = store.get("keyview_layout")
    bad = keyview_controller("3*3")
    check(
        "切到不存在的布局 → ok=False + 说清原因",
        not bad.ok and "3*3" in bad.reason,
        f"结论={bad}",
    )
    check(
        "切失败**不动配置**（磁盘上还是真的能用的那个）",
        store.get("keyview_layout") == before_config,
        f"配置={store.get('keyview_layout')!r}",
    )

    controller.stop()
    window.close()
    _pump(app, 20)


# ==================================================================== §10 装配


def section_wiring(app) -> None:
    """§10：装配——SettingsController 把 keyview 与处理器装到**每个新窗口**上。"""
    from deskpet import settings as settings_mod
    from deskpet.app import SettingsController
    from deskpet.pet_window import PetWindow

    section("§10 装配：每个新窗口都装上 keyview 与换布局处理器")

    root = build_kv_package("kv_wiring", layouts=("2*8",))
    spec = load_spec(root)
    pet = load_pet(root)
    store = make_config("wiring")
    store.set("keyview_keys", {"r1k1": {"vk": 0x46, "name": "F"}})

    window = PetWindow(pet, 6, config=store, keyview=spec, keyview_layout="2*8")
    controller = SettingsController(store, window)

    class Changer:
        def __call__(self, layout):
            from deskpet.settings import KeyviewOutcome

            return KeyviewOutcome(True, layout)

        def set_suspended(self, suspended: bool) -> None:
            return None

    controller.set_keyview(spec)
    controller.set_keyview_changer(Changer())

    controller.open()
    dialog = controller.dialog
    check("设置窗口打开了", dialog is not None, f"dialog={dialog}")
    if dialog is None:
        window.close()
        return
    check(
        "**第 1 个窗口**上装了 keyview（网格按素材包摆出来，16 格）",
        dialog._key_grid is not None and len(dialog._key_grid._buttons) == 16,
        f"按钮={0 if dialog._key_grid is None else len(dialog._key_grid._buttons)}",
    )
    check(
        "**第 1 个窗口**上装了换布局处理器",
        dialog._keyview_changer is not None,
        f"changer={dialog._keyview_changer!r}",
    )

    # 关掉 → 再开一个**全新**的窗口：处理器必须**重新装**（task12/13/14 同一条教训）
    dialog.close()
    _pump(app, 20)
    controller.open()
    dialog2 = controller.dialog
    check(
        "关掉再开 → 是一个**新窗口**",
        dialog2 is not None and dialog2 is not dialog,
        "新实例",
    )
    check(
        "**第 2 个窗口**上照样装了换布局处理器（忘了重装 = 只写配置、画面不动）",
        dialog2 is not None and dialog2._keyview_changer is not None,
        f"changer={None if dialog2 is None else dialog2._keyview_changer!r}",
    )
    check(
        "**第 2 个窗口**上照样有网格",
        dialog2 is not None
        and dialog2._key_grid is not None
        and len(dialog2._key_grid._buttons) == 16,
        "网格",
    )
    if dialog2 is not None:
        dialog2.close()
    _pump(app, 20)

    # ---- 字段表纪律：新组与新的种类都在，且**没有**动宠物形态的设置行
    keys = [field.key for field in settings_mod.FIELDS]
    check(
        "FIELDS 里有 keyview_layout / keyview_keys（真值只有这一处）",
        "keyview_layout" in keys and "keyview_keys" in keys,
        f"键={keys}",
    )
    check(
        "GROUP_ORDER 里 键盘 摆在 行为 **之后**",
        settings_mod.GROUP_ORDER == ("常规", "声音", "行为", "键盘"),
        f"组={settings_mod.GROUP_ORDER}",
    )
    kinds = {field.key: field.kind for field in settings_mod.FIELDS}
    check(
        "两个新行各自用的是 COMBO / KEYGRID（网格**没有**塞进 COMBO 分支）",
        kinds.get("keyview_layout") is settings_mod.FieldKind.COMBO
        and kinds.get("keyview_keys") is settings_mod.FieldKind.KEYGRID,
        f"种类={kinds.get('keyview_layout')} / {kinds.get('keyview_keys')}",
    )
    check(
        "宠物形态的设置行**一个都没动**（前六行还是原来那些）",
        [field.key for field in settings_mod.FIELDS][:6]
        == ["pet", "", "hotkey", "volume", "idle_seconds", "antialias"],
        f"前六行={[field.key for field in settings_mod.FIELDS][:6]}",
    )

    window.close()
    _pump(app, 20)


# ==================================================================== §11 纪律


def section_discipline(app, hash_before: str) -> None:
    section("§11 自检纪律（assets/ 未被改动、不写用户真实目录、源码卫生）")

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
        "自检在 assets/ 里没有留下任何东西",
        not any(path.name.startswith("task19") for path in (ROOT / "assets").iterdir()),
        "占位包全部造在 data/task19_out/",
    )

    # ---- 源码卫生：**判意图，不盯字面**（§4.26 那条教训）
    grid_source = (ROOT / "src" / "deskpet" / "keygrid.py").read_text(encoding="utf-8")
    check(
        "keygrid 里**没有**直接写配置（写配置只有设置窗口那一条路）",
        "ConfigStore" not in grid_source and ".apply(" not in grid_source,
        "网格只发信号",
    )
    capture_source = (ROOT / "src" / "deskpet" / "keycapture.py").read_text(encoding="utf-8")
    check(
        "组合录入那一条路**没被改坏**（三条硬约定与两个类都还在）",
        "快捷键必须包含 Ctrl / Alt / Shift / Win 之一" in capture_source
        and "class KeyCaptureEdit" in capture_source
        and "class SingleKeyCaptureButton" in capture_source,
        "两条路各自独立",
    )
    spec_source = (ROOT / "src" / "deskpet" / "keyview_spec.py").read_text(encoding="utf-8")
    check(
        "绑定规范化在**数据层**（不是界面里现拼一份）",
        "def normalize_bindings" in spec_source
        and "def bindings_to_config" in spec_source,
        "键 id -> {vk, name} 的唯一拼法",
    )
    # ---- 判**意图**（§4.26 那条教训：源码卫生断言别写成"某个子串不许出现"）
    #
    # ⚠ 判据要落在**可执行代码**上，注释与文档字符串不算：本项目的大量注释
    # 本来就在讨论"界面不许持有监听器"这件事，把注释也算进去的话，
    # 这条断言会因为它想证明的那条纪律**被写下来**而变红（§4.26 同一种毛病）。
    settings_code = _strip_comments(
        (ROOT / "src" / "deskpet" / "settings.py").read_text(encoding="utf-8")
    )
    check(
        "界面**不直接持有**监听器（挂起只走装进来的钩子）",
        "import key_listener" not in settings_code
        and "KeyListener(" not in settings_code
        and "KeyListenerController" not in settings_code,
        "可执行代码里既不 import 也不构造监听器",
    )


# ==================================================================== 主入口

#: 确认框的两个答案（与 `QMessageBox.StandardButton` 同值）。
#: ⚠ 直接写数字而不是引 `QMessageBox.Yes`：自检**只在 `QuestionPatch` 里**
#: 碰 Qt 的那个类，别处引它会让"这个值到底谁说了算"变成两处。
NO = 65536
YES = 16384


class QuestionPatch:
    """把 QMessageBox.question 换成一个可控的答案（**只在自检里**）。

    ⚠ 它必须**归还**原函数：自检跑在 offscreen 上，一个真的模态框会把
    整个会话挂住（§4.9 的"模态弹窗"坑）。
    """

    def __init__(self) -> None:
        self._original = None

    def __enter__(self):
        from PySide6.QtWidgets import QMessageBox

        self._original = QMessageBox.question
        return self

    def answer_with(self, answer) -> None:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.question = staticmethod(lambda *a, **k: answer)

    def __exit__(self, *exc) -> bool:
        from PySide6.QtWidgets import QMessageBox

        if self._original is not None:
            QMessageBox.question = self._original
        return False


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    app = QApplication.instance() or QApplication([])
    hash_before = _hash_assets()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    _LINES.append("task19 自检 —— 键盘形态之三：按键配置界面")
    _LINES.append(f"工作区根：{ROOT}")

    started = time.perf_counter()
    section_layout_combo(app)
    section_grid_order(app)
    section_capture(app)
    section_duplicate(app)
    with QuestionPatch() as patch:
        section_reset(app, patch.answer_with)
        section_write_failure(app, patch.answer_with)
    section_bad_binding(app)
    section_suspend(app)
    section_layout_switch(app)
    section_wiring(app)
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
    raise SystemExit(main())
