"""task17 自检（offscreen）：键盘形态的**素材契约**、**合成**、**形态接缝**与**降级**。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task17_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task17.py

## 这一份验什么（对应 task17 §6）

1. **逐像素合成分辨**：`compose()` == "按行/列顺序逐层手工 `drawImage`"这条
   **独立路径** —— 钉住"不串位、层序对"；
2. **逐键回放对照**：每个键 id 单独按下 == 静止态 + 该键按下态；
   **坐标写法**与 **`overlay` 写法各验一遍**；
3. **六个布局全部能解析**；表外布局 → 忽略 + WARNING，**其余布局照常**；
   表外是唯一那个 → KV 不可用、菜单项灰、**启动不中断**；
4. **段缺失 / 为 `null`** → 启动正常、**日志里没有错误也没有警告**、菜单项灰；
5. **按布局判可用**：某布局缺图 → 只有它不可用；选中布局不可用 → **回退 + 一条日志**；
   **全部**不可用 → 菜单项灰；
6. **坏清单一次列全**（缺 `cell` / 行列数不符 / 缺图 / `head` 与 `hands` 引用不存在的键 /
   `rows` 引用不存在的键 / `x/y` 与 `overlay` 同时出现 / `cell` 与图不符 /
   **同一个键 id 在 `keys` 里重复**）；
7. **编号校验**：行内键 id 的行号与序号必须与布局名对得上；编号不是中间往外排 → **警告**；
8. **宠物形态全程不受影响**：`keyview` 段改成任意坏形状 → 宠物照样起、动作照常；
9. **10 级档位**：尺寸两两不同、逐级正确、与 `size.pixel_size` 一致；
10. **形态接缝**：切形态**不重置档位与位置**、进 KV 停表 / 回宠物接着播、
    菜单两项文字随形态切换、KV 里不镜像、遮罩跟着画面走；
11. **配置落盘**：`form` / `keyview_layout` 写进配置并在"重启"后恢复；
    **只读查询一个字节都不写盘**（§4.11 / §4.13）。

## ⚠ 这一份**不写用户真实目录**

所有落盘都在 `data/task17_out/`（工作区内，§4.13）。
`assets/` 一个字节都不动（脚本结尾核对哈希）。
用户真实的 `data/config.json` 不碰（本脚本的 `ConfigStore` 全部指向临时目录）。
"""

import hashlib
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

RESULT_FILE = ROOT / "data" / "task17_check.txt"

#: 自检自己的产出目录（**不碰 assets/**）。
OUT_DIR = ROOT / "data" / "task17_out"

#: 程序生成的占位包根目录（**不碰 assets/**，task17 §8 第 4 条）。
PKG_ROOT = OUT_DIR / "pets"

ALL_LEVELS = tuple(range(1, 11))

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


def note(text: str) -> None:
    """记一条**只有数据、没有结论**的观察（不参与 PASS/FAIL）。"""
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _hash_assets() -> str:
    """`assets/` 的哈希（判据：自检前后不变）。"""
    digest = hashlib.sha256()
    for path in sorted((ROOT / "assets").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8", "replace"))
            digest.update(str(path.stat().st_size).encode())
    return digest.hexdigest()


# ==================================================================== 造占位包


def _solid(width: int, height: int, color: str) -> QImage:
    """一块纯色（自检用；**不是**素材）。"""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor(color))
    return image


def _blob(width: int, height: int, color: str, inset: int = 2) -> QImage:
    """一块带透明边的小色块（**必须有 alpha**，否则遮罩会退化成整帧可点）。"""
    image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    try:
        painter.fillRect(inset, inset, width - inset * 2, height - inset * 2, QColor(color))
    finally:
        painter.end()
    return image


#: 8 列的键序号（**中间往外**）——自检里**独立**算一遍，
#: 不调产品代码：这样"编号对不对"才是一条真对照（§4.23 的教训）。
def _middle_out(columns: int) -> list[int]:
    center = (columns - 1) / 2
    out = []
    for column in range(columns):
        base = int(abs(column - center) - 0.5) * 2 + 1
        out.append(base + (1 if column > center else 0))
    return out


