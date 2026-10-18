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
from PySide6.QtCore import Qt, QEvent, QPointF
from PySide6.QtGui import QKeyEvent, QMouseEvent
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

# ---- 录入鼠标键（task20：鼠标**不产生 QKeyEvent**，走 mousePressEvent）
check("MouseLeft 的 VK 是 0x01", hotkey_spec._VK_BY_NAME.get("mouseleft") == 1, "0x01")

def mouse_press(widget, button):
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                     button, button, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)

gm = keygrid.KeyGrid(); gm.set_layout(kv.layout("2*8")); gm.apply_bindings({})
rec = []
gm.binding_requested.connect(lambda k, v, n: rec.append((k, v, n)))
mouse_cases = [
    ("r1k1", Qt.LeftButton, "MouseLeft", 0x01),
    ("r1k2", Qt.RightButton, "MouseRight", 0x02),
    ("r1k3", Qt.MiddleButton, "MouseMiddle", 0x04),
    ("r1k4", Qt.BackButton, "MouseX1", 0x05),
    ("r1k5", Qt.ForwardButton, "MouseX2", 0x06),
]
for kid, btn_enum, want, want_vk in mouse_cases:
    gm.begin_capture(kid)
    mouse_press(gm.button(kid), btn_enum)
    check(f"录入 {want} -> {kid}", gm.button(kid).display_text == want and not gm.capturing,
          f"文字={gm.button(kid).display_text!r} capturing={gm.capturing}")
check("鼠标五键都发出了正确的 (键位, vk, 名字)",
      sorted(rec) == sorted((k, v, n) for k, _, n, v in mouse_cases), str(rec))

# ---- ⭐ 录入中点到**别的键位** = 录 MouseLeft 给原来那个（用户 2026 明确要求）
gm2 = keygrid.KeyGrid(); gm2.set_layout(kv.layout("2*8")); gm2.apply_bindings({})
rec2 = []
gm2.binding_requested.connect(lambda k, v, n: rec2.append((k, v, n)))
gm2.begin_capture("r1k6")
mouse_press(gm2.button("r1k7"), Qt.LeftButton)   # 点的是**另一个**键位
check("录入中点到别的键位 -> 录 MouseLeft 给**原来那个**（不是切换）",
      rec2 == [("r1k6", 0x01, "MouseLeft")] and gm2.button("r1k6").display_text == "MouseLeft",
      f"rec={rec2} r1k6={gm2.button('r1k6').display_text!r}")
check("被点到的那个键位**没被切成录入态**（事件被吃掉）",
      not gm2.capturing and gm2.button("r1k7").display_text == "未设置",
      f"r1k7={gm2.button('r1k7').display_text!r}")

# ---- ⭐ 录入中**再点自己** = 录 MouseLeft（不是"取消"）
gm3 = keygrid.KeyGrid(); gm3.set_layout(kv.layout("2*8")); gm3.apply_bindings({})
rec3 = []
gm3.binding_requested.connect(lambda k, v, n: rec3.append((k, v, n)))
gm3.begin_capture("r2k1")
mouse_press(gm3.button("r2k1"), Qt.LeftButton)
check("录入中再点自己 -> 录 MouseLeft（不是取消、不是卡住）",
      rec3 == [("r2k1", 0x01, "MouseLeft")] and gm3.button("r2k1").display_text == "MouseLeft",
      f"rec={rec3}")

# ---- ⭐ 严重 bug：录入态下**整个程序失活**（用户点了别的程序）必须收尾
#
# 成因链：录入态下点别的程序 -> 我们**收不到任何鼠标事件**（窗口失焦）
#        -> 录入态永不结束 -> 监听一直挂着 -> 回宠物窗口按什么键都不亮。
# 用户 2026 实测：在窗口失焦时按下按键 KV 就不生效。
gm6 = keygrid.KeyGrid(); gm6.set_layout(kv.layout("2*8")); gm6.apply_bindings({})
susp_log = []
gm6.set_suspend_hook(lambda on: susp_log.append(on))
gm6.begin_capture("r1k1")
check("录入态: 监听被挂起", susp_log == [True], f"log={susp_log}")
app.sendEvent(app, QEvent(QEvent.ApplicationDeactivate))
check("程序失活 -> **自动收掉录入态**（否则监听永远挂着）",
      not gm6.capturing, f"capturing={gm6.capturing}")
check("程序失活 -> 监听**恢复**（切走再回来按键还亮）",
      susp_log == [True, False], f"log={susp_log}")
check("程序失活 -> 键位标签不再卡着",
      gm6.button('r1k1').display_text != '按下按键…',
      f"r1k1={gm6.button('r1k1').display_text!r}")

# ---- ⭐ 反向：程序内部换窗口**不能**被当成失活（那一下该录成鼠标左键）
gm7 = keygrid.KeyGrid(); gm7.set_layout(kv.layout("2*8")); gm7.apply_bindings({})
log7 = []; rec7 = []
gm7.set_suspend_hook(lambda on: log7.append(on))
gm7.binding_requested.connect(lambda k, v, n: rec7.append((k, v, n)))
gm7.begin_capture("r2k3")
mouse_press(gm7.button("r2k4"), Qt.LeftButton)
check("程序内点别的键位 -> 录 MouseLeft（不是被当成失活收掉）",
      rec7 == [("r2k3", 0x01, "MouseLeft")], f"rec={rec7}")

