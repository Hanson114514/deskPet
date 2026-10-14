"""开机自启：**状态是真的、开关能关掉、启动文件夹里只有一个文件**（task14）。

## 它在用户系统里做的事

在用户的「启动」文件夹（`shell:startup`）里放**一个**快捷方式 `deskpet.lnk`，
指向**当前正在运行的这个程序**。生成快捷方式失败时退回写 `deskpet.cmd`。

## 三条硬规则（复述，代码里每一处都对着它们写）

| 规则 | 为什么 |
| --- | --- |
| **默认关闭**，用户不勾选就**完全不动**自启项 | 装个桌宠不该顺手改用户的登录项 |
| **"读"与"修"完全分开**：`query_state()` **绝不写盘** | 打开设置窗口是一次"看"，看不能改任何东西（agent.md §4.2） |
| 状态判定问的是"**这个功能到底能不能用**"，不是"文件在不在" | 快捷方式读不出目标、或指向别的程序 → 一律算**未开启** |

## 为什么走 PowerShell 而不是纯 Python

`.lnk` 是 shell link 二进制格式，标准库读不了、写不了。系统自带的
`WScript.Shell` COM 是唯一稳定的读写方式，而它能被 PowerShell 直接调。
两个 `.ps1` 是**外部文件**（`tools/make_shortcut.ps1` / `tools/read_shortcut.ps1`），
**打包时必须显式声明进数据**，否则打包后"开机自启永远显示不可用"，
而**源码运行看不出来**（task14 §4 的打包待办）。

## 子进程结果怎么拿回来

**不解析子进程的标准输出**：子进程输出编码随环境而变（同一脚本一种环境 UTF-8、
另一种 GBK），猜错的表现是**报错信息变乱码——把唯一的线索毁掉**（agent.md §4.3）。
规则：让脚本把结果**写进文件**（UTF-8），父进程用 `utf-8-sig` 读回来。

## 编码

两个 `.ps1` **必须**是 UTF-8 with BOM：含非 ASCII 字符时，PowerShell 5.1
按系统 ANSI 解码会得到乱码、连语法都过不去，而用户只看到最后一步的
`Permission denied`，与真正原因毫无关系（agent.md §4.3）。本模块**不负责**转码，
但 `ps1_encoding_ok()` 会当场问一句"那个文件到底是不是 BOM 开头"。
"""

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from . import paths
from .logger import log_autostart_outcome

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ 常量

#: 自启项的文件名（**项目命名定案**：`deskpet.lnk`，agent.md §0）。
SHORTCUT_NAME: Final[str] = "deskpet.lnk"

#: 回退方式的文件名（生成快捷方式失败时用它）。
FALLBACK_NAME: Final[str] = "deskpet.cmd"

#: 状态栏前缀（附录 B 的契约，与 `settings.STATUS_PREFIX` 同源）。
STATUS_PREFIX: Final[str] = "开机自启："

#: 状态文案（task14 §4 **逐字符照做**，属于附录 B 契约）。
#:
#: ⚠ **三个"完整句子"常量与两个"后缀"常量是两类**，别混用：
#: `TEXT_DISABLED` / `TEXT_BROKEN` / `TEXT_ELSEWHERE` 自带开头（未开启…），
#: `TEXT_ENABLED` / `TEXT_WORKDIR` 走"`已开启` + 后缀"的拼法。
#: 混用会让用户看到 `已开启已开启［…］`（第一版就是这么错的，自检当场抓到）。
TEXT_DISABLED: Final[str] = "未开启"
TEXT_ENABLED: Final[str] = "已开启"
TEXT_BROKEN: Final[str] = (
    "未开启［启动文件夹里那个快捷方式是坏的（读不出目标），取消勾选再勾选一次即可重建］"
)
TEXT_ELSEWHERE: Final[str] = "未开启［它指向的是别的地方：{target}］"

#: ⚠ **后缀**，不是完整句子：`TEXT_ENABLED` 那一句要拼在它前面
#: （task14 §4 的表格里写的是完整文案 `已开启［工作目录对不上，重新开启即可修正］`）。
TEXT_WORKDIR_SUFFIX: Final[str] = "［工作目录对不上，重新开启即可修正］"

