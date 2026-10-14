"""task11 自检：设置窗口（五行设置、改完立即生效、绝不显示假成功）。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task11_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task11.py

**窗口用例走 offscreen 平台插件**。offscreen 的虚拟屏幕只有 **800×800**
（§4.14），所以"居中 / 1.3 处"这类几何断言一律**在给定的屏幕矩形上**验
（§4.11：别断言随环境变化的量）；真屏幕那几条由 `check_task11_real.py` 验。

自检**只碰自己造的临时包**（`data/` 下的 scratch 根）：`assets/` 一个字节都不动，
用户真实目录也不碰（§4.11）。
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
RESULT_FILE = ROOT / "data" / "task11_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task11_scratch"

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


def _source_of(name: str) -> str:
    return (ROOT / "src" / "deskpet" / name).read_text(encoding="utf-8")


def _code_only(name: str) -> str:
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，task06/07/08 各踩过）。"""
    import io
    import tokenize

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


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _label_texts(window) -> list[str]:
    """窗口里所有 `QLabel` 的文字（找"标题带后缀"这类断言用）。"""
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in window.findChildren(QLabel)]


def _value_label_text(window, key: str) -> str:
    """某一行滑块右边的数值标签文字（`_value_labels` 里那一份）。"""
    label = window._value_labels.get(key)
    return label.text() if label is not None else ""


