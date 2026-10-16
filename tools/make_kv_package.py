"""把 raw/kv/Img 的 keyview 素材 + AI 换过的人物，做成一个正式的素材包片段。

一次性构建工具，做五件事：

1. **对齐人物**：把 `weedy_figure.png` 等比缩放到参考底图 `Line1.png` 里人所在的框
   （实测 226x244 @ (183,27)）——位置、尺寸、画布一次对齐；
2. 从参考 prefab 的 `m_Children` 取 16 个键的**坐标与尺寸**，并与 `keyN.png` 的实际像素
   尺寸逐一对账；
3. 产出**两个分开的整画布图层**：`figure.png`（人物）+ `piano.png`（键盘）。
   ⚠ **不合并**：参考 prefab 的真层序是 `Main(人物) → Piano(键盘) → Key1..16 → 手 → 头`
   （取自 `SizeObject.m_Children`，那才是渲染顺序；**不是**文件里 block 出现的顺序）。
   合成顺序搞反 = 人物盖住键盘；
4. 把 17 张 **270x420 的半画布手部图补成 540x420 整画布**（左半 / 右半，与 prefab 的锚点一致）；
5. 把 `keyview` 段写进 `assets/<命名空间>/asset.json`（**原样保留其余内容**）。

键位身份 = `r{行}k{序号}`，**序号从中间往两边增加**（左→右 8 列是 7 5 3 1 2 4 6 8）。
这样 4 键/6 键布局就是 8 键布局的**居中子集**（前 N 个序号），
与 ADOFAI 本身的键位规律一致（默认 8 键 A S D F J K L ; ，4 键取中间的 D F J K）。
"""

import json
import re
import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPen

app = QGuiApplication([])

ROOT = Path(r"C:\projects\deskPet")
SRC = ROOT / "raw/kv/Img"
REF_IMG = ROOT / "raw/LineKeyViewer/LineKeyViewer-Unity/Assets/Img"
PREFAB = ROOT / "raw/LineKeyViewer/LineKeyViewer-Unity/Assets/LineKeyViewer.prefab"
PKG = ROOT / "assets/weedy"
KVDIR = PKG / "keyview"
CHECK = ROOT / "data/kv_check"

CELL = (540, 420)
LAYOUT = "2*8"
ALPHA_THR = 16          # 低于它的算"淡雾"（AI 输出四周的 alpha 噪声），不是人

# 参考 prefab 里的键号（1..16）按**行**分组，每行 8 个、按画面左→右。
#   画面**上排** = Key9..Key16（prefab 里 y 更大 = 更靠上）→ 第 1 行 r1
#   画面**下排** = Key1..Key8                            → 第 2 行 r2
ROWS = ((9, 10, 11, 12, 13, 14, 15, 16), (1, 2, 3, 4, 5, 6, 7, 8))

# 手与键的关系取自参考实现 KeyInputManager（left = num>=12 or 4<=num<8）
#   画面**左半**（x 0..270，第 1..4 列）用 prefab 的 RightHand + unpressed_right.png
#   画面**右半**（x 270..540，第 5..8 列）用 prefab 的 LeftHand + unpressed_left.png
HAND_LEFT_COLS = (0, 1, 2, 3)
HAND_RIGHT_COLS = (4, 5, 6, 7)
HAND_LEFT_UP = "unpressed_right.png"
HAND_RIGHT_UP = "unpressed_left.png"


def middle_out_numbers(n_cols: int) -> list[int]:
    """左→右每一列的序号，**中间往两边增加**。

    8 列 → [7,5,3,1,2,4,6,8]；4 列 → [3,1,2,4]；6 列 → [5,3,1,2,4,6]。
    于是"取前 N 个序号"正好是**居中**的 N 个键（4 键 ⊂ 6 键 ⊂ 8 键）。
    """
    center = (n_cols - 1) / 2
    out = []
    for col in range(n_cols):
        base = int(abs(col - center) - 0.5) * 2 + 1
        out.append(base + (1 if col > center else 0))
    return out


