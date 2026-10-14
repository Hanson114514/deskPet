"""task06 自检：右键菜单（换动作 / 休息 / 镜像翻转）+ 镜像渲染与记忆。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task06_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，否则中文输出在 GBK 控制台会炸，§4.11）：

    python tools/check_task06.py

**本脚本不改素材**（`assets/` 是红线）：镜像只用**渲染层**的翻转，
所以这里能直接断言"翻出来的图确实是镜像的"，而仓库里的 PNG 一个字节都没动。

**窗口相关的用例走 offscreen 平台插件**，不弹真窗口。
注意 offscreen **不支持 `setMask`**（§4.9），所以本脚本不断言"遮罩生效"，
只断言"换帧/换镜像时确实重算了遮罩"（用桩计数）。
"点击穿透"与"真窗口下拖动手感"属真平台验证，见脚本末尾的说明。
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task06_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task06_scratch"

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


class _LogCapture:
    """把 `deskpet` 的日志收进内存（验证"过滤 + 警告"用）。"""

    def __init__(self) -> None:
        import logging

        self.records: list[str] = []
        self._handler = logging.Handler()
        self._handler.emit = self._emit  # type: ignore[method-assign]
        self._logger = logging.getLogger("deskpet")
        self._previous = self._logger.level

    def _emit(self, record) -> None:
        self.records.append(record.getMessage())

    def __enter__(self):
        self._logger.addHandler(self._handler)
        self._logger.setLevel(1)
        return self

    def __exit__(self, *exc) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._previous)

    def text(self) -> str:
        return "\n".join(self.records)


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _write_config(path: Path, payload: object, *, raw_text: str | None = None) -> None:
    """写一份配置文件（`raw_text` 给了就原样写，用来造坏 JSON）。"""
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


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
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    _check_menu_contract()
    _check_menu_states()
    _check_mirror_table()
    _check_mirror_config()
    _check_render_cache_key()
    _check_mirrored_pixels()
    _check_repeated_toggles()
    _check_window_integration(app)
    _check_no_alias_for_sit()
    _check_cli_scope()
    return _finish()


# ------------------------------------------------------------ §1 菜单契约


def _check_menu_contract() -> None:
    from deskpet import context_menu as CM

    section("1. 菜单契约：顺序 / 文字 / 稳定 id")

    expected_order = (
        "resize",
        "action",
        "mirror",
        "settings",
        "visibility",
        "(separator)",
        "quit",
    )
    check(
        "MENU_ORDER 与卡片一致（含分隔线占位）",
        CM.MENU_ORDER == expected_order,
        f"实际 {CM.MENU_ORDER}",
    )

    # 附录 B 的用户可见文字，一个字都不能差。
    expected_texts = {
        "resize": "调整大小…",
        "rest": "坐下休息",
        "stand": "站起来",
        "mirror": "镜像翻转",
        "settings": "设置…",
        "visibility": "隐藏宠物",
        "quit": "退出",
    }
    actual_texts = {
        "resize": CM.TEXT_RESIZE,
        "rest": CM.TEXT_REST,
        "stand": CM.TEXT_STAND_UP,
        "mirror": CM.TEXT_MIRROR,
        "settings": CM.TEXT_SETTINGS,
        "visibility": CM.TEXT_VISIBILITY,
        "quit": CM.TEXT_QUIT,
    }
    check(
        "菜单文字与附录 B 完全一致",
        actual_texts == expected_texts,
        f"实际 {actual_texts}",
    )

    from PySide6.QtWidgets import QWidget

    from deskpet import size as S

    parent = QWidget()
    state = CM.MenuState(action="idle", can_mirror=True, mirror_on=True)
    menu = CM.build_menu(parent, state)

    actions = [a for a in menu.actions() if not a.isSeparator()]
    ids = [CM.action_id(a) for a in actions]
    check(
        "菜单共 6 个可点项（+1 条分隔线）",
        len(actions) == 6 and len(menu.actions()) == 7,
        f"可点 {len(actions)} / 全部 {len(menu.actions())}",
    )
    check(
        "可点项的 id 顺序 = resize/action/mirror/settings/visibility/quit",
        ids == ["resize", "action", "mirror", "settings", "visibility", "quit"],
        f"实际 {ids}",
    )
    check(
        "菜单的 parent 是宠物窗口（否则跟随置顶会丢）",
        menu.parent() is parent,
        f"实际 parent={menu.parent()!r}",
    )

    texts = [a.text() for a in actions]
    check(
        "rest 态下 action 项文字是『坐下休息』",
        texts[1] == "坐下休息",
        f"实际 {texts[1]}",
    )
    resting = CM.build_menu(parent, CM.MenuState(action="rest", manual_rest=True, can_rest=True))
    rest_texts = [a.text() for a in resting.actions() if not a.isSeparator()]
    check(
        "休息中点出来的菜单 action 项文字变成『站起来』",
        rest_texts[1] == "站起来",
        f"实际 {rest_texts[1]}",
    )
    check(
        "文字变了但 id 不变（接线靠 id 不靠文字）",
        CM.action_id(resting.actions()[1]) == "action",
        f"实际 {CM.action_id(resting.actions()[1])!r}",
    )
    check(
        "id 存在 QAction.setData 里（不是靠文字反查）",
        resting.actions()[1].data() == "action",
        f"实际 data={resting.actions()[1].data()!r}",
    )

    # 演示"改文字不影响接线"：直接把文字换掉，id 仍然取得到。
    odd = CM.build_menu(parent, CM.MenuState(action="idle", can_mirror=True))
    mirror_action = odd.actions()[2]
    mirror_action.setText("翻转")
    check(
        "把『镜像翻转』改成『翻转』后 id 仍然是 mirror",
        CM.action_id(mirror_action) == "mirror",
        f"文字 {mirror_action.text()!r} → id {CM.action_id(mirror_action)!r}",
    )
    check(
        "action_id(None) 返回空串（没选 / 未知项）",
        CM.action_id(None) == "",
        f"实际 {CM.action_id(None)!r}",
    )
    del S


# ------------------------------------------------------------ §2 勾选/禁用


def _check_menu_states() -> None:
    from PySide6.QtWidgets import QWidget

    from deskpet import context_menu as CM

    section("2. 勾选态与禁用态（按当前状态现算）")

    parent = QWidget()

    def build(state: CM.MenuState):
        return [a for a in CM.build_menu(parent, state).actions() if not a.isSeparator()]

    # 默认状态：三项未接线 → 禁用（不是消失）。
    default = build(CM.MenuState(action="idle", can_mirror=True, can_rest=True))
    by_id = {CM.action_id(a): a for a in default}
    check(
        "调整大小… 灰色不可点（仍然在菜单里）",
        by_id["resize"].isEnabled() is False and "resize" in by_id,
        f"enabled={by_id['resize'].isEnabled()}",
    )
    check(
        "设置… 灰色不可点",
        by_id["settings"].isEnabled() is False,
        f"enabled={by_id['settings'].isEnabled()}",
    )
    check(
        "隐藏宠物 灰色不可点",
        by_id["visibility"].isEnabled() is False,
        f"enabled={by_id['visibility'].isEnabled()}",
    )
    check("退出可用", by_id["quit"].isEnabled() is True, "enabled=True")
    check(
        "禁用项不是被隐藏（三项都还在菜单里）",
        {"resize", "settings", "visibility"} <= set(by_id),
        f"菜单里实际有 {sorted(by_id)}",
    )

    # 镜像：适用范围外 → 禁用；且**不显示勾选**。
    outside = {CM.action_id(a): a for a in build(CM.MenuState(action="waving", can_mirror=False))}
    check(
        "适用范围外（waving）镜像项灰色不可点",
        outside["mirror"].isEnabled() is False,
        f"enabled={outside['mirror'].isEnabled()}",
    )
    check(
        "适用范围外不显示勾选（免得像『点了没反应』）",
        outside["mirror"].isChecked() is False,
        f"checked={outside['mirror'].isChecked()}",
    )

    inside_on = {CM.action_id(a): a for a in build(CM.MenuState(action="idle", can_mirror=True, mirror_on=True))}
    check(
        "翻转开着时镜像项显示为已勾选",
        inside_on["mirror"].isChecked() is True and inside_on["mirror"].isEnabled() is True,
        f"checked={inside_on['mirror'].isChecked()} enabled={inside_on['mirror'].isEnabled()}",
    )
    inside_off = {CM.action_id(a): a for a in build(CM.MenuState(action="idle", can_mirror=True, mirror_on=False))}
    check(
        "没翻转时镜像项不勾选",
        inside_off["mirror"].isChecked() is False,
        f"checked={inside_off['mirror'].isChecked()}",
    )

    # 素材里没有 rest 时，『坐下休息』点了也没用 → 灰掉。
    no_rest = {CM.action_id(a): a for a in build(CM.MenuState(action="idle", can_rest=False))}
    check(
        "素材里没有 rest 时 action 项灰色不可点",
        no_rest["action"].isEnabled() is False,
        f"enabled={no_rest['action'].isEnabled()}",
    )


# ------------------------------------------------------------ §3 镜像状态表


def _check_mirror_table() -> None:
    from deskpet import config, mirror as M
    from deskpet.config import ConfigStore

    section("3. 镜像状态表：适用范围与记忆")

    check(
        "MIRRORABLE 直接引用 config.MIRROR_STATES（真值只有一处）",
        M.MIRRORABLE is config.MIRROR_STATES,
        f"实际 {M.MIRRORABLE!r}",
    )
    check(
        "适用范围 = idle/waiting/rest",
        M.MIRRORABLE == ("idle", "waiting", "rest"),
        f"实际 {M.MIRRORABLE}",
    )
    for action in ("running_left", "running_right", "waving", "温蒂-倾听-基建-Special-x1", ""):
        check(
            f"适用范围外 can_mirror({action!r}) 为假",
            M.can_mirror(action) is False,
            f"实际 {M.can_mirror(action)}",
        )
    for action in M.MIRRORABLE:
        check(f"适用范围内 can_mirror({action!r}) 为真", M.can_mirror(action) is True, "True")

    cfg = _fresh_dir(SCRATCH / "table") / "config.json"
    store = ConfigStore(cfg)
    store.load()
    state = M.MirrorState(store)

    check(
        "配置里没记录过 → 一律不翻转",
        all(state.is_on(a) is False for a in M.MIRRORABLE),
        f"实际 {state.snapshot()}",
    )
    check("适用范围外 is_on 恒为假", state.is_on("waving") is False, "False")

    seen: list[tuple[str, bool]] = []
    state.mirror_changed.connect(lambda a, on: seen.append((a, on)))

    ok = state.toggle("idle")
    check("toggle(idle) 成功", ok is True, f"实际 {ok}")
    check("toggle 后 is_on(idle) 为真", state.is_on("idle") is True, "True")
    check(
        "mirror_changed 发出 (idle, True)",
        seen == [("idle", True)],
        f"实际 {seen}",
    )

    # 各自独立记忆
    check("rest 仍然是关的（按状态分别记忆）", state.is_on("rest") is False, "False")
    state.toggle("rest")
    check("rest 翻过一次后为真", state.is_on("rest") is True, "True")
    check("idle 不受 rest 影响", state.is_on("idle") is True, "True")
    check("waiting 从未翻过，仍是假", state.is_on("waiting") is False, "False")

    # 适用范围外不许翻
    before = state.snapshot()
    ok_outside = state.toggle("waving")
    check("适用范围外 toggle 返回 False", ok_outside is False, f"实际 {ok_outside}")
    check("适用范围外 toggle 不改内存", state.snapshot() == before, f"实际 {state.snapshot()}")
    check(
        "适用范围外 toggle 不发信号",
        seen == [("idle", True), ("rest", True)],
        f"实际 {seen}",
    )

    # 再翻一次翻回来
    state.toggle("idle")
    check("再 toggle 一次翻回来", state.is_on("idle") is False, "False")


# ------------------------------------------------------------ §4 mirror 配置


def _check_mirror_config() -> None:
    from deskpet import config, mirror as M
    from deskpet.config import ConfigStore

    section("4. config.mirror 字段：过滤 / 警告 / 写回")

    check(
        "mirror 是已知键（不会写回时被丢掉）",
        "mirror" in config.DEFAULTS and config.DEFAULTS["mirror"] == {},
        f"默认值 {config.DEFAULTS['mirror']!r}",
    )

    # 非法项：适用范围外的键 + 非布尔值 → 都丢弃 + 警告。
    cfg = _fresh_dir(SCRATCH / "bad") / "config.json"
    _write_config(cfg, {"mirror": {"running_left": True, "idle": "yes"}})
    store = ConfigStore(cfg)
    with _LogCapture() as logs:
        store.load()
    text = logs.text()
    check(
        "running_left 与 idle(\"yes\") 都被丢弃",
        store.get("mirror") == {},
        f"实际 {store.get('mirror')!r}",
    )
    check(
        "两个非法项各有一条警告",
        "running_left" in text and "idle" in text and "丢弃" in text,
        f"日志：{text[-200:]}",
    )
    state = M.MirrorState(store)
    check(
        "running_left 不会被翻（丢弃后不翻转）",
        state.is_on("running_left") is False,
        "False",
    )

    # 部分合法：合规的照样采用。
    cfg2 = _fresh_dir(SCRATCH / "partial") / "config.json"
    _write_config(cfg2, {"mirror": {"idle": True, "rest": "no", "sit": True}})
    store2 = ConfigStore(cfg2)
    with _LogCapture():
        store2.load()
    check(
        "合规项照样采用（idle 留下）",
        store2.get("mirror") == {"idle": True},
        f"实际 {store2.get('mirror')!r}",
    )

    # 整体坏掉 → 退化为"永远不翻转"，不阻断启动。
    cfg3 = _fresh_dir(SCRATCH / "broken") / "config.json"
    _write_config(cfg3, {"mirror": ["idle"]})
    store3 = ConfigStore(cfg3)
    with _LogCapture():
        store3.load()
    check(
        "mirror 不是对象 → 回退空对象且不崩",
        store3.get("mirror") == {},
        f"实际 {store3.get('mirror')!r}",
    )
    state3 = M.MirrorState(store3)
    check(
        "配置整体坏掉 → 永远不翻转",
        all(state3.is_on(a) is False for a in M.MIRRORABLE),
        f"实际 {state3.snapshot()}",
    )

    # 写回：真的落进 config.json，且**只含适用范围内的键**。
    cfg4 = _fresh_dir(SCRATCH / "write") / "config.json"
    store4 = ConfigStore(cfg4)
    store4.load()
    state4 = M.MirrorState(store4)
    state4.toggle("idle")
    check("写盘后 config.json 真的出现了 mirror", cfg4.exists(), f"存在={cfg4.exists()}")
    written = _read_config(cfg4)
    check(
        "文件里的 mirror = {\"idle\": true}",
        written.get("mirror") == {"idle": True},
        f"实际 {written.get('mirror')!r}",
    )
    check(
        "mirror 没被『只保存已知键』的过滤丢掉",
        "mirror" in written,
        f"文件里的键：{sorted(written)}",
    )
    check(
        "其余已知键照常写全",
        {"pet", "hotkey", "volume", "idle_seconds", "size_level", "position"} <= set(written),
        f"文件里的键：{sorted(written)}",
    )

    # 重启后读得回来（task06 §2 第 6 条）。
    store5 = ConfigStore(cfg4)
    store5.load()
    state5 = M.MirrorState(store5)
    check(
        "重新读配置后翻转状态还在（重启验证）",
        state5.is_on("idle") is True and store5.has("mirror") is True,
        f"实际 is_on(idle)={state5.is_on('idle')} has={store5.has('mirror')}",
    )

    # 未记录过 ≠ 记成 False
    state5.toggle("idle")  # 翻回去 → 现在记录的是 False
    store6 = ConfigStore(cfg4)
    store6.load()
    state6 = M.MirrorState(store6)
    check(
        "记成 false 与没记录过都能读到『不翻转』",
        state6.is_on("idle") is False and state6.is_on("waiting") is False,
        f"idle={state6.is_on('idle')} waiting={state6.is_on('waiting')}",
    )

    # --- 写失败：必须回滚，且**内存与磁盘保持一致**（task06 §6 倒数第 4 条）---
    _check_write_failure_rollback()


def _check_write_failure_rollback() -> None:
    """写失败注入：勾选回滚 + 内存回滚 + **下一次能真的写成功**。

    注入的是 `_write` **返回 False**，不是"把 `_write` 换成会抛异常的桩"——
    `_write()` 内部本来就吞异常，换桩测的是桩（agent.md §4.13 明确写过这一条）。
    """
    from deskpet import mirror as M
    from deskpet.config import ConfigStore

    section("4b. 写失败注入：回滚勾选 + 内存与磁盘保持一致")

    cfg = _fresh_dir(SCRATCH / "writefail") / "config.json"
    _write_config(cfg, {})
    store = ConfigStore(cfg)
    store.load()
    state = M.MirrorState(store)

    signals: list[tuple[str, bool]] = []
    state.mirror_changed.connect(lambda a, on: signals.append((a, on)))

    real_write = store._write
    store._write = lambda: False  # type: ignore[method-assign]
    try:
        ok = state.toggle("idle")
    finally:
        store._write = real_write  # type: ignore[method-assign]

    check("写失败时 toggle 返回 False", ok is False, f"实际 {ok}")
    check(
        "写失败时内存表未变（勾选据此回滚）",
        state.is_on("idle") is False and state.snapshot() == {},
        f"实际 {state.snapshot()}",
    )
    check(
        "写失败时**不发** mirror_changed（不显示假成功）",
        signals == [],
        f"实际 {signals}",
    )
    check(
        "写失败时 ConfigStore 的内存也回滚了（与磁盘一致）",
        store.get("mirror") == {},
        f"实际 {store.get('mirror')!r}",
    )
    check(
        "写失败留下了原因（界面要能把系统原因原样带出来）",
        bool(store.last_error("mirror")),
        f"last_error={store.last_error('mirror')!r}",
    )

    # 关键回归：失败之后再成功写一次，必须**真的落到磁盘上**。
    # （修之前：内存里已经改成 True，第二次 set 走"值没变"短路返回 True 却不写盘。）
    ok2 = state.toggle("idle")
    check("恢复写盘后再 toggle 成功", ok2 is True, f"实际 {ok2}")
    check(
        "这次**真的**写进了文件（不是走了『值没变』短路）",
        cfg.exists() and _read_config(cfg).get("mirror") == {"idle": True},
        f"文件里的 mirror={_read_config(cfg).get('mirror') if cfg.exists() else None!r}",
    )
    fresh = ConfigStore(cfg)
    fresh.load()
    check(
        "重新读盘的镜像表与内存一致",
        fresh.get("mirror") == store.get("mirror") == state.snapshot(),
        f"文件 {fresh.get('mirror')!r} / 内存 {store.get('mirror')!r} / 表 {state.snapshot()!r}",
    )


# ------------------------------------------------------------ §5 缓存键


def _check_render_cache_key() -> None:
    from deskpet.rendering import FrameRenderer

    section("5. 帧缓存键包含镜像维度（§4.7 的必然踩点）")

    spec = _load_spec()
    renderer = FrameRenderer(spec, 1.0)
    plain = renderer.cache_key("idle", 0, 4, False)
    flipped = renderer.cache_key("idle", 0, 4, True)
    check("镜像不同的两个键不相等", plain != flipped, f"{plain} vs {flipped}")
    check(
        "键里含镜像布尔值",
        plain[-1] is False and flipped[-1] is True,
        f"{plain} / {flipped}",
    )
    check(
        "键有 4 个维度（动作/帧号/档位/镜像）",
        len(plain) == 4,
        f"实际 {len(plain)} 维：{plain}",
    )
    check(
        "不传 mirrored 时默认不镜像（向后兼容）",
        renderer.cache_key("idle", 0, 4) == plain,
        f"{renderer.cache_key('idle', 0, 4)}",
    )
    check(
        "越界档位与夹取后共用同一份缓存",
        renderer.cache_key("idle", 0, 99, True) == renderer.cache_key("idle", 0, 10, True),
        "level 99 与 10 的键相同",
    )

    # 缓存行为：同一张问两次只算一次 miss；翻转态各占一个键。
    renderer.clear()
    renderer.frame("idle", 0, 4, False)
    after_first = renderer.misses
    renderer.frame("idle", 0, 4, False)
    check(
        "同一张（同一镜像态）第二次命中缓存",
        renderer.misses == after_first,
        f"misses {after_first} → {renderer.misses}",
    )
    renderer.frame("idle", 0, 4, True)
    check(
        "翻转态是另一个缓存条目（不是复用未翻转的）",
        renderer.misses == after_first + 1,
        f"misses {after_first} → {renderer.misses}",
    )


# ------------------------------------------------------------ §6 像素级镜像


def _check_mirrored_pixels() -> None:
    import logging

    from deskpet.rendering import FrameRenderer

    section("6. 翻转后帧内容**确实是镜像的**（像素级）")

    spec = _load_spec()
    renderer = FrameRenderer(spec, 1.0)
    plain = renderer.frame("idle", 0, 4, False).toImage()
    flipped = renderer.frame("idle", 0, 4, True).toImage()

    check(
        "镜像前后尺寸相同（不拉伸、不裁切）",
        (plain.width(), plain.height()) == (flipped.width(), flipped.height()),
        f"{plain.width()}x{plain.height()} vs {flipped.width()}x{flipped.height()}",
    )
    check(
        "镜像后内容与原来不同（不是『没变化』）",
        plain != flipped,
        "两张图逐像素不完全相同",
    )

    width, height = plain.width(), plain.height()
    # 判据 1：**不透明区域（剪影）必须完全镜像对应**。
    # 颜色允许 ±1 的插值噪声（SmoothTransformation 的产物），剪影不允许有偏差。
    silhouette_bad = 0
    for y in range(height):
        for x in range(width):
            a = (plain.pixel(x, y) >> 24) & 0xFF
            b = (flipped.pixel(width - 1 - x, y) >> 24) & 0xFF
            if (a >= 128) != (b >= 128):
                silhouette_bad += 1
    check(
        "不透明剪影完全镜像对应（x' = width-1-x）",
        silhouette_bad == 0,
        f"不符的像素数 {silhouette_bad}",
    )

    # 判据 2：颜色差异只能是插值噪声（每通道 <= 1），不能是"变成另一张图"。
    big_diff = 0
    for y in range(height):
        for x in range(width):
            a = plain.pixel(x, y)
            b = flipped.pixel(width - 1 - x, y)
            if a == b:
                continue
            deltas = [abs(((a >> s) & 0xFF) - ((b >> s) & 0xFF)) for s in (0, 8, 16, 24)]
            if max(deltas) > 1:
                big_diff += 1
    check(
        "颜色差异只到 ±1（插值噪声，不是另一张图）",
        big_diff == 0,
        f"超过 ±1 的像素数 {big_diff}",
    )

    # 判据 3：**再次翻转必须回到原图**（逐位相同）——这是"镜像正确"最硬的一条。
    plain_again = renderer.frame("idle", 0, 4, False).toImage()
    check(
        "同一镜像态取两次得到逐位相同的图（缓存没有串味）",
        plain_again == plain,
        "两次取未翻转的图完全一致",
    )

    # 判据 4：与"整幅直接翻转"这条独立路径结果一致（不是自己跟自己比）。
    from PySide6.QtCore import Qt

    from deskpet import pet_loader, size
    from deskpet.rendering import image_size

    source = pet_loader.load_frame_image(spec, "idle", 0)
    manual = source.flipped(Qt.Horizontal)
    target = size.pixel_size(image_size(manual), 4)
    manual_scaled = manual.scaled(
        target[0], target[1], Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    check(
        "与『直接翻转原图再缩放』的结果一致",
        manual_scaled == flipped,
        "两条独立路径给出同一张图",
    )

    # 留痕：产品代码的**实际调用**不许用已弃用的 mirrored()
    # （只在注释里提到它不算——注释里提"别改回去"正是这份代码想说的话）。
    rendering_src = (ROOT / "src" / "deskpet" / "rendering.py").read_text(encoding="utf-8")
    call_lines = [
        line.strip()
        for line in rendering_src.splitlines()
        if ".mirrored(" in line or ".flipped(" in line
    ]
    deprecated_calls = [line for line in call_lines if ".mirrored(" in line]
    # 注释行（以 # 开头或整行都在 docstring 里）不算调用。
    # 这里只要求"确实有一行在调 flipped，且没有一行在调 mirrored"。
    check(
        "翻转走 flipped()（实际调用，不是只有注释提到）",
        any(".flipped(" in line for line in call_lines)
        and not any(
            line.startswith("image") or line.startswith("self") or "=" in line.split("#")[0]
            for line in deprecated_calls
        ),
        f"含镜像调用的行：{call_lines}",
    )
    logging.getLogger(__name__).debug("镜像像素检查完成")


# ------------------------------------------------------------ §7 连续翻转


def _check_repeated_toggles() -> None:
    """专门盯"缓存键漏了镜像"：连续翻转 10 次，每次内容都必须真的换面。"""
    from deskpet.rendering import FrameRenderer

    section("7. 连续翻转 10 次不出现『翻转无效』")

    spec = _load_spec()
    renderer = FrameRenderer(spec, 4)

    baseline = renderer.frame("idle", 0, 4, False).toImage()
    flipped_once = renderer.frame("idle", 0, 4, True).toImage()

    bad = 0
    for round_index in range(10):
        want_mirror = bool(round_index % 2)
        image = renderer.frame("idle", 0, 4, want_mirror).toImage()
        expected = flipped_once if want_mirror else baseline
        if image != expected:
            bad += 1
    check(
        "10 次翻转每次都取到正确的那一面",
        bad == 0,
        f"取错面的次数 {bad}",
    )

    # 再换几帧、换几个档位交叉翻，确认镜像维度在各处都生效。
    cross_bad = 0
    for index in range(0, 6):
        for level in (2, 4, 7):
            for want in (False, True):
                img = renderer.frame("idle", index, level, want).toImage()
                ref = renderer.frame("idle", index, level, want).toImage()
                if img != ref:
                    cross_bad += 1
    check(
        "跨帧/跨档位的镜像取值稳定",
        cross_bad == 0,
        f"不一致的次数 {cross_bad}",
    )

    # LRU 淘汰之后镜像态也不能串味。
    small = FrameRenderer(spec, 1.0, limit=2)
    a = small.frame("idle", 0, 4, False).toImage()
    b = small.frame("idle", 0, 4, True).toImage()
    for index in range(1, 5):
        small.frame("idle", index, 4, index % 2 == 0)
    check(
        "小容量 LRU 淘汰后，再取镜像图仍然正确",
        small.frame("idle", 0, 4, False).toImage() == a
        and small.frame("idle", 0, 4, True).toImage() == b,
        f"缓存上限 2、塞了 6 张之后仍取到原样",
    )


# ------------------------------------------------------------ §8 窗口集成


class _MaskSpy:
    """统计 `apply_mask` 被调用了几次（offscreen 不支持真 setMask，§4.9）。"""

    def __init__(self, window) -> None:
        self.window = window
        self.calls = 0
        self._original = window.apply_mask

    def __enter__(self):
        def wrapped(image):
            self.calls += 1
            return self._original(image)

        self.window.apply_mask = wrapped  # type: ignore[method-assign]
        return self

    def __exit__(self, *exc) -> None:
        self.window.apply_mask = self._original  # type: ignore[method-assign]


def _make_window(*, mirror_store=None):
    from deskpet.mirror import MirrorState
    from deskpet.pet_window import PetWindow

    spec = _load_spec()
    mirror = MirrorState(mirror_store) if mirror_store is not None else None
    window = PetWindow(spec, 4, mirror=mirror)
    return window, mirror


def _check_window_integration(app) -> None:
    from deskpet.config import ConfigStore

    section("8. 窗口集成：菜单状态 / 切动作 / 翻转重绘遮罩")

    cfg = _fresh_dir(SCRATCH / "window") / "config.json"
    store = ConfigStore(cfg)
    store.load()

    window, mirror = _make_window(mirror_store=store)
    window.play_action("idle", auto_start=False)

    check("窗口拿得到镜像表", mirror is not None and window.mirror is mirror, "同一个实例")

    state = window.menu_state()
    check(
        "menu_state：idle 可镜像、未翻转",
        state.can_mirror is True and state.mirror_on is False,
        f"can_mirror={state.can_mirror} mirror_on={state.mirror_on}",
    )
    check(
        "menu_state：初始不是手动休息（文字应为『坐下休息』）",
        state.manual_rest is False and state.action_text() == "坐下休息",
        f"manual_rest={state.manual_rest} 文字={state.action_text()!r}",
    )
    check(
        "menu_state：weedy 有 rest（该项可用）",
        state.can_rest is True,
        "True",
    )

    # --- 三、四项：点镜像翻转 → 画面立刻变面 + 遮罩重算 ---
    before = window.frame_image.copy()
    with _MaskSpy(window) as spy:
        ok = window.toggle_mirror()
    check("toggle_mirror 成功", ok is True, f"实际 {ok}")
    check("翻转后窗口的画面确实变了", window.frame_image != before, "帧内容不同")
    check(
        "翻转后重算了遮罩（衍生数据失效，§4.7）",
        spy.calls >= 1,
        f"apply_mask 调用 {spy.calls} 次",
    )
    check(
        "翻转后菜单状态显示为已勾选",
        window.menu_state().mirror_on is True,
        "mirror_on=True",
    )

    # --- 四：再点一次翻回来 ---
    window.toggle_mirror()
    check("再 toggle 一次翻回来", window.mirrored is False, "mirrored=False")
    check(
        "勾选取消",
        window.menu_state().mirror_on is False,
        "mirror_on=False",
    )

    # --- 五：切到 rest 不翻；切回 idle 仍然翻 ---
    window.toggle_mirror()  # idle 翻上
    check("idle 翻着", window.mirrored is True, "mirrored=True")

    window.toggle_rest()
    check("点『坐下休息』后动作变 rest", window.action == "rest", f"实际 {window.action!r}")
    check("rest 不翻（各自独立）", window.mirrored is False, "mirrored=False")
    check(
        "菜单文字变成『站起来』",
        window.menu_state().manual_rest is True
        and window.menu_state().action_text() == "站起来",
        f"manual_rest={window.menu_state().manual_rest}",
    )

    window.toggle_rest()
    check("再点一次站起来回到 idle", window.action == "idle", f"实际 {window.action!r}")
    check("切回 idle 仍然是翻的（记忆生效）", window.mirrored is True, "mirrored=True")
    check(
        "菜单文字变回『坐下休息』",
        window.menu_state().action_text() == "坐下休息",
        f"实际 {window.menu_state().action_text()!r}",
    )

    # --- 五续：rest 单独翻过之后也各自记住 ---
    window.toggle_rest()
    check("再坐下", window.action == "rest", f"实际 {window.action!r}")
    window.toggle_mirror()
    check("rest 上翻一次", window.mirrored is True, "mirrored=True")
    window.toggle_rest()  # 站起来
    check("切到 idle", window.action == "idle", f"实际 {window.action!r}")
    check("idle 仍然是翻的", window.mirrored is True, "mirrored=True")
    window.toggle_rest()  # 再坐下
    check(
        "切回 rest 仍然是翻的（各自独立记忆）",
        window.action == "rest" and window.mirrored is True,
        f"action={window.action} mirrored={window.mirrored}",
    )
    window.play_action("idle", auto_start=False)

    # --- 八：适用范围内外 ---
    window.play_action("waving", auto_start=False)
    waving_state = window.menu_state()
    check(
        "waving 时镜像项不可用（适用范围外）",
        waving_state.can_mirror is False and waving_state.mirror_on is False,
        f"can_mirror={waving_state.can_mirror}",
    )
    check(
        "waving 时 toggle_mirror 被拒（不写盘、画面不动）",
        window.toggle_mirror() is False and window.mirrored is False,
        "返回 False",
    )
    # 被拒的请求**不能改动配置**：先记下此刻的表，被拒之后必须逐项不变。
    # （不写死具体的字面量——那段序列翻了很多次，写死等于把测试绑在序列上。）
    table_before_reject = dict(store.get("mirror"))
    reject_result = window.toggle_mirror()
    check(
        "适用范围外被拒后配置没有被改动",
        reject_result is False and store.get("mirror") == table_before_reject,
        f"拒绝前 {table_before_reject!r} / 拒绝后 {store.get('mirror')!r}",
    )
    check(
        "被拒的项也没有被写进配置（waving 不是合法键）",
        "waving" not in store.get("mirror"),
        f"实际 {store.get('mirror')!r}",
    )
    window.play_action("idle", auto_start=False)

    # --- 九：Esc / 点菜单外 → 不做任何事 ---
    snapshot = (
        window.action,
        window.mirrored,
        window.manual_rest,
        window.base_position,
        window.level,
    )
    window._handle_menu_choice("")
    check(
        "没选中任何项（Esc / 点菜单外）→ 不做任何事",
        (
            window.action,
            window.mirrored,
            window.manual_rest,
            window.base_position,
            window.level,
        )
        == snapshot,
        f"状态快照不变：{snapshot}",
    )

    # 未知 id 也不许"顺手实现"
    window._handle_menu_choice("resize")
    window._handle_menu_choice("settings")
    window._handle_menu_choice("visibility")
    check(
        "点到未接线的 id 什么都不做（不是顺手实现）",
        (
            window.action,
            window.mirrored,
            window.manual_rest,
            window.base_position,
            window.level,
        )
        == snapshot,
        f"状态快照不变：{snapshot}",
    )

    # --- 退出项真的关窗口 ---
    closed: list[bool] = []
    window.close = lambda *a, **k: closed.append(True)  # type: ignore[method-assign]
    window._handle_menu_choice("quit")
    check("点『退出』会关闭窗口", closed == [True], f"实际 {closed}")
    del window

    # --- 没有配置时（mirror=None）也不崩 ---
    bare, bare_mirror = _make_window()
    bare.play_action("idle", auto_start=False)
    check("没有镜像表时 mirrored 恒为假", bare.mirrored is False, "False")
    check("没有镜像表时 toggle_mirror 返回 False", bare.toggle_mirror() is False, "False")
    check(
        "没有镜像表时菜单里镜像项不可点",
        bare.menu_state().can_mirror is False,
        "can_mirror=False",
    )

    # --- 拖动中右键不打断拖动（§6 最后一条）---
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QContextMenuEvent

    drag_window, _ = _make_window(mirror_store=store)
    drag_window.play_action("idle", auto_start=False)
    drag_window._dragging = True
    drag_window._press_global = QPoint(1000, 1000)
    drag_window._drag_base_pos = QPoint(500, 500)
    drag_window.drag_by(40, 40)
    moved_to = drag_window.base_position

    event = QContextMenuEvent(
        QContextMenuEvent.Mouse, QPoint(10, 10), QPointF(1010.0, 1010.0).toPoint()
    )
    popup_called: list[bool] = []
    import deskpet.pet_window as PW

    original_build = PW.context_menu.build_menu
    PW.context_menu.build_menu = lambda *a, **k: popup_called.append(True)  # type: ignore[assignment]
    try:
        drag_window.contextMenuEvent(event)
    finally:
        PW.context_menu.build_menu = original_build  # type: ignore[assignment]

    check(
        "拖动中右键不弹菜单（明确忽略）",
        popup_called == [],
        f"build_menu 被调用 {len(popup_called)} 次",
    )
    check(
        "拖动中右键不打断拖动（位置与拖动状态都没变）",
        drag_window.base_position == moved_to and drag_window._dragging is True,
        f"位置 {drag_window.base_position} / dragging={drag_window._dragging}",
    )
    # 松手之后右键恢复正常
    drag_window._dragging = False
    PW.context_menu.build_menu = lambda *a, **k: popup_called.append(True)  # type: ignore[assignment]
    try:
        # 只验"会走到构建菜单"这一步：exec 会真的弹窗，所以用桩替掉。
        class _FakeMenu:
            def setAttribute(self, *a, **k) -> None:
                pass

            def exec(self, *a, **k):
                return None

        PW.context_menu.build_menu = lambda *a, **k: (popup_called.append(True), _FakeMenu())[1]  # type: ignore[assignment]
        drag_window.contextMenuEvent(event)
    finally:
        PW.context_menu.build_menu = original_build  # type: ignore[assignment]
    check(
        "松手后右键正常弹菜单",
        popup_called == [True],
        f"build_menu 被调用 {len(popup_called)} 次",
    )
    del app


# ------------------------------------------------------------ §9 旧名 sit


def _check_no_alias_for_sit() -> None:
    from deskpet import config, mirror as M
    from deskpet.config import ConfigStore

    section("9. 旧键名 sit 被当作适用范围外丢弃（不许做别名）")

    check(
        "MIRRORABLE 里没有 sit",
        "sit" not in M.MIRRORABLE,
        f"实际 {M.MIRRORABLE}",
    )

    cfg = _fresh_dir(SCRATCH / "sit") / "config.json"
    _write_config(cfg, {"mirror": {"sit": True}})
    store = ConfigStore(cfg)
    with _LogCapture() as logs:
        store.load()
    check(
        "sit 键被丢弃（mirror 成空对象）",
        store.get("mirror") == {},
        f"实际 {store.get('mirror')!r}",
    )
    check(
        "丢弃 sit 时有警告",
        "sit" in logs.text(),
        f"日志：{logs.text()[-160:]}",
    )
    check("启动不崩（load 正常返回）", store.has("mirror") is True, "has(mirror)=True")

    state = M.MirrorState(store)
    check(
        "sit 不会被翻",
        state.is_on("sit") is False and state.is_on("rest") is False,
        f"snapshot={state.snapshot()}",
    )

    # 产品代码里不许出现 sit 别名映射
    mirror_src = (ROOT / "src" / "deskpet" / "mirror.py").read_text(encoding="utf-8")
    check(
        "mirror.py 里没有为 sit 做别名",
        '"sit"' not in mirror_src,
        "搜不到 sit 字符串常量",
    )
    del config


# ------------------------------------------------------------ §10 命令行


def _check_cli_scope() -> None:
    from deskpet.app import _build_parser

    section("10. 命令行：本 task 不新增参数")

    parser = _build_parser()
    help_text = parser.format_help()
    check(
        "--fps 仍然不得复活",
        "--fps" not in help_text.split(),
        "--help 里搜不到 --fps",
    )
    check(
        "没有偷偷加镜像相关的命令行开关（镜像只走右键菜单）",
        "--mirror" not in help_text,
        "--help 里搜不到 --mirror",
    )
    parsed = parser.parse_args(["--action", "waving"])
    check(
        "--action waving 能解析（用来验『适用范围外』那条）",
        parsed.action == "waving",
        f"实际 {parsed.action!r}",
    )


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
    except OSError as exc:  # pragma: no cover
        shown = f"（结果写不出去：{exc}）"
    print(_LINES[-1])
    print(f"结果已写入：{shown}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