def _read_config(path: Path) -> dict:
    """把 `config.json` 读回来（**判据要看磁盘，不看内存**）。"""
    import json

    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _raise_permission_denied(*args, **kwargs):
    """给 `os.replace` 用的桩：抛出与"拒绝访问"一致的异常（§6 的注入写法）。"""
    raise PermissionError("拒绝访问")


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

    app = QApplication.instance() or QApplication([])  # noqa: F841

    # ------------------------------------------------------------ §0 脚手架
    section("0. 脚手架：字段描述表能读出来")

    from deskpet import settings as st
    from deskpet import sounds

    check(
        "FIELDS 有 5 行",
        len(st.FIELDS) == 5,
        f"实测 {len(st.FIELDS)}",
    )
    check(
        "行顺序 = 角色 / 开机自启 / 呼出快捷键 / 音量 / 久置时间",
        tuple(field.title for field in st.FIELDS)
        == ("角色", "开机自启", "呼出快捷键", "音量", "久置时间"),
        "、".join(field.title for field in st.FIELDS),
    )
    check(
        "开机自启这一行**没有配置键**（它是 task14 的系统状态，不是 config 项）",
        st.FIELDS[1].key == "" and st.FIELDS[1].kind is st.FieldKind.CHECK,
        f"key={st.FIELDS[1].key!r}",
    )
    check(
        "分组归属 = 常规×3 / 声音×1 / 行为×1",
        tuple(field.group for field in st.FIELDS)
        == ("常规", "常规", "常规", "声音", "行为"),
        "、".join(field.group for field in st.FIELDS),
    )
    check(
        "分组顺序 = 常规 / 声音 / 行为",
        st.GROUP_ORDER == ("常规", "声音", "行为"),
        "、".join(st.GROUP_ORDER),
    )

    # 真值只允许一处：别的模块不许再写一遍量程 / 单位 / 提示文字。
    for module in ("app.py", "context_menu.py", "pet_window.py", "keycapture.py"):
        code = _code_only(module)
        check(
            f"{module} 里没有第二份设置界面真值",
            "音量" not in code and "久置时间" not in code,
            "行标题只允许出现在 settings.py",
        )

    # ------------------------------------------------------------ 附录 B 文字
    section("2. 文字：附录 B 逐字")

    check("窗口标题 = 桌宠设置", st.TITLE == "桌宠设置", st.TITLE)
    check(
        "分组 = 常规 / 声音 / 行为",
        st.GROUP_ORDER == ("常规", "声音", "行为"),
        "、".join(st.GROUP_ORDER),
    )
    check(
        "五个行标题逐字一致",
        tuple(field.title for field in st.FIELDS)
        == ("角色", "开机自启", "呼出快捷键", "音量", "久置时间"),
        "、".join(field.title for field in st.FIELDS),
    )
    check(
        "五句提示逐字一致",
        tuple(field.hint for field in st.FIELDS)
        == (
            "换一个形象；切换需要重启桌宠（会自动问你要不要现在重启）",
            "登录 Windows 后自动启动桌宠",
            "隐藏后按此键呼出；点右边按钮后按一次想要的组合",
            "点击互动与坐下时的音效音量",
            "鼠标静止这么久后开始张望，再过一半时间坐下",
        ),
        "、".join(field.hint[:6] for field in st.FIELDS),
    )
    check("逃生门按钮 = 打开启动文件夹", st.TEXT_OPEN_STARTUP == "打开启动文件夹")
    check("状态栏前缀 = 开机自启：", st.STATUS_PREFIX == "开机自启：")
    check(
        "未实现后缀 = （暂未开放）",
        st.NOT_READY_SUFFIX == "（暂未开放）",
        st.NOT_READY_SUFFIX,
    )
    check("失败前缀 = 设置失败：", st.FAILED_PREFIX == "设置失败：", st.FAILED_PREFIX)
    check("当前项前缀 = ● ", st.CURRENT_PREFIX == "● ", repr(st.CURRENT_PREFIX))
    check(
        "坏包后缀 = 　⚠ 不可用",
        st.BROKEN_SUFFIX == "　⚠ 不可用",
        repr(st.BROKEN_SUFFIX),
    )
    check(
        "角色项文字 = 展示名（命名空间）",
        st.PetChoice("phoebe-2", "菲比", True).label(False) == "菲比（phoebe-2）",
        st.PetChoice("phoebe-2", "菲比", True).label(False),
    )
    check(
        "当前项多一个 `● ` 前缀",
        st.PetChoice("phoebe-2", "菲比", True).label(True) == "● 菲比（phoebe-2）",
        st.PetChoice("phoebe-2", "菲比", True).label(True),
    )
    check(
        "坏包项带 `　⚠ 不可用` 后缀",
        st.PetChoice("phoebe-2", "菲比", False).label(False) == "菲比（phoebe-2）　⚠ 不可用",
        st.PetChoice("phoebe-2", "菲比", False).label(False),
    )

    # ------------------------------------------------------------ §3 描述表
    section("3. 字段描述表：控件类型与量程")

    kinds = tuple(field.kind for field in st.FIELDS)
    check(
        "控件类型 = 下拉 / 勾选 / 录入 / 滑块 / 滑块",
        kinds
        == (
            st.FieldKind.COMBO,
            st.FieldKind.CHECK,
            st.FieldKind.KEYSEQ,
            st.FieldKind.SLIDER,
            st.FieldKind.SLIDER,
        ),
        "、".join(kind.value for kind in kinds),
    )
    volume, idle = st.FIELDS[3], st.FIELDS[4]
    check(
        "音量量程 0–100、单位为空",
        (volume.minimum, volume.maximum, volume.unit) == (0, 100, ""),
        f"{volume.minimum}–{volume.maximum} 单位 {volume.unit!r}",
    )
    check(
        "久置时间量程 5–300、单位为 ` 秒`",
        (idle.minimum, idle.maximum, idle.unit) == (5, 300, " 秒"),
        f"{idle.minimum}–{idle.maximum} 单位 {idle.unit!r}",
    )
    check(
        "音量量程引用 sounds 的真值（不另写 0/100）",
        (volume.minimum, volume.maximum) == (sounds.VOLUME_MIN, sounds.VOLUME_MAX),
        f"{volume.minimum}–{volume.maximum}",
    )
    check(
        # ⚠ task12 起 `呼出快捷键` 已接线，只剩 `开机自启`（task14）。
        # 这三条期望值是 task12 按事实更新的——它们原文就是"本 task 的边界"，
        # 而那个边界已经被下游 task 推掉了（与 task10 更新 task07 那两条同理）。
        "未实现的只剩 开机自启 一行",
        tuple(field.title for field in st.FIELDS if not field.implemented)
        == ("开机自启",),
        "、".join(field.title for field in st.FIELDS if not field.implemented) or "（没有）",
    )
    check(
        "已删除的两个开关不在描述表里",
        not any("置顶" in field.title or "记住" in field.title for field in st.FIELDS),
        "始终置顶 / 记住位置与大小",
    )
    check(
        "`mirror` 不在设置界面的字段表里",
        all(field.key != "mirror" for field in st.FIELDS),
        "它是按状态记忆的，不该给人手改",
    )

    return _window_sections()


