"""task13 自检（offscreen）：单实例保护、重启自己、换角色流程。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task13_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task13.py

## 这一份能验什么、不能验什么

**能验**：

1. 单实例语义：抢锁 → 第二个 `acquire()` 返回 `None`；放锁之后又能抢到；
2. **放锁 = 释放所有权 + 关闭句柄**（只 `ReleaseMutex` 是不够的，这是实测事实）；
3. 保护机制不可用时 `acquire()` 给**空壳**（`available=False`）而**不是** `None`
   ——"不可用"与"已有实例"必须分得开，前者不许阻断启动；
4. `--restart-delay` 的解析（负数/非数字报错退出、0 合法）与"缺省时不等"；
5. `restart.build_args()`：含 `--restart-delay`、打包/源码两条分支、
   源码优先 `pythonw.exe`；
6. `restart_self()` 的三种结果：启动成功（→ 放锁 + 返回真）、
   启动失败（→ **不放锁** + 返回假）、`startDetached` 抛异常也不崩；
7. 重启失败的文案与"**当前实例不退出**"（`RestartOutcome.started=False`）；
8. 换角色流程（打桩 `restart`/`confirm`）：
   可用包 + 同意 → 请求重启；可用包 + 不同意 → `重启后生效`；
   坏包 → 详情 + 回退默认形象 + **把配置改回默认形象** + 不重启；
   写失败 → `保存失败，重启后会回到原来的角色` + 界面与配置一致；
9. 角色回退时**写回配置**（`_persist_pet_fallback`），且**空串**而不是 `weedy`；
10. **只读查询不写盘**（`--list-*` 撞上坏包时 `config.json` 哈希不变）；
11. 源码级检查：`--list-*` 早于单实例检查、用了 `startDetached`（不是 `start`）、
    互斥体名字**不带 `Global\\`**（作用域必须是当前登录会话）。

**不能验**（交给 `check_task13_real.py`）：真起两个进程、真的重启一次并看到
新实例起来、新实例的父进程不是旧实例、新实例的工作目录、连续重启 5 次。
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task13_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task13_scratch"

#: 自检用的互斥体名字后缀：**绝不能用生产那个名字**，否则会跟真正开着的
#: 桌宠抢锁（跑一次自检就把用户的桌宠判成"已有实例"是不可能的，但反过来
#: "自检自己被用户的桌宠挡住"会让所有断言莫名其妙地 FAIL）。
MUTEX_SUFFIX = "selfcheck-task13"

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
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，task06/07/08/11/12 都踩过）。

    ⚠ 它把 token 用空格拼起来，所以 `QProcess.startDetached` 会变成
    `QProcess . startDetached`。拿**带点的字面量**去 `in` 它永远匹配不上
    ——配套用 `_squashed()`。
    """
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


def _squashed(name: str) -> str:
    """代码去掉**所有空白**：判"有没有写某个带点/带括号的调用"时用它。"""
    return _code_only(name).replace(" ", "")


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


# ------------------------------------------------------------------ §1 单实例