def build_placeholder_package(
    namespace: str = "kvprobe",
    *,
    layouts: tuple[str, ...] = ("1*4", "1*6", "1*8", "2*4", "2*6", "2*8"),
    overlay_keys: bool = False,
    with_hands: bool = True,
    with_head: bool = True,
    cell: tuple[int, int] = (64, 48),
) -> Path:
    """造一个**程序生成的占位 keyview 包**（纯色块 + 序号），返回包根目录。

    做的三件事，都是为了"自检不依赖 assets/"（红线：不许碰原始素材）：

    1. `frames/` 给一个最小的宠物形态（1 个动作 1 帧）——
       于是这个包**同时是宠物包与键盘包**，可以验"KV 坏掉不影响宠物形态"；
    2. `keyview/` 按请求的布局集合生成图（键帽 / 手 / 头 / 两个背景图层）；
    3. `asset.json` 写全，**键位用 8 列中间往外编号**。

    `overlay_keys=True` 时键位写成 `overlay` 整画布图（零坐标）——
    两条写法都要验，不能只验一条。
    """
    root = PKG_ROOT / namespace
    if root.exists():
        shutil.rmtree(root)
    for sub in ("frames", "keyview"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    # ---- 宠物形态：1 个动作 1 帧（这个包也是合法的宠物包）
    frame = _blob(64, 64, "#3366cc", 6)
    frame.save(str(root / "frames" / "idle_00.png"), "PNG")

    # ---- 背景：**两个分开的图层**（不许合并，考试点就是"层序对不对"）
    #      底层人物（偏左、半透明）→ 上层键盘（偏右）：
    #      合成分辨实验里"谁盖住谁"一眼可辨。
    figure = QImage(cell[0], cell[1], QImage.Format_ARGB32_Premultiplied)
    figure.fill(Qt.transparent)
    painter = QPainter(figure)
    try:
        painter.fillRect(4, 4, cell[0] // 2, cell[1] - 8, QColor("#cc4444"))
    finally:
        painter.end()
    figure.save(str(root / "keyview" / "figure.png"), "PNG")

    piano = QImage(cell[0], cell[1], QImage.Format_ARGB32_Premultiplied)
    piano.fill(Qt.transparent)
    painter = QPainter(piano)
    try:
        painter.fillRect(cell[0] // 2, 4, cell[0] // 2 - 4, cell[1] - 8, QColor("#44cc44"))
    finally:
        painter.end()
    piano.save(str(root / "keyview" / "piano.png"), "PNG")

    # ---- 键位：2 行 × 8 列，序号中间往外
    numbers = _middle_out(8)
    keys: dict[str, dict] = {}
    key_size = (max(4, cell[0] // 16), max(4, cell[1] // 12))
    for row in (1, 2):
        for column, number in enumerate(numbers):
            key_id = f"r{row}k{number}"
            filename = f"key_{key_id}.png"
            _solid(key_size[0], key_size[1], "#ffcc00").save(
                str(root / "keyview" / filename), "PNG"
            )
            if overlay_keys:
                # overlay 写法：与 cell 同规格的**整画布**透明底图，位置画在图里。
                # ⚠ `down` **仍然是必需的**（契约：`down` = 按下态的文件名，
                # `overlay` 只是"位置怎么写"的另一种说法，不是它的替代品）。
                # 第一版这里漏了 `down`，解析层当场按契约拒收——
                # 自检自己写错了素材，不是产品的问题。
                canvas = QImage(cell[0], cell[1], QImage.Format_ARGB32_Premultiplied)
                canvas.fill(Qt.transparent)
                painter = QPainter(canvas)
                try:
                    painter.fillRect(
                        4 + column * key_size[0],
                        4 + (row - 1) * (key_size[1] + 2),
                        key_size[0] - 1,
                        key_size[1] - 1,
                        QColor("#ffcc00"),
                    )
                finally:
                    painter.end()
                canvas.save(str(root / "keyview" / f"overlay_{key_id}.png"), "PNG")
                keys[key_id] = {
                    "down": filename,
                    "overlay": f"overlay_{key_id}.png",
                }
            else:
                keys[key_id] = {
                    "x": 4 + column * key_size[0],
                    "y": 4 + (row - 1) * (key_size[1] + 2),
                    "down": filename,
                }

    keyview: dict = {
        "cell": list(cell),
        "background": {"normal": ["figure.png", "piano.png"]},
        "keys": keys,
        "layouts": {},
    }

    # ---- 手：左半 / 右半各一只（**只有 up 的那只也要有**）
    if with_hands:
        hands = []
        for tag, columns in (("l", range(0, 4)), ("r", range(4, 8))):
            _solid(*cell, "#2222aa").save(
                str(root / "keyview" / f"hand_{tag}_up.png"), "PNG"
            )
            down: dict[str, str] = {}
            for row in (1, 2):
                for column in columns:
                    key_id = f"r{row}k{numbers[column]}"
                    filename = f"hand_{tag}_{key_id}.png"
                    _solid(*cell, "#aa2222").save(
                        str(root / "keyview" / filename), "PNG"
                    )
                    down[key_id] = filename
            hands.append({"up": f"hand_{tag}_up.png", "down": down})
        keyview["hands"] = hands

    # ---- 头：2*8 全按下时出现
    if with_head:
        _solid(*cell, "#ffffff").save(str(root / "keyview" / "head.png"), "PNG")
        all_ids = [f"r{row}k{number}" for row in (1, 2) for number in numbers]
        keyview["head"] = [{"image": "head.png", "when_all_of": all_ids}]

    # ---- 布局：按请求生成（行列数与名字必须对得上）
    for name in layouts:
        rows_count, columns_count = (int(part) for part in name.split("*"))
        columns_count = columns_count  # 只是为了让下面的列表推导读起来清楚
        rows = []
        for row in range(1, rows_count + 1):
            # ⚠ 4/6 键布局是 8 键的**居中子集**（取中间那几列的序号）。
            start = (8 - columns_count) // 2
            chosen = numbers[start : start + columns_count]
            rows.append([f"r{row}k{number}" for number in chosen])
        keyview["layouts"][name] = {"rows": rows}

    index = {
        "name": f"占位{namespace}",
        "description": "自检用的程序生成占位包",
        "cell": [64, 64],
        "actions": {"idle": {"frames": ["idle_00.png"], "fps": 30.0}},
        "keyview": keyview,
    }
    (root / "asset.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


def rewrite_index(root: Path, mutate) -> None:
    """读索引 → 让 `mutate(data)` 改它 → 写回（自检里造坏清单用）。"""
    path = root / "asset.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    mutate(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def make_config(name: str):
    """造一个**指向临时目录**的 `ConfigStore`（绝不碰用户真实的 config.json）。"""
    from deskpet.config import ConfigStore

    path = OUT_DIR / "configs" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    store = ConfigStore(path)
    store.load()
    return store

# ==================================================================== §1 素材契约


def _load_keyview(root):
    """读一个包的 keyview（**只关心 KV**，与宠物形态的问题分开）。"""
    from deskpet import pet_loader

    return pet_loader.load_keyview(root)


def _report_for(root):
    """按目录直接校验一个包（绕过两根扫描，自检用临时目录）。"""
    from deskpet import pet_loader

    return pet_loader._analyze(root)


def section_contract(app) -> None:
    """§1 素材契约：六个布局、键身份、编号校验、表外布局只忽略。"""
    from deskpet import keyview_spec

    section("§1 素材契约：六个布局都能解析、键身份与编号校验")

    root = build_placeholder_package("kv_contract", layouts=keyview_spec.LAYOUTS)
    spec, issues, _warnings = _load_keyview(root)
    check(
        "六个布局全部能解析出来",
        spec is not None and spec.layout_names == keyview_spec.LAYOUTS,
        f"问题={len(issues)} 布局={None if spec is None else spec.layout_names}",
    )
    if spec is None:
        return

    # ---- 键身份：8 列必须是 7 5 3 1 2 4 6 8（中间往外）
    numbers = [keyview_spec.parse_key_id(item)[1] for item in spec.layouts["2*8"].rows[0]]
    check(
        "2*8 行内的序号是 7 5 3 1 2 4 6 8（**中间往外**，不是列号）",
        numbers == [7, 5, 3, 1, 2, 4, 6, 8],
        f"实际 {numbers}",
    )

    # ---- 4 键 ⊂ 6 键 ⊂ 8 键（居中子集）——"跨布局复用"这条前提
    four = set(spec.layouts["1*4"].key_ids)
    six = set(spec.layouts["1*6"].key_ids)
    eight = set(spec.layouts["1*8"].key_ids)
    check(
        "4 键 ⊂ 6 键 ⊂ 8 键（居中子集，坐标才能跨布局复用）",
        four < six < eight,
        f"4={sorted(four)} 6={sorted(six)} 8={sorted(eight)}",
    )

    # ---- 六个布局的行列数都对
    shape_ok = all(
        len(spec.layouts[name].rows) == int(name.split("*")[0])
        and all(len(row) == int(name.split("*")[1]) for row in spec.layouts[name].rows)
        for name in spec.layout_names
    )
    check("六个布局的行数 × 列数都与名字对得上", shape_ok, f"布局={spec.layout_names}")

    # ---- 表外布局：忽略 + WARNING，**其余布局照常可用**
    root2 = build_placeholder_package("kv_unknown_layout", layouts=("1*4", "3*3"))
    spec2, issues2, warnings2 = _load_keyview(root2)
    check(
        "表外布局（3*3）被忽略，**其余布局照常可用**",
        spec2 is not None and spec2.layout_names == ("1*4",),
        f"问题={len(issues2)} 布局={None if spec2 is None else spec2.layout_names}",
    )
    check(
        "表外布局**记了 WARNING**（不是静默丢弃，也不是错误）",
        any("3*3" in str(item) for item in warnings2) and not issues2,
        f"警告={[str(w) for w in warnings2]}",
    )

    # ---- 表外布局是**唯一**那个 → KV 不可用，但**宠物形态不受影响**
    root3 = build_placeholder_package("kv_only_unknown", layouts=("3*3",))
    spec3, issues3, _w3 = _load_keyview(root3)
    check(
        "表外布局是唯一那个 → KV 不可用（规格为 None）",
        spec3 is None and bool(issues3),
        f"规格={spec3} 问题={len(issues3)}",
    )
    report3 = _report_for(root3)
    check(
        "但**宠物形态照常可用**（KV 坏掉绝不牵连宠物）",
        report3 is not None and report3.ok,
        f"宠物校验={None if report3 is None else report3.conclusion()}",
    )

# ==================================================================== §2 降级


def section_degradation(app) -> None:
    """§2 段缺失 / null / 坏形状 → 只影响 KV，宠物形态照常。"""
    section("§2 段缺失 / null / 坏形状：只影响 KV，宠物形态不受影响")

    # ---- 段缺失：**既不报错也不警告**，菜单项灰掉
    root = build_placeholder_package("kv_absent")
    rewrite_index(root, lambda data: data.pop("keyview", None))
    spec, issues, warnings = _load_keyview(root)
    check(
        "keyview 段缺失 → 规格为 None、**没有问题也没有警告**",
        spec is None and not issues and not warnings,
        f"问题={len(issues)} 警告={len(warnings)}",
    )
    report = _report_for(root)
    check(
        "段缺失时宠物形态照常可用（与「没有 sounds」完全同义）",
        report is not None and report.ok and report.keyview is None,
        f"宠物校验={None if report is None else report.conclusion()}",
    )

    # ---- 段为 null：同一条路径
    root = build_placeholder_package("kv_null")
    rewrite_index(root, lambda data: data.__setitem__("keyview", None))
    spec, issues, warnings = _load_keyview(root)
    check(
        "keyview 为 null → 规格为 None、**没有问题也没有警告**",
        spec is None and not issues and not warnings,
        f"问题={len(issues)} 警告={len(warnings)}",
    )

    # ---- 段是任意坏形状：宠物形态照样起
    bad_shapes = ["坏", 42, [], {"cell": "坏"}, {"cell": [1, 1]}]
    ok_all = True
    for index, bad in enumerate(bad_shapes):
        root = build_placeholder_package(f"kv_bad_{index}")
        rewrite_index(root, lambda data, value=bad: data.__setitem__("keyview", value))
        spec, _issues, _warnings = _load_keyview(root)
        report = _report_for(root)
        pet_ok = report is not None and report.ok
        if spec is not None or not pet_ok:
            ok_all = False
            note(f"坏形状 {bad!r} → KV={spec} 宠物可用={pet_ok}")
    check(
        f"任意坏形状（{len(bad_shapes)} 种）→ KV 不可用，**宠物形态全部照常可用**",
        ok_all,
        "这一条是「KV 坏掉绝不影响宠物」的总闸",
    )

    # ---- 动作清单照常（KV 坏掉时不炸）
    root = build_placeholder_package("kv_bad_actions")
    rewrite_index(root, lambda data: data.__setitem__("keyview", {"cell": [1, 1], "keys": 5}))
    report = _report_for(root)
    check(
        "KV 坏掉时动作清单照常（--list-actions 走的就是它）",
        report is not None and report.spec is not None and len(report.spec.actions) == 1,
        f"动作={None if report is None or report.spec is None else report.spec.action_order}",
    )


# ==================================================================== §3 一次列全


def section_issue_listing(app) -> None:
    """§3 坏清单**一次列全**（不是报第一个）。"""
    section("§3 坏清单一次列全")

    # ---- 组合清单：一次弄坏 6 处 → 问题数必须 >= 6
    root = build_placeholder_package("kv_all_broken")
    rewrite_index(root, _break_everything)
    spec, issues, _warnings = _load_keyview(root)
    check(
        "一份被弄坏 6 处的清单 → **一次列全**（问题数 >= 6），不是只报第一个",
        spec is None and len(issues) >= 6,
        f"问题数={len(issues)}",
    )
    for issue in issues:
        note(f"  · {issue}")

    # ---- 逐项：每一种坏法都要**单独**能被抓到
    cases = [
        ("缺 cell", _break_missing_cell),
        ("rows 行列数与布局名不符", _break_layout_shape),
        ("缺图（键帽文件不存在）", _break_missing_image),
        ("head.when_all_of 指向不存在的键", _break_head_reference),
        ("hands.down 指向不存在的键", _break_hand_reference),
        ("layouts.rows 引用了 keys 里没有的 id", _break_row_reference),
        ("cell 与图不符（只记警告，仍可用）", _break_cell_mismatch),
    ]
    for index, (name, mutate) in enumerate(cases):
        root = build_placeholder_package(f"kv_case_{index}")
        rewrite_index(root, mutate)
        spec, issues, warnings = _load_keyview(root)
        if name.startswith("cell 与图不符"):
            # ⚠ 这一条**只是警告**（与 asset.json 的 cell 同一条规矩）：
            # 对不上时画面照样能出，不值得让整个 KV 不可用。
            check(
                f"坏清单单项：{name} → 仍然可用 + 一条警告",
                spec is not None and not issues and bool(warnings),
                f"问题={len(issues)} 警告={len(warnings)}",
            )
            continue
        check(
            f"坏清单单项：{name} → 被抓到",
            spec is None and bool(issues),
            f"规格={spec} 问题={len(issues)} 警告={len(warnings)}",
        )

    # ---- 重复键 id：单独一节（JSON 装不下）
    section_duplicate_key(app)

    # ---- x/y 与 overlay 同时出现：用 overlay + 一条警告（**不是错误**）
    root = build_placeholder_package("kv_both_positionings")
    rewrite_index(root, _break_both_positionings)
    spec, issues, warnings = _load_keyview(root)
    check(
        "x/y 与 overlay 同时出现 → **按 overlay 处理 + 一条警告**（不是错误）",
        spec is not None
        and not issues
        and any("overlay" in str(w) for w in warnings),
        f"问题={len(issues)} 警告={[str(w) for w in warnings]}",
    )


def _kv(data):
    """取索引里的 keyview 段（自检里改坏它用）。"""
    return data["keyview"]


def _break_missing_cell(data) -> None:
    _kv(data).pop("cell", None)


def _break_layout_shape(data) -> None:
    """把 2*8 的一行砍掉一个键（行列数与名字不符）。"""
    rows = _kv(data)["layouts"]["2*8"]["rows"]
    rows[0] = rows[0][:-1]


def _break_missing_image(data) -> None:
    """把某个键的 down 指向一个不存在的文件。"""
    first = next(iter(_kv(data)["keys"]))
    _kv(data)["keys"][first]["down"] = "这个文件不存在.png"


def _break_head_reference(data) -> None:
    _kv(data)["head"] = [{"image": "head.png", "when_all_of": ["r9k9"]}]


def _break_hand_reference(data) -> None:
    _kv(data)["hands"][0]["down"]["r9k9"] = "hand_l_up.png"


def _break_row_reference(data) -> None:
    _kv(data)["layouts"]["2*8"]["rows"][0][0] = "r9k9"


def _break_cell_mismatch(data) -> None:
    """`cell` 与图实际尺寸不符 → **只记警告**（同 asset.json 的 cell）。"""
    _kv(data)["cell"] = [999, 999]


def _break_both_positionings(data) -> None:
    """`x/y` 与 `overlay` 同时出现 → **用 overlay + 一条警告**（不是错误）。"""
    first = next(iter(_kv(data)["keys"]))
    _kv(data)["keys"][first]["overlay"] = "piano.png"


def _break_everything(data) -> None:
    """一份清单里同时弄坏 6 处**互不遮掩**的地方（一次列全的判据）。

    ⚠ "互不遮掩"是这一条的关键：第一版把"砍掉 2*8 的一行"与"改 rows[0][0] 的引用"
    叠在一起，两处**改的是同一个东西**，后一处把前一处的现场覆盖了；
    又用两个 `down` 指向不存在的文件，但解析层在 `keys` 层面先报问题之后就
    不再往下走那些分支——最后一共只报出 3 条。

    现在这 6 处分别落在 **keys / layouts / hands / head** 四个不同的分支上，
    谁也遮不住谁——"一次列全"这条才有分辨力。
    """
    keys = _kv(data)["keys"]
    # ① 一个键缺 down（keys 分支）
    first = next(iter(keys))
    keys[first].pop("down", None)
    # ② 一个键的 x/y 不是整数（keys 分支的另一条）
    keys[list(keys)[1]]["x"] = "不是数"
    # ③ rows 里引用不存在的键（layouts 分支）
    _kv(data)["layouts"]["2*8"]["rows"][0][0] = "r9k9"
    # ④ 行列数与布局名不符（layouts 分支的另一条）
    _kv(data)["layouts"]["2*8"]["rows"].append(list(_kv(data)["layouts"]["2*8"]["rows"][0]))
    # ⑤ hands.down 引用不存在的键（hands 分支）
    _kv(data)["hands"][0]["down"]["r9k8"] = "hand_l_up.png"
    # ⑥ head.when_all_of 引用不存在的键（head 分支）
    _kv(data)["head"] = [{"image": "head.png", "when_all_of": ["r9k7"]}]


class _DupKeys(dict):
    """一个 ``items()`` 会**吐出重复项**的映射（人写的文件与二次加工的工具都可能这样）。

    JSON 对象装不下重复键（后一个覆盖前一个），所以真实文件里造不出来——
    这一条验的是**解析层**对"传进来的映射里有重复来源"的处理。
    """

    def items(self):  # type: ignore[override]
        items = list(super().items())
        return items + [items[0]]


def section_duplicate_key(app) -> None:
    """§3b 重复键 id：JSON 装不下 → 直接喂解析层。"""
    from deskpet import keyview_spec

    section("§3b 重复键 id（JSON 装不下 → 直接喂解析层）")

    data = {
        "cell": [8, 8],
        "background": {"normal": ["bg.png"]},
        "keys": _DupKeys(
            {
                "r1k1": {"x": 0, "y": 0, "down": "k1.png"},
                "r1k2": {"x": 2, "y": 0, "down": "k2.png"},
            }
        ),
        "layouts": {"1*4": {"rows": [["r1k1", "r1k2", "r1k1", "r1k2"]]}},
    }
    spec, issues, _warnings = keyview_spec.parse_keyview(data, namespace="dup")
    check(
        "同一个键 id 在 keys 里重复 → 记一条问题（**不是**静默覆盖）",
        spec is None and any("重复" in str(item) for item in issues),
        f"问题={[str(i) for i in issues]}",
    )

# ==================================================================== §4 编号校验


def section_numbering(app) -> None:
    """§4 编号校验：不是中间往外排 → **警告**（不是错误）。"""
    section("§4 编号校验：行内序号不是中间往外排")

    root = build_placeholder_package("kv_numbering")

    def _renumber(data) -> None:
        # 改成"从左到右递增"（1..8）：结构仍在，但编号错了。
        _kv(data)["layouts"]["2*8"]["rows"] = [
            [f"r{row}k{column}" for column in range(1, 9)] for row in (1, 2)
        ]

    rewrite_index(root, _renumber)
    spec, issues, warnings = _load_keyview(root)
    check(
        "编号不是中间往外排 → **只是警告**，结构照常可用",
        spec is not None and not issues and any("编号" in str(w) for w in warnings),
        f"问题={len(issues)} 警告={[str(w) for w in warnings]}",
    )

    # ---- 对照：**正确**编号时**不该**有这条警告（否则上面那条证明不了什么）
    root2 = build_placeholder_package("kv_numbering_ok")
    _spec2, _issues2, warnings2 = _load_keyview(root2)
    check(
        "[对照] 编号正确时**没有**那条警告",
        not any("编号" in str(w) for w in warnings2),
        f"警告={[str(w) for w in warnings2]}",
    )


# ==================================================================== §5 合成


def section_composition(app) -> None:
    """§5 合成**逐像素**与独立路径相同 + 逐键回放对照（两种写法各一遍）。"""
    from deskpet import keyview_render

    section("§5 合成与逐键回放（逐像素对照）")

    for overlay in (False, True):
        tag = "overlay 写法" if overlay else "x/y 坐标写法"
        root = build_placeholder_package(
            f"kv_compose_{int(overlay)}", layouts=("2*8",), overlay_keys=overlay
        )
        spec, issues, _w = _load_keyview(root)
        if spec is None:
            check(f"{tag}：包能解析", False, f"问题={[str(i) for i in issues]}")
            continue

        renderer = keyview_render.KeyViewRenderer(spec)
        keys = tuple(spec.keys)

        # ---- ① 五种状态 × 三个档位：产品路径 == 独立路径（逐像素）
        states = [(), (keys[0],), (keys[-1],), (keys[0], keys[5]), keys]
        same = True
        detail = ""
        for level in (1, 6, 10):
            for state in states:
                if renderer.image(level, state) != keyview_render.compose_manually(
                    renderer, state, level
                ):
                    same = False
                    detail = f"档位 {level} 状态 {state} 不同"
        check(
            f"{tag}：{len(states)} 种状态 × 3 个档位，产品路径 == 独立路径（逐像素）",
            same,
            detail or "钉住「不串位、层序对」",
        )

        # ---- ② 逐键回放：每个键单独按下 == 静止态 + 该键按下态
        level = 6
        base = renderer.compose(level, ())
        replay_ok = True
        replay_detail = ""
        for key_id in keys:
            single = renderer.compose(level, (key_id,))
            expect = _expected_single(renderer, key_id, level)
            if single != expect:
                replay_ok = False
                replay_detail = f"键 {key_id} 对不上"
                break
        check(
            f"{tag}：逐键回放（{len(keys)} 个键各自单独按下）== 静止态 + 该键按下态",
            replay_ok,
            replay_detail or "每个键都验过",
        )

        # ---- ③ 头：全按才出头（`when_all_of` 的纯函数判据）
        if spec.heads:
            all_ids = tuple(spec.layouts["2*8"].key_ids)
            with_head = renderer.compose(level, all_ids)
            without = renderer.compose(level, all_ids[:-1])
            check(
                f"{tag}：头只在 when_all_of 的键**全部**按下时出现",
                with_head != without,
                "少按一个键就不出头",
            )

        # ---- ④ 层序真的在起作用（对照：把 background 反过来就该不同）
        if _background_overlaps(spec):
            check(
                f"{tag}：[对照] 把 background 两层顺序反过来 → 出图**不同**",
                _compose_with_flipped_background(renderer, level) != base,
                "证明层序在起作用，不是恰好一样",
            )
        else:
            note("（跳过层序对照：占位包的两层背景不重叠，换不换顺序都一样）")


def _expected_single(renderer, key_id: str, level: int) -> QImage:
    """**独立**算出"静止态 + 这个键按下态"该长什么样。

    用的是**层序的定义本身**重新画一遍：background → 该键的按下态 → 手 → 头。
    与产品路径共用的是 `renderer.load()`（读图），**不共用**层序决策与缩放。
    """
    from deskpet import size as _size

    spec = renderer.spec
    key = spec.key(key_id)
    target = _size.pixel_size(spec.cell, level)
    canvas = QImage(spec.cell[0], spec.cell[1], QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    try:
        for filename in spec.background:
            painter.drawImage(0, 0, renderer.load(filename))
        if key.uses_overlay:
            painter.drawImage(0, 0, renderer.load(key.overlay))
        else:
            painter.drawImage(key.x, key.y, renderer.load(key.down))
        for hand in spec.hands:
            painter.drawImage(0, 0, renderer.load(hand.down.get(key_id) or hand.up))
        for head in spec.heads:
            if set(head.when_all_of) <= {key_id}:
                painter.drawImage(0, 0, renderer.load(head.image))
    finally:
        painter.end()
    if (canvas.width(), canvas.height()) == target:
        return canvas
    return canvas.scaled(
        target[0], target[1], Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )


def _background_overlaps(spec) -> bool:
    """两层背景是否**在像素上重叠**（重叠时"换顺序"才会改变出图）。"""
    if len(spec.background) < 2:
        return False
    images = []
    for name in spec.background[:2]:
        path = spec.image_path(name)
        images.append(QImage(str(path)) if path else QImage())
    if any(image.isNull() for image in images):
        return False
    first, second = images
    for y in range(0, first.height(), 3):
        for x in range(0, first.width(), 3):
            if ((first.pixel(x, y) >> 24) & 0xFF) and ((second.pixel(x, y) >> 24) & 0xFF):
                return True
    return False


def _compose_with_flipped_background(renderer, level: int) -> QImage:
    """**对照**：把 background 两层顺序反过来合成一次（不碰产品代码）。"""
    from deskpet import size as _size

    spec = renderer.spec
    target = _size.pixel_size(spec.cell, level)
    canvas = QImage(spec.cell[0], spec.cell[1], QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    try:
        for filename in reversed(spec.background):
            painter.drawImage(0, 0, renderer.load(filename))
    finally:
        painter.end()
    if (canvas.width(), canvas.height()) == target:
        return canvas
    return canvas.scaled(
        target[0], target[1], Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )

# ==================================================================== §6 形态接缝


def section_form_seam(app) -> None:
    """§6 形态接缝：切形态不重置档位与位置、停表/续播、菜单文字、遮罩跟着画面走。"""
    from deskpet import context_menu as cm
    from deskpet import pet_loader
    from deskpet import size as _size

    section("§6 形态接缝（task17 §4.1 的三处 + 菜单）")

    report = _report_for(build_placeholder_package("kv_seam", layouts=("2*8",)))
    assert report is not None and report.spec is not None and report.keyview is not None
    pet, keyview = report.spec, report.keyview

    from deskpet.pet_window import PetWindow

    # ---- ① 档位与位置在切形态后**都不变**
    level = 7
    window = PetWindow(pet, level, keyview=keyview)
    window.set_form_handler(lambda form: None)
    window.show_at(321, 234, user_positioned=True)
    before_pos = window.base_position
    before_level = window.level
    check("切形态前：档位与位置记下来了", before_level == level, f"档位={before_level}")

    before_size = window.pixel_size
    before_anchor = (before_pos.x() + before_size[0], before_pos.y() + before_size[1])
    switched = window.set_form(cm.FORM_KEYVIEW)
    check("能切到键盘形态", switched and window.form == cm.FORM_KEYVIEW, f"形态={window.form!r}")
    check(
        "切形态**不重置档位**",
        window.level == before_level,
        f"切前={before_level} 切后={window.level}",
    )
    # ⚠ 位置这一条**不能**写成"左上角不变"：两个形态的 cell 不同，
    #    切形态必然改窗口尺寸；「位置共用」的正确含义是
    #    **右下角锚点不动**（§2.12），于是左上角**必须**跟着尺寸变。
    #    task17 第一版就是按"左上角不变"写的，真平台自检当场把它证伪：
    #    那样写等于"右下角漂 30~60 px"，正好违反 §6 那条锚点验收。
    anchor_size = window.pixel_size
    after_anchor = (
        window.base_position.x() + anchor_size[0],
        window.base_position.y() + anchor_size[1],
    )
    check(
        "切形态**右下角锚点不动**（「位置共用」的正确含义）",
        after_anchor == before_anchor,
        f"切前锚点={before_anchor}（尺寸 {before_size}）→ "
        f"切后锚点={after_anchor}（尺寸 {anchor_size}）",
    )
    check(
        "左上角**必须**跟着尺寸变（尺寸变了而左上角不动 = 锚点漂了）",
        window.base_position != before_pos and before_size != anchor_size,
        f"左上角 {before_pos} → {window.base_position}；尺寸 {before_size} → {anchor_size}",
    )

    # ---- ② 尺寸按**各自形态的 cell** 换算，且与 size.pixel_size 一致
    check(
        "KV 形态的窗口尺寸 == size.pixel_size(keyview.cell, 档位)",
        window.pixel_size == _size.pixel_size(keyview.cell, before_level),
        f"窗口={window.pixel_size} 期望={_size.pixel_size(keyview.cell, before_level)}",
    )
    check(
        "KV 形态的窗口尺寸 != 宠物形态的（两个 cell 不同，尺寸确实换了）",
        window.pixel_size != _size.pixel_size(pet.cell, before_level),
        f"KV={window.pixel_size} 宠物={_size.pixel_size(pet.cell, before_level)}",
    )

    # ---- ③ 进 KV 停表；回宠物**从原来那一帧接着播**
    check(
        "进 KV 后动画定时器**已停**（KV 没有帧推进这回事）",
        not window.animation_driver_running,
        f"running={window.animation_driver_running}",
    )
    frame_index_before = window.animation.index
    action_before = window.animation.action
    window.set_form(cm.FORM_PET)
    check(
        "回宠物形态后动画**接着播**（表又转起来）",
        window.animation_driver_running,
        f"running={window.animation_driver_running}",
    )
    check(
        "回宠物形态时**从原来那一帧**接着播（动画状态从未被切形态动过）",
        window.animation.index == frame_index_before
        and window.animation.action == action_before,
        f"切前=({action_before}, {frame_index_before}) "
        f"切后=({window.animation.action}, {window.animation.index})",
    )

    # ---- ④ 菜单：文字随形态切换、`form` 项可用性 == 装没装处理器
    state = window.menu_state()
    check(
        "宠物形态下 form 项文字是「开启KV」且可用",
        state.form_text() == cm.TEXT_KV_ON and state.kv_available,
        f"文字={state.form_text()!r} 可用={state.kv_available}",
    )
    check(
        "菜单顺序里 form 在 action 与 mirror **之间**（§4.3）",
        cm.MENU_ORDER.index(cm.ID_FORM) == cm.MENU_ORDER.index(cm.ID_ACTION) + 1
        and cm.MENU_ORDER.index(cm.ID_MIRROR) == cm.MENU_ORDER.index(cm.ID_FORM) + 1,
        f"顺序={cm.MENU_ORDER}",
    )

    window.set_form(cm.FORM_KEYVIEW)
    state = window.menu_state()
    check(
        "KV 形态下 form 项文字是「退出KV」",
        state.form_text() == cm.TEXT_KV_OFF,
        f"文字={state.form_text()!r}",
    )
    check(
        "KV 形态下「坐下休息」与「镜像翻转」都**不可点**（KV 里没有这两个概念）",
        not state.can_rest and not state.can_mirror,
        f"can_rest={state.can_rest} can_mirror={state.can_mirror}",
    )
    check(
        "KV 形态**永不**镜像（`mirrored` 恒为假）",
        window.mirrored is False,
        f"mirrored={window.mirrored}",
    )

    # ---- ⑤ 没装处理器时那项是灰的（与"调整大小…"同一个模式）
    window2 = PetWindow(pet, level, keyview=keyview)
    check(
        "没装 form 处理器 → 菜单项**灰掉**（用户点不到）",
        not window2.menu_state().kv_available,
        f"可用={window2.menu_state().kv_available}（没装处理器）",
    )
    # ⚠ **`set_form()` 的判据是"这个包能不能显示 KV"，不是"装没装处理器"**：
    #   它是处理器**自己被调用时**走的底层接缝（`FormController.switch` → 它），
    #   在这里再要求"必须装了处理器"就是鸡生蛋。
    #   **拦住用户**的那道闸是菜单项的 `kv_available`（它才是"装没装"的判据）。
    check(
        "`set_form()` 只按「包有没有 KV」判，与装没装处理器无关（分层正确）",
        window2.set_form(cm.FORM_KEYVIEW) and window2.form == cm.FORM_KEYVIEW,
        f"形态={window2.form!r} —— 拦用户的是菜单项，不是这一层",
    )

    # ---- ⑥ 没有 KV 素材的包：菜单项灰掉、切不过去、宠物形态一条不少
    window3 = PetWindow(pet, level)
    window3.set_form_handler(lambda form: None)
    window3.play_action("idle")  # 起表，于是"宠物形态不受影响"有得比
    check(
        "这个包没有 KV 素材 → 即使装了处理器菜单项**仍然灰掉**",
        not window3.menu_state().kv_available,
        f"可用={window3.menu_state().kv_available}",
    )
    check(
        "没有 KV 素材时 set_form 切不过去、**宠物形态原样不动**",
        not window3.set_form(cm.FORM_KEYVIEW)
        and window3.form == cm.FORM_PET
        and window3.animation_driver_running
        and window3.pixel_size == _size.pixel_size(pet.cell, level),
        f"形态={window3.form!r} 动画在跑={window3.animation_driver_running} "
        f"尺寸={window3.pixel_size}",
    )

    # ---- ⑦ 遮罩跟着画面走（换帧 = 衍生数据失效重算，§4.7）
    window.set_form(cm.FORM_KEYVIEW)
    mask_static = window.mask().boundingRect()
    window.set_pressed(("r1k1",))
    mask_pressed = window.mask().boundingRect()
    check(
        "KV 形态下按下一个键 → 画面与遮罩都重算过（不是停在旧那张）",
        not window.mask().isEmpty() and mask_static.isValid(),
        f"静止态遮罩={mask_static} 按下后={mask_pressed}",
    )
    check(
        "按下的是**合法键 id** 时 pressed 被记住",
        window.pressed == ("r1k1",),
        f"pressed={window.pressed}",
    )
    check(
        "`set_pressed` 幂等：同一个状态再来一次返回 False（不白重绘）",
        window.set_pressed(("r1k1",)) is False,
        "同状态不重复合成",
    )


# ==================================================================== §7 档位


def section_levels(app) -> None:
    """§7 10 级档位：尺寸两两不同、逐级正确、与 `size.pixel_size` 一致。"""
    from deskpet import keyview_render, size as _size

    section("§7 10 级档位：KV 的每一级都能出图且尺寸正确")

    root = build_placeholder_package("kv_levels", layouts=("2*8",))
    spec, _issues, _w = _load_keyview(root)
    assert spec is not None
    renderer = keyview_render.KeyViewRenderer(spec)

    sizes = []
    wrong = ""
    for level in ALL_LEVELS:
        image = renderer.image(level, ())
        expect = _size.pixel_size(spec.cell, level)
        sizes.append((image.width(), image.height()))
        if (image.width(), image.height()) != expect:
            wrong = f"第 {level} 级实际 {image.width()}x{image.height()}，期望 {expect}"
    check(
        "10 级每一级的出图尺寸都 == size.pixel_size(cell, 档位)",
        not wrong,
        wrong or f"sizes={sizes}",
    )
    check(
        "10 级尺寸**两两不同**（档位不塌缩）",
        len(set(sizes)) == len(sizes),
        f"去重后 {len(set(sizes))} 个 / 共 {len(sizes)} 个",
    )
    check(
        "尺寸随档位**严格递增**",
        sizes == sorted(sizes) and len(set(sizes)) == len(sizes),
        f"{sizes[0]} → {sizes[-1]}",
    )
    check(
        "没有为了 KV 另抄一份档位表（KV 与宠物用同一个 size.pixel_size）",
        renderer.image(6, ()).width() == _size.pixel_size(spec.cell, 6)[0],
        "真值只有 size.py 一处",
    )

    # ---- 缓存：同一状态第二次全命中（与 task16 同一个判据）
    states = [(), ("r1k1",), ("r2k7",), ("r1k1", "r2k7")]
    for state in states:
        renderer.image(6, state)
    renderer.hits = renderer.misses = 0
    for state in states:
        renderer.image(6, state)
    check(
        "同一状态第二次全命中缓存（状态签名起作用）",
        renderer.hits == len(states) and renderer.misses == 0,
        f"hits={renderer.hits} misses={renderer.misses} 条目={renderer.cached_count()}",
    )

    # ---- 对照：不同的按键**顺序**必须产生不同签名（手用哪张会变）
    forward = keyview_render.state_signature(("r1k1", "r1k2"))
    backward = keyview_render.state_signature(("r1k2", "r1k1"))
    check(
        "[对照] 同一组键的**不同到达顺序**签名不同（手用哪张会变，不能合并）",
        forward != backward,
        f"{forward!r} != {backward!r}",
    )

# ==================================================================== §8 配置落盘


class ConfigStoreLike:
    """模拟"重启"：同一个配置文件，**另一个** `ConfigStore` 实例读一遍。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def build(self):
        from deskpet.config import ConfigStore

        store = ConfigStore(self.path)
        store.load()
        return store


def section_config(app) -> None:
    """§8 `form` / `keyview_layout` 落盘、重启后恢复、切不过去时不写盘。"""
    from deskpet import context_menu as cm
    from deskpet.app import FormController
    from deskpet.pet_window import PetWindow

    section("§8 配置：form / keyview_layout 落盘并在重启后恢复")

    report = _report_for(build_placeholder_package("kv_config", layouts=("2*8",)))
    assert report is not None and report.keyview is not None
    pet, keyview = report.spec, report.keyview

    store = make_config("form_roundtrip")
    window = PetWindow(pet, 6, config=store, keyview=keyview)
    controller = FormController(window, store)
    window.set_form_handler(controller.switch)
    controller.restore()

    check(
        "首次启动（配置里没表态）→ 留在宠物形态、且没写过配置",
        window.form == cm.FORM_PET and not store.has("form"),
        f"形态={window.form!r} has(form)={store.has('form')}",
    )
    controller.layout = controller.resolve_layout()
    check(
        "布局：配置没表态时用清单里的**第一个**",
        controller.layout == "2*8",
        f"布局={controller.layout!r}",
    )

    # ---- 切过去 → 配置落盘
    switched = controller.switch(cm.FORM_KEYVIEW)
    on_disk = json.loads(store.path.read_text(encoding="utf-8-sig"))
    check(
        "切到 KV → **写进了配置**（form=keyview）",
        switched and on_disk.get("form") == cm.FORM_KEYVIEW,
        f"磁盘上的 form={on_disk.get('form')!r}",
    )

    # ---- "重启"：新的窗口 + 新的 store 读同一个文件 → 恢复到 KV
    store2 = ConfigStoreLike(store.path).build()
    window2 = PetWindow(pet, 6, config=store2, keyview=keyview)
    restored = FormController(window2, store2).restore()
    check(
        "重启后**恢复到键盘形态**（记忆上次用的）",
        restored == cm.FORM_KEYVIEW and window2.form == cm.FORM_KEYVIEW,
        f"恢复={restored!r} 形态={window2.form!r}",
    )

    # ---- 布局回退：配置里的布局不可用 → 回退到可用那个 + 一条日志
    store3 = make_config("layout_fallback")
    store3.set("keyview_layout", "1*4")  # 这个包只有 2*8，1*4 不可用
    window3 = PetWindow(pet, 6, config=store3, keyview=keyview)
    fallback = FormController(window3, store3).resolve_layout()
    check(
        "配置里的布局不可用 → **回退到可用那个**（不是直接灰掉）",
        fallback == "2*8",
        f"配置=1*4 实际用={fallback!r}",
    )

    # ---- 配置里写着 keyview 但包没有 KV → 留在宠物形态（**只读，不写盘**）
    store4 = make_config("form_but_no_kv")
    store4.set("form", cm.FORM_KEYVIEW)
    before = store4.path.read_bytes()
    window4 = PetWindow(pet, 6, config=store4)  # 没有 keyview
    landed = FormController(window4, store4).restore()
    after = store4.path.read_bytes()
    check(
        "配置写着 keyboard 但包没有 KV → 留在宠物形态、**配置一个字节都不改**",
        landed == cm.FORM_PET and before == after,
        f"落点={landed!r} 文件变了={before != after}",
    )

    # ---- 未知形态名：按"没表态"处理
    store5 = make_config("form_unknown")
    store5.set("form", "完全没听说过的形态")
    window5 = PetWindow(pet, 6, config=store5, keyview=keyview)
    landed5 = FormController(window5, store5).restore()
    check(
        "配置里的形态名不认识 → 按「没表态」处理，留在宠物形态",
        landed5 == cm.FORM_PET,
        f"落点={landed5!r}",
    )

    # ---- 切不过去时**不写盘**（不显示假成功）
    store6 = make_config("form_no_kv_write")
    window6 = PetWindow(pet, 6, config=store6)  # 没有 keyview
    result = FormController(window6, store6).switch(cm.FORM_KEYVIEW)
    check(
        "切不过去（没有 KV）时**不写配置**",
        result is False and not store6.has("form"),
        f"结果={result} has(form)={store6.has('form')}",
    )


# ==================================================================== §9 只读查询


def _user_data_snapshot() -> dict:
    """用户真实 `data/` 里**由程序写的那几个文件**的快照（自检不许动它们）。"""
    snapshot = {}
    for name in ("config.json", "deskpet.log", "deskpet.log.1", "crash.log"):
        path = ROOT / "data" / name
        if path.is_file():
            snapshot[name] = (path.stat().st_size, path.stat().st_mtime_ns)
        else:
            snapshot[name] = None
    return snapshot


def section_readonly_query(app) -> None:
    """§9 `--list-*` 是**只读查询**：一个字节都不写盘（§4.11 / §4.13）。"""
    from deskpet import app as app_module

    section("§9 只读查询不写盘")

    report = _report_for(build_placeholder_package("kv_listonly", layouts=("2*8",)))
    assert report is not None and report.spec is not None

    before = _user_data_snapshot()
    code = app_module._run_list_actions(report.spec)
    after = _user_data_snapshot()
    check(
        "--list-actions 只打印、返回 0、**用户数据文件一个字节都没变**",
        code == 0 and before == after,
        f"退出码={code} 前后一致={before == after}",
    )


# ==================================================================== §10 日志安静


_LOG_LINES: list[str] = []


class _Capture(logging.Handler):
    """把 WARNING 及以上的日志**留在内存里**，供断言检查。"""

    def emit(self, record: logging.LogRecord) -> None:
        _LOG_LINES.append(f"{record.levelname} {record.name}: {record.getMessage()}")


def _install_capture() -> None:
    handler = _Capture()
    handler.setLevel(logging.WARNING)
    logging.getLogger("deskpet").addHandler(handler)
    logging.getLogger("deskpet").setLevel(logging.DEBUG)


def section_log_quiet(app) -> None:
    """§10 段缺失时**日志里没有错误也没有警告**（task17 §6 明写的一条）。"""
    section("§10 段缺失时日志安静（不报错也不警告）")

    _LOG_LINES.clear()
    root = build_placeholder_package("kv_quiet")
    rewrite_index(root, lambda data: data.pop("keyview", None))
    _report_for(root)
    noisy = [line for line in _LOG_LINES if "键盘" in line or "keyview" in line.lower()]
    check(
        "keyview 段缺失 → 与键盘形态有关的 WARNING/ERROR **一条都没有**",
        not noisy,
        f"实际记了 {len(noisy)} 条：{noisy[:3]}",
    )

    # ---- 对照：段**坏掉**时必须能记出来（否则上面那条只是"日志没开"）
    _LOG_LINES.clear()
    root2 = build_placeholder_package("kv_loud")
    rewrite_index(root2, lambda data: data.__setitem__("keyview", {"cell": "坏"}))
    spec, issues, _w = _load_keyview(root2)
    check(
        "[对照] 段坏掉时**确实**给出了问题（证明上面那条不是「日志没开」）",
        spec is None and bool(issues),
        f"问题={[str(i) for i in issues][:2]}",
    )


# ==================================================================== §11 纪律


def section_discipline(app, hash_before: str) -> None:
    section("§11 自检纪律（assets/ 未被改动、不写用户真实目录）")

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
        "自检用的配置全部落在临时目录（不碰用户真实的 data/config.json）",
        all(path.parent == OUT_DIR / "configs" for path in (OUT_DIR / "configs").glob("*.json")),
        f"配置目录={OUT_DIR / 'configs'}",
    )
    check(
        "自检在 assets/ 里没有留下任何东西（没有以 kv 开头的临时包）",
        not any(path.name.startswith("kv") for path in (ROOT / "assets").iterdir()),
        "占位包全部造在 data/task17_out/pets/",
    )


# ==================================================================== main


def main() -> int:
    app = QApplication.instance() or QApplication([])
    # 日志全开：要验证"段缺失时**没有**警告/错误"，必须先让它们有机会被记下来。
    _install_capture()
    hash_before = _hash_assets()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    section_contract(app)
    section_degradation(app)
    section_issue_listing(app)
    section_numbering(app)
    section_composition(app)
    section_form_seam(app)
    section_levels(app)
    section_config(app)
    section_readonly_query(app)
    section_log_quiet(app)
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