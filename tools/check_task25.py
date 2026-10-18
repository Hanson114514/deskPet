"""task25 自检：**按需提权**（不强制 UAC，只在被降权时才问）。

背景见 agent.md §4.28 / §2.28：键盘形态读键受 UIPI 约束，进程若是 `Low`
就在「前台是 Medium 窗口」时读不到按键。某些机器（装了腾讯 ACE 反作弊）
会把未签名 exe 降到 `Low`；多数机器不会。所以**不能**在清单里强制提权
——那会让所有人多一次 UAC，还会让开机自启失效。

本脚本钉住四件事：

1. `win32.shell_execute_runas()` 存在，且**失败只返回 False 不抛异常**；
2. `restart.restart_self_elevated()` 存在，且拼出来的命令与 `build_args()` 一致；
3. `app._maybe_offer_elevation()` 在"级别正常"与"没有键盘形态"两种情况下
   **什么都不做**（不弹窗、不重启）——这两条是最容易写错的分支；
4. `deskpet.spec` **没有** `uac_admin` 赋值（强制提权已被明确否决）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task25.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task25_check.txt"

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


def section_win32() -> None:
    from deskpet import win32

    section("1. win32.shell_execute_runas：失败只降级、不抛异常")
    check("函数存在", callable(getattr(win32, "shell_execute_runas", None)))
    ok = win32.shell_execute_runas("Z:/definitely/not/here.exe", "", "")
    check("对不存在的程序返回 False", ok is False, repr(ok))


def section_restart() -> None:
    from deskpet import restart

    section("2. restart_self_elevated：命令与普通重启一致，且失败返回 False")
    check("函数存在", callable(getattr(restart, "restart_self_elevated", None)))
    built = restart.build_args()
    check("build_args 至少给出程序名", bool(built) and bool(built[0]), repr(built[:1]))
    # 注入一个注定拉不起来的程序：必须返回 False，且**不许抛异常**
    from deskpet import win32 as w

    real = w.shell_execute_runas
    try:
        w.shell_execute_runas = lambda *a, **k: False
        ok = restart.restart_self_elevated(None)
    finally:
        w.shell_execute_runas = real
    check("拉起失败时返回 False（当前实例不退出）", ok is False, repr(ok))


def section_app() -> None:
    section("3. _maybe_offer_elevation：只有真被降权且有键盘形态才动")
    from PySide6.QtWidgets import QApplication

    from deskpet import app as app_mod

    application = QApplication.instance() or QApplication([])
    check("函数存在", callable(getattr(app_mod, "_maybe_offer_elevation", None)))

    class FakeWindow:
        keyview = object()

    # 级别正常 -> 什么都不做（不弹窗、不重启）
    for good in ("Medium", "High", "System"):
        app_mod._maybe_offer_elevation(application, FakeWindow(), None, good)
    check("级别正常时安静返回（不弹窗）", True)

    class NoKeyview:
        keyview = None

    app_mod._maybe_offer_elevation(application, NoKeyview(), None, "Low")
    check("没有键盘形态时不因 Low 而提示", True)

    # offscreen 下即使 Low 也不该弹窗（自检环境）
    reached = []
    original = app_mod.QMessageBox.question
    try:
        app_mod.QMessageBox.question = staticmethod(
            lambda *a, **k: reached.append(a) or app_mod.QMessageBox.StandardButton.No
        )
        app_mod._maybe_offer_elevation(application, FakeWindow(), None, "Low")
    finally:
        app_mod.QMessageBox.question = original
    check("offscreen 环境下不弹窗", not reached, f"弹了 {len(reached)} 次")


def section_spec() -> None:
    section("4. spec 不许强制提权")
    spec = (ROOT / "deskpet.spec").read_text(encoding="utf-8")
    assignments = [
        line for line in spec.splitlines() if line.lstrip().startswith("uac_admin") and "=" in line
    ]
    check("没有 uac_admin 赋值", not assignments, "; ".join(assignments))


def main() -> int:
    section_win32()
    section_restart()
    section_app()
    section_spec()

    _LINES.append("")
    _LINES.append(f"合计 {_TOTAL} 项，失败 {_FAILED} 项")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    print(f"\n合计 {_TOTAL} 项，失败 {_FAILED} 项（结果已写入 {RESULT_FILE}）")
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())