def load(path: Path) -> QImage:
    img = QImage(str(path))
    if img.isNull():
        raise SystemExit(f"读不出来：{path}")
    return img.convertToFormat(QImage.Format_ARGB32)


def overlay(size: tuple[int, int], items: list[tuple[QImage, int, int]]) -> QImage:
    canvas = QImage(*size, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    for img, x, y in items:
        painter.drawImage(x, y, img)
    painter.end()
    return canvas.convertToFormat(QImage.Format_ARGB32)


def alpha_bbox(img: QImage, thr: int) -> tuple[int, int, int, int]:
    minx, miny, maxx, maxy = img.width(), img.height(), -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            if ((img.pixel(x, y) >> 24) & 0xFF) >= thr:
                minx, miny = min(minx, x), min(miny, y)
                maxx, maxy = max(maxx, x), max(maxy, y)
    if maxx < 0:
        raise SystemExit("图上没有任何不透明像素")
    return minx, miny, maxx, maxy


def align_figure(reference: QImage, figure: QImage) -> tuple[QImage, str]:
    """把 figure 缩放到 reference 里人所在的框，贴到同尺寸画布上。"""
    rx0, ry0, rx1, ry1 = alpha_bbox(reference, ALPHA_THR)
    sx0, sy0, sx1, sy1 = alpha_bbox(figure, ALPHA_THR)
    rw, rh = rx1 - rx0 + 1, ry1 - ry0 + 1
    sw, sh = sx1 - sx0 + 1, sy1 - sy0 + 1

    crop = figure.copy(sx0, sy0, sw, sh)
    for y in range(crop.height()):          # 清掉框内的淡雾，免得缩小时糊出灰边
        for x in range(crop.width()):
            if ((crop.pixel(x, y) >> 24) & 0xFF) < ALPHA_THR:
                crop.setPixel(x, y, 0)
    scaled = crop.convertToFormat(QImage.Format_ARGB32_Premultiplied).scaled(
        rw, rh, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    info = (
        f"参考底图人的框 ({rx0},{ry0}) {rw}x{rh}；AI 图人的框 ({sx0},{sy0}) {sw}x{sh}；"
        f"等比缩放 {rw / sw:.4f}x{rh / sh:.4f}"
    )
    return overlay(CELL, [(scaled, rx0, ry0)]), info


def read_prefab_keys() -> dict[int, tuple[int, int, int, int]]:
    """返回 {键号: (x, unity_y, w, h)}（Unity 的 y 从下往上）。"""
    text = PREFAB.read_text(encoding="utf-8")
    blocks = {}
    for chunk in text.split("--- !u!")[1:]:
        head = chunk.splitlines()[0]
        m = re.match(r"(\d+) &(\d+)", head)
        if not m:
            continue
        fields = {}
        for line in chunk.splitlines()[1:]:
            mm = re.match(r"^  (\w+): (.*)$", line)
            if mm:
                fields.setdefault(mm.group(1), mm.group(2))
        blocks[m.group(2)] = (m.group(1), fields, chunk)

    names = {
        i: re.search(r"m_Name: (.*)", ch).group(1).strip()
        for i, (cls, _f, ch) in blocks.items()
        if cls == "1"
    }
    out: dict[int, tuple[int, int, int, int]] = {}
    for obj_id, (cls, fields, _ch) in blocks.items():
        if cls != "224":       # RectTransform
            continue
        go = re.search(r"fileID: (\d+)", fields.get("m_GameObject", "0"))
        name = names.get(go.group(1), "") if go else ""
        if not re.fullmatch(r"Key\d+", name):
            continue
        pos = re.search(r"x: ([\d.-]+), y: ([\d.-]+)", fields["m_AnchoredPosition"])
        size = re.search(r"x: ([\d.-]+), y: ([\d.-]+)", fields["m_SizeDelta"])
        out[int(name[3:])] = (
            int(pos.group(1)), int(pos.group(2)), int(size.group(1)), int(size.group(2))
        )
    return out


# ------------------------------------------------------------------ 造图
prefab = read_prefab_keys()
if any(n not in prefab for row in ROWS for n in row):
    raise SystemExit("prefab 里缺键位")

# ⚠ 清空产物目录再重建：本目录**完全由本脚本生成**，不清会留下旧命名的文件
resolved = KVDIR.resolve()
if resolved.parent != PKG.resolve() or resolved.name != "keyview":
    raise SystemExit(f"拒绝操作：{resolved} 不是预期的产物目录")
if resolved.exists():
    for f in resolved.iterdir():
        if f.is_file():
            f.unlink()
KVDIR.mkdir(parents=True, exist_ok=True)
CHECK.mkdir(parents=True, exist_ok=True)
print(f"产物目录已清空重建：{resolved}")

# 键位表：id → (x, y_top, w, h, 参考 prefab 键号)
nums = middle_out_numbers(8)
keys: dict[str, tuple[int, int, int, int, int]] = {}
problems: list[str] = []
notes: list[str] = []
for r, row in enumerate(ROWS, start=1):
    for col, key_no in enumerate(row):
        ux, uy, w, h = prefab[key_no]
        kid = f"r{r}k{nums[col]}"
        png = load(SRC / f"key{key_no}.png")
        dw, dh = abs(png.width() - w), abs(png.height() - h)
        if dw > 1 or dh > 1:
            problems.append(f"key{key_no}.png 实际 {png.width()}x{png.height()}，prefab 写 {w}x{h}")
        elif dw or dh:
            notes.append(f"key{key_no}.png {png.width()}x{png.height()} 比 prefab 的 {w}x{h} 小 1px")
        keys[kid] = (ux, CELL[1] - uy - h, w, h, key_no)      # y 从上往下
        shutil.copyfile(SRC / f"key{key_no}.png", KVDIR / f"key_{kid}.png")

# 两个分开的整画布图层（**别合并**：真层序是 人物 → 键盘 → 键 → 手 → 头）
figure, align_info = align_figure(load(REF_IMG / "Line1.png"), load(SRC / "weedy_figure.png"))
figure.save(str(KVDIR / "figure.png"), "PNG")
shutil.copyfile(SRC / "piano.png", KVDIR / "piano.png")

# 手：半画布 → 整画布
hands = []
for cols, up_name, tag, x_off in (
    (HAND_LEFT_COLS, HAND_LEFT_UP, "l", 0),
    (HAND_RIGHT_COLS, HAND_RIGHT_UP, "r", 270),
):
    up = load(SRC / up_name)
    if (up.width(), up.height()) != (270, CELL[1]):
        problems.append(f"{up_name} 是 {up.width()}x{up.height()}，期望 270x{CELL[1]}")
    overlay(CELL, [(up, x_off, 0)]).save(str(KVDIR / f"hand_{tag}_up.png"), "PNG")
    down: dict[str, str] = {}
    for r, row in enumerate(ROWS, start=1):
        for col in cols:
            key_no = row[col]
            kid = f"r{r}k{nums[col]}"
            img = load(SRC / f"pressed_key{key_no}.png")
            if (img.width(), img.height()) != (270, CELL[1]):
                problems.append(
                    f"pressed_key{key_no}.png 是 {img.width()}x{img.height()}，期望 270x{CELL[1]}"
                )
            fn = f"hand_{tag}_{kid}.png"
            overlay(CELL, [(img, x_off, 0)]).save(str(KVDIR / fn), "PNG")
            down[kid] = fn
    hands.append({"up": f"hand_{tag}_up.png", "down": down})

# ------------------------------------------------------------------ keyview 段
keyview = {
    "cell": list(CELL),
    "background": {"normal": ["figure.png", "piano.png"]},
    "keys": {
        kid: {"x": x, "y": y, "down": f"key_{kid}.png"}
        for kid, (x, y, _w, _h, _k) in keys.items()
    },
    "hands": hands,
    "layouts": {
        LAYOUT: {
            "rows": [
                [f"r{r}k{n}" for n in nums] for r in range(1, len(ROWS) + 1)
            ]
        }
    },
}

index_path = PKG / "asset.json"
# ⚠ 踩过的坑：`Path.read_text()` 会把 CRLF **归一成 LF**，而 `Path.write_text()` 在 Windows 上
# 又会把 `\n` **翻译成 CRLF**。一读一写就把整个文件的行尾换了一遍（git 看是"575 行全改"）。
# 所以这里按**字节**读、按**原来的行尾**写。
raw_bytes = index_path.read_bytes()
newline = "\r\n" if b"\r\n" in raw_bytes else "\n"
original = raw_bytes.decode("utf-8-sig")
data = json.loads(original)
if json.dumps(data, ensure_ascii=False, indent=2) + "\n" != original.replace("\r\n", "\n"):
    print("⚠ asset.json 的排版与 json.dumps(indent=2) 不一致，写回后整文件会重排")
data["keyview"] = keyview
index_path.write_bytes(
    (json.dumps(data, ensure_ascii=False, indent=2) + newline).encode("utf-8")
)

# ------------------------------------------------------------------ 对账 + 对照图
print("人物对齐:", align_info)
print(f"写入 {index_path}（新增 keyview 段，其余键原样）；图片落到 {resolved}")
print(f"行尾沿用原文件的 {'CRLF' if newline == chr(13) + chr(10) else 'LF'}")

print("\n键位身份 ↔ 参考 prefab 键号（左→右）：")
for r, row in enumerate(ROWS, start=1):
    pairs = " ".join(f"r{r}k{nums[c]}←Key{row[c]}" for c in range(8))
    print(f"  第 {r} 行: {pairs}")

ok = True
for kid, (_x, _y, _w, _h, key_no) in keys.items():
    img = load(KVDIR / f"key_{kid}.png")
    src_img = load(SRC / f"key{key_no}.png")
    if (img.width(), img.height()) != (src_img.width(), src_img.height()):
        print(f"  ✗ {kid} 拷贝后尺寸变了")
        ok = False
print("拷贝后的键帽图与原图尺寸：" + ("全部一致" if ok else "有不一致"))
for n in notes:
    print("  记一笔 -", n)
for p in problems:
    print("  问题 -", p)


# 键位 id → 画面列号（0 起）。⚠ 不能拿 k 后面的数字当列号：序号是从中间往外排的
COL_OF = {f"r{r}k{n}": c for r in (1, len(ROWS)) for c, n in enumerate(nums)}


def compose(pressed: list[str], tag: str) -> None:
    """独立小合成器（只看效果，不是产品代码）。层序照 prefab：人 → 键盘 → 键 → 手。"""
    items = [(figure, 0, 0), (load(SRC / "piano.png"), 0, 0)]
    for kid in pressed:
        x, y, _w, _h, _k = keys[kid]
        items.append((load(KVDIR / f"key_{kid}.png"), x, y))
    for cols, tag_hand in ((HAND_LEFT_COLS, "l"), (HAND_RIGHT_COLS, "r")):
        mine = [k for k in pressed if COL_OF[k] in cols]
        fn = f"hand_{tag_hand}_{mine[-1]}.png" if mine else f"hand_{tag_hand}_up.png"
        items.append((load(KVDIR / fn), 0, 0))
    white = QImage(*CELL, QImage.Format_ARGB32)
    white.fill(Qt.white)
    p = QPainter(white)
    p.drawImage(0, 0, overlay(CELL, items))
    p.end()
    white.scaled(CELL[0] * 2, CELL[1] * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(
        str(CHECK / f"{tag}.png")
    )


probe = QImage(*CELL, QImage.Format_ARGB32)
probe.fill(Qt.white)
pp = QPainter(probe)
pp.drawImage(0, 0, figure)
pp.drawImage(0, 0, load(SRC / "piano.png"))
pen = QPen(Qt.red)
pen.setWidth(1)
pp.setPen(pen)
for x, y, w, h, _k in keys.values():
    pp.drawRect(x, y, w - 1, h - 1)
pp.end()
probe.scaled(CELL[0] * 2, CELL[1] * 2, Qt.IgnoreAspectRatio, Qt.FastTransformation).save(
    str(CHECK / "keypos.png")
)

compose([], "rest")
compose(["r2k1", "r1k5"], "two_pressed")
compose(list(keys), "all_pressed")
print("对照图写到", CHECK)
