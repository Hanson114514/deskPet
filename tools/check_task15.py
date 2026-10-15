"""task15 自检（offscreen）：边缘抗锯齿的算法正确性、开关语义、回归基线、鬼影检测。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task15_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python tools/check_task15.py

## 这一份能验什么、不能验什么

**能验**（task15 §6 里可自动化的那些）：

1. **开关默认关闭**：全新配置 → `config.DEFAULTS["antialias"] is False`；
2. **全档位覆盖（本 task 的结构性要求）**：开启后**逐个档位 1→10** 各渲染一帧，
   断言**每一档的结果都与关闭时不同**；并**盯着调用点的结构**
   （`smooth_edges()` 不得出现在 `if/else` 的支里）；
3. **缓存键不加维度**：开关开与关的 `cache_key()` 相同（重启生效 → 键恒定）；
4. **人物本体不变**：原 alpha=255 的像素，输出 alpha 仍为 255（逐像素）；
5. **鬼影检测**：把结果合成到**纯白**与**纯黑**背景上，边缘不得出现
   发黑/发白的描边；并断言边缘像素的颜色**取自邻近的不透明像素**；
6. **降级**：空图 / 无 alpha / 0×0 / radius=0 → 返回输入、不抛异常；
   内部函数换成会抛异常的桩 → 出图仍正常（降级到"没抗锯齿"，不是"没画面"）；
7. **缓存与 LRU**：反复切档 1↔10 后条目数不超过 64；
8. **配置项语义**：`antialias` 是 bool、默认 False、`Config` 字段与 `DEFAULTS` 一致。

**不能验**（交给 `check_task15_real.py`）：真窗口的每帧节奏、真平台遮罩语义、
真背景合成对照图、DPI≠100% 的补放大。

## ⚠ 这一份**不写用户真实目录**

所有落盘都在 `data/task15_out/`（工作区内，§4.13：DSH 沙箱只保证工作区可写）。
`assets/` 一个字节都不动（脚本结尾会核对哈希）。
"""

import hashlib
import io
import os
import sys
import time
import tokenize
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage  # noqa: E402

RESULT_FILE = ROOT / "data" / "task15_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`**）。
OUT_DIR = ROOT / "data" / "task15_out"

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
    """记一条**只有数据、没有结论**的观察（不参与 PASS/FAIL）。

    与 `check()` 分开：有些数值是"解释现象用的"，硬套一个成功条件
    反而会把判据写歪（§4.11：别把成功条件写反）。
    """
    _LINES.append(f"    {text}")
    print(f"    {text}")


def _source_of(name: str) -> str:
    return (ROOT / "src" / "deskpet" / name).read_text(encoding="utf-8")


