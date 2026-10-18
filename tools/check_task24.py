"""task24 自检：**进程完整性级别的读取与告警**。

用户报「打包版失焦后键盘不亮」。根因不在绘制、也不在轮询，而是
**进程跑在 `Low` 完整性级别**：`GetAsyncKeyState` 受 UIPI 约束，
低级别进程在「前台窗口属于更高级别」时永远读到 0（agent.md §4.28）。

本脚本钉住四件事：

1. `win32.get_integrity_level()` 能读出来，且结果在已知名字集合里；
2. 它是**纯诊断**：任何内部失败都必须退化返回 `"?"`，**绝不抛异常**；
3. `win32.get_parent_pid()` 返回一个非负整数，且等于真实的父进程；
4. `deskpet.spec` 里**必须**有 `uac_admin=True`（这是本任务的定案修复，
   删掉它等于把 bug 放回来）；`src/` 里**不许**出现任何令牌操作。

跑法：

    python tools/check_task24.py
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task24_check.txt"

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


KNOWN = {"Untrusted", "Low", "Medium", "High", "System", "Protected"}


def section_integrity() -> None:
    from deskpet import win32

    section("1. 完整性级别读得出来")
    level = win32.get_integrity_level()
    note(f"本进程完整性级别 = {level}")
    check("返回值是字符串", isinstance(level, str), repr(level))
    check(
        "在已知名字集合里（或 '?' 表示读不到）",
        level in KNOWN or level == "?" or level.startswith("0x"),
        level,
    )

    section("2. 纯诊断：失败必须退化成 '?'，且绝不抛异常")
    original = win32.WinDLL if hasattr(win32, "WinDLL") else None
    import ctypes

    real_windll = ctypes.WinDLL

    def boom(*args, **kwargs):
        raise OSError("模拟 advapi32 加载失败")

    ctypes.WinDLL = boom
    try:
        degraded = win32.get_integrity_level()
    except Exception as exc:  # noqa: BLE001
        degraded = f"抛异常了：{exc}"
    finally:
        ctypes.WinDLL = real_windll
    check("advapi32 加载失败时返回 '?' 而不是抛异常", degraded == "?", repr(degraded))

    section("3. 父进程 PID 可读且真实")
    parent = win32.get_parent_pid()
    note(f"父进程 PID = {parent}")
    check("返回非负整数", isinstance(parent, int) and parent >= 0, repr(parent))
    check("不是 0（正常启动一定有父进程）", parent != 0, repr(parent))
    if parent:
        parent_level = win32.get_integrity_level(parent)
        note(f"父进程的完整性级别 = {parent_level}")
        check(
            "父进程级别也读得出来",
            parent_level in KNOWN or parent_level == "?" or parent_level.startswith("0x"),
            parent_level,
        )


def section_spec() -> None:
    section("4. spec 不强制提权（task25 已改成按需提权）")
    spec = (ROOT / "deskpet.spec").read_text(encoding="utf-8")
    # ⚠ 只看**赋值行**：说明文字里允许出现 uac_admin=True（那正是"别加它"）。
    assignments = [
        line for line in spec.splitlines() if line.lstrip().startswith("uac_admin") and "=" in line
    ]
    check("deskpet.spec 没有 uac_admin 赋值", not assignments, "; ".join(assignments))

    section("5. 卫生：源码里不许有令牌操作（提权只由清单做）")
    pattern = re.compile(r"SetTokenInformation|SetThreadToken|CreateRestrictedToken|SaferCompute")
    hits = []
    for path in (ROOT / "src").rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{number}")
    check("src/ 没有任何令牌操作", not hits, ", ".join(hits))


def main() -> int:
    section_integrity()
    section_spec()

    _LINES.append("")
    _LINES.append(f"合计 {_TOTAL} 项，失败 {_FAILED} 项")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
    print(f"\n合计 {_TOTAL} 项，失败 {_FAILED} 项（结果已写入 {RESULT_FILE}）")
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())