def section_single_instance() -> None:
    section("§1 单实例保护：抢锁 / 已有实例 / 放锁 / 不可用")
    from deskpet import single_instance as si, win32

    check("Win32 内核接口可用（本机）", win32.is_available(), f"{win32.is_available()}")
    check("is_available() 为真", si.is_available())

    name = si.mutex_name(MUTEX_SUFFIX)
    check(
        "互斥体名字不带 Global\\ 前缀（作用域 = 当前登录会话）",
        not name.startswith("Global\\") and name.startswith(si.MUTEX_PREFIX),
        name,
    )
    check("名字里带当前用户名", si._current_user() in name, name)

    # --- 抢锁 → 第二个被拒
    first = si.acquire(MUTEX_SUFFIX)
    check("第一个实例抢到了锁", first is not None and first.held, repr(first))
    second = si.acquire(MUTEX_SUFFIX)
    check("第二个实例拿到 None（= 已有实例在跑）", second is None, repr(second))

    # --- 放锁之后又能抢到（**这是重启交接的核心事实**）
    released = first.release()
    check("release() 返回真", released)
    third = si.acquire(MUTEX_SUFFIX)
    check("放锁之后新实例能抢到", third is not None and third.held, repr(third))
    if third is not None:
        third.release()
    check("release() 幂等（第二次调用不炸、返回真）", first.release() is True)

    # --- "不可用"与"已有实例"必须分得开
    unavailable = si._unavailable("probe-name", "自检注入")
    check(
        "保护不可用时给的是**空壳**而不是 None（不阻断启动）",
        unavailable is not None and not unavailable.held and not unavailable.available,
    )
    check("空壳的 release() 是安全的", unavailable.release() is True)

    # --- is_available() 打桩成假时 acquire() 仍然给空壳
    original = win32.is_available
    try:
        win32.is_available = lambda: False
        degraded = si.acquire(MUTEX_SUFFIX)
        check(
            "Win32 不可用时 acquire() 给空壳（照常启动、不返回 None）",
            degraded is not None and not degraded.held and not degraded.available,
            repr(degraded),
        )
    finally:
        win32.is_available = original

    # --- 建不出互斥体时同样是空壳
    original_create = win32.create_mutex
    try:
        win32.create_mutex = lambda _name: (0, False)
        failed = si.acquire(MUTEX_SUFFIX)
        check(
            "建互斥体失败时给空壳（不阻断启动）",
            failed is not None and not failed.held and not failed.available,
            repr(failed),
        )
    finally:
        win32.create_mutex = original_create


def section_release_semantics() -> None:
    """**实测事实**：只 `ReleaseMutex` 不放句柄，新实例进不来（§4 的坑）。"""
    section("§2 放锁的真实语义：释放所有权 **+** 关闭句柄")
    from deskpet import single_instance as si, win32

    name = si.mutex_name(MUTEX_SUFFIX + "-release")
    handle, created = win32.create_mutex(name)
    check("直接建互斥体成功且是我们创建的", bool(handle) and created, f"handle={handle}")

    # 只释放所有权（不关句柄）→ 新实例**仍然**拿不到
    win32.release_mutex(handle)
    handle2, created2 = win32.create_mutex(name)
    check(
        "只 ReleaseMutex（句柄还开着）时，新实例仍然看到 ERROR_ALREADY_EXISTS",
        bool(handle2) and not created2,
        f"handle={handle2} created={created2}",
    )
    win32.close_handle(handle2)

    # 关掉句柄 → 新实例才拿得到
    win32.close_handle(handle)
    handle3, created3 = win32.create_mutex(name)
    check(
        "CloseHandle 之后新实例才拿得到（created=True）",
        bool(handle3) and created3,
        f"handle={handle3} created={created3}",
    )
    win32.close_handle(handle3)

    code = _squashed("single_instance.py")
    check(
        "release() 里同时调了 ReleaseMutex 与 CloseHandle",
        "win32.release_mutex" in code and "win32.close_handle" in code,
    )


def section_mutex_name_scope() -> None:
    """互斥体名字里**不许**出现 `Global\\`（那会让所有用户互相排斥）。"""
    section("§3 作用域：仅当前登录会话")
    code = _code_only("single_instance.py")
    check(
        "源码里没有 Global\\ 前缀（只有**解释它为什么不能用**的注释/字符串会提到）",
        "Global" not in code,
    )
    check(
        "文档里解释了这个作用域是怎么来的",
        "当前登录会话" in _source_of("single_instance.py"),
    )


# ------------------------------------------------------------------ §4 重启参数