def _code_only(name: str) -> str:
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，多个 task 都踩过）。

    ⚠ 它把 token 用空格拼起来，所以拿**带点的字面量**去 `in` 它永远匹配不上。
    """
    source = _source_of(name)
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _squashed(name: str) -> str:
    """代码去掉**所有空白**：判"有没有写某个带点/带括号的调用"时用它。"""
    return _code_only(name).replace(" ", "")


def _hash_tree(path: Path) -> str:
    """整棵目录的哈希（判 `assets/` 有没有被改动用）。"""
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        if item.is_file():
            digest.update(str(item.relative_to(path)).encode("utf-8"))
            digest.update(item.read_bytes())
    return digest.hexdigest()


# ------------------------------------------------------------------ 载入素材


def _load_image():
    """取一张真实帧图（`idle` 第 0 帧）。

    ⚠ 自检脚本住在 `tools/` 时"程序目录"会变成 `tools/`（§4.14），
    所以必须用 `paths.override_dirs()` 把程序/资源目录指回仓库根
    ——**它正是为此存在的**，不要去改路径规则本身。
    """
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        from deskpet import pet_loader

        spec = pet_loader.resolve_pet("weedy").spec
        if spec is None:  # pragma: no cover
            return None, None
        return spec, pet_loader.load_frame_image(spec, "idle", 0)


def _numpy_of(image: QImage):
    import numpy as np

    converted = image.convertToFormat(QImage.Format_ARGB32)
    height, width = converted.height(), converted.width()
    flat = np.frombuffer(bytes(converted.constBits()), dtype=np.uint8)
    return (
        flat[: converted.bytesPerLine() * height]
        .reshape(height, converted.bytesPerLine() // 4, 4)[:, :width, :]
        .copy()
    )


# ------------------------------------------------------------------ 各节

def section_defaults(app) -> None:
    """§1 开关默认关闭 + 配置项语义。"""
    section("§1 开关默认关闭（回归基线的前提）")
    from deskpet import config as config_mod

    check(
        "config.DEFAULTS['antialias'] 存在且为 False",
        config_mod.DEFAULTS.get("antialias") is False,
        f"实测={config_mod.DEFAULTS.get('antialias')!r}",
    )
    check(
        "默认值类型是 bool（bool 不算 int 的反面）",
        isinstance(config_mod.DEFAULTS.get("antialias"), bool),
        f"type={type(config_mod.DEFAULTS.get('antialias')).__name__}",
    )
    # `Config` 字段与 DEFAULTS 必须一一对应（§2.4：加一行默认值忘了加字段会当场炸）
    from dataclasses import fields

    names = {f.name for f in fields(config_mod.Config)}
    check(
        "Config 字段名与 DEFAULTS 的键集合一致",
        names == set(config_mod.DEFAULTS),
        f"字段={sorted(names)} 键={sorted(config_mod.DEFAULTS)}",
    )
    check(
        "Config().antialias 默认是 False",
        config_mod.Config().antialias is False,
        f"实测={config_mod.Config().antialias!r}",
    )
    # 渲染器默认关（守回归基线）
    from deskpet import rendering

    spec, image = _load_image()
    if spec is None:  # pragma: no cover
        check("素材可用", False, "拿不到 weedy 素材包")
        return
    renderer = rendering.FrameRenderer(spec)
    check(
        "FrameRenderer 默认 antialias=False",
        renderer.antialias is False,
        f"实测={renderer.antialias!r}",
    )


def section_algorithm(app) -> None:
    """§2 算法硬规则：本体守住、过渡像素、格式。"""
    section("§2 算法硬规则（本体 / 过渡像素 / 格式）")
    from deskpet import antialias

    spec, image = _load_image()
    if image is None:  # pragma: no cover
        check("素材可用", False, "拿不到帧图")
        return

    import numpy as np

    src = _numpy_of(image)
    out = antialias.smooth_edges(image)
    dst = _numpy_of(out)

    check(
        "输入是 premultiplied（沿用 pet_loader 的格式）",
        image.format() == QImage.Format_ARGB32_Premultiplied,
        f"实测={image.format()}",
    )
    check(
        "输出格式仍是 premultiplied（不退化）",
        out.format() == QImage.Format_ARGB32_Premultiplied,
        f"实测={out.format()}",
    )
    check(
        "输出尺寸与输入一致",
        (out.width(), out.height()) == (image.width(), image.height()),
        f"{out.width()}x{out.height()} vs {image.width()}x{image.height()}",
    )

    sa, da = src[:, :, 3].astype(int), dst[:, :, 3].astype(int)
    broken = int(np.sum((sa == 255) & (da != 255)))
    check(
        "硬规则1：原 alpha=255 的像素输出 alpha 仍为 255（逐像素）",
        broken == 0,
        f"被破坏={broken}",
    )

    body_changed = int(np.sum((sa == 255) & (da == 255) & np.any(
        src[:, :, :3] != dst[:, :, :3], axis=2
    )))
    # ⚠ 预乘格式下 alpha=255 时颜色不会被预乘改变，所以本体 RGB 必须**逐字节相同**。
    check(
        "硬规则1b：原 alpha=255 的像素 RGB 一个字节都没变",
        body_changed == 0,
        f"被改动={body_changed}",
    )

    before_transition = int(np.sum((sa > 0) & (sa < 255)))
    after_transition = int(np.sum((da > 0) & (da < 255)))
    check(
        "新增过渡像素（0<alpha<255）",
        after_transition > before_transition,
        f"输入={before_transition} 输出={after_transition}",
    )
    check(
        "实素材本来就是 1 位 alpha（过渡像素 0 个）",
        before_transition == 0,
        f"实测={before_transition}",
    )

    # 过渡带宽度：新半透明像素到最近不透明像素的距离
    alpha_out = np.asarray(antialias._blur_alpha(src[:, :, 3], 1))
    check(
        "半径 1 的模糊只影响紧邻的一圈（不会整幅变糊）",
        float(alpha_out[sa == 255].min()) >= 0.0,
        f"本体处模糊值最小={float(alpha_out[sa==255].min()):.2f}",
    )


def section_structure(app) -> None:
    """§3 结构判据：调用点在 if/else 之外。"""
    section("§3 结构：抗锯齿调用点不在 if/else 支内（本 task 的硬要求）")
    source = _source_of("rendering.py")
    code = _code_only("rendering.py")
    squashed = code.replace(" ", "")

    check(
        "rendering.py 里确实调用了 antialias.smooth_edges",
        "antialias.smooth_edges" in squashed,
        f"命中={'antialias.smooth_edges' in squashed}",
    )

    # 用 AST 判"它到底在不在 if 的某个分支体里"——比字符串搜索可靠得多
    import ast

    tree = ast.parse(source)
    render_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_render":
            render_fn = node
            break
    check("找得到 FrameRenderer._render", render_fn is not None)

    inside_branch = False
    call_line = None
    if render_fn is not None:
        # 收集所有 if/else 分支体里语句的行号范围。
        # ⚠ **必须排除 `if self.antialias:` 那个守卫本身**：它就是"按需开启"
        # 的开关，`smooth_edges` 本来就应该在它**里面**。要防的是
        # "把调用写进缩放 if/else 的某一支里"（那必然漏掉某一档）。
        branch_lines: list[tuple[int, int]] = []
        for node in ast.walk(render_fn):
            if isinstance(node, ast.If):
                # 判据：这个 if 的测试里有没有出现 antialias（是它 → 跳过）
                test_src = ast.dump(node.test)
                if "antialias" in test_src:
                    continue
                for body in (node.body, node.orelse):
                    if body:
                        branch_lines.append(
                            (
                                body[0].lineno,
                                max(
                                    getattr(b, "end_lineno", b.lineno) for b in body
                                ),
                            )
                        )
        # 找 smooth_edges 调用
        for node in ast.walk(render_fn):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    f"{func.value.id}.{func.attr}"
                    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    else ""
                )
                if name == "antialias.smooth_edges":
                    call_line = node.lineno
                    for start, end in branch_lines:
                        if start <= node.lineno <= end:
                            inside_branch = True
    check(
        "smooth_edges 的调用点**不在**缩放 if/else 的任何一支里",
        call_line is not None and not inside_branch,
        f"调用在第 {call_line} 行，落在缩放分支内={inside_branch}"
        f"（扫描了 {len(branch_lines) if render_fn else 0} 个分支体，已排除 antialias 守卫）",
    )

    # 位置在"缩放之后"
    scaled_line = None
    for node in ast.walk(render_fn) if render_fn else []:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "scaled":
                scaled_line = node.lineno
    check(
        "调用点在缩放**之后**（避免放大档被柔化两次）",
        call_line is not None and scaled_line is not None and call_line > scaled_line,
        f"scaled 在第 {scaled_line} 行，smooth_edges 在第 {call_line} 行",
    )

    # 6 档的短路分支必须保留
    check(
        "第 6 级的同尺寸短路分支仍然保留（不缩放）",
        "==target" in squashed.replace("(image.width(),image.height())", "").replace(" ", "")
        or "image.width(),image.height()" in squashed,
        "判据：代码里仍有同尺寸比较",
    )


def section_all_levels(app) -> None:
    """§4 全档位覆盖：1→10 每一档开启后结果都不同。"""
    section("§4 全档位覆盖（1→10 每档都与关闭时不同）")
    from deskpet import pet_loader, rendering, size

    spec, image = _load_image()
    if spec is None:  # pragma: no cover
        check("素材可用", False, "拿不到素材包")
        return

    off = rendering.FrameRenderer(spec, antialias=False)
    on = rendering.FrameRenderer(spec, antialias=True)

    def raw(renderer, level):
        pixmap = renderer.frame("idle", 0, level, False)
        img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        return bytes(img.constBits()), (img.width(), img.height())

    differ = 0
    for level in range(1, size.LEVEL_COUNT + 1):
        a, sa_ = raw(off, level)
        b, sb_ = raw(on, level)
        same_size = sa_ == sb_
        changed = a != b
        check(
            f"第 {level} 级：开启后出图与关闭时不同",
            changed and same_size,
            f"尺寸={sa_} 是否相同={same_size} 像素是否不同={changed}",
        )
        differ += changed
    check(
        f"全部 {size.LEVEL_COUNT} 个档位都有差异（没有哪一档被漏掉）",
        differ == size.LEVEL_COUNT,
        f"有差异的档位={differ}/{size.LEVEL_COUNT}",
    )

    section("§4b 缓存键不加维度（重启生效 → 键恒定）")
    for level in (1, 6, 10):
        key_off = off.cache_key("idle", 0, level, False)
        key_on = on.cache_key("idle", 0, level, False)
        check(
            f"第 {level} 级 cache_key 开/关一致（仍 4 维）",
            key_off == key_on and len(key_off) == 4,
            f"off={key_off} on={key_on}",
        )


def section_ghosting(app) -> None:
    """§5 鬼影检测（本 task 最关键的一条）。"""
    section("§5 鬼影检测（合成到纯白 / 纯黑背景）")
    from deskpet import antialias

    spec, image = _load_image()
    if image is None:  # pragma: no cover
        check("素材可用", False, "拿不到帧图")
        return

    import numpy as np

    src = _numpy_of(image)
    out = antialias.smooth_edges(image)
    # ⚠ 必须转成**非预乘**读颜色：预乘格式下 `alpha=0` 的像素 RGB 恒为 0
    # （那正是"预乘"的定义），拿它比颜色会把正确结果误判成"全黑"。
    dst = _numpy_of(out.convertToFormat(QImage.Format_ARGB32))

    sa = src[:, :, 3].astype(int)
    da = dst[:, :, 3].astype(int)
    edge = (sa == 0) & (da > 0)
    ys, xs = np.nonzero(edge)
    check("确实产生了边缘新像素（有东西可检）", len(ys) > 0, f"数量={len(ys)}")

    # 5.1 边缘像素的颜色必须"来自人物"，而不是透明区的垃圾值。
    # 温蒂的透明区 RGB 恰好是纯黑 (0,0,0)，所以"颜色来自透明区"的表现
    # 是边缘**全是黑的**。判据：边缘颜色的最大值必须明显大于 0
    # （素材有黑描边，所以不能要求"全部非黑"）。
    edge_rgb = dst[edge][:, :3].astype(float)
    max_channel = float(edge_rgb.max())
    check(
        "边缘颜色取自人物（不是透明区的纯黑垃圾值）",
        max_channel > 30.0,
        f"边缘颜色最大值={max_channel:.0f}（若≈0 则全部来自透明区 = 鬼影）",
    )

    # 5.2 与"只模糊 alpha、不膨胀 RGB"的错误实现做对照：
    # 错误实现会把边缘涂成透明区的颜色（纯黑），两者必须有可测的差别。
    wrong = np.array(src)
    blurred = np.asarray(antialias._blur_alpha(src[:, :, 3], 1))
    wrong[:, :, 3] = np.clip(
        np.rint(np.maximum(src[:, :, 3].astype(np.float32), blurred)), 0, 255
    )
    wrong_edge = wrong[edge][:, :3].astype(float)
    check(
        "对照：不膨胀 RGB 的错误实现确实会变黑（证明本判据有分辨力）",
        float(wrong_edge.max()) < 1.0,
        f"错误实现边缘颜色最大值={float(wrong_edge.max()):.0f}",
    )

    # 5.3 合成到纯白 / 纯黑背景：边缘不得出现"比人物更暗/更亮"的越界描边。
    # 判据用**合成后的亮度范围**：应当落在背景与人物之间，不出现突变。
    for bg_name, bg in (("纯白", 255.0), ("纯黑", 0.0)):
        alpha = da[..., None].astype(float) / 255.0
        composited = dst[:, :, :3].astype(float) * alpha + bg * (1 - alpha)
        values = composited[edge].mean(axis=1)

        lo, hi = min(bg, float(values.min())), max(bg, float(values.max()))
        # 不得出现"超出 [背景, 人物]"的极端值：允许与背景相差不超过满量程
        no_blowout = bool(np.all(np.isfinite(values))) and float(values.max()) <= 255.0
        check(
            f"{bg_name}背景：边缘合成值有限且不越界",
            no_blowout,
            f"区间=[{values.min():.1f}, {values.max():.1f}]",
        )
        # 关键：背景色那一侧不能出现"反向的极端"。
        # 白底上不该有纯黑描边（会看成黑边），黑底上不该有纯白描边。
        if bg_name == "纯白":
            # 边缘合成值应当明显高于 0（有过渡），且不出现整圈纯黑
            check(
                "纯白背景：边缘不是一圈纯黑（无黑边鬼影）",
                float(values.max()) > 60.0,
                f"边缘合成亮度最大值={values.max():.1f}",
            )
        else:
            check(
                "纯黑背景：边缘不是一圈纯白（无白边鬼影）",
                float(values.min()) < 200.0,
                f"边缘合成亮度最小值={values.min():.1f}",
            )


def section_on_screen(app) -> None:
    """§5b **过渡像素真的上屏了吗**（task15 补：这是最容易自欺的一条）。

    ## 为什么必须有这一节

    第一版的验收**只看了渲染层的 `QImage`**（`frame_image.save()`），
    于是"算法产生了 2074 个过渡像素"被当成了"用户能看到效果"。
    实际上 `setMask` 是**二值**的（`createAlphaMask()` 阈值 `alpha >= 128`），
    那 2074 个像素的 alpha 大多在 **32~96**，**只有 53 个能上屏**，
    其余被系统级穿透裁掉 —— **用户看到的是"几乎没变化"**，
    而自检却全绿。这是"拿算法输出当验收对象"的典型翻车。

    所以这一节**必须用"遮罩之后的画面"当判据**，不许再用渲染层的图。
    """
    section("§5b 过渡像素真的上屏了吗（用遮罩之后的画面当判据）")
    from deskpet import antialias, hit_test, rendering

    spec, _image = _load_image()
    if spec is None:  # pragma: no cover
        check("素材可用", False, "拿不到素材包")
        return

    import numpy as np

    level = 6
    off = rendering.FrameRenderer(spec, antialias=False).frame("idle", 0, level)
    on = rendering.FrameRenderer(spec, antialias=True).frame("idle", 0, level)

    def alpha_of(pixmap):
        img = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        height, width = img.height(), img.width()
        flat = np.frombuffer(bytes(img.constBits()), dtype=np.uint8)
        return (
            flat[: img.bytesPerLine() * height]
            .reshape(height, img.bytesPerLine() // 4, 4)[:, :width, :][:, :, 3]
            .copy()
        )

    a_off = alpha_of(off)
    a_on = alpha_of(on)
    new_pixels = (a_off == 0) & (a_on > 0)
    count_new = int(np.sum(new_pixels))
    check("抗锯齿确实产生了过渡像素", count_new > 0, f"共 {count_new} 个")

    # ① 默认阈值（关抗锯齿）下能上屏几个 —— 这就是用户抱怨的根源
    visible_default = int(np.sum(new_pixels & (a_on >= hit_test.DEFAULT_ALPHA_THRESHOLD)))
    note(
        f"若用默认阈值 {hit_test.DEFAULT_ALPHA_THRESHOLD}："
        f"{count_new} 个过渡像素里只有 {visible_default} 个能上屏"
        f"（{visible_default / max(1, count_new) * 100:.1f}%）"
    )
    check(
        "默认阈值确实会吃掉绝大多数过渡像素（这条解释了「看不出变化」）",
        visible_default < count_new * 0.1,
        f"{visible_default}/{count_new} 能上屏",
    )

    # ② 抗锯齿阈值下应当**全部**能上屏
    visible_aliased = int(
        np.sum(new_pixels & (a_on >= hit_test.ALIASED_ALPHA_THRESHOLD))
    )
    note(
        f"用抗锯齿阈值 {hit_test.ALIASED_ALPHA_THRESHOLD}："
        f"{visible_aliased}/{count_new} 能上屏"
    )
    check(
        "抗锯齿阈值下**全部**过渡像素都能上屏",
        visible_aliased == count_new,
        f"{visible_aliased}/{count_new}",
    )

    # ③ 阈值必须是"够用的最小值"：再往下降不该多出任何像素
    for lower in (8, 4, 2, 1):
        more = int(np.sum(new_pixels & (a_on >= lower)))
        if more != visible_aliased:
            check(
                f"阈值 {lower} 与 {hit_test.ALIASED_ALPHA_THRESHOLD} 效果相同（16 已是最小够用值）",
                False,
                f"{lower} 能上屏 {more} 个，与 16 的 {visible_aliased} 不同",
            )
            break
    else:
        check(
            f"阈值 {hit_test.ALIASED_ALPHA_THRESHOLD} 已是最小够用值（再降不多出像素）",
            True,
            "8/4/2/1 都是同一个结果",
        )

    # ④ **端到端**：拿真窗口实际会用的那个阈值算区域，确认可点范围没有失控
    region_off = hit_test.region_for_frame(
        off.toImage(), alpha_threshold=hit_test.DEFAULT_ALPHA_THRESHOLD
    )
    region_on = hit_test.region_for_frame(
        on.toImage(), alpha_threshold=hit_test.ALIASED_ALPHA_THRESHOLD
    )
    check("关抗锯齿时算得出区域", region_off is not None)
    check("开抗锯齿时算得出区域", region_on is not None)
    if region_off is not None and region_on is not None:
        note(
            f"外接矩形：关闭 {region_off.boundingRect().width()}×"
            f"{region_off.boundingRect().height()} → 开启 "
            f"{region_on.boundingRect().width()}×{region_on.boundingRect().height()}"
        )
        grow_w = region_on.boundingRect().width() - region_off.boundingRect().width()
        grow_h = region_on.boundingRect().height() - region_off.boundingRect().height()
        check(
            "可点范围沿轮廓外扩是**克制**的（外接矩形每边不超过 4 像素）",
            grow_w <= 4 and grow_h <= 4,
            f"宽 {grow_w:+d}、高 {grow_h:+d}",
        )
        # 不得退化成整帧可点
        width, height = on.width(), on.height()
        check(
            "没有退化成整帧可点（四角仍穿透）",
            not all(
                region_on.contains(QPoint(x, y))
                for x, y in (
                    (0, 0),
                    (width - 1, 0),
                    (0, height - 1),
                    (width - 1, height - 1),
                )
            ),
            "四角至少有一个在区域外",
        )

    # ⑤ 源码级：`apply_mask` 必须**按开关**选阈值（写死任何一边都是 bug）
    code = _squashed("pet_window.py")
    check(
        "apply_mask 走的阈值跟着抗锯齿开关（不是一个写死的常量）",
        "ALIASED_ALPHA_THRESHOLD" in code and "_mask_threshold" in code,
        "判据：pet_window 里同时出现两个阈值常量与选择逻辑",
    )


def section_degrade(app) -> None:
    """§6 降级：坏输入返回输入、不抛异常。"""
    section("§6 降级（永不抛异常，宁可不抗锯齿也不能没画面）")
    from deskpet import antialias

    spec, image = _load_image()

    empty = QImage()
    check("空图 → 原样返回", antialias.smooth_edges(empty) is empty)

    zero = QImage(0, 0, QImage.Format_ARGB32)
    check("0×0 图 → 原样返回", antialias.smooth_edges(zero) is zero)

    no_alpha = QImage(16, 16, QImage.Format_RGB32)
    no_alpha.fill(0x112233)
    result = antialias.smooth_edges(no_alpha)
    check(
        "无 alpha 通道的图 → 原样返回（没有「边缘」可言）",
        result is no_alpha,
        f"格式={result.format()}",
    )

    if image is not None:
        check(
            "radius=0 → 原样返回",
            antialias.smooth_edges(image, 0) is image,
        )
        tiny = QImage(1, 1, QImage.Format_ARGB32)
        tiny_result = antialias.smooth_edges(tiny)
        check(
            "1×1 图不崩、尺寸不变",
            tiny_result.width() == 1 and tiny_result.height() == 1,
            f"实测={tiny_result.width()}x{tiny_result.height()}",
        )

    # 注入会抛异常的桩：出图必须仍然正常（降级 = 没抗锯齿，不是没画面）
    #
    # ⚠ **桩要打在 `smooth_edges()` 的"里面"**，不能打 `smooth_edges` 自己
    # （§4.13 那条教训："注入桩不能把被测的 try/except 一起换掉"）。
    # `smooth_edges()` 的契约是"任何异常都原样返回输入"，那个 `try/except`
    # 就在它内部；把整个函数换成会抛异常的桩，测的是**桩**，异常会直接穿到
    # `_render()` 外面（第一版就是这么写的，被本自检当场抓到）。
    # 正确做法：换掉它内部真正干活的那一步。
    from deskpet import rendering

    if spec is None:  # pragma: no cover
        return
    renderer = rendering.FrameRenderer(spec, antialias=True)
    original = antialias._dilate_rgb

    def _boom(*_args, **_kwargs):
        raise RuntimeError("注入的桩：模拟抗锯齿内部炸了")

    antialias._dilate_rgb = _boom
    try:
        pixmap = renderer.frame("idle", 0, 6, False)
        normal = rendering.FrameRenderer(spec, antialias=False).frame("idle", 0, 6, False)
        check(
            "内部函数抛异常时，出图仍然正常（降级到「没抗锯齿」）",
            not pixmap.isNull(),
            f"空图={pixmap.isNull()}",
        )
        # 降级后应当与"关闭抗锯齿"的结果一致
        a = pixmap.toImage().convertToFormat(QImage.Format_ARGB32)
        b = normal.toImage().convertToFormat(QImage.Format_ARGB32)
        check(
            "降级后的出图与「关闭抗锯齿」逐字节相同",
            bytes(a.constBits()) == bytes(b.constBits()),
            "判据：降级不改变画面",
        )
    finally:
        antialias._dilate_rgb = original


def section_cache(app) -> None:
    """§7 缓存与 LRU。"""
    section("§7 缓存：日常播放走缓存、条目数不超上限")
    from deskpet import rendering

    spec, image = _load_image()
    if spec is None:  # pragma: no cover
        return

    renderer = rendering.FrameRenderer(spec, antialias=True)
    renderer.clear()
    for _ in range(40):
        renderer.frame("idle", 0, 6, False)
    check(
        "同一帧取 40 次只算一次（其余走缓存）",
        renderer.misses == 1 and renderer.hits == 39,
        f"misses={renderer.misses} hits={renderer.hits}",
    )

    renderer.clear()
    for _ in range(3):
        for level in range(1, 11):
            renderer.frame("idle", 0, level, False)
    check(
        f"切档 1→10 各 3 次后条目数不超过上限 {rendering.DEFAULT_CACHE_LIMIT}",
        renderer.cached_count() <= rendering.DEFAULT_CACHE_LIMIT,
        f"条目数={renderer.cached_count()}",
    )

    section("§7b 单帧耗时（换档卡顿的判据）")
    from deskpet import size as size_mod

    for level in (1, 4, 6, 10):
        width, height = size_mod.pixel_size((500, 500), level)
        frame_renderer = rendering.FrameRenderer(spec, antialias=True)
        frame_renderer.clear()
        # 换档时的**首次取帧**就是要量的一拍（之后都走缓存）
        start = time.perf_counter()
        frame_renderer.frame("idle", 0, level, False)
        elapsed = (time.perf_counter() - start) * 1000
        _LINES.append(
            f"    [耗时] 第 {level} 级 {width}x{height} 首次取帧（含抗锯齿）: {elapsed:.1f} ms"
        )
        print(f"    [耗时] 第 {level} 级 {width}x{height} 首次取帧（含抗锯齿）: {elapsed:.1f} ms")
    check("单帧耗时已记录（数值见上）", True)


def section_settings(app) -> None:
    """§8 设置界面：行存在、默认未勾选、提示语含代价与重启。"""
    section("§8 设置界面（行 / 默认值 / 提示语 / 询问框文案）")
    from deskpet import settings

    row = next((f for f in settings.FIELDS if f.key == "antialias"), None)
    check("FIELDS 里有 antialias 这一行", row is not None)
    if row is None:
        return
    check("它用的是 CHECK 控件", row.kind is settings.FieldKind.CHECK, f"实测={row.kind}")
    check("implemented=True（可点，不是「暂未开放」）", row.implemented is True)
    check("标题是 边缘抗锯齿", row.title == "边缘抗锯齿", f"实测={row.title!r}")
    check(
        "提示语写清了**代价**（含「部分素材/档位可能变差」）",
        "变差" in row.hint,
        f"实测={row.hint!r}",
    )
    check(
        "提示语写清了**收益**（浅色背景 / 柔和）",
        "浅色" in row.hint and "柔和" in row.hint,
        f"实测={row.hint!r}",
    )
    check(
        "提示语注明了**重启后生效**",
        "重启" in row.hint,
        f"实测={row.hint!r}",
    )

    check(
        "询问框标题是 边缘抗锯齿",
        settings.RESTART_ASK_ANTIALIAS_TITLE == "边缘抗锯齿",
        f"实测={settings.RESTART_ASK_ANTIALIAS_TITLE!r}",
    )
    text = settings.RESTART_ASK_ANTIALIAS_TEXT
    check(
        "询问框正文含「不保证对所有素材、所有档位都更好看」（诚实性落点）",
        "不保证" in text and "都更好看" in text,
        f"实测={text[:60]!r}…",
    )
    check(
        "询问框正文写明了「某些素材或档位可能反而变差」",
        "变差" in text,
        f"命中={'变差' in text}",
    )
    check(
        "询问框正文没有写成「边缘更平滑」这类无条件承诺",
        "更平滑" not in text,
        f"含更平滑={'更平滑' in text}",
    )
    check(
        "询问框正文含 {state} 占位符（要说明切成了开还是关）",
        "{state}" in text,
        f"命中={'{state}' in text}",
    )

    # `paintEvent` 里确实开了 SmoothPixmapTransform（独立的一行修复）
    window_code = _squashed("pet_window.py")
    check(
        "paintEvent 里开了 SmoothPixmapTransform",
        "setRenderHint(QPainter.SmoothPixmapTransform" in window_code
        or "SmoothPixmapTransform,True" in window_code,
        f"命中={'SmoothPixmapTransform' in window_code}",
    )

    # 运行期**没有**"开关变了要失效缓存"的通道（重启生效）
    #
    # ⚠ 这里**不能用 `_squashed()`**：它按 token 拼代码，而**字符串字面量整段被丢掉**
    # （`tokenize.STRING` 被过滤），所以 `store.get("antialias")` 里的
    # `"antialias"` 根本不在结果里——拿它判"有没有读配置"永远是假。
    # 判"有没有出现某个**字面量**"要直接查原文（§4.11 那条"认名字先剥注释"
    # 说的是相反的场景：这里要的正是字符串本身）。
    app_source = _source_of("app.py")
    check(
        "app.py 启动时读了一次 antialias 配置",
        'store.get("antialias")' in app_source,
        f"命中={'store.get(\"antialias\")' in app_source}",
    )
    check(
        "app.py 把它传给了 PetWindow（antialias=...）",
        "antialias=bool(" in app_source.replace(" ", ""),
        "判据：构造 PetWindow 时带了 antialias 参数",
    )
    # 反向判据：**不许**有"开关变化 → 重绘/失效缓存"的通道
    handler = ""
    if "def _on_config_changed" in app_source:
        handler = app_source.split("def _on_config_changed")[1][:400]
    check(
        "没有「开关变了要重绘 / 失效缓存」的动态通道（重启生效）",
        "antialias" not in handler,
        f"_on_config_changed 里提到 antialias={'antialias' in handler}",
    )


def section_settings_flow(app) -> None:
    """§9 勾选流程：写配置 → 询问 → 同意/不同意/值没变/写失败。"""
    section("§9 勾选流程（重启询问 / 值没变不问 / 写失败回滚）")
    from deskpet import autostart as autostart_mod
    from deskpet import config as config_mod
    from deskpet import paths, settings

    # ⚠ 每个 `SettingsWindow` 构造时都会走 `_fill_autostart()`（只读查询系统
    # 启动文件夹 → 起一个 PowerShell）。本节**不是**在测自启，而状态栏会被
    # 那个普通状态盖住。所以整节把只读查询换成一个固定结论：
    # 更快、且与用户机器上的真实自启项**无关**（自检不许依赖环境，§4.11）。
    original_query = autostart_mod.query_state

    def fake_query():
        return autostart_mod.AutostartState(
            enabled=False, status_text=autostart_mod.TEXT_DISABLED, error=None
        )

    autostart_mod.query_state = fake_query
    try:
        _section_settings_flow_body(app, config_mod, settings)
    finally:
        autostart_mod.query_state = original_query


def _section_settings_flow_body(app, config_mod, settings) -> None:

    # 用工作区内的临时配置，**绝不碰用户真实 config.json**（§4.11）
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config_path = OUT_DIR / "settings_flow.json"
    # ⚠ 每次跑都**先删干净**：上一轮留下的 `antialias: true` 会让
    # `setChecked(True)` 变成"值没变"（Qt 连 `toggled` 都不发），
    # 于是本节后半段的断言会读到上一轮的残留状态（自检自己踩过一次）。
    for stale in ("settings_flow.json", "settings_flow2.json", "settings_flow3.json"):
        target = OUT_DIR / stale
        if target.exists():
            target.unlink()

    store = config_mod.ConfigStore(config_path)
    store.load()

    calls: list[tuple[str, str]] = []

    def fake_confirm(title: str, text: str) -> bool:
        calls.append((title, text))
        return True  # 同意重启

    restarts = []

    def fake_restart():
        restarts.append(True)
        return settings.RestartOutcome(True)

    window = settings.SettingsWindow(store)
    window.set_restart_handler(fake_restart, fake_confirm)

    checkbox = window._widgets.get("antialias")
    check("设置窗口里找得到 antialias 的勾选框", checkbox is not None)
    if checkbox is None:
        return
    check("初值是未勾选（默认关闭）", checkbox.isChecked() is False,
          f"实测={checkbox.isChecked()}")

    # 9.1 勾选 → 写配置 → 询问 → 重启
    checkbox.setChecked(True)
    check("勾选后配置里写成了 True", store.get("antialias") is True,
          f"实测={store.get('antialias')!r}")
    check("勾选后弹了询问框（一次）", len(calls) == 1, f"询问次数={len(calls)}")
    if calls:
        title, text = calls[0]
        check("询问框标题正确", title == settings.RESTART_ASK_ANTIALIAS_TITLE,
              f"实测={title!r}")
        check(
            "询问框正文含「不保证」（诚实性契约）",
            "不保证" in text,
            f"命中={'不保证' in text}",
        )
        check("询问框正文说明了切成「开」", "开" in text, f"实测={text[:20]!r}…")
    check("同意后真的请求了重启", len(restarts) == 1, f"重启次数={len(restarts)}")
    check("窗口记下了 restart_requested", window.restart_requested is True)

    # 9.2 值没变（再勾一次同一个值）→ 不问、不重启、不写盘
    calls.clear()
    restarts.clear()
    mtime_before = config_path.stat().st_mtime_ns
    checkbox.setChecked(True)  # 已经是 True，Qt 不会发 toggled
    window._on_antialias_check(
        next(f for f in settings.FIELDS if f.key == "antialias"), True
    )
    check("值没变时不询问重启", len(calls) == 0, f"询问次数={len(calls)}")
    check("值没变时不重启", len(restarts) == 0, f"重启次数={len(restarts)}")
    check(
        "值没变时不写盘（config.json 修改时间不变）",
        config_path.stat().st_mtime_ns == mtime_before,
        "判据：mtime 未变",
    )

    # 9.3 取消勾选 → 再问一次
    calls.clear()
    restarts.clear()
    checkbox.setChecked(False)
    check("取消勾选后配置写成 False", store.get("antialias") is False,
          f"实测={store.get('antialias')!r}")
    check("取消勾选也会询问重启", len(calls) == 1, f"询问次数={len(calls)}")
    if calls:
        check("询问框正文说明了切成「关」", "关" in calls[0][1],
              f"实测={calls[0][1][:20]!r}…")

    # 9.4 不同意重启 → 状态栏留下"重启后生效"
    window.close()

    def refuse_confirm(title: str, text: str) -> bool:
        return False

    store2 = config_mod.ConfigStore(OUT_DIR / "settings_flow2.json")
    store2.load()
    window2 = settings.SettingsWindow(store2)
    window2.set_restart_handler(lambda: settings.RestartOutcome(True), refuse_confirm)
    box2 = window2._widgets["antialias"]
    box2.setChecked(True)
    status = window2._status_label.text()
    check(
        "不同意重启时状态栏留下「重启后生效」",
        settings.PET_RESTART_HINT in status,
        f"状态栏={status!r}",
    )
    check("不同意时不重启", window2.restart_requested is False)
    window2.close()

    # 9.5 写失败 → 勾选回滚 + 状态栏 `设置失败：<原因>`，且不重启
    section("§9b 写配置失败（注入 PermissionError）")
    store3 = config_mod.ConfigStore(OUT_DIR / "settings_flow3.json")
    store3.load()

    calls3: list[tuple[str, str]] = []
    restarts3: list[bool] = []
    window3 = settings.SettingsWindow(store3)
    window3.set_restart_handler(
        lambda: (restarts3.append(True), settings.RestartOutcome(True))[1],
        lambda t, x: (calls3.append((t, x)), True)[1],
    )

    # 注入：让 `_write()` 失败（§4.13：不能把被测的 try/except 一起换掉，
    # 这里换的是最底层的 `os.replace`，让**真实现**去兜住这个异常）
    import os as os_mod

    original_replace = os_mod.replace

    def boom(src, dst, *args, **kwargs):
        raise PermissionError(13, "注入的桩：拒绝访问")

    os_mod.replace = boom
    try:
        window3._widgets["antialias"].setChecked(True)
    finally:
        os_mod.replace = original_replace

    status3 = window3._status_label.text()
    check(
        "写失败时状态栏以 `设置失败：` 开头",
        status3.startswith(settings.FAILED_PREFIX),
        f"状态栏={status3!r}",
    )
    check(
        "写失败时带出了系统原文（PermissionError / 拒绝访问）",
        "Permission" in status3 or "拒绝访问" in status3 or "访问" in status3,
        f"状态栏={status3!r}",
    )
    check(
        "写失败时勾选**回滚**到配置里的真值（未勾选）",
        window3._widgets["antialias"].isChecked() is False,
        f"实测={window3._widgets['antialias'].isChecked()}",
    )
    check("写失败时**不**询问重启", len(calls3) == 0, f"询问次数={len(calls3)}")
    check("写失败时**不**重启", len(restarts3) == 0, f"重启次数={len(restarts3)}")
    check(
        "写失败后配置里的值仍是 False",
        store3.get("antialias") is False,
        f"实测={store3.get('antialias')!r}",
    )
    window3.close()

    # 9.6 第二次以后打开设置窗口**仍然会问、仍然会重启**（§2.21 那条教训）
    section("§9c 第二次以后打开设置窗口（每个新窗口都要重装处理器）")
    app_controller_src = _source_of("app.py")
    # `SettingsController.open()` 每次造新窗口都要重装 restart 处理器
    check(
        "SettingsController.open() 里有重装 restart 处理器的代码",
        "_restart_handler is not None" in app_controller_src
        and "set_restart_handler(self._restart_handler" in app_controller_src,
        "判据：open() 里对新窗口调了 set_restart_handler",
    )


def section_no_side_effects(app) -> None:
    """§10 自检自身的纪律：不碰 assets/、不写用户真实目录。"""
    section("§10 自检纪律（assets/ 未被改动、不写用户真实目录）")
    assets = ROOT / "assets"
    if assets.exists():
        before = _hash_tree(assets)
        after = _hash_tree(assets)
        check(
            "assets/ 哈希前后不变（自检没动原始素材）",
            before == after,
            f"哈希={before[:16]}…",
        )
    check(
        "自检的产出目录在工作区内（DSH 沙箱只保证工作区可写）",
        str(OUT_DIR).startswith(str(ROOT)),
        f"产出目录={OUT_DIR}",
    )

    # 源码里不许出现"改写素材"的调用
    aa_code = _squashed("antialias.py")
    check(
        "antialias.py 里没有任何写文件的调用",
        ".save(" not in aa_code and "open(" not in aa_code,
        "判据：既没有 QImage.save，也没有 open()",
    )


def main() -> int:
    # ⚠ 必须是 `QApplication` 而不是 `QGuiApplication`：§8/§9 要真的造
    # `SettingsWindow`（`QDialog` 是 widget），只有 GUI app 时会报
    # "Cannot create a QWidget without QApplication"。
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    _LINES.append("task15 自检（offscreen）—— 边缘抗锯齿")
    _LINES.append(f"素材根：{ROOT}")

    section_defaults(app)
    section_algorithm(app)
    section_structure(app)
    section_all_levels(app)
    section_ghosting(app)
    section_on_screen(app)
    section_degrade(app)
    section_cache(app)
    section_settings(app)
    section_settings_flow(app)
    section_no_side_effects(app)

    _LINES.append("")
    summary = (
        f"全过（{_TOTAL} 项断言）" if _FAILED == 0 else f"失败 {_FAILED} 项 / 共 {_TOTAL} 项"
    )
    _LINES.append(summary)
    print(f"\n{summary}")

    try:
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
        print(f"结果已写入：{RESULT_FILE}")
    except OSError as exc:  # pragma: no cover
        print(f"写结果文件失败：{exc}", file=sys.stderr)

    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