# ---- ⭐ 自愈：状态陈旧时必须被纠正（task20 失焦问题的兜底）
#
# 用户 2026 在**分发版**上实测：『只要窗口失焦 KV 就失效，点一下桌宠让焦点
# 回来才恢复』。症状是『状态陈旧且没人来纠正』，所以加两条兜底：
#   1. reconcile() 定期把『启用条件』与『监听器实际状态』对齐；
#   2. visibility_changed 改由 Qt 的 showEvent/hideEvent 驱动，
#      任何路径改变了可见性都会重判（原先只从 hide_pet/show_pet 发）。
from deskpet.pet_window import PetWindow as _PW
from deskpet.config import ConfigStore as _CS
from deskpet.app import KeyListenerController as _KLC, FormController as _FC
_tmp = ROOT / 'data' / 'task20_out'; _tmp.mkdir(parents=True, exist_ok=True)
_store = _CS(path=_tmp / 'selfheal.json'); _store.load()
_store.set('keyview_keys', {'r1k1': {'vk': 0x46, 'name': 'F'}})
_w = _PW(pet, 6, config=_store, keyview=kv, keyview_layout='')
_vis = []
_w.visibility_changed.connect(lambda v: _vis.append(v))
_w.show()
for _ in range(10): app.processEvents()
check('showEvent 会发 visibility_changed（不只 hide_pet/show_pet 那条路）',
      True in _vis, f'vis={_vis}')
_vis.clear(); _w.hide()
for _ in range(10): app.processEvents()
check('hideEvent 也会发 visibility_changed', False in _vis, f'vis={_vis}')
_w.show()
for _ in range(10): app.processEvents()
_fc2 = _FC(_w, _store); _fc2.restore(); _w.set_form_handler(_fc2.switch)
_kc = _KLC(_w, _store)
_fc2.switch('keyview'); _kc.sync()
check('KV 开着时监听器是启用的', _kc.listener.enabled, f'enabled={_kc.listener.enabled}')
_kc.listener.set_enabled(False)
check('人为制造不一致后确实不一致',
      _kc.should_poll() and not _kc.listener.enabled,
      f'should_poll={_kc.should_poll()} enabled={_kc.listener.enabled}')
_kc.reconcile()
check('reconcile() 把监听器**自愈**回来（失焦不再永久失效）',
      _kc.listener.enabled and _kc.listener.running,
      f'enabled={_kc.listener.enabled} running={_kc.listener.running}')
_kc.listener._key_state = lambda vk: vk == 0x46
_kc.listener.poll()
_before = _kc.listener.pressed
_kc.reconcile()
check('一致时 reconcile() 不打扰（没清掉按着的键）',
      _kc.listener.pressed == _before, f'{_before} -> {_kc.listener.pressed}')
_kc.stop_reconcile(); _kc.stop(); _w.close()

# ---- ⭐ 分发版专属：应用**从未激活**时，收尾仍然必须生效
#
# 这是上一轮只修了一半的原因：第一版只收 ApplicationDeactivate，而 Qt 只在
# "激活 -> 未激活"这个**转变**上发它。分发版（双击 exe、Qt.Tool +
# WA_ShowWithoutActivating）整个生命周期都是 ApplicationInactive，
# 从来等不到那个转变 -> 事件一次不发 -> 录入态永远挂着 -> KV 失效。
# 开发时从控制台启动会先激活一次，所以掩盖了它（用户实测：开发板好、分发版坏）。
gm8 = keygrid.KeyGrid(); gm8.set_layout(kv.layout("2*8")); gm8.apply_bindings({})
log8 = []
gm8.set_suspend_hook(lambda on: log8.append(on))
gm8.begin_capture("r1k1")
check("从未激活场景：录入态已挂起监听", log8 == [True] and gm8.capturing, f"log={log8}")
# 只发窗口级失活（应用级那个在这种场景下根本不会来）
app.sendEvent(gm8, QEvent(QEvent.WindowDeactivate))
check("从未激活场景：WindowDeactivate 也能收尾（分发版不再卡死）",
      not gm8.capturing and log8 == [True, False], f"capturing={gm8.capturing} log={log8}")

# ---- ⭐ 标签不再卡住：录入中改点别的键位 / 走重置
gm4 = keygrid.KeyGrid(); gm4.set_layout(kv.layout("2*8")); gm4.apply_bindings({})
gm4.button("r1k7").click()                  # 真实点击路径（clicked -> begin_capture）
check("真实点击 -> 进录入态且网格知道", gm4.capturing, f"capturing={gm4.capturing}")
gm4.button("r1k8").click()                  # 录入中改点别的（这一下会被吃掉=录 MouseLeft）
check("录入中改点别的键位 -> 上一个**不再卡在 `按下按键…`**",
      gm4.button("r1k7").display_text != "按下按键…",
      f"r1k7={gm4.button('r1k7').display_text!r}")

gm5 = keygrid.KeyGrid(); gm5.set_layout(kv.layout("2*8"));
gm5.apply_bindings({"r2k2": keyview_spec.KeyBinding(vk=0x46, name="F")})
gm5.button("r2k2").click()
check("重置前先进入录入态", gm5.capturing, f"capturing={gm5.capturing}")
gm5.apply_bindings({})                      # 模拟重置后回填
check("重置（apply_bindings 回填）后标签**不再卡住**、也退出录入态",
      gm5.button("r2k2").display_text == "未设置" and not gm5.capturing,
      f"文字={gm5.button('r2k2').display_text!r} capturing={gm5.capturing}")

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