def _window_sections() -> int:
    """§4 起：真建窗口，走产品自己的路径。"""
    from PySide6.QtWidgets import QApplication, QComboBox, QGroupBox, QScrollArea, QSlider

    app = QApplication.instance() or QApplication([])

    from deskpet import paths, pet_loader
    from deskpet.config import ConfigStore
    from deskpet import settings as st

    with paths.override_dirs(program=ROOT, resource=ROOT):
        scratch = _fresh_dir(SCRATCH)
        cfg = scratch / "config.json"

        # -------------------------------------------------------- §4 窗口形态
        section("4. 窗口：标题 / 尺寸 / 分组 / 禁用行")

        store = ConfigStore(cfg)
        store.load()
        window = st.SettingsWindow(store, current_pet="weedy")

        check("标题 = 桌宠设置", window.windowTitle() == "桌宠设置", window.windowTitle())
        check(
            "尺寸 = 560 × 516 逻辑单位",
            (window.width(), window.height()) == (560, 516),
            f"{window.width()}×{window.height()}",
        )
        check("是模态窗口（抓住输入焦点）", window.isModal(), f"isModal={window.isModal()}")

        boxes = window.findChildren(QGroupBox)
        check(
            "三组，顺序 = 常规 / 声音 / 行为",
            [box.title() for box in boxes] == ["常规", "声音", "行为"],
            "、".join(box.title() for box in boxes),
        )
        check("内容区装在 QScrollArea 里（够高的问题不靠调大数字）",
              len(window.findChildren(QScrollArea)) >= 1)

        # 禁用态与标题后缀：只剩 `开机自启` 一行未实现。
        # ⚠ task12 起 `呼出快捷键` **已解锁**（它原来那两条期望值已按事实更新，
        # 理由见上一节：那个"未实现"的边界是 task11 自己划的，task12 推掉了它）。
        combo, check_w, key_w = (
            window._widgets["pet"],
            window._find_slider(window._widgets["volume"]),
            window._widgets["hotkey"],
        )
        check("角色下拉可用", isinstance(combo, QComboBox) and combo.isEnabled())
        check(
            "呼出快捷键控件**可用**（task12 已接线）",
            key_w.isEnabled(),
            f"enabled={key_w.isEnabled()}",
        )
        check(
            "呼出快捷键行标题**不带** `（暂未开放）`",
            "呼出快捷键" in _label_texts(window)
            and "呼出快捷键（暂未开放）" not in _label_texts(window),
            "、".join(t for t in _label_texts(window) if "快捷键" in t),
        )
        check(
            "开机自启行标题带 `（暂未开放）`",
            "开机自启（暂未开放）" in _label_texts(window),
            "、".join(t for t in _label_texts(window) if "自启" in t),
        )
        check(
            "未实现的项是**禁用**不是隐藏（控件仍在界面上）",
            "hotkey" in window._widgets and window._widgets["hotkey"].isVisibleTo(window),
            "控件对象存在且属于窗口",
        )

        # 滑块量程（真控件上的值，不是描述表里的）。
        vol = window._find_slider(window._widgets["volume"])
        idle = window._find_slider(window._widgets["idle_seconds"])
        check(
            "音量滑块量程 0–100",
            (vol.minimum(), vol.maximum()) == (0, 100),
            f"{vol.minimum()}–{vol.maximum()}",
        )
        check(
            "久置时间滑块量程 5–300",
            (idle.minimum(), idle.maximum()) == (5, 300),
            f"{idle.minimum()}–{idle.maximum()}",
        )
        check(
            "数值标签带单位：久置时间 = ` 秒`、音量为空",
            _value_label_text(window, "idle_seconds").endswith(" 秒")
            and not _value_label_text(window, "volume").endswith("秒"),
            f"久置={_value_label_text(window, 'idle_seconds')!r} "
            f"音量={_value_label_text(window, 'volume')!r}",
        )
        check(
            "打开窗口时控件值 = 配置里的值",
            vol.value() == store.get("volume") and idle.value() == store.get("idle_seconds"),
            f"volume={vol.value()} idle={idle.value()}",
        )

        # -------------------------------------------------------- §5 只读
        section("5. 打开设置窗口只读不写（时间戳与内容都不变）")

        before = cfg.read_bytes() if cfg.exists() else None
        check(
            "构造窗口**没有**创建 config.json（读操作只读）",
            cfg.exists() is False,
            f"存在={cfg.exists()}",
        )
        window2 = st.SettingsWindow(store, current_pet="weedy")
        check(
            "再开一个窗口仍不写盘",
            (cfg.read_bytes() if cfg.exists() else None) == before,
            "文件内容逐字节相同",
        )
        window2.deleteLater()
        window.deleteLater()

        # -------------------------------------------------------- §6 写入与回滚
        section("6. 改完立即生效 + 写失败回滚 + 不出现写入回路")

        store = ConfigStore(cfg)
        store.load()
        window = st.SettingsWindow(store, current_pet="weedy")
        vol = window._find_slider(window._widgets["volume"])
        idle = window._find_slider(window._widgets["idle_seconds"])

        writes: list[tuple[str, object]] = []
        store.config_changed.connect(lambda key, value: writes.append((key, value)))

        vol.setValue(35)
        check(
            "拖音量 → 立刻写回配置（config.json 里的 volume 同步更新）",
            store.get("volume") == 35 and _read_config(cfg).get("volume") == 35,
            f"内存={store.get('volume')} 磁盘={_read_config(cfg).get('volume')}",
        )
        check(
            "拖音量 → 发出 config_changed（音量立刻生效挂在这一条上）",
            ("volume", 35) in writes,
            f"收到 {writes}",
        )
        check(
            "数值标签跟着滑块走",
            _value_label_text(window, "volume") == "35",
            _value_label_text(window, "volume"),
        )

        idle.setValue(7)
        check(
            "拖久置时间 → 立刻写回配置（状态机每次判定现读，无需重启）",
            store.get("idle_seconds") == 7 and _read_config(cfg).get("idle_seconds") == 7,
            f"内存={store.get('idle_seconds')} 磁盘={_read_config(cfg).get('idle_seconds')}",
        )
        check(
            "久置时间的数值标签带单位",
            _value_label_text(window, "idle_seconds") == "7 秒",
            repr(_value_label_text(window, "idle_seconds")),
        )

        # 写失败：换掉 `_write`（§4.13 的教训：桩要返回 False，不抛）。
        section("6b. 注入写失败：控件回滚 + 状态栏带出系统原因 + 无回路")

        window._last_failure = ""
        window.refresh_status()
        store._write = lambda: False
        writes.clear()
        vol.setValue(90)
        check(
            "写失败 → 控件**回滚**到配置里的真值",
            vol.value() == store.get("volume") == 35,
            f"滑块={vol.value()} 配置={store.get('volume')}",
        )
        check(
            "写失败 → 状态栏 = `设置失败：` + 系统原因",
            window._status_label.text().startswith("设置失败："),
            repr(window._status_label.text()),
        )
        check(
            "写失败 → **没有**发 config_changed（绝不显示假成功）",
            writes == [],
            f"收到 {writes}",
        )

        # 写入回路：回滚会改控件 → 若没有守卫就会再写一次 → 无限递归。
        calls = {"n": 0}
        def _counting_write() -> bool:
            calls["n"] += 1
            if calls["n"] > 50:
                raise AssertionError("写入回路：回滚又触发了写入")
            return False

        store._write = _counting_write
        vol.setValue(80)
        check(
            "写失败时**不出现写入回路**（一次改动只尝试写一次）",
            calls["n"] == 1,
            f"_write 被调 {calls['n']} 次",
        )
        check(
            "回路测试后控件仍在真值上",
            vol.value() == 35,
            f"滑块={vol.value()}",
        )

        # 恢复写盘能力：下一次改动必须真的写进文件。
        del store._write  # 去掉实例属性，回到类上的真实现
        vol.setValue(45)
        check(
            "恢复写盘后改值**真的**写进文件（内存与磁盘一致）",
            vol.value() == 45 and _read_config(cfg).get("volume") == 45,
            f"滑块={vol.value()} 磁盘={_read_config(cfg).get('volume')}",
        )
        check(
            "恢复后状态栏不再显示失败",
            not window._status_label.text().startswith("设置失败："),
            repr(window._status_label.text()),
        )

        # 真实现的写失败（注入 os.replace 抛异常，§4.13：测真实现而不是桩）。
        section("6c. 真实现 + os.replace 抛异常：系统原文原样带出来")

        from deskpet import config as config_module

        real_replace = config_module.os.replace
        config_module.os.replace = _raise_permission_denied
        try:
            before_value = store.get("volume")
            vol.setValue(70)
            check(
                "真实现写失败 → 控件回滚",
                vol.value() == before_value,
                f"滑块={vol.value()} 期望={before_value}",
            )
            check(
                "真实现写失败 → 状态栏带出**系统原文**「拒绝访问」",
                "拒绝访问" in window._status_label.text(),
                repr(window._status_label.text()),
            )
        finally:
            config_module.os.replace = real_replace

        # -------------------------------------------------------- §7 错误优先
        section("7. 状态栏：error 存在时失败原因优先于状态本身")

        # 先把状态栏清干净（§6c 刚注入过一次失败，那是**上一次操作**的结论）。
        # 不重置的话这里验的就不是"正常情况下显示什么"了。
        store._write = lambda: False
        vol.setValue(90)
        check(
            "有 error 时显示的是**失败原因**而不是状态",
            window._status_label.text().startswith("设置失败："),
            repr(window._status_label.text()),
        )
        del store._write
        vol.setValue(60)
        check(
            "写成功之后状态栏回到状态本身",
            window._status_label.text() == "开机自启：未开启",
            repr(window._status_label.text()),
        )
        vol.setValue(65)
        check(
            "**正常情况下**显示状态本身（不是失败原因）",
            window._status_label.text() == "开机自启：未开启",
            repr(window._status_label.text()),
        )

        window.close()
        window.deleteLater()

        # -------------------------------------------------------- §8 角色候选
        section("8. 角色下拉：全部包（含坏包）+ ● / ⚠ 标记")

        # 用户包根是 `<程序目录>/data/pets`（`paths.user_pets_dir()`），所以把
        # **程序目录**重定向到自检自己的 scratch 根，用户包就落在它下面了。
        # ⚠ 直接把包丢进 `<ROOT>/data/pets` 会污染用户真实目录（§4.11 禁止）。
        fake_program = _fresh_dir(SCRATCH / "program")
        (fake_program / "data").mkdir(parents=True, exist_ok=True)
        _write_fake_pet(fake_program / "data" / "pets" / "goodpet", "好宠物", broken=False)
        _write_fake_pet(fake_program / "data" / "pets" / "brokepet", "坏宠物", broken=True)

        with paths.override_dirs(program=fake_program, resource=ROOT):
            choices = st.pet_choices("weedy")
            names = [choice.namespace for choice in choices]
            check(
                "候选里能看到**全部**包（自带两个 + 自检造的两个）",
                set(names) >= {"weedy", "weedy_sightseer", "goodpet", "brokepet"},
                "、".join(names),
            )
            broken = next(choice for choice in choices if choice.namespace == "brokepet")
            check("坏包**仍然出现在**候选里", broken is not None, broken.namespace)
            check("坏包标记为不可用", broken.ok is False, f"ok={broken.ok}")
            check(
                "坏包的文字带 `　⚠ 不可用`",
                broken.label(False).endswith("　⚠ 不可用"),
                broken.label(False),
            )
            check(
                "坏包的原因被带出来（task13 要「提供详情」）",
                bool(broken.issues),
                "；".join(broken.issues[:1]),
            )
            good = next(choice for choice in choices if choice.namespace == "goodpet")
            check("好包标记为可用", good.ok is True, f"ok={good.ok}")
            check(
                "当前生效的那个排在**第一位**",
                choices[0].namespace == "weedy",
                f"第一位 = {choices[0].namespace}",
            )
            check(
                "当前生效项带 `● ` 前缀",
                choices[0].label(True).startswith("● "),
                choices[0].label(True),
            )
            check(
                "非当前项**不带** `● ` 前缀",
                not any(
                    choice.label(False).startswith("● ")
                    for choice in choices
                    if choice.namespace != "weedy"
                ),
                "只有当前项带前缀",
            )

            # 选一个可用包 → 写配置 + 提示"重启后生效"。
            store = ConfigStore(fake_program / "data" / "cfg2.json")
            store.load()
            window = st.SettingsWindow(store, current_pet="weedy")
            combo = window._widgets["pet"]
            index = next(
                position
                for position, choice in enumerate(window._choices)
                if choice.namespace == "goodpet"
            )
            combo.setCurrentIndex(index)
            check(
                "选可用包 → 配置被写入",
                store.get("pet") == "goodpet",
                f"pet={store.get('pet')!r}",
            )
            check(
                "选可用包 → 提示「重启后生效」",
                st.PET_RESTART_HINT in window._status_label.text(),
                repr(window._status_label.text()),
            )
            check(
                "选完可用包后 `● ` 挪到了新项上",
                combo.currentText().startswith("● "),
                combo.currentText(),
            )
            check(
                "settings.py 里**没有**任何重启代码（重启属 task13）",
                "QProcess" not in _code_only("settings.py")
                and "startDetached" not in _code_only("settings.py"),
                "卡里把自动重启明确留给 task13（提示文字里出现「重启」是说明，不是实现）",
            )

            # 选一个坏包 → 提示详情 + 退回默认形象 + 不重启 + 界面回真实状态。
            #
            # ⚠ task13 推掉了 task11 划的这条边界（与 task12 推掉"呼出快捷键未实现"
            # 同理）：task11 时"坏包不动配置"，task13 §6 明确要求
            # **"配置被改回默认形象"**（否则下次启动必然再踩一次同一个坑）。
            # 所以下面两条期望值按 task13 的行为改写：
            # ① 配置从 `goodpet` 被改回**默认形象**（写空串 = 回到"没表态"，
            #    真值在 `pet_spec.DEFAULT_PET_NAMESPACE`，不在这里抄一个 weedy）；
            # ② 界面上的当前项因此变成**默认形象**那一项。
            section("8b. 选坏包：提示详情 + 退回默认形象 + **配置改回默认形象**（task13）")

            index = next(
                position
                for position, choice in enumerate(window._choices)
                if choice.namespace == "brokepet"
            )
            combo.setCurrentIndex(index)
            check(
                "选坏包 → 配置**改回默认形象**（空串，不让下次启动再踩同一个坑）",
                store.get("pet") == "",
                f"pet={store.get('pet')!r}",
            )
            check(
                "选坏包 → 状态栏说出默认形象",
                "默认形象" in window._status_label.text()
                and st.DEFAULT_PET_NAMESPACE in window._status_label.text(),
                repr(window._status_label.text()[:90]),
            )
            check(
                "选坏包 → 提示里带着**那个包的问题**（task13 要「提供详情」）",
                "「brokepet」的问题" in window._status_label.text(),
                repr(window._status_label.text()[-70:]),
            )
            # 界面必须与"真实生效的角色"一致：配置已经是空串 → 当前项就该是默认形象。
            check(
                "选坏包 → 界面**退回真实状态**（当前项是默认形象，与配置一致）",
                combo.currentText().startswith("● ")
                and st.DEFAULT_PET_NAMESPACE in combo.currentText(),
                combo.currentText(),
            )
            window.close()
            window.deleteLater()

        # -------------------------------------------------------- §9 逃生门
        section("9. 左下角逃生门：打开启动文件夹")

        store = ConfigStore(scratch / "cfg3.json")
        store.load()
        window = st.SettingsWindow(store, current_pet="weedy")

        from PySide6.QtWidgets import QDialogButtonBox, QPushButton

        button = window._startup_button
        check(
            "有 `打开启动文件夹` 按钮",
            isinstance(button, QPushButton) and button.text() == "打开启动文件夹",
            button.text() if isinstance(button, QPushButton) else type(button).__name__,
        )
        box = window.findChild(QDialogButtonBox)
        check("按钮挂在底部的按钮行里", box is not None and button in box.buttons())
        # `QDialogButtonBox.button()` 只收 `StandardButton`，而这两颗都是自定义按钮，
        # 所以"左下角"要按**布局里的次序**判：逃生门必须排在关闭按钮左边。
        # ⚠ 遍历要用 `layout().itemAt(i)`：`layout().children()` 里一个 widget 都没有
        # （它装的是 layout item，不是子对象），第一版就是这么写的、实测 count=0。
        layout = box.layout() if box is not None else None
        widgets = [
            layout.itemAt(index).widget()
            for index in range(layout.count())
            if layout.itemAt(index) is not None and layout.itemAt(index).widget() is not None
        ]
        check(
            "逃生门排在按钮行最左（左下角、随手可及）",
            len(widgets) >= 2 and widgets[0] is button,
            "、".join(w.text() for w in widgets),
        )
        check(
            "关闭按钮在它右边",
            len(widgets) >= 2 and widgets[-1].text() == "关闭",
            "、".join(w.text() for w in widgets),
        )

        # 点击真的会去开 shell:startup（**拦下 QDesktopServices**，不自检开资源管理器）。
        opened: list[str] = []
        from deskpet import settings as st_module

        real_open = st_module.QDesktopServices.openUrl
        st_module.QDesktopServices.openUrl = lambda url: (opened.append(url.toString()), True)[1]
        try:
            button.click()
        finally:
            st_module.QDesktopServices.openUrl = real_open
        check(
            "点击打开的是 `shell:startup`",
            opened == ["shell:startup"],
            f"实测 {opened}",
        )
        check(
            "没有自己拼 %APPDATA%（启动文件夹的推导真值留给 task14）",
            "APPDATA" not in _code_only("settings.py"),
            "判据必须用 _code_only()：解释「为什么不用它」的注释里会出现这个词",
        )

        # -------------------------------------------------------- §10 mirror 保住
        section("10. 关掉设置窗口后 mirror 没被过滤掉")

        store2 = ConfigStore(scratch / "cfg4.json")
        store2.load()
        store2.set("mirror", {"idle": True})
        check(
            "写 mirror 之后配置里有它",
            _read_config(scratch / "cfg4.json").get("mirror") == {"idle": True},
            _read_config(scratch / "cfg4.json").get("mirror"),
        )

        window = st.SettingsWindow(store2, current_pet="weedy")
        window._find_slider(window._widgets["volume"]).setValue(33)
        window.close()
        window.deleteLater()
        after = _read_config(scratch / "cfg4.json")
        check(
            "设置窗口写过盘之后 `mirror` **仍然在**配置里（已知键过滤认得它）",
            after.get("mirror") == {"idle": True},
            f"mirror={after.get('mirror')!r}",
        )
        check(
            "设置窗口**不显示** mirror 这一项",
            all(field.key != "mirror" for field in st.FIELDS),
            "只在配置里的已知键里，不在字段表里",
        )

        # -------------------------------------------------------- §11 关闭与重开
        section("11. 关闭（Esc / 关闭按钮）之后能再打开；实例引用被释放")

        store = ConfigStore(scratch / "cfg5.json")
        store.load()
        store.set("volume", 41)

        from deskpet.pet_window import PetWindow
        from deskpet import pet_loader
        from deskpet.app import SettingsController

        spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
        pet_window = PetWindow(spec, 4)

        controller = SettingsController(store, pet_window)
        check("一开始没有设置窗口", controller.dialog is None, "dialog=None")

        controller.open()
        first = controller.dialog
        check("open() 之后有一个设置窗口", first is not None and first.isVisible(),
              f"visible={first.isVisible() if first else None}")

        # 连点 5 次 → 只有一个窗口，且被前置。
        opened: list[str] = []
        real_show = st.SettingsWindow.show
        st.SettingsWindow.show = lambda self: opened.append("show")
        try:
            for _ in range(5):
                controller.open()
        finally:
            st.SettingsWindow.show = real_show
        check(
            "连点 5 次 `设置…` → 始终是**同一个**窗口实例",
            controller.dialog is first,
            f"同一个={controller.dialog is first}",
        )
        check(
            "连点 5 次 → **不会**再 show 出新窗口（走前置那条路）",
            opened == [],
            f"show 被调 {len(opened)} 次",
        )
        check(
            "重复打开时会重新居中（屏幕可能换了）",
            controller.dialog is first,
            "走 reload + center_on_screen + raise_",
        )

        # Esc 关闭 → 引用被释放 → 再打开是**新的**窗口。
        first.reject()  # Esc 的效果就是 reject()
        check("Esc 关闭后引用被释放（dialog=None）", controller.dialog is None,
              f"dialog={controller.dialog!r}")

        controller.open()
        second = controller.dialog
        check(
            "再打开得到一个**新的**窗口实例（不复用已关掉的那个）",
            second is not None and second is not first,
            f"新实例={second is not first}",
        )
        check(
            "新窗口的值来自**配置**（不是上一次窗口的残留）",
            second._find_slider(second._widgets["volume"]).value() == store.get("volume"),
            f"滑块={second._find_slider(second._widgets['volume']).value()} "
            f"配置={store.get('volume')}",
        )

        second.accept()  # 「关闭」按钮的效果就是 accept()
        check("点「关闭」之后引用同样被释放", controller.dialog is None,
              f"dialog={controller.dialog!r}")
        controller.open()
        check("点「关闭」之后也能再次打开", controller.dialog is not None)
        # 退出收尾：`close()` 把窗口关掉，而 `finished` 会顺手清掉引用
        # （`WA_DeleteOnClose` 下那个对象马上就会被析构，留着引用是错的）。
        still_open = controller.dialog
        controller.close()
        check(
            "退出收尾会把设置窗口收掉（模态窗口不关会让 app.exec() 不返回）",
            still_open is not None and not still_open.isVisible(),
            f"visible={still_open.isVisible() if still_open is not None else None}",
        )
        check(
            "收掉之后引用被清空，不会留一个已析构的对象",
            controller.dialog is None,
            f"dialog={controller.dialog!r}",
        )

        # -------------------------------------------------------- §12 居中与最小尺寸
        section("12. 居中（水平居中、垂直约 1/3）+ 拉小仍点得到按钮")

        store = ConfigStore(scratch / "cfg6.json")
        store.load()
        window = st.SettingsWindow(store, current_pet="weedy")
        window.center_on_screen()
        screen = window.screen()
        area = screen.availableGeometry()
        expected_left = area.x() + (area.width() - window.width()) // 2
        check(
            "水平居中",
            abs(window.x() - expected_left) <= 1,
            f"实测 x={window.x()} 期望 {expected_left}（屏幕宽 {area.width()}）",
        )
        # 垂直：窗口**中心**落在屏幕高度的 1/3 处。
        center = window.y() + window.height() // 2
        expected_center = area.y() + int(area.height() * 1.0 / 3.0)
        check(
            "垂直约 1/3 处（窗口中心在屏幕高度的 1/3）",
            abs(center - expected_center) <= 2,
            f"实测中心 y={center} 期望 {expected_center}（屏幕高 {area.height()}）",
        )
        check(
            "1/3 的取值是常量 VERTICAL_RATIO，不是手写 0.33",
            abs(st.VERTICAL_RATIO - 1.0 / 3.0) < 1e-9,
            f"VERTICAL_RATIO={st.VERTICAL_RATIO}",
        )

        # 手动把窗口拉小到内容放不下：下限由 setMinimumSize 顶住。
        window.resize(200, 120)
        minimum = window.minimumSize()
        check(
            "setMinimumSize 顶住了手动拉小（放不下也不会截断到点不到按钮）",
            window.width() >= minimum.width() and window.height() >= minimum.height(),
            f"拉小后 {window.width()}×{window.height()}，下限 "
            f"{minimum.width()}×{minimum.height()}",
        )
        check(
            "内容区在 QScrollArea 里（真放不下时出滚动条，而不是把按钮挤出去）",
            len(window.findChildren(QScrollArea)) >= 1,
            "五行装在滚动区，状态栏与按钮行在滚动区之外",
        )
        window.close()
        window.deleteLater()

        # -------------------------------------------------------- §13 快捷键骨架
        section("13. 快捷键录入控件（骨架：展示顺序 / 禁用 / 不写配置）")

        from deskpet import keycapture

        check(
            "展示顺序固定 Ctrl → Alt → Shift → Win",
            keycapture.MODIFIER_ORDER == ("Ctrl", "Alt", "Shift", "Win"),
            "、".join(keycapture.MODIFIER_ORDER),
        )
        check(
            "乱序输入也按固定顺序展示",
            keycapture.modifiers_text(["Win", "Ctrl", "Alt"]) == "Ctrl+Alt+Win",
            keycapture.modifiers_text(["Win", "Ctrl", "Alt"]),
        )

        edit = keycapture.KeyCaptureEdit()
        edit.set_read_only(True)
        edit.begin_capture()
        check(
            "禁用时进不了录入态",
            edit.listening is False,
            f"listening={edit.listening}",
        )
        edit.set_read_only(False)
        edit.set_shortcut_text("Ctrl+Alt+Shift+P")
        check("能显示当前快捷键", edit.display_text == "Ctrl+Alt+Shift+P", edit.display_text)

        captured: list[str] = []
        rejected: list[str] = []
        cancelled: list[int] = []
        edit.captured.connect(captured.append)
        edit.rejected.connect(rejected.append)
        edit.cancelled.connect(lambda: cancelled.append(1))

        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        def _press(key: int, modifiers=Qt.NoModifier) -> None:
            edit.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, modifiers))

        edit.begin_capture()
        _press(Qt.Key_Control, Qt.ControlModifier)
        check(
            "修饰键**单独按下不算**一次录入，只实时显示",
            captured == [] and edit.listening and "…" in edit.display_text,
            repr(edit.display_text),
        )
        _press(Qt.Key_P, Qt.ControlModifier)
        check(
            "再按主键 → 录成 `Ctrl+P`",
            captured == ["Ctrl+P"],
            f"captured={captured}",
        )

        edit.begin_capture()
        _press(Qt.Key_A, Qt.NoModifier)
        check(
            "**至少一个修饰键**：单独按 A 被拒且保留原值",
            captured == ["Ctrl+P"] and len(rejected) == 1,
            f"rejected={rejected}",
        )
        check(
            "拒绝时说的是那句话（附录 B）",
            rejected and rejected[0] == "快捷键必须包含 Ctrl / Alt / Shift / Win 之一",
            rejected[0] if rejected else "（没有拒绝）",
        )
        _press(Qt.Key_Escape, Qt.NoModifier)
        check(
            "Esc 取消录入（不是把 Esc 录进去）",
            cancelled == [1] and edit.display_text == "Ctrl+P",
            f"cancelled={cancelled} 显示={edit.display_text!r}",
        )
        check(
            "录入控件**不碰配置**（写配置只有设置窗口那一条路径）",
            "config" not in _code_only("keycapture.py").lower(),
            "keycapture.py 里没有 ConfigStore",
        )

        return _finish()


def _write_fake_pet(root: Path, display_name: str, *, broken: bool) -> None:
    """造一个自检用的素材包（`broken=True` 时索引故意写坏）。

    **只写在自检自己的 scratch 里**：`assets/` 一个字节都不动（§4.11）。
    """
    frames = root / "frames"
    frames.mkdir(parents=True, exist_ok=True)
    index = root / "asset.json"
    if broken:
        index.write_text("{ 这不是 JSON", encoding="utf-8")
        return

    _write_png(frames / "idle_00.png", 8, 8)
    index.write_text(
        "{\n"
        f'  "name": "{display_name}",\n'
        '  "cell": [8, 8],\n'
        '  "actions": {"idle": {"frames": ["idle_00.png"], "fps": 12.0}}\n'
        "}\n",
        encoding="utf-8",
    )


def _write_png(path: Path, width: int, height: int) -> None:
    """写一张最小的**带 alpha** 的 PNG（自检用；不引入任何图像库）。"""
    import struct
    import zlib

    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * width for _ in range(height))

    def _chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw))
        + _chunk(b"IEND", b"")
    )
    path.write_bytes(data)



if __name__ == "__main__":
    raise SystemExit(main())