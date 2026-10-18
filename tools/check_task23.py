"""task23 自检（offscreen）：**设置窗口里每一行都必须真的画得出来**。

用户报：「隐藏呼出快捷键的设置按键消失了」。

根因：`keycapture.KeyCaptureEdit` 是个 `QWidget`，但它**没有布局**、
按钮只是它的子控件 —— 于是 `sizeHint()` 恒为 `(-1, -1)`，在表单行里按
**高度 0** 参与布局。task21 UX-12 把表单策略从 `WrapLongRows` 换成
`WrapAllRows` 之后，这一行就被压成了一条线，看起来像控件凭空消失。

本脚本钉住两件事：

1. 每个 `FIELDS` 行的控件**都存在、可见、且高度 > 0**（不是被压扁）；
2. `KeyCaptureEdit` 的 `sizeHint()/minimumSizeHint()` 高度都 > 0，
   而且它在任何表单行策略下都不会退化成 0 高。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task23.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task23_check.txt"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
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


def _make_window(app):
    from deskpet import config as config_mod
    from deskpet import settings

    cfg_dir = ROOT / "data" / "task23_out"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "config.json"
    path.write_text("{}", encoding="utf-8")
    store = config_mod.ConfigStore(path)
    store.load()
    window = settings.SettingsWindow(store, "", None, None)
    window.show()
    app.processEvents()
    window.adjustSize()
    app.processEvents()
    return window


def section_every_row_has_height(app) -> None:
    section("1. FIELDS 的每一行都画得出来（存在 / 可见 / 高度 > 0）")

    from deskpet import settings

    window = _make_window(app)
    missing = []
    collapsed = []
    printed = []
    for field in settings.FIELDS:
        widget = window._widgets.get(field.key, window._widgets.get(field.key or ""))
        if field.key == "":
            continue
        if widget is None:
            missing.append(field.title)
            continue
        printed.append(f"{field.title}={widget.height()}px")
        if not widget.isVisible() or widget.height() <= 0:
            collapsed.append((field.title, widget.height(), widget.isVisible()))
    check("每个字段都有控件", not missing, f"缺控件的：{missing}")
    check(
        "每个字段都可见且高度 > 0（没有被压成一条线）",
        not collapsed,
        f"被压扁的：(标题, 高度, 可见) = {collapsed}",
    )
    note("各行实际高度：" + "、".join(printed))
    window.close()


def section_hotkey_row(app) -> None:
    section("2. 呼出快捷键这一行（用户报的那一行）")

    window = _make_window(app)
    widget = window._widgets.get("hotkey")
    check("呼出快捷键控件存在", widget is not None, type(widget).__name__ if widget else "无")
    if widget is None:
        window.close()
        return
    check(
        "呼出快捷键控件的高度 > 0",
        widget.height() > 0,
        f"实测 {widget.height()} px（0 就是「消失了」）",
    )
    check(
        "呼出快捷键控件可见",
        widget.isVisible(),
        f"visible={widget.isVisible()}",
    )
    check(
        "它的 sizeHint 高度 > 0（布局按它排）",
        widget.sizeHint().height() > 0,
        f"sizeHint={widget.sizeHint()}",
    )
    check(
        "它的 minimumSizeHint 高度 > 0（不会被压成 0）",
        widget.minimumSizeHint().height() > 0,
        f"minimumSizeHint={widget.minimumSizeHint()}",
    )
    check(
        "按钮上有文字（能看出当前快捷键）",
        bool(widget.display_text),
        f"display_text={widget.display_text!r}",
    )
    window.close()


def section_widget_contract(app) -> None:
    section("3. KeyCaptureEdit 的尺寸契约（根因就在这儿）")

    from deskpet import keycapture

    edit = keycapture.KeyCaptureEdit()
    edit.show()
    app.processEvents()
    check(
        "KeyCaptureEdit 有布局（否则 sizeHint 恒为 -1,-1）",
        edit.layout() is not None,
        f"layout={type(edit.layout()).__name__ if edit.layout() else None}",
    )
    check(
        "独立显示时高度 > 0",
        edit.height() > 0,
        f"{edit.height()} px",
    )
    edit.close()

    # 换几种行策略：高度都必须 > 0（task21 换成 WrapAllRows 就是这个坑）。
    from PySide6.QtWidgets import QFormLayout, QWidget

    results = {}
    for name, policy in (
        ("WrapLongRows", QFormLayout.WrapLongRows),
        ("WrapAllRows", QFormLayout.WrapAllRows),
        ("DontWrapRows", QFormLayout.DontWrapRows),
    ):
        host = QWidget()
        form = QFormLayout(host)
        form.setRowWrapPolicy(policy)
        probe = keycapture.KeyCaptureEdit()
        form.addRow("呼出快捷键", probe)
        host.show()
        app.processEvents()
        host.adjustSize()
        app.processEvents()
        results[name] = probe.height()
        host.close()
    check(
        "三种行策略下高度都 > 0",
        all(h > 0 for h in results.values()),
        f"{results}",
    )


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        _LINES.append("task23 自检 —— 设置窗口每一行都要真的画得出来")
        _LINES.append(f"工作区根：{ROOT}")

        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])

        section_every_row_has_height(app)
        section_hotkey_row(app)
        section_widget_contract(app)

    section("结论")
    if _FAILED:
        note(f"失败 {_FAILED} 项 / 共 {_TOTAL} 项")
    else:
        note(f"全过（{_TOTAL} 项断言）")

    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    print(f"\n结果已写入：{RESULT_FILE}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