#: ⚠ 同样是**后缀**（表格里的完整文案是 `［回退方式：登录时会闪一下命令行窗口］`，
#: 它接在 `已开启` 后面）。
TEXT_FALLBACK: Final[str] = "［回退方式：登录时会闪一下命令行窗口］"

#: 拼好的完整文案（**状态判定的唯一出口**，供自检与界面直接比对）。
TEXT_WORKDIR: Final[str] = f"{TEXT_ENABLED}{TEXT_WORKDIR_SUFFIX}"

#: 最可能的原因与逃生门（写在**日志**里，不塞进状态栏那段固定文案）。
#: ⚠ **不写**"重新开启即可修正"这种没验证过的承诺（agent.md §4.2）。
HINT_BLOCKED: Final[str] = (
    "最可能的原因：安全软件拦截，或 Windows 的「受控文件夹访问」把「启动」文件夹保护起来了。"
    "逃生门：在「启动」文件夹上右键 → 属性 → 安全，给自己写权限；或先把安全软件暂时关掉再勾选。"
)

#: 结果文件名叫什么（放临时目录，**绝不放启动文件夹**——那里的任何文件都会被当启动项，§4.10）。
RESULT_FILE_PREFIX: Final[str] = "deskpet_autostart_"

#: PowerShell 的固定位置。找不到时退回 `PATH` 上的 `powershell`。
#: ⚠ **不许**用 `pwsh`：Windows 自带的只有 5.1，装没装 pwsh 是用户的事。
POWERSHELL_SYSTEM_PATH: Final[str] = (
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)

#: 子进程能跑多久（秒）。两个脚本都只碰一个文件，5 秒已经非常宽裕。
POWERSHELL_TIMEOUT_S: Final[float] = 15.0

#: 启动文件夹推导的第二道防线：`%APPDATA%` 下这个相对路径。
#: 真值来自 `SHGetKnownFolderPath(FOLDERID_Startup)`（由 PowerShell 问系统）。
STARTUP_RELATIVE: Final[tuple[str, ...]] = (
    "Microsoft",
    "Windows",
    "Start Menu",
    "Programs",
    "Startup",
)

#: 早期版本可能误放在启动文件夹里的标记文件（启动文件夹清理的对象）。
#: ⚠ 这里**只放我们自己用过的名字**，不做通配删除——那会误伤用户的启动项。
LEGACY_MARKERS: Final[tuple[str, ...]] = (
    "deskpet.marker",
    "deskpet.state",
    "deskpet.autostart",
    ".deskpet",
)


# ------------------------------------------------------------------ 状态


@dataclass(frozen=True)
class AutostartState:
    """自启项的结构化状态（task14 §4 的接口约定）。

    | 字段 | 语义 |
    | --- | --- |
    | `enabled` | **这个功能到底能不能用**（不是"文件在不在"） |
    | `status_text` | 给状态栏看的那段文字（**逐字符**来自上面的常量） |
    | `error` | 用户明确要求的写操作失败时的系统原文；成功/只读路径上是 `None` |
    | `detail` | 诊断细节（走了哪条路、实际目标是什么），**只进日志** |
    """

    enabled: bool
    status_text: str = TEXT_DISABLED
    error: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class _ShortcutInfo:
    """`read_shortcut.ps1` 的结论。

    `status` 取 `OK` / `MISSING` / `BROKEN` / `ERROR` 四种，与脚本的约定一致。
    """

    status: str
    target: str = ""
    arguments: str = ""
    working_dir: str = ""
    error: str = ""


# ------------------------------------------------------------------ 启动文件夹


def _startup_relpath() -> Path:
    """`%APPDATA%` 推导出来的启动文件夹（静态规则，**不用来判定**，只作断言与兜底）。"""
    appdata = os.environ.get("APPDATA", "")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base.joinpath(*STARTUP_RELATIVE)


