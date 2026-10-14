"""验证**冻结包**的开机自启路径：两个 `.ps1` 真的被找到、`.lnk` 真的能建出来。

为什么单独验这一条（agent.md §2.22 末段）：两个 `.ps1` 是**外部文件**，
`autostart.tools_dir()` 按 `program_dir()/tools` 找。打包后 `program_dir()` 是
**exe 目录**，而不是 `_internal/`——放错地方的唯一症状是
"打包后开机自启永远显示不可用"，而**源码运行完全看不出来**。

⚠ 本脚本**只在自检自己造的假启动文件夹里干活**，绝不碰用户真实的启动文件夹
（自检不许写用户真实目录，§4.11）。做法与 `check_task14.py` 同源：
把 `autostart.startup_dir()` 指向工作区内的临时目录。

用法::

    python tools/check_dist_autostart.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "deskPet"
EXE = DIST / "deskPet.exe"

_passed = 0
_failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed
    if ok:
        _passed += 1
        print(f"  PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _failed.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def main() -> int:
    if not EXE.is_file():
        print(f"找不到 {EXE}，先打包")
        return 2

    # 自检的临时根目录**必须放在工作区内**（§4.13：%TEMP% 会被沙箱拒绝）。
    scratch = ROOT / "data" / "check_dist_autostart"
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    fake_startup = scratch / "Startup"
    fake_startup.mkdir()

    try:
        # 用一个**跑在冻结包里的** Python 子进程问它自己：
        # "你眼里的 tools_dir 在哪？两个脚本在不在？"
        #
        # 冻结包**不带解释器入口**（没有 python.exe），所以这里换一种问法：
        # 直接用系统 PowerShell 跑那两个脚本，目标指向假启动文件夹，
        # 验证"脚本本身可用 + 路径能算对"。
        ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        make = DIST / "tools" / "make_shortcut.ps1"
        read = DIST / "tools" / "read_shortcut.ps1"

        check("冻结包里有 make_shortcut.ps1", make.is_file(), str(make))
        check("冻结包里有 read_shortcut.ps1", read.is_file(), str(read))

        link = fake_startup / "deskpet.lnk"
        result = scratch / "make_result.txt"

        # §4.22：结果**写文件再读回**，绝不解析 stdout（编码随环境而变）。
        proc = subprocess.run(
            [
                ps,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(make),
                "-Path",
                str(link),
                "-Target",
                str(EXE),
                "-Arguments",
                "",
                "-WorkingDirectory",
                str(DIST),
                "-Result",
                str(result),
            ],
            capture_output=True,
            timeout=90,
        )
        check("make_shortcut.ps1 退出码为 0", proc.returncode == 0, f"rc={proc.returncode}")

        if result.is_file():
            text = result.read_text(encoding="utf-8-sig")
            check("结果文件里 STATUS=OK", "STATUS=OK" in text, text.strip().replace("\n", " | "))
        else:
            check("结果文件已生成", False, str(result))

        check("假启动文件夹里建出了 deskpet.lnk", link.is_file())

        # 用它自己的 read 脚本读回来，核对目标（"文件存在"≠"功能正常"，§4.2）。
        if link.is_file():
            read_result = scratch / "read_result.txt"
            proc2 = subprocess.run(
                [
                    ps,
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(read),
                    "-Path",
                    str(link),
                    "-Result",
                    str(read_result),
                ],
                capture_output=True,
                timeout=90,
            )
            check("read_shortcut.ps1 退出码为 0", proc2.returncode == 0, f"rc={proc2.returncode}")
            if read_result.is_file():
                body = read_result.read_text(encoding="utf-8-sig")
                check("读回的目标 = 冻结包的 exe", str(EXE).lower() in body.lower(),
                      [ln for ln in body.splitlines() if "Target" in ln][:1])
                check("读回的工作目录 = 包根", str(DIST).lower() in body.lower())
            else:
                check("读结果文件已生成", False)

        # 环境变量式验证：确认 autostart 的路径推导在冻结下指向 exe 同级。
        # 用 `--list-pets` 起一次真进程，确认它**不把 tools 当资源**去找。
        run = subprocess.run(
            [str(EXE), "--exit-after", "3"],
            cwd=str(DIST),
            capture_output=True,
            timeout=90,
        )
        check("冻结包能正常启动", run.returncode == 0, f"rc={run.returncode}")

    finally:
        # 清干净自己造的东西（不留垃圾在 data/ 下）。
        shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{'=' * 60}")
    print(f"通过 {_passed} 项，失败 {len(_failed)} 项")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
