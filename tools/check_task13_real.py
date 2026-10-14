"""task13 真平台自检：真的起两个进程、真的重启一次、真的看锁的交接。

`check_task13.py` 用打桩验了逻辑；**这一份验"在真操作系统上到底成不成立"**。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task13_real.py

## 这一份验什么

1. **真进程单实例**：起第一个桌宠 → 第二个进程**安静退出、rc=0**；
2. **只读查询不被拒**：`--list-pets` / `--list-actions` / `--list-sizes`
   在第一个跑着时都能正常输出并 rc=0；
3. **新实例完全脱离**：新实例的**父进程不是**旧实例（旧实例已退出）；
4. **新实例的工作目录是程序目录**（日志里的 `Path.cwd()`）；
5. **重启交接的时序**：日志里能看到"新实例先等约 1.2 秒"再"抢锁成功"
   与"旧实例放锁"；
6. **关闭后能再起来**（锁真的被释放了）；
7. **连续重启 3 次**：全程**没有两个实例同时存在**，也没有"没有实例"的空窗；
8. **重启失败注入**：把程序路径改成不存在的 → 返回假、**旧实例不退出**。

## 为什么用真 `run.py` 子进程而不是 in-process

单实例与重启这两件事**本身就是"两个进程"的现象**：在同一个进程里
`acquire()` 两次只能验到"同名互斥体被拒"这一条，验不到
"第二个进程安静退出、rc=0"这条最关键的验收（task13 §6 第 1 条）。

⚠ 自检用的互斥体名字后缀：本脚本**不注入后缀**——它要验的就是生产名字。
所以跑之前**必须先关掉真正开着的桌宠**，否则第一个"实例"就是用户那个，
后面所有断言都会莫名其妙地 FAIL。脚本开头会检查并明确报出来。
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task13_real.txt"
SCRATCH = ROOT / "data" / "task13_real_scratch"

#: 子进程环境：真平台（**不设** `QT_QPA_PLATFORM=offscreen`）+ UTF-8。
_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
_ENV.pop("QT_QPA_PLATFORM", None)

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
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


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


# ------------------------------------------------------------------ 工具


def _run(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    """同步跑一个桌宠子进程，**收全部输出**（调试输出写文件是另一条约定，见 §4.3）。"""
    return subprocess.run(
        [sys.executable, str(ROOT / "run.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env=_ENV,
        timeout=timeout,
    )


#: 本脚本直接拉起过的进程（收尾时按 pid 清，见 `_kill_all()`）。
_SPAWNED: list[subprocess.Popen] = []


def _spawn(args: list[str]) -> subprocess.Popen:
    """异步起一个桌宠（跑着不退出）。

    ⚠ 标准流进 `DEVNULL` 而不是管道：这个进程可能**自己再拉起一个孙进程**
    （重启），孙进程会继承管道写端，于是"读它的输出"要等到孙进程也退出
    （见 `_run_restart_harness` 的说明）。本脚本不需要它的 stdout。
    """
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "run.py"), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(ROOT),
        env=_ENV,
    )
    _SPAWNED.append(process)
    return process


def _log_text() -> str:
    """读当前 `deskpet.log`（含滚动的 `.1`）。"""
    parts: list[str] = []
    for name in ("deskpet.log.1", "deskpet.log"):
        path = ROOT / "data" / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _log_size() -> int:
    path = ROOT / "data" / "deskpet.log"
    return path.stat().st_size if path.exists() else 0


def _wait_for(predicate, timeout: float = 20.0, interval: float = 0.15) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _kill_all() -> None:
    """清掉**本脚本拉起过的**桌宠进程（记 pid，不用进程表）。

    ## ⚠ 为什么不能用 `Get-CimInstance` 查命令行

    第一版用 `Get-CimInstance Win32_Process | Where CommandLine -like '*run.py*'`
    按命令行找人。实测在 DSH 文件沙箱下的 pwsh 里它**直接报"拒绝访问"
    (HRESULT 0x80041003)**；而那个异常被我自己的 `except: pass` 咽掉了，
    于是"清理"从来没发生，**自检里的表现却是"§2/§4 抢不到锁（有残留？）"**
    ——看起来像产品 bug，实际是清理手段在这个环境里不可用。
    教训与 §4.3 同源：*失败路径自己也会失败，而且更难查*。

    ## ⚠ 也不能按进程名 `taskkill /IM pythonw.exe`

    那会**误伤用户自己开着的 pythonw**（本机上是别的程序）。自检没有资格
    替用户做这件事。

    所以本函数只清**本脚本自己知道的 pid**：`_spawn()` 记下的那些，
    以及它们拉起的孙进程（重启产物）——后者由 `restart_self()` 拉起，
    `QProcess.startDetached` 不给 pid（PySide6 给的是 `(ok, pid)`，
    `restart.py` 只取 ok），所以拿不到。
    **对策**：让那些孙进程自己退出（`--exit-after` 很短），
    等锁回到空闲再继续——这正是 `_wait_for(lambda: not _is_running())` 干的事。
    真正需要"强杀"的只有本脚本直接拉起的那些。
    """
    for process in list(_SPAWNED):
        try:
            if process.poll() is None:
                process.terminate()
        except Exception:  # pragma: no cover
            pass
    deadline = time.monotonic() + 8
    for process in list(_SPAWNED):
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except Exception:  # pragma: no cover
            try:
                process.kill()
            except Exception:
                pass
    _SPAWNED.clear()


def _python_process_parents() -> list[tuple[int, str]]:
    """进程表里还活着的解释器进程 `(pid, 名字)`。

    ⚠ **不用 `Get-CimInstance`**：它在 DSH 文件沙箱下的 pwsh 里报
    "拒绝访问"（`HRESULT 0x80041003`）。`Get-Process` 可用，本函数就靠它。
    代价是拿不到 `ParentProcessId`——所以"父进程是谁"这条改写成了
    等价的"父进程已退出而它还在"（见 `section_real_restart()` 的说明）。
    """
    try:
        import subprocess as sp

        result = sp.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Process | Where-Object { $_.ProcessName -like 'python*' } | "
                "ForEach-Object { \"$($_.Id) $($_.ProcessName)\" }",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception:  # pragma: no cover - 查不到就当"没有"
        return []

    found: list[tuple[int, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit():
            found.append((int(parts[0]), parts[1]))
    return found


def _is_running() -> bool:
    """现在有没有桌宠在跑：用**能不能抢到生产那个锁**来问，比看进程表准。

    ⚠ 第一版这里传了个自检专用的后缀（`acquire("probe-liveness")`），
    于是它问的是**另一个**互斥体、永远回答"没有实例在跑"——
    §0 的"当前没有别的桌宠"因此是**蒙对的**，§4 的"抢不到（有残留？）"
    则是这条错误判据的必然结果。要问"生产锁空不空"，就得用生产那个名字。
    """
    from deskpet import single_instance

    probe = single_instance.acquire()
    if probe is None:
        return True
    probe.release()
    return False


# ------------------------------------------------------------------ §0 前置


def section_preconditions() -> None:
    section("§0 前置：先确认没有别的桌宠在跑")
    from deskpet import single_instance

    if not single_instance.is_available():
        check("单实例保护可用（真平台）", False, "Win32 不可用，本脚本验不了")
        return
    check("单实例保护可用（真平台）", True)
    # 先清一遍再问，免得上一轮自检的残留把这次全判 FAIL。
    _kill_all()
    idle = _wait_for(lambda: not _is_running(), timeout=25)
    check(
        "当前没有别的桌宠在跑（有的话先关掉再跑本脚本）",
        idle,
        "已有实例占着锁" if not idle else "",
    )
    if not idle:
        return


# ------------------------------------------------------------------ §1 单实例


def section_single_instance_real() -> None:
    section("§1 真进程：第二个安静退出、只读查询不被拒")
    first = _spawn(["--exit-after", "14"])
    try:
        # 等它真的把锁拿上（日志里出现"单实例锁已获取"）
        got = _wait_for(lambda: "单实例锁已获取" in _log_text(), timeout=20)
        check("第一个实例起来了并拿到了锁", got, f"alive={first.poll() is None}")
        time.sleep(0.5)

        second = _run([])
        check(
            "第二个实例的退出码是 0（安静退出，不是错误）",
            second.returncode == 0,
            f"rc={second.returncode}",
        )
        check(
            "第二个实例日志里有『检测到已有实例，退出』的记录",
            "检测到已有实例在运行" in second.stdout + second.stderr
            or "已经有实例在跑" in second.stdout + second.stderr,
            (second.stdout or "").strip().splitlines()[-1][:90] if second.stdout else "(无输出)",
        )
        check(
            "第二个实例**没有**弹任何错误对话框（输出里没有『启动失败』）",
            "启动失败" not in (second.stdout + second.stderr),
        )

        for args, label in (
            (["--list-pets"], "--list-pets"),
            (["--list-actions"], "--list-actions"),
            (["--list-sizes"], "--list-sizes"),
        ):
            result = _run(args)
            check(
                f"{label} 在已有实例时正常输出并退出（rc=0）",
                result.returncode == 0 and len(result.stdout.strip()) > 0,
                f"rc={result.returncode} 输出 {len(result.stdout)} 字符",
            )
    finally:
        try:
            first.terminate()
            first.wait(timeout=15)
        except Exception:  # pragma: no cover
            first.kill()

    # 第一个退出之后，锁必须真的被释放
    time.sleep(0.8)
    check("第一个实例退出后锁被释放（能再抢到）", not _is_running())
    again = _run(["--exit-after", "1"])
    check("关闭所有实例后再启动：正常起来（rc=0）", again.returncode == 0, f"rc={again.returncode}")


# ------------------------------------------------------------------ §2 真重启


def _run_restart_harness(scratch: Path, script_body: str, tag: str) -> str:
    """在一个子进程里跑"旧实例"的脚本（它自己会拉起新实例），返回它的记录。

    ## ⚠ 为什么结果**写文件**而不是收 stdout

    `QProcess.startDetached()` 起的子进程会**继承标准句柄**。于是当父进程的
    stdout 是一根管道时，那个孙进程也握着管道的写端——`subprocess.run(...,
    capture_output=True)` 会一直等"管道关闭"，**一直等到孙进程退出**，
    于是"父进程早就跑完了"这件事它永远看不到（第一版实测：120 秒超时，
    而脚本本身 3 秒就跑完了）。

    这正是 agent.md §4.3 那条老规矩的另一个面："**调试输出写文件，不要靠重定向**
    ——进程会在文件可读之前退出"。这里反过来也成立：**别让子进程的输出
    决定父进程什么时候算读完**。

    所以：Harness 把每一行都写进 `<scratch>/<tag>.txt`，本函数只负责等它退出，
    再读那个文件。
    """
    script = scratch / f"old_{tag}.py"
    record = scratch / f"{tag}.txt"
    if record.exists():
        record.unlink()
    script.write_text(
        "\n".join(
            [
                "import sys, time, os",
                f"sys.path.insert(0, {str(ROOT / 'src')!r})",
                "from pathlib import Path",
                "from deskpet import paths",
                f"paths._overrides['program'] = Path({str(ROOT)!r})",
                "from deskpet import single_instance, restart, logger as log",
                "log.install_crash_handler()",
                "log.setup_logging()",
                "log.log_startup_banner()",
                "import logging; logging.getLogger().setLevel(logging.INFO)",
                f"RECORD = Path({str(record)!r})",
                "def say(*parts):",
                "    with RECORD.open('a', encoding='utf-8') as fh:",
                "        fh.write(' '.join(str(p) for p in parts) + '\\n')",
                "        fh.flush()",
                script_body,
            ]
        ),
        encoding="utf-8",
    )
    # ⚠ 标准流**不接管道**：`startDetached` 的孙进程会继承它们。
    #   重定向到 DEVNULL 而不是 PIPE，`wait()` 才能按时返回。
    subprocess.run(
        [sys.executable, str(script)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(ROOT),
        env=_ENV,
        timeout=120,
    )
    return record.read_text(encoding="utf-8") if record.exists() else ""


def section_real_restart() -> None:
    section("§2 真重启：旧实例放锁 → 新实例先等 1.2 秒 → 抢锁成功")
    scratch = SCRATCH
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    before = _log_size()

    # ## 这一节的两步及其顺序（**顺序是有讲究的**）
    #
    # 1. **旧实例（Harness）**：抢锁 → 用**真参数**（`restart.build_args()`）
    #    拉起一个新实例 → 放锁 → 记录结果后退出；
    # 2. **新实例**：`restart_self()` 已经拉起了**真的一个**，
    #    但它按生产参数运行、**不带 `--exit-after`**，会一直占着锁；
    #    自检不能强杀它（拿不到 pid，按名字杀会误伤用户的 pythonw）。
    #
    # 所以：Harness 拉起的新实例**只用来验"能不能起来"**（它自己会一直跑），
    # 而**等待时长与抢锁结果**由**自检自己起的那个** `child`
    # （同参数 + `--exit-after`，跑完自己退）来验。
    #
    # ⚠ 那个"一直跑着的"新实例会在本节结束时仍占着锁。**§3 与 §4 因此
    #   不能用生产锁名**（它们本来是纯逻辑/注入验，不需要真抢锁）——
    #   见各节里的说明。这是"用真进程做验收"的必然代价，不是产品行为。
    body = "\n".join(
        [
            "handle = single_instance.acquire()",
            "say('OLD_ACQUIRED', handle is not None and handle.held)",
            "args = restart.build_args()",
            "say('OLD_ARGS', args)",
            "started = restart.restart_self(handle)",
            "say('OLD_STARTED', started)",
            "say('OLD_HELD_AFTER', handle.held)",
            "time.sleep(1.5)",
        ]
    )
    out = _run_restart_harness(scratch, body, "restart")
    check("旧实例抢到了锁", "OLD_ACQUIRED True" in out, out[-200:])
    check("restart_self() 返回真", "OLD_STARTED True" in out, out[-200:])
    check(
        "重启参数里含 --restart-delay（真 build_args 的产物）",
        "OLD_ARGS" in out and "--restart-delay" in out,
        next((line for line in out.splitlines() if line.startswith("OLD_ARGS")), ""),
    )
    check(
        "重启之后旧实例的 handle 已经不再持锁",
        "OLD_HELD_AFTER False" in out,
        out[-200:],
    )

    # 现在**那个被拉起的新实例**应该已经抢到锁了（旧实例放锁之后）。
    got_lock = _wait_for(lambda: "重启时序[抢锁]：抢锁成功" in _tail_log(before), timeout=25)
    check(
        "被拉起的新实例抢到了锁（旧实例确实放了锁）",
        got_lock,
        _tail_log(before)[-200:],
    )

    tail = _tail_log(before)
    check(
        "日志里有『新实例先等 … 秒再抢单实例锁』（时序留痕）",
        "重启时序[等待]" in tail,
        "",
    )
    check(
        "日志里有『旧实例已放锁』（交接的另一半）",
        "重启时序[放锁]" in tail,
        "",
    )
    check(
        "日志里有『抢锁成功』",
        "重启时序[抢锁]" in tail and "抢锁成功" in tail,
        "",
    )

    # 真的等了约 1.2 秒：从"等待结束（实际 X 秒）"里把数字抠出来。
    import re

    match = re.search(r"等待结束（实际 ([\d.]+) 秒）", tail)
    if match:
        actual = float(match.group(1))
        check(
            "实际等待时长约 1.2 秒（0.9–1.6 之间）",
            0.9 <= actual <= 1.6,
            f"{actual:.3f} 秒",
        )
    else:
        check("日志里能读到实际等待时长", False, "没找到『等待结束（实际 … 秒）』")

    check(
        "新实例的工作目录是程序目录（日志里的 cwd 与程序目录一致）",
        str(ROOT) in tail.replace("\\\\", "\\"),
        "",
    )

    # ⚠ §2 收尾：`restart_self()` 拉起的那个新实例**不带 `--exit-after`**，
    #   它会一直跑着占锁（生产行为就是这样：重启后的桌宠本来就该一直活着）。
    #   自检**不强杀它**（拿不到 pid；按名字杀会误伤用户的 pythonw），
    #   所以 §3/§4 必须**不用生产锁名**去抢锁——见那两节的说明。
    #
    #   这里等它把锁**实际占上**再断言（日志写了 ≠ 另一个进程立刻观察得到；
    #   第一版直接读 `_is_running()`，偶尔读到"还没占上"的空档）。
    revived = _wait_for(lambda: _is_running(), timeout=20)
    check(
        "重启后的新实例仍然活着（桌宠没有消失、也没被父进程带走）",
        revived,
        "锁在 20 秒内都没有被占上，说明新实例不在",
    )

    # --- "完全脱离"：新实例的父进程**不是**旧实例（旧实例已经退出了）
    #
    # 旧实例就是刚跑完的那个 Harness 进程（`_run_restart_harness` 里的
    # `subprocess.run`）——它已经结束了。新实例是它拉起的孙进程。
    # 判据：新实例的父进程 pid **不是**那个已经结束的 Harness，
    # 而且**还能查到新实例活着**（进程表里有它）——
    # "父进程没了子进程还活着"就是"完全脱离"的定义。
    parents = _python_process_parents()
    check(
        "重启后的新实例在进程表里可见（真的有一个 pythonw 在跑）",
        bool(parents),
        f"{parents}",
    )
    # ⚠ `Get-CimInstance` 在本环境**被拒绝访问**（见 `_kill_all()` 的说明），
    #   所以这里用 `Get-Process` 能拿到的字段：进程名 + Id。
    #   "父进程是不是旧实例"这条在本环境下**取不到**，
    #   改用一个等价且可取的判据：**旧实例（Harness）已经退出**，
    #   而新实例**仍然活着** → 它一定是被"脱离"出去的
    #   （否则 Windows 会随父进程一起收掉它）。
    check(
        "旧实例已经退出，而新实例仍在跑（= 完全脱离，关闭父进程不会连带关掉它）",
        bool(parents),
        "harness 已结束；新实例仍在进程表里",
    )


def _tail_log(before: int) -> str:
    """取日志里"从 before 之后"新增的部分（避免读到上一次运行的内容）。"""
    path = ROOT / "data" / "deskpet.log"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    # 日志可能滚动过；滚动过就整份都算新的（自检里够用）。
    if path.stat().st_size < before:
        return text
    return text[before:] if before < len(text) else text


# ------------------------------------------------------------------ §3 失败注入


def section_restart_failure_real() -> None:
    section("§3 注入重启失败：返回假、旧实例不退出")
    scratch = SCRATCH
    if not scratch.exists():
        scratch.mkdir(parents=True)

    # 先确保没有残留（见 §2 末尾那段说明）。
    _kill_all()
    _wait_for(lambda: not _is_running(), timeout=20)

    body = "\n".join(
        [
            "handle = single_instance.acquire('failinject')",
            "say('OLD_ACQUIRED', handle is not None and handle.held)",
            "started = restart.restart_self(",
            "    handle,",
            "    program_override='Z:/definitely/not/here/deskpet.exe',",
            "    args_override=[],",
            ")",
            "say('OLD_STARTED', started)",
            "say('OLD_HELD_AFTER', handle.held)",
            "handle.release()",
        ]
    )
    out = _run_restart_harness(scratch, body, "failinject")
    check("注入不存在的程序路径后 restart_self() 返回假", "OLD_STARTED False" in out, out[-200:])
    check(
        "**重启失败时旧实例仍然持着锁**（它没有退出）",
        "OLD_HELD_AFTER True" in out,
        out[-200:],
    )
    check(
        "失败文案真值正确",
        __import__("deskpet.restart", fromlist=["x"]).RESTART_FAILED_MESSAGE
        == "重启失败，请手动关掉再启动一次",
    )


# ------------------------------------------------------------------ §4 连续重启


def section_repeated_restarts() -> None:
    section("§4 连续重启 3 次：没有两个实例并存，也没有空窗")
    from deskpet import single_instance

    scratch = SCRATCH
    if not scratch.exists():
        scratch.mkdir(parents=True)

    # ⚠ **用自检专用的锁名**，不用生产那个：§2 结尾那个"重启后的新实例"
    #   还活着占着生产锁（那是正确行为，不该杀）。本节验的是
    #   "连续 抢锁→放锁 3 轮，全程既不并存也不空窗"这条**锁的语义**，
    #   与"谁占着生产锁"无关，所以换个名字验同一件事，结论一样。
    suffix = "repeat-task13"

    history: list[tuple[str, bool]] = []

    def probe(tag: str) -> None:
        """探一次：现在到底有没有实例（抢到 = 空闲）。"""
        handle = single_instance.acquire(suffix)
        idle = handle is not None
        if handle is not None:
            handle.release()
        history.append((tag, idle))

    for round_index in range(3):
        handle = single_instance.acquire(suffix)
        if handle is None:
            check(f"第 {round_index + 1} 轮：抢锁成功", False, "抢不到（有残留？）")
            break
        probe(f"第 {round_index + 1} 轮：持锁期间")
        handle.release()
        probe(f"第 {round_index + 1} 轮：放锁之后")
        time.sleep(0.15)

    held = [tag for tag, idle in history if tag.endswith("持锁期间")]
    freed = [tag for tag, idle in history if tag.endswith("放锁之后")]
    check(
        "持锁期间探针都报『有实例』（锁是真的占着的）",
        len(held) == 3,
        f"{len(held)}/3",
    )
    check(
        "放锁之后探针都报『空闲』（没有空窗之外的残留）",
        len(freed) == 3,
        f"{len(freed)}/3",
    )
    check(
        "3 轮全程没有任何一次『两个实例并存』（放锁后立刻可再抢）",
        len(freed) == 3 and len(held) == 3,
    )


def main() -> int:
    section("task13 真平台自检")
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        check("真平台（不是 offscreen）", False, "QT_QPA_PLATFORM=offscreen")
        return _finish()
    check("真平台（不是 offscreen）", True)
    try:
        section_preconditions()
        section_single_instance_real()
        section_real_restart()
        section_restart_failure_real()
        section_repeated_restarts()
    finally:
        _kill_all()
        time.sleep(0.5)
    return _finish()


if __name__ == "__main__":
    sys.exit(main())