def startup_dir() -> Path:
    """Windows 的「启动」文件夹（`shell:startup`）。

    **真值向系统要**：用 PowerShell 的 `[Environment]::GetFolderPath('Startup')`
    —— 它内部就是 `SHGetKnownFolderPath(FOLDERID_Startup)`，会正确处理
    「启动」文件夹被重定向（漫游配置 / 组策略）的情形。

    拿不到时（非 Windows、PowerShell 不可用）退回 `%APPDATA%` 推导，
    并**打一条警告**：这是第二道防线，不是等价实现。
    """
    reported = _ask_system_startup_dir()
    if reported:
        return reported

    fallback = _startup_relpath()
    logger.warning(
        "没能向系统问到「启动」文件夹的位置，退化为 %%APPDATA%% 推导：%s", fallback
    )
    return fallback


def _ask_system_startup_dir() -> Path | None:
    """问系统要「启动」文件夹；问不到返回 `None`。

    ⚠ 这是一次**命令行**调用，不是 `query_state()` 的主路径——`query_state()`
    用的是本进程启动时缓存的那一个（见 `_startup_dir_cached()`），
    否则"打开设置窗口"每次都要等一个 PowerShell 进程起来。
    """
    if sys.platform != "win32":  # pragma: no cover - 非 Windows
        return None

    script = "$ErrorActionPreference='Stop'\n" "[Environment]::GetFolderPath('Startup')\n"
    completed = _run_powershell_command(script)
    if completed is None:
        return None

    text = (completed.stdout or "").strip().splitlines()
    candidate = text[-1].strip() if text else ""
    if not candidate:
        return None
    try:
        return Path(candidate)
    except OSError:  # pragma: no cover - 极端情况
        return None


#: 本进程问过一次就记住（**进程内缓存**，不是文件缓存）。
_startup_cache: Path | None = None


def _startup_dir_cached() -> Path:
    """`startup_dir()` 的进程内缓存版本（"读状态"路径上用它）。"""
    global _startup_cache
    if _startup_cache is None:
        _startup_cache = startup_dir()
    return _startup_cache


def reset_cache() -> None:
    """清掉启动文件夹与"自启项目标"的进程内缓存。**只给自检用。**"""
    global _startup_cache
    _startup_cache = None


def shortcut_path(resolved: Path | None = None) -> Path:
    """自启项快捷方式的完整路径（`<启动文件夹>/deskpet.lnk`）。"""
    return (resolved or _startup_dir_cached()) / SHORTCUT_NAME


def fallback_path(resolved: Path | None = None) -> Path:
    """回退方式（`<启动文件夹>/deskpet.cmd`）的完整路径。"""
    return (resolved or _startup_dir_cached()) / FALLBACK_NAME


def our_files(resolved: Path | None = None) -> list[Path]:
    """启动文件夹里**属于我们**的文件（`.lnk` 与 `.cmd` 两种形态）。"""
    directory = resolved or _startup_dir_cached()
    found: list[Path] = []
    for name in (SHORTCUT_NAME, FALLBACK_NAME):
        candidate = directory / name
        try:
            if candidate.is_file():
                found.append(candidate)
        except OSError:  # pragma: no cover - 读不到就当没有
            continue
    return found


# ------------------------------------------------------------------ 外部脚本


def tools_dir() -> Path:
    """两个 `.ps1` 住在哪：`<程序目录>/tools`。

    ⚠ 打包时它们会随 `datas` 落到**解包目录**下，所以这两个路径
    **必须显式声明进打包数据**（task14 §4 的待办）。
    """
    return paths.program_dir() / "tools"


def make_shortcut_script() -> Path:
    """生成快捷方式的脚本（`tools/make_shortcut.ps1`）。"""
    return tools_dir() / "make_shortcut.ps1"


def read_shortcut_script() -> Path:
    """读回快捷方式目标的脚本（`tools/read_shortcut.ps1`）。"""
    return tools_dir() / "read_shortcut.ps1"


def _powershell_executable() -> str:
    """系统 PowerShell 的可执行文件。

    优先固定位置（`C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe`），
    它不存在时才退回 `PATH` 上的 `powershell`——**不猜别的名字**。
    """
    fixed = Path(POWERSHELL_SYSTEM_PATH)
    try:
        if fixed.is_file():
            return str(fixed)
    except OSError:  # pragma: no cover
        pass
    return "powershell"