def section_restart_args() -> None:
    section("§4 --restart-delay 与 build_args()")
    from deskpet import restart as rs

    check(
        "RESTART_DELAY_DEFAULT = 1.2（**不许改小**）",
        rs.RESTART_DELAY_DEFAULT == 1.2,
        f"{rs.RESTART_DELAY_DEFAULT}",
    )

    args = rs.build_args()
    check("build_args() 含 --restart-delay", rs.RESTART_DELAY_ARG in args, " ".join(args))
    idx = args.index(rs.RESTART_DELAY_ARG)
    check(
        "参数紧跟着秒数，且缺省是 1.2",
        idx + 1 < len(args) and args[idx + 1] == "1.2",
        args[idx + 1] if idx + 1 < len(args) else "(缺)",
    )

    # 源码运行：pythonw.exe + run.py
    from deskpet import paths

    check("源码运行（未打包）", not paths.is_frozen())
    check(
        "源码运行时 program 是 pythonw.exe（不带控制台）",
        Path(args[0]).name.lower() == "pythonw.exe",
        args[0],
    )
    check(
        "源码运行时带上了 run.py 的绝对路径（取自程序目录，不是 argv[0]）",
        Path(args[1]) == paths.program_dir() / "run.py",
        args[1],
    )

    # 打包分支：把 is_frozen 打桩成真
    original_frozen = paths.is_frozen
    try:
        paths.is_frozen = lambda: True
        frozen_args = rs.build_args()
        check(
            "打包时 program 是 sys.executable（exe 自己），且不带脚本参数",
            frozen_args[0] == sys.executable and frozen_args[1] == rs.RESTART_DELAY_ARG,
            " ".join(frozen_args),
        )
    finally:
        paths.is_frozen = original_frozen

    # 自定义秒数
    custom = rs.build_args(2.5)
    check("自定义秒数被原样写进参数", custom[-1] == "2.5", " ".join(custom))

    # `restart-self` 只带这一个参数（档位/位置在配置里，不传）
    check(
        "重启参数里没有 --size-level / --position / --pet（它们本来就在配置里）",
        not any(a in ("--size-level", "--position", "--pet", "--sprite") for a in args),
        " ".join(args),
    )
    check(
        "失败文案逐字正确",
        rs.RESTART_FAILED_MESSAGE == "重启失败，请手动关掉再启动一次",
        rs.RESTART_FAILED_MESSAGE,
    )


