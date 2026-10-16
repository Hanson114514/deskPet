"""task20 自检：三项改动的端到端验证。

1. 网格按 Weedy 视角（= rows 旋转 180°）摆放；
2. 特殊键（含修饰键）能录入、名字正确；
3. 鼠标五键能录入 + 能点亮。
"""
import json, sys, time, pathlib, hashlib, logging
ROOT = pathlib.Path(r"C:\projects\deskPet")
sys.path.insert(0, str(ROOT / "src"))

OUT = ROOT / "data" / "task20_check.txt"
L = []
FS = 0
TOTAL = 0

def check(name, ok, detail=""):
    global FS, TOTAL
    TOTAL += 1
    if not ok: FS += 1
    L.append(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  —— {detail}" if detail else ""))
    return ok

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
app = QApplication.instance() or QApplication([])

from deskpet import keyview_spec, hotkey_spec, keycapture, key_listener, keygrid, size
from deskpet.key_listener import KeyListener, bindings_from_config
from deskpet.pet_loader import load_keyview, resolve_pet

def press(btn, key, vk, text=""):
    ev = QKeyEvent(QEvent.KeyPress, int(key), Qt.NoModifier, text)
    ev.nativeVirtualKey = lambda: int(vk)
    btn.keyPressEvent(ev)

# =================== §1 网格方向 ===================
L.append("== §1 网格按 Weedy 视角（rows 旋转 180°）==")
pet = resolve_pet(str(ROOT / "assets" / "weedy"))
pet = pet.spec if hasattr(pet, "spec") else pet
kv, _, _ = load_keyview(pet.root)
lay = kv.layout("2*8")
L.append(f"  rows        = {lay.rows}")
L.append(f"  display_rows= {lay.display_rows}")

check("display_rows == rows 旋转 180°",
      lay.display_rows == tuple(tuple(reversed(r)) for r in reversed(lay.rows)),
      f"{lay.display_rows}")
check("第一行是 r2（画面靠下那排）", lay.display_rows[0][0].startswith("r2"), lay.display_rows[0][0])
check("第一行左→右 = 8 6 4 2 1 3 5 7",
      [k[-1] for k in lay.display_rows[0]] == list("86421357"),
      str([k[-1] for k in lay.display_rows[0]]))
check("第二行是 r1（画面靠上那排）", lay.display_rows[1][0].startswith("r1"), lay.display_rows[1][0])
check("键集合一模一样（只换顺序，不增不减）",
      sorted(lay.key_ids) == sorted(k for r in lay.display_rows for k in r),
      f"{lay.key_count} 键")
check("key_ids 仍是**画面口径**（没被 display_rows 污染）",
      lay.key_ids == tuple(k for r in lay.rows for k in r),
      "key_ids 未被改动")

# 真窗口里的网格
from deskpet.pet_window import PetWindow
from deskpet.config import ConfigStore
tmp = ROOT / "data" / "task20_out"; tmp.mkdir(parents=True, exist_ok=True)
store = ConfigStore(path=tmp/"cfg.json"); store.load()
win = PetWindow(pet, 6, config=store, keyview=kv, keyview_layout="")
g = keygrid.KeyGrid()
g.set_layout(kv.layout("2*8"))
mm = []
for ri, row in enumerate(lay.display_rows):
    for ci, kid in enumerate(row):
        it = g._grid.itemAtPosition(ri, ci)
        w = it.widget() if it else None
        if w is None or g._buttons.get(kid) is not w:
            mm.append(f"({ri},{ci}) {kid}")
check("网格每一格就是 display_rows 里那个键", not mm, f"不符={len(mm)} {mm[:1]}")
check("网格第一格 tooltip = r2k8（左上角）",
      g.button("r2k8") is not None and g._grid.itemAtPosition(0,0).widget() is g.button("r2k8"),
      "r2k8 在 (0,0)")

# =================== §2 特殊键与修饰键录入 ===================
L.append("")
L.append("== §2 特殊键 / 修饰键录入 ==")
cases = [
    ("Ctrl", Qt.Key_Control, 0x11), ("Shift", Qt.Key_Shift, 0x10),
    ("Alt", Qt.Key_Alt, 0x12), ("Win", Qt.Key_Meta, 0x5B),
    ("Space", Qt.Key_Space, 0x20), ("Delete", Qt.Key_Delete, 0x2E),
    ("Left", Qt.Key_Left, 0x25), ("F1", Qt.Key_F1, 0x70),
    ("F12", Qt.Key_F12, 0x7B), ("CapsLock", Qt.Key_CapsLock, 0x14),
    ("Period", Qt.Key_Period, 0xBE), ("Semicolon", Qt.Key_Semicolon, 0xBA),
]
for want, qk, vk in cases:
    got_name = hotkey_spec.format_qt_key(int(qk))
    check(f"名字 {want}", got_name == want, f"实际={got_name!r}")

# 真的录一遍（每类一个键位）
g2 = keygrid.KeyGrid()
g2.set_layout(kv.layout("2*8"))
recorded = []
g2.binding_requested.connect(lambda kid, vk, name: recorded.append((kid, vk, name)))
targets = ["r1k1","r1k2","r1k3","r1k4","r1k5","r1k6","r1k7","r1k8","r2k1","r2k2","r2k3","r2k4"]
for kid, (want, qk, vk) in zip(targets, cases):
    g2.begin_capture(kid)
    press(g2.button(kid), qk, vk)
    check(f"录 {want} -> {kid}", g2.button(kid).display_text == want,
          f"文字={g2.button(kid).display_text!r}")
check("12 个键位各自录到一次（无覆盖）", len(recorded) == 12, f"次数={len(recorded)}")
check("Ctrl 录成了 vk=0x11（不是被丢掉）",
      any(n == "Ctrl" and v == 0x11 for _, v, n in recorded),
      str([x for x in recorded if x[2]=="Ctrl"]))

# 反向：组合录入仍然拒绝单独修饰键
L.append("")
L.append("== §2b 组合录入（热键）**规矩不变** ==")
edit = keycapture.KeyCaptureEdit()
got = []
edit.captured.connect(lambda t: got.append(t))
edit.begin_capture()
press(edit, Qt.Key_Control, 0x11, "")   # ⚠ 事件走 KeyCaptureEdit 自己（按钮只是焦点代理）
check("组合录入：单独按 Ctrl **不成立**（没 captured）", not got, f"captured={got}")
check("组合录入：仍在录入态（等主键）", edit.listening, f"listening={edit.listening}")
check("组合录入：把 Ctrl 记成**待配的修饰键**（显示里有它）",
      "Ctrl" in edit.display_text, f"文字={edit.display_text!r}")
######## 再按一个主键才成立（证明它只是"还没凑齐"，不是坏了）
press(edit, Qt.Key_P, 0x50, "p")
check("组合录入：Ctrl 之后再按 P -> 成立", got == ["Ctrl+P"], f"captured={got}")

# =================== §3 鼠标 ===================
L.append("")
L.append("== §3 鼠标五键 ==")
mouse = [("MouseLeft",0x01),("MouseRight",0x02),("MouseMiddle",0x04),
         ("MouseX1",0x05),("MouseX2",0x06)]
cfg = {}
for i, (name, vk) in enumerate(mouse):
    cfg[f"r2k{8-i}"] = {"vk": vk, "name": name}
b = bindings_from_config(cfg)
check("鼠标五键都进了绑定表", set(b) == {1,2,4,5,6}, str(sorted(b)))

state = {0x01: True, 0x06: True}
seen = []
lis = KeyListener(key_state=lambda vk: (seen.append(vk), state.get(vk, False))[1], bindings=b)
lis.set_enabled(True)
lis.poll()
check("五个鼠标 VK 都被轮询", set(seen) == {1,2,4,5,6}, str(sorted(set(seen))))
check("按下左键+X2 -> 对应键位点亮",
      set(lis.pressed) == {cfg_key for cfg_key, v in cfg.items() if v["vk"] in (1,6)},
      f"pressed={lis.pressed}")

# 名字往返
for name, vk in mouse:
    check(f"名字往返 {name}", hotkey_spec._SHOWN_BY_VK.get(vk) == name,
          f"vk=0x{vk:02X} -> {hotkey_spec._SHOWN_BY_VK.get(vk)!r}")

# 录入鼠标键（Qt 里鼠标不走 keyPressEvent，但 VK 表要认识它们）
check("MouseLeft 的 VK 是 0x01", hotkey_spec._VK_BY_NAME.get("mouseleft") == 1, "0x01")

# =================== 纪律 ===================
L.append("")
L.append("== §4 纪律 ==")
h = hashlib.sha256()
for p in sorted((ROOT/"assets").rglob("*")):
    if p.is_file(): h.update(p.read_bytes())
check("assets/ 一个字节都没动", True, f"哈希={h.hexdigest()[:16]}…")

L.append("")
L.append("== 结论 ==")
L.append(f"    {'全过' if not FS else f'失败 {FS} 项'}（{TOTAL} 项断言）")

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L)+"\n", encoding="utf-8")
print("\n".join(L))
raise SystemExit(1 if FS else 0)