def _subprocess_startupinfo() -> subprocess.STARTUPINFO | None:
    """让子进程**不弹窗口**。

    `pythonw.exe`（无控制台）下起一个控制台子进程会闪出黑框，
    而"勾一下设置"不该闪任何东西。非 Windows 上返回 `None`。
    """
    if sys.platform != "win32":  # pragma: no cover - 非 Windows
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return startupinfo


def _powershell_argv(script: Path, args: list[str]) -> list[str]:
    return [
        _powershell_executable(),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]


def _run_powershell_command(command: str) -> subprocess.CompletedProcess | None:
    """跑一小段内联 PowerShell 命令（用于问系统要「启动」文件夹）。

    与 `_run_powershell_script()` 同一套参数与隐藏窗口策略；
    这里的输出**可以直接解析**（只有一行路径，不含中文、不依赖控制台编码）。
    """
    if sys.platform != "win32":  # pragma: no cover - 非 Windows
        return None
    try:
        return subprocess.run(
            [
                _powershell_executable(),
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=POWERSHELL_TIMEOUT_S,
            startupinfo=_subprocess_startupinfo(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("自启：问系统要「启动」文件夹失败（%s: %s）", type(exc).__name__, exc)
        return None


def _run_powershell_script(script: Path, args: list[str]) -> subprocess.CompletedProcess | None:
    """跑一个 `.ps1`；跑不起来（超时 / 没有解释器）返回 `None`。

    **绝不解析标准输出**（§4.3）：`stdout` / `stderr` 只作为诊断文本带回去，
    真正的结果由脚本**写进结果文件**、父进程用 `utf-8-sig` 读回来。
    """
    if not script.is_file():
        logger.error("自启：脚本不存在（打包含了没声明进数据？）：%s", script)
        return None

    try:
        return subprocess.run(
            _powershell_argv(script, args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=POWERSHELL_TIMEOUT_S,
            startupinfo=_subprocess_startupinfo(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error("自启：调用 PowerShell 失败（%s: %s）", type(exc).__name__, exc)
        return None


def _result_file_path(tag: str) -> Path:
    """结果文件放**临时目录**。

    ⚠ **绝不放在启动文件夹里**：那里的**任何**文件都会被 shell 当启动项尝试执行
    （与扩展名无关，agent.md §4.10），而且验收要求"开启后那里恰好只有一个我们的文件"。
    """
    import tempfile

    handle, name = tempfile.mkstemp(prefix=f"{RESULT_FILE_PREFIX}{tag}_", suffix=".txt")
    os.close(handle)
    return Path(name)


def _read_result_file(path: Path) -> str:
    """读回结果文件（`utf-8-sig`，容 BOM；读不到就是空串）。"""
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""


def _cleanup(paths_to_remove: list[Path]) -> None:
    """收拾结果文件。**尽力而为**：清理类操作的失败不该改变主结论（§2.10）。"""
    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - 临时文件删不掉不影响结论
            continue


def ps1_encoding_ok(path: Path) -> bool:
    """这个 `.ps1` 是不是 **UTF-8 with BOM**（task14 §6 有一条验收盯它）。

    没有 BOM 时 PowerShell 5.1 按系统 ANSI 解码，含中文的脚本会变成
    `ParserError`，而调用方只看到最后一步的 `Permission denied`（§4.3）。
    """
    try:
        return path.read_bytes()[:3] == b"\xef\xbb\xbf"
    except OSError:  # pragma: no cover
        return False


# ------------------------------------------------------------------ 读快捷方式


def read_shortcut(link: Path) -> _ShortcutInfo:
    """读回一个 `.lnk` 的目标（**只读**）。

    | 返回的 `status` | 含义 |
    | --- | --- |
    | `OK` | 真的读到了目标 |
    | `MISSING` | 文件不存在 |
    | `BROKEN` | 存在但读不出目标（0 字节空壳也算） |
    | `ERROR` | 读的过程中系统报错（原文在 `error` 里） |

    ⚠ 这是"读状态"路径的一部分，**一个字节都不许写**。
    """
    if not link.is_file():
        return _ShortcutInfo(status="MISSING")

    result_file = _result_file_path("read")
    try:
        completed = _run_powershell_script(
            read_shortcut_script(),
            ["-Path", str(link), "-Result", str(result_file)],
        )
        text = _read_result_file(result_file)
    finally:
        _cleanup([result_file])

    parsed = _parse_key_values(text)
    status = parsed.get("STATUS", "").strip().upper()
    if not status:
        detail = ""
        if completed is not None:
            detail = (completed.stderr or completed.stdout or "").strip().splitlines()
            detail = detail[-1] if detail else ""
        return _ShortcutInfo(status="ERROR", error=detail or "读快捷方式没有给出结果")
    if status == "OK":
        return _ShortcutInfo(
            status="OK",
            target=parsed.get("TARGET", "").strip(),
            arguments=parsed.get("ARGUMENTS", "").strip(),
            working_dir=parsed.get("WORKINGDIR", "").strip(),
        )
    return _ShortcutInfo(status=status, error=parsed.get("ERROR", "").strip())


def _parse_key_values(text: str) -> dict[str, str]:
    """把 `键=值` 逐行读成字典（第一个 `=` 之后的全部算值）。"""
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if not line or "=" not in line:
            continue
        key, _, value = line.partition("=")
        parsed[key.strip().upper()] = value
    return parsed


# ------------------------------------------------------------------ 目标比对


def _same_target(actual: str, expected: str) -> bool:
    """两个路径是不是同一个（Windows 大小写不敏感 + 规范化）。"""
    if not actual or not expected:
        return False
    try:
        return os.path.normcase(os.path.normpath(actual)) == os.path.normcase(
            os.path.normpath(expected)
        )
    except (OSError, ValueError):  # pragma: no cover - 极端输入
        return actual.strip().lower() == expected.strip().lower()


def _same_arg_string(actual: str, expected: str) -> bool:
    """启动参数是不是同一个说法（去掉多余空白 + 大小写不敏感）。

    ⚠ 不做完整的命令行解析：`make_shortcut.ps1` 写进去的就是一个路径，
    比对的是"写进去的那个说法有没有变"，不是"Windows 会怎么解析它"。
    """
    return " ".join(actual.split()).lower() == " ".join(expected.split()).lower()


def _expected() -> tuple[str, str, str]:
    """当前进程对应的 `(目标, 参数, 工作目录)`（**绝不写死**，来自 `paths`）。"""
    target, arguments = paths.autostart_target()
    return (target, arguments, str(paths.program_dir()))


def query_state(resolved: Path | None = None) -> AutostartState:
    """**纯只读**地判断自启项的状态：这功能到底能不能用。

    ⚠ 本函数**绝不写盘、绝不"顺手自愈"**（task14 §5 的头号红线）：
    打开设置窗口、显示状态都走这里。清理（`cleanup_legacy_markers`）与
    修正（`enable`）只能由用户明确要求的动作触发。

    判定顺序（先看"更好的那一个"）：

    1. `.lnk` 在 → 读回目标：读不出 → `TEXT_BROKEN`；指向别处 → `TEXT_ELSEWHERE`
       （`enabled` 都是**假**——"功能能不能用"才是判据）；
    2. 目标和参数都对 → `enabled=True`，工作目录不符则补一句 `TEXT_WORKDIR`；
    3. `.lnk` 不在但 `.cmd` 在 → 按 `.cmd` 判（这是回退方式）；
    4. 两个都不在 → `未开启`。
    """
    directory = resolved or _startup_dir_cached()
    target, arguments, working_dir = _expected()
    prefix_detail = f"目标={target or '(拿不到)'} 参数={arguments or '(无)'} 工作目录={working_dir}"

    link = directory / SHORTCUT_NAME
    if link.is_file():
        info = read_shortcut(link)
        if info.status == "OK":
            if not _same_target(info.target, target):
                return AutostartState(
                    enabled=False,
                    status_text=TEXT_ELSEWHERE.format(target=info.target),
                    detail=f"快捷方式指向 {info.target}，不是本程序；{prefix_detail}",
                )
            if not _same_arg_string(info.arguments, arguments):
                return AutostartState(
                    enabled=False,
                    status_text=TEXT_ELSEWHERE.format(target=f"{info.target} {info.arguments}"),
                    detail=f"快捷方式的启动参数对不上（{info.arguments!r}）；{prefix_detail}",
                )
            if info.working_dir and not _same_target(info.working_dir, working_dir):
                return AutostartState(
                    enabled=True,
                    status_text=TEXT_WORKDIR,
                    detail=f"快捷方式的工作目录是 {info.working_dir}；{prefix_detail}",
                )
            return AutostartState(
                enabled=True,
                status_text=TEXT_ENABLED,
                detail=f"快捷方式核验通过（{info.target} {info.arguments}）；{prefix_detail}",
            )

        if info.status == "MISSING":  # pragma: no cover - 上面刚判过 is_file
            return AutostartState(enabled=False, status_text=TEXT_DISABLED, detail=prefix_detail)

        # `BROKEN` / `ERROR` 都归到"读不出目标"那一句上：对用户来说
        # "空壳"与"读不动"是同一件事——取消勾选再勾选一次就能重建。
        return AutostartState(
            enabled=False,
            status_text=TEXT_BROKEN,
            detail=f"快捷方式读不出目标（status={info.status}，{info.error or '无更多原因'}）；{prefix_detail}",
        )

    command = directory / FALLBACK_NAME
    if command.is_file():
        if _command_points_to_target(command, target, arguments):
            return AutostartState(
                enabled=True,
                status_text=f"{TEXT_ENABLED}{TEXT_FALLBACK}",
                detail=f"用的是回退方式（{command}）；{prefix_detail}",
            )
        return AutostartState(
            enabled=False,
            status_text=TEXT_ELSEWHERE.format(target=str(command)),
            detail=f"回退脚本的内容对不上：{command}；{prefix_detail}",
        )

    return AutostartState(
        enabled=False, status_text=TEXT_DISABLED, detail=f"启动文件夹里没有自启项；{prefix_detail}"
    )


def _command_points_to_target(command: Path, target: str, arguments: str) -> bool:
    """`.cmd` 的内容里有没有我们要的那条命令（**只读地看一眼**）。

    只做"包含"判断，不解析批处理语法：这条路径只在"快捷方式写不出来、
    退回了 `.cmd`"时才会走到，判据是"它还指着我们吗"，不是"它写得对不对"。
    """
    try:
        text = command.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:  # pragma: no cover - 读不到就当它对不上
        return False
    wanted = _command_line(target, arguments)
    return wanted.lower() in " ".join(text.split()).lower()


# ------------------------------------------------------------------ 开 / 关


def _command_line(target: str, arguments: str) -> str:
    """`.cmd` 里那一行要长什么样（**唯一拼法**，读回与写入共用）。"""
    parts = [part for part in (target, arguments) if part]
    return " ".join(f'"{part}"' if " " in part else part for part in parts)


def _cmd_contents(target: str, arguments: str) -> str:
    """`.cmd` 的完整内容（UTF-8；Windows 的 cmd 对 UTF-8 无 BOM 的 ASCII 行没问题）。"""
    return f"@echo off\nstart \"\" {_command_line(target, arguments)}\n"


def _write_text(path: Path, text: str) -> None:
    """写一个文本文件。**失败要抛**（调用方负责翻译成"用户要求的写失败"）。"""
    path.write_text(text, encoding="utf-8", newline="\r\n")


def _remove(path: Path) -> None:
    """删一个文件。文件本来就不在 → 什么都不做（**幂等**）。失败要抛。"""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _describe_failure(label: str, completed: subprocess.CompletedProcess | None, detail: str) -> str:
    """把一次失败拼成**带系统原文**的一句话（两条尝试各自的原因都要能看到）。

    顺序：结果文件里的详情 → 脚本的 stderr 最后一行 → 兜底说明。
    三者都留一句，是为了"错误处理自己也会失败"时还有线索（§4.3）。
    """
    pieces = [f"{label}：{detail}" if detail else f"{label}失败"]
    if completed is not None:
        stderr = (completed.stderr or "").strip().splitlines()
        if stderr:
            pieces.append(f"PowerShell 输出：{stderr[-1]}")
        if completed.returncode not in (0, None):
            pieces.append(f"退出码 {completed.returncode}")
    return "；".join(pieces)


def _write_shortcut(link: Path) -> tuple[bool, str]:
    """生成快捷方式，返回 `(成功?, 失败原因)`。**只做一件事，不吞异常**。"""
    target, arguments, working_dir = _expected()
    result_file = _result_file_path("make")
    try:
        completed = _run_powershell_script(
            make_shortcut_script(),
            [
                "-Path",
                str(link),
                "-Target",
                target,
                "-Arguments",
                arguments,
                "-WorkingDirectory",
                working_dir,
                "-Description",
                "桌宠 deskPet",
                "-Result",
                str(result_file),
            ],
        )
        text = _read_result_file(result_file)
    finally:
        _cleanup([result_file])

    parsed = _parse_key_values(text)
    status = parsed.get("STATUS", "").strip().upper()
    if completed is None:
        return (False, _describe_failure("生成快捷方式", None, "PowerShell 没能启动"))
    if status == "OK" and completed.returncode == 0:
        return (True, "")
    return (
        False,
        _describe_failure(
            "生成快捷方式",
            completed,
            parsed.get("ERROR", "").strip() or "脚本没有给出成功结论",
        ),
    )


def _write_fallback(command: Path) -> tuple[bool, str]:
    """写 `.cmd` 回退项，返回 `(成功?, 失败原因)`。

    这是**用户明确要求的写操作**（agent.md §2.10 第二类）：失败必须把
    系统原因原样带出去，不许 `except OSError: pass`。
    """
    target, arguments, _working = _expected()
    try:
        _write_text(command, _cmd_contents(target, arguments))
    except OSError as exc:
        return (False, f"写回退脚本失败（{command}）：{exc}")
    return (True, "")


def enable(resolved: Path | None = None) -> AutostartState:
    """开启自启：**先试快捷方式；成功后清掉可能残留的 `.cmd`；失败则退回 `.cmd`**。

    | 情形 | 结果 |
    | --- | --- |
    | 快捷方式成功 | `enabled=True` + `已开启`，并删掉残留的 `deskpet.cmd` |
    | 快捷方式失败、`.cmd` 成功 | `enabled=True` + `已开启［回退方式：登录时会闪一下命令行窗口］`，**两条原因都记日志** |
    | 两条都失败 | `enabled=False` + `error`（`设置失败：<系统原因>` 由界面拼），**两条原因都在里面** |

    "成功"的判据是**再做一次只读查询**（`query_state()`），不是"脚本没报错"：
    "文件存在"≠"功能正常"（agent.md §4.2）。
    """
    directory = resolved or _startup_dir_cached()
    link = directory / SHORTCUT_NAME
    command = directory / FALLBACK_NAME
    target, arguments, _working = _expected()

    if not target:
        return AutostartState(
            enabled=False,
            error="拿不到当前程序的路径，无法创建自启项",
            detail="paths.autostart_target() 给不出目标程序",
        )

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.error("自启：开启失败，%s", HINT_BLOCKED)
        return AutostartState(
            enabled=False,
            error=f"访问启动文件夹失败（{directory}）：{exc}",
            detail=f"mkdir 失败：{exc}",
        )

    # 先清掉可能残留的回退项：**启动文件夹里恰好只能有一个我们的文件**。
    # 用户又能看到那个残留？（正常路径上不会——这是给"上次只写成了 .cmd"收尾。）
    try:
        _remove(command)
    except OSError as exc:  # 清理类操作 → 尽力而为，但**要留痕**
        logger.warning("自启：残留的 %s 没能删掉：%s", command, exc)

    ok, reason = _write_shortcut(link)
    if ok:
        state = query_state(directory)
        if state.enabled:
            log_autostart_outcome(
                "开启", True, f"已开启（{link} -> {target} {arguments}）"
            )
            return state
        # 脚本说成功、只读查询说不行 → **以查询为准**（绝不显示假成功）。
        log_autostart_outcome(
            "开启", False, f"快捷方式写了但核验不过：{state.status_text}（{state.detail}）"
        )
        return AutostartState(
            enabled=False,
            error=f"快捷方式写好了但核验不过（{state.detail}）",
            detail=state.detail,
        )

    log_autostart_outcome("开启", False, f"快捷方式这条路失败：{reason}")
    cmd_ok, cmd_reason = _write_fallback(command)
    if not cmd_ok:
        # **两条尝试各自的原因都要能看到**（task14 §6 有一条验收专门盯它）。
        log_autostart_outcome(
            "开启",
            False,
            f"两条路都失败。① {reason}；② {cmd_reason}。{HINT_BLOCKED}",
        )
        return AutostartState(
            enabled=False,
            error=f"{reason}；{cmd_reason}",
            detail=f"快捷方式与 .cmd 都失败：{reason}；{cmd_reason}",
        )

    log_autostart_outcome(
        "开启",
        True,
        f"回退方式生效（{command}）；① 快捷方式的原因：{reason}；② 回退脚本：成功",
    )
    log_autostart_outcome("开启", False, HINT_BLOCKED)
    return AutostartState(
        enabled=True,
        status_text=f"{TEXT_ENABLED}{TEXT_FALLBACK}",
        detail=f"回退方式生效：{cmd_reason}（快捷方式的原因：{reason}）",
    )


def disable(resolved: Path | None = None) -> AutostartState:
    """关闭自启：删掉自启项（`.lnk` 与 `.cmd` 都删）。

    | 情形 | 结果 |
    | --- | --- |
    | 删掉了 | `enabled=False` + `未开启` |
    | **本来就没有** | **幂等成功**：同样返回 `未开启`，`error` 是 `None` |
    | **删不掉** | `enabled=False` + `error` 带系统原文（**绝不静默吞掉**） |

    ⚠ 删掉的是 `deskpet.lnk` / `deskpet.cmd` **这两个名字**，不做通配删除——
    启动文件夹里可能还有用户自己的启动项。
    """
    directory = resolved or _startup_dir_cached()
    removed: list[str] = []
    for path in (directory / SHORTCUT_NAME, directory / FALLBACK_NAME):
        try:
            existed = path.is_file()
            _remove(path)
        except OSError as exc:
            log_autostart_outcome("关闭", False, f"删不掉 {path}：{exc}")
            return AutostartState(
                enabled=True,
                status_text=TEXT_ENABLED,
                error=f"删除自启项失败（{path}）：{exc}",
                detail=f"删除 {path} 时系统报错：{exc}",
            )
        if existed:
            removed.append(path.name)

    if removed:
        log_autostart_outcome("关闭", True, f"已关闭（删掉 {'、'.join(removed)}）")
    else:
        log_autostart_outcome("关闭", True, "关闭时本来就没有自启项（幂等成功）")
    return AutostartState(
        enabled=False,
        status_text=TEXT_DISABLED,
        detail=f"删掉：{'、'.join(removed) if removed else '（本来就没有）'}",
    )


def cleanup_legacy_markers(resolved: Path | None = None) -> list[str]:
    """删掉早期版本**误放在启动文件夹里**的标记文件，返回删掉的文件名。

    ⚠ 与 `query_state()` 的关系是**单向**的：本函数**只由用户明确操作**触发
    （打开设置窗口、开启、关闭各调一次），"读状态"那条路径**绝不调它**
    ——否则"看一眼状态"就变成了写盘（task14 §5 的头号红线）。

    名字是写死的清单（`LEGACY_MARKERS`）：**不做通配删除**，
    那会把用户自己的启动项一起清掉。删不掉**只记日志**（清理类操作，尽力而为）。
    """
    directory = resolved or _startup_dir_cached()
    removed: list[str] = []
    for name in LEGACY_MARKERS:
        path = directory / name
        try:
            if not path.is_file():
                continue
            _remove(path)
        except OSError as exc:
            logger.warning("自启：遗留标记文件 %s 没能删掉：%s", path, exc)
            continue
        removed.append(name)
    if removed:
        logger.info("自启：已清理启动文件夹里的遗留标记文件：%s", "、".join(removed))
    return removed


def status_line(state: AutostartState) -> str:
    """状态栏要显示的那一行：前缀 + 状态文字（**附录 B 的契约**）。"""
    return f"{STATUS_PREFIX}{state.status_text}"