def section_restart_delay_cli() -> None:
    """`--restart-delay` 的解析：命令行坏值 → argparse 报错退出（rc=2）。"""
    section("§5 --restart-delay 的命令行解析")
    prog = ROOT / "run.py"

    for bad in ("-1", "abc", "inf", "nan"):
        result = subprocess.run(
            [sys.executable, str(prog), "--restart-delay", bad],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            timeout=60,
        )
        check(
            f"--restart-delay {bad} → rc=2（argparse 用法错误）",
            result.returncode == 2,
            f"rc={result.returncode}",
        )

    # `0` 是合法的：走一条只读查询，确认它**不被解析器拒绝**
    result = subprocess.run(
        [sys.executable, str(prog), "--restart-delay", "0", "--list-pets"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=60,
    )
    check(
        "--restart-delay 0 合法（不等）",
        result.returncode == 0 and "素材包" in result.stdout,
        f"rc={result.returncode}",
    )

    # 缺省时**一秒都不等**：只读查询的耗时应当很短
    import time

    started = time.monotonic()
    subprocess.run(
        [sys.executable, str(prog), "--list-actions"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=60,
    )
    elapsed = time.monotonic() - started
    check(
        "不带 --restart-delay 时没有那 1.2 秒的等待",
        elapsed < 1.2,
        f"耗时 {elapsed:.3f} 秒",
    )


# ------------------------------------------------------------------ §6 重启自己


def section_restart_self() -> None:
    section("§6 restart_self()：成功放锁、失败不放锁、异常不崩")
    from deskpet import paths as paths_module
    from deskpet import restart as rs, single_instance as si

    # 假句柄：只记"有没有被 release"，不去碰真的内核对象
    class FakeHandle:
        def __init__(self) -> None:
            self.released = 0
            self.name = "fake"

        def release(self) -> bool:
            self.released += 1
            return True

    calls: list[tuple] = []

    # --- 成功：真的起一个进程（跑一条无害的只读查询）
    def fake_start_ok(program, args, cwd):
        calls.append((program, args, cwd))
        return True

    from PySide6.QtCore import QProcess

    original_start = QProcess.startDetached
    try:
        QProcess.startDetached = staticmethod(fake_start_ok)  # type: ignore[assignment]
        handle = FakeHandle()
        ok = rs.restart_self(handle, program_override="dummy.exe", args_override=["x"])
        check("启动成功时 restart_self() 返回真", ok is True)
        check("启动成功时**放了锁**", handle.released == 1, f"released={handle.released}")
        # ⚠ 期望值用**产品自己的函数**算，别写死仓库根：自检脚本住在 `tools/`，
        # 于是"程序目录"就是 `tools/`（§4.13：程序目录跟着入口脚本走）。
        check(
            "工作目录传的是 paths.program_dir()",
            calls and Path(calls[0][2]) == paths_module.program_dir(),
            f"{calls[0][2] if calls else '(没调用)'} vs {paths_module.program_dir()}",
        )

        # --- 失败：startDetached 返回假
        QProcess.startDetached = staticmethod(lambda *a: False)  # type: ignore[assignment]
        handle2 = FakeHandle()
        ok2 = rs.restart_self(handle2, program_override="no-such.exe", args_override=[])
        check("启动失败时 restart_self() 返回假", ok2 is False)
        check(
            "**启动失败时绝不放锁**（放了就等于'桌宠没了'）",
            handle2.released == 0,
            f"released={handle2.released}",
        )

        # --- 异常：也不许把异常甩给调用方
        def boom(*_a):
            raise OSError("注入的启动异常")

        QProcess.startDetached = staticmethod(boom)  # type: ignore[assignment]
        handle3 = FakeHandle()
        ok3 = rs.restart_self(handle3, program_override="boom.exe", args_override=[])
        check("startDetached 抛异常时返回假、不崩", ok3 is False)
        check("异常路径也不放锁", handle3.released == 0, f"released={handle3.released}")
    finally:
        QProcess.startDetached = original_start  # type: ignore[assignment]

    # --- handle 为 None 时（保护不可用）不该崩
    try:
        QProcess.startDetached = staticmethod(lambda *a: True)  # type: ignore[assignment]
        check("handle=None 时不崩", rs.restart_self(None, program_override="d.exe") is True)
    finally:
        QProcess.startDetached = original_start  # type: ignore[assignment]

    # --- 源码级：必须用 startDetached，**不许**用 start
    code = _squashed("restart.py")
    check("用了 QProcess.startDetached", "QProcess.startDetached" in code)
    check(
        "没有用 QProcess.start(（那会随父进程退出带走子进程）",
        "QProcess.start(" not in code.replace("QProcess.startDetached(", ""),
    )

    # --- ⚠ 回归：`startDetached()` 在 PySide6 6.x 返回的是 **元组**，
    #     对它直接 `bool()` 永远为真（`bool((False, -1)) == True`）→
    #     "重启失败"会被当成成功、旧实例照常放锁退出 → **桌宠彻底消失**。
    #     这是 task13 真平台自检抓到的真 bug，这里钉一条防回归。
    from PySide6.QtCore import QProcess as _QP

    real = _QP.startDetached(sys.executable, ["-c", "pass"], str(ROOT))
    check(
        "PySide6 的 startDetached 返回元组（元组才是那个坑的前提）",
        isinstance(real, tuple),
        f"{type(real).__name__} {real!r}",
    )
    check(
        "bool((False, -1)) 确实为真（说明**不能**直接 bool 整个返回值）",
        bool((False, -1)) is True,
    )
    check(
        "_start_detached() 对真实路径返回真",
        rs._start_detached(sys.executable, ["-c", "pass"], str(ROOT)) is True,
    )
    check(
        "_start_detached() 对不存在的路径返回假（取的是元组的第一个元素）",
        rs._start_detached("Z:/definitely/not/here/deskpet.exe", [], str(ROOT)) is False,
    )

    # 端到端：不存在的路径 → restart_self 返回假 + **不放锁**
    handle4 = FakeHandle()
    ok4 = rs.restart_self(
        handle4,
        program_override="Z:/definitely/not/here/deskpet.exe",
        args_override=[],
    )
    check("不存在的程序路径 → restart_self() 返回假", ok4 is False)
    check(
        "**此时绝不放锁**（放了 = 桌宠消失，task13 §6 明写不许）",
        handle4.released == 0,
        f"released={handle4.released}",
    )


# ------------------------------------------------------------------ §7 换角色


def section_settings_restart() -> None:
    section("§7 换角色流程：同意 / 不同意 / 坏包 / 写失败")
    from PySide6.QtWidgets import QApplication

    from deskpet import paths
    from deskpet.config import ConfigStore
    from deskpet.settings import PetChoice, RestartOutcome, SettingsWindow

    app = QApplication.instance() or QApplication([])

    scratch = _fresh_dir(SCRATCH / "settings")
    store = ConfigStore(scratch / "config.json")
    store.load()

    def build(restart=None, confirm=None, current="weedy"):
        window = SettingsWindow(store, current_pet=current)
        if restart is not None or confirm is not None:
            window.set_restart_handler(restart, confirm)
        return window

    good = PetChoice("weedy_sightseer", "温蒂（倾听）", True)
    bad = PetChoice("broken_pack", "坏包", False, ("asset.json 不见了", "cell 非法"))

    # --- 可用包 + 同意 → 请求重启
    events: list[str] = []
    window = build(
        restart=lambda: (events.append("restart"), RestartOutcome(True))[1],
        confirm=lambda *_a: (events.append("confirm"), True)[1],
    )
    field = next(f for f in __import__("deskpet.settings", fromlist=["FIELDS"]).FIELDS if f.key == "pet")
    window._select_pet(field, good)
    check("同意重启时调了询问框", "confirm" in events, str(events))
    check("同意之后真的请求了重启", window.restart_requested is True)
    check("写进配置的是新命名空间", store.get("pet") == "weedy_sightseer", str(store.get("pet")))

    # --- 可用包 + 不同意 → 提示"重启后生效"，不重启
    store2 = ConfigStore(scratch / "c2.json")
    store2.load()
    window2 = SettingsWindow(store2, current_pet="weedy")
    window2.set_restart_handler(
        lambda: RestartOutcome(True), lambda *_a: False
    )
    window2._select_pet(field, good)
    check("不同意时**没有**请求重启", window2.restart_requested is False)
    check(
        "不同意时状态栏说『重启后生效』",
        "重启后生效" in window2._status_label.text(),
        window2._status_label.text(),
    )
    check("不同意时当前实例继续运行（配置照常写进去）", store2.get("pet") == "weedy_sightseer")

    # --- 没装确认处理器 → 默认不重启
    store3 = ConfigStore(scratch / "c3.json")
    store3.load()
    window3 = SettingsWindow(store3, current_pet="weedy")
    window3._select_pet(field, good)
    check("没装确认处理器时默认**不**重启（安全默认）", window3.restart_requested is False)

    # --- 坏包 → 详情 + 回退默认形象 + 配置改回默认形象 + 不重启
    store4 = ConfigStore(scratch / "c4.json")
    store4.load()
    store4.set("pet", "weedy_sightseer")  # 先指向一个**别的**包
    window4 = SettingsWindow(store4, current_pet="weedy_sightseer")
    window4.set_restart_handler(lambda: RestartOutcome(True), lambda *_a: True)
    window4._select_pet(field, bad)
    check("坏包时**不重启**", window4.restart_requested is False)
    check(
        "坏包时状态栏带出那个包的问题（详情）",
        "asset.json 不见了" in window4._status_label.text(),
        window4._status_label.text()[:80],
    )
    check(
        "坏包时配置被改回默认形象（**空串**，不是 weedy）",
        store4.get("pet") == "",
        repr(store4.get("pet")),
    )
    on_disk = json.loads((scratch / "c4.json").read_text(encoding="utf-8"))
    check("磁盘上也是空串（真的落盘了）", on_disk.get("pet") == "", repr(on_disk.get("pet")))

    # --- 坏包 + 配置本来就是空串 → 一个字节都不写
    store5 = ConfigStore(scratch / "c5.json")
    store5.load()
    window5 = SettingsWindow(store5, current_pet="weedy")
    window5._select_pet(field, bad)
    check(
        "配置本来就是空串时不写盘（空串已经是'默认形象'的意思）",
        not (scratch / "c5.json").exists(),
        str(store5.get("pet")),
    )

    # --- 写失败 → 那句提示 + 界面与配置一致
    store6 = ConfigStore(scratch / "c6.json")
    store6.load()
    original_write = store6._write

    def failing_write():
        store6._last_errors["*"] = "写配置失败：注入（PermissionError: 拒绝访问）"
        return False

    store6._write = failing_write  # type: ignore[method-assign]
    window6 = SettingsWindow(store6, current_pet="weedy")
    window6.set_restart_handler(lambda: RestartOutcome(True), lambda *_a: True)
    window6._select_pet(field, good)
    text = window6._status_label.text()
    check(
        "写失败时提示『保存失败，重启后会回到原来的角色』",
        "保存失败，重启后会回到原来的角色" in text,
        text[:100],
    )
    check("写失败时系统原文也在（失败原因优先）", "拒绝访问" in text, text[:100])
    check("写失败时**不重启**", window6.restart_requested is False)
    check("写失败时配置仍是旧值", store6.get("pet") == "", repr(store6.get("pet")))
    store6._write = original_write  # type: ignore[method-assign]

    # --- 重启失败 → 当前实例继续运行
    store7 = ConfigStore(scratch / "c7.json")
    store7.load()
    window7 = SettingsWindow(store7, current_pet="weedy")
    window7.set_restart_handler(
        lambda: RestartOutcome(False, "新实例没能起来"), lambda *_a: True
    )
    window7._select_pet(field, good)
    check("重启失败时 restart_requested 仍为假", window7.restart_requested is False)
    check(
        "重启失败时状态栏说原因是『新实例没能起来』（不说重启后生效）",
        "新实例没能起来" in window7._status_label.text(),
        window7._status_label.text()[:100],
    )

    for window in (window, window2, window3, window4, window5, window6, window7):
        window.close()
    app.processEvents()


# ------------------------------------------------------------------ §8 角色回退写回


def section_pet_fallback_persist() -> None:
    section("§8 启动时角色回退 → 把配置写回默认形象（且只读查询不写）")
    scratch = _fresh_dir(SCRATCH / "fallback")
    program = scratch / "prog"
    program.mkdir()
    (program / "data").mkdir()

    # 造一个坏包：只有目录与坏索引
    pets = program / "data" / "pets" / "broken_pack"
    pets.mkdir(parents=True)
    (pets / "asset.json").write_text("{ not json", encoding="utf-8")

    config = program / "data" / "config.json"
    config.write_text(json.dumps({"pet": "broken_pack"}), encoding="utf-8")
    before = _sha(config)

    # 用一个**子进程**跑真实启动路径。⚠ `paths.override_dirs` 是
    # **上下文管理器**，直接调用它不会设置任何东西（第一版就这么写错了，
    # 报出来的是"config.json 在 bin/ 下面"）。这里用 `with` 把它包住。
    runner = scratch / "bin" / "runner.py"
    runner.parent.mkdir(parents=True)
    runner.write_text(
        "\n".join(
            [
                "import sys",
                f"sys.path.insert(0, {str(ROOT / 'src')!r})",
                "from deskpet import paths",
                "import logging",
                "logging.basicConfig(level=logging.INFO)",
                f"with paths.override_dirs(program={str(program)!r}, resource={str(ROOT)!r}):",
                "    from deskpet.config import ConfigStore",
                "    store = ConfigStore()",
                "    store.load()",
                "    print('CFGPATH', store.path)",
                "    print('BEFORE', repr(store.get('pet')))",
                "    from deskpet.app import _resolve_or_die",
                "    spec = _resolve_or_die(None, store, query_only=False)",
                "    print('SPEC', spec.namespace)",
                "    print('AFTER', repr(store.get('pet')))",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(runner)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(runner.parent),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=90,
    )
    out = result.stdout + result.stderr
    check("子进程正常跑完（回退不阻断启动）", result.returncode == 0, f"rc={result.returncode}")
    check("启动时回退到了默认形象 weedy", "SPEC weedy" in out, out[-300:])
    check("内存里的 pet 变成空串", "AFTER ''" in out, out[-300:])
    after = json.loads(config.read_text(encoding="utf-8"))
    check(
        "磁盘上真的写回了默认形象（空串，不是 weedy）",
        after.get("pet") == "",
        repr(after.get("pet")),
    )

    # --- 只读查询**一个字节都不写**
    config.write_text(json.dumps({"pet": "broken_pack"}), encoding="utf-8")
    before_query = _sha(config)
    runner_query = scratch / "bin" / "runner_query.py"
    runner_query.write_text(
        "\n".join(
            [
                "import sys",
                f"sys.path.insert(0, {str(ROOT / 'src')!r})",
                "from deskpet import paths",
                f"with paths.override_dirs(program={str(program)!r}, resource={str(ROOT)!r}):",
                "    from deskpet.config import ConfigStore",
                "    store = ConfigStore()",
                "    store.load()",
                "    from deskpet.app import _resolve_or_die",
                "    spec = _resolve_or_die(None, store, query_only=True)",
                "    print('QUERY_SPEC', spec.namespace)",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(runner_query)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(runner_query.parent),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        timeout=90,
    )
    check(
        "只读查询（query_only=True）撞上坏包时 config.json **哈希不变**",
        _sha(config) == before_query,
        f"{before_query[:12]}",
    )

    # --- 源码级：回写用的是空串（默认形象的真值只有一处）
    #
    # ⚠ 这里**不能**用 `_squashed()`：它把 STRING token 整个丢掉，
    # 于是 `apply("pet", "")` 这种"要判的就是字符串字面量"的检查
    # 永远匹配不上（第一版就这么写错的）。要判字面量就直接读原文。
    source = _source_of("app.py")
    check(
        "_persist_pet_fallback 写的是空串",
        'store.apply("pet", "")' in source,
    )
    check(
        "没有把 weedy 写进配置（默认形象的真值只在 pet_spec 一处）",
        'store.apply("pet", "weedy")' not in source,
    )


# ------------------------------------------------------------------ §9 源码级


def section_source_order() -> None:
    section("§9 启动编排的源码级检查")
    # ⚠ `_code_only()` 把 token 用空格拼起来（`args . list_pets`），
    # 所以"带点的字面量"必须拿 `_squashed()` 去比 —— §4.11 的老坑，别再踩。
    code = _code_only("app.py")
    flat = _squashed("app.py")

    order = [
        ("--list-pets", "args.list_pets"),
        ("--list-actions", "args.list_actions"),
        ("单实例", "single_instance.acquire"),
        ("建 QApplication", "QApplication(raw_argv)"),
    ]
    positions = {name: flat.find(needle) for name, needle in order}
    ok_order = all(position >= 0 for position in positions.values()) and list(
        positions.values()
    ) == sorted(positions.values())
    check(
        "顺序：--list-pets → --list-actions → 单实例 → 建 QApplication",
        ok_order,
        str(positions),
    )

    check(
        "--list-sizes 不抢单实例锁（它是只读查询、又必须晚于 QApplication）",
        "query_only_run" in code,
    )

    # 等待发生在抢锁之前（`_wait_before_lock` 的定义在文件里更靠前，
    # 所以这里比的是**调用点**）
    wait_at = flat.find("_wait_before_lock(args.restart_delay)")
    lock_at = flat.find("single_instance.acquire()")
    check(
        "重启等待发生在抢锁**之前**",
        0 <= wait_at < lock_at,
        f"wait@{wait_at} lock@{lock_at}",
    )

    check("退出收尾里放了锁", "instance.release()" in flat)

    # 热键/单实例的"每个新窗口都要重装"教训
    settings_code = _squashed("settings.py")
    check(
        "SettingsWindow 有 set_restart_handler()",
        "defset_restart_handler" in settings_code,
    )
    check(
        "SettingsController.open() 里每个新窗口都重装了重启处理器",
        "dialog.set_restart_handler(self._restart_handler,self._confirm_handler)" in flat,
    )

    # 两个真值只在一处
    check(
        "--restart-delay 的参数名真值只有一处（restart.RESTART_DELAY_ARG）",
        "RESTART_DELAY_ARG" in _source_of("restart.py")
        and _source_of("app.py").count('"--restart-delay"') == 0,
    )


def main() -> int:
    section("task13 自检（offscreen）")
    section_single_instance()
    section_release_semantics()
    section_mutex_name_scope()
    section_restart_args()
    section_restart_delay_cli()
    section_restart_self()
    section_settings_restart()
    section_pet_fallback_persist()
    section_source_order()
    return _finish()


if __name__ == "__main__":
    sys.exit(main())
