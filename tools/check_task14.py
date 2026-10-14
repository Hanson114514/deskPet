"""task14 自检（offscreen）：开机自启的状态、开关、回退、只读与幂等。

按 agent.md §A.3：**一个脚本、共用一个 `check()`、按节输出**。
结果**写文件**（`data/task14_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task14.py

## ⚠ 这个脚本**绝不碰真实的「启动」文件夹**

所有读写都发生在自检自己造的假启动文件夹里（`data/task14_scratch/startup`），
用 `autostart` 的 `resolved` 形参注入。所以：

- 跑自检**不会**给用户装上或卸掉任何自启项；
- 只有 §9 那条"真启动文件夹在哪"是只读地问系统，
  并且只断言"它等于 `%APPDATA%` 推导出来的那个"（不变量，§4.11）。

## 这一份能验什么、不能验什么

**能验**（task14 §6 里可自动化的那些）：

1. 全新状态：**未开启** → 勾选 → `deskpet.lnk` 出现且指向当前程序 → **已开启**；
2. 取消勾选 → 文件消失；**重复取消** → 幂等成功、`error` 为 `None`；
3. **"文件在不在" ≠ "功能能不能用"**：把 `.lnk` 改成指向记事本 / 清空成 0 字节 →
   两种状态文案**逐字符**对得上；
4. 工作目录不符 → `已开启［工作目录对不上，重新开启即可修正］`；
5. **只读路径绝不写盘**：把写函数换成会抛 `PermissionError` 的桩，
   证明"打开设置窗口"走不到写路径（§4.2 指定的验证方式）；
6. 注入"快捷方式失败 + `.cmd` 也失败" → 勾选**回滚** + 状态栏 `设置失败：<原因>`
   + **两条尝试的原因都能看到**；
7. 注入"删除失败" → **报错并带出原因**（不是默默弹回来）；
8. **启动文件夹里恰好只有一个我们的文件**（没有 `.lnk` + `.cmd` 并存）；
9. 手工放一个遗留标记文件 → 程序**主动删除**它，且不影响自启状态；
10. 源码级检查：读/写函数分开、不写死路径、`.ps1` 是 UTF-8 with BOM。

**不能验**（交给 `check_task14_real.py`）：真在**真的**启动文件夹里建出 `.lnk`、
注销重登后真的自启、点 `打开启动文件夹` 真的弹出资源管理器。
"""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tokenize
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task14_check.txt"

#: 自检自己造东西的地方（**不碰真实启动文件夹、不碰 `assets/`**）。
SCRATCH = ROOT / "data" / "task14_scratch"

#: 自检用的假启动文件夹（每个小节用前会清干净）。
FAKE_STARTUP = SCRATCH / "startup"

#: `.ps1` 的 BOM 前缀。
BOM = b"\xef\xbb\xbf"

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
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，多个 task 都踩过）。

    ⚠ 它把 token 用空格拼起来，所以拿**带点的字面量**去 `in` 它永远匹配不上。
    """
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


def squashed_of(code: str) -> str:
    """把一段**已经 `_code_only()` 过**的代码去掉空白（判调用用这个形式）。

    ⚠ 为什么必须有它：`_code_only()` 是"用空格把 token 拼起来"，
    于是 `read_shortcut(` 会变成 `read_shortcut (`、`QProcess.startDetached` 会变成
    `QProcess . startDetached`。拿**带括号/带点**的字面量直接去 `in` 它
    **永远匹配不上**——task06/08/11/12/13 都在这上面栽过一次（§4.11）。
    """
    return code.replace(" ", "")


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


# ------------------------------------------------------------------ 准备

#: **程序目录**覆盖：自检脚本住在 `tools/`，"程序目录"本来会变成 `tools/`
#: （§4.13），于是 `tools/*.ps1` 与 `<程序目录>/run.py` 全都找不到。
#: `paths.override_dirs` 正是为此存在的（不要去改路径规则本身）。
import contextlib  # noqa: E402

from deskpet import autostart, paths  # noqa: E402
from deskpet import settings as settings_mod  # noqa: E402


@contextlib.contextmanager
def program_at_root():
    """把"程序目录"临时指回仓库根（自检住在 `tools/`，见 §4.13）。"""
    with paths.override_dirs(program=ROOT, resource=ROOT):
        yield


# ------------------------------------------------------------------ 1. 启动文件夹解析


def section_startup_dir() -> None:
    section("§1 启动文件夹的解析（只读地问系统）")
    with program_at_root():
        resolved = autostart.startup_dir()
        guessed = autostart._startup_relpath()

        check(
            "startup_dir() 与 %APPDATA% 推导的结果一致",
            os.path.normcase(str(resolved)) == os.path.normcase(str(guessed)),
            f"system={resolved} appdata={guessed}",
        )
        check(
            "启动文件夹确实存在（是目录）",
            resolved.is_dir(),
            str(resolved),
        )
        check(
            "它是 %APPDATA% 底下那一条（不变量，不写死盘符）",
            str(resolved).lower().startswith(os.environ.get("APPDATA", "\0").lower()),
            f"resolved={resolved}",
        )
        # 幂等：连问两次给同一个答案（缓存不改变语义）
        check(
            "重复调用给同一个答案",
            autostart.startup_dir() == resolved,
            str(autostart.startup_dir()),
        )
        # 自启项路径拼得对
        check(
            "shortcut_path() 是 <启动文件夹>/deskpet.lnk",
            autostart.shortcut_path(resolved).name == autostart.SHORTCUT_NAME
            and autostart.shortcut_path(resolved).parent == resolved,
            str(autostart.shortcut_path(resolved)),
        )
        check(
            "回退项是 deskpet.cmd（task14 §4 逐字约定）",
            autostart.FALLBACK_NAME == "deskpet.cmd",
            autostart.FALLBACK_NAME,
        )


# ------------------------------------------------------------------ 2. 自我定位


def section_self_location() -> None:
    section("§2 自我定位（按当前进程反推，绝不写死）")
    with program_at_root():
        target, arguments = paths.autostart_target()
        check(
            "源码运行时目标是 pythonw.exe（不带控制台的那个）",
            Path(target).name.lower() == "pythonw.exe",
            target,
        )
        check(
            "源码运行时参数是 <程序目录>/run.py",
            Path(arguments).name == "run.py" and Path(arguments).parent == ROOT,
            arguments,
        )
        check(
            "目标文件真的存在",
            Path(target).is_file(),
            target,
        )

        # 打包分支：`is_frozen()` 为真时用 `sys.executable`
        original = paths.is_frozen
        fake_exe = str(ROOT / "data" / "task14_scratch" / "deskpet.exe")
        try:
            paths.is_frozen = lambda: True
            import sys as _sys

            saved = _sys.executable
            try:
                _sys.executable = fake_exe
                frozen_target, frozen_args = paths.autostart_target()
            finally:
                _sys.executable = saved
        finally:
            paths.is_frozen = original
        check(
            "打包运行时目标是 exe 自己、参数为空",
            frozen_target == fake_exe and frozen_args == "",
            f"target={frozen_target!r} args={frozen_args!r}",
        )

        # **不许写死**：源码里搜不到仓库路径或 pythonw 的绝对路径
        code = _squashed("paths.py")
        check(
            "paths.py 里没有写死仓库路径",
            "c:\\\\projects" not in code.lower() and "deskpet\\\\run.py" not in code.lower(),
        )
        check(
            "paths.py 里没有写死解释器绝对路径",
            "c:\\\\python" not in code.lower(),
        )


# ------------------------------------------------------------------ 3. 开 / 关往返


def section_roundtrip() -> None:
    section("§3 开 / 关往返（在自检自己的假启动文件夹里）")
    with program_at_root():
        fake = _fresh_dir(FAKE_STARTUP)

        initial = autostart.query_state(fake)
        check(
            "全新状态是「未开启」",
            initial.enabled is False and initial.status_text == autostart.TEXT_DISABLED,
            f"enabled={initial.enabled} text={initial.status_text!r}",
        )
        check(
            "全新状态下启动文件夹是空的",
            list(fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )

        enabled = autostart.enable(fake)
        check(
            "勾选后 enabled=True",
            enabled.enabled is True,
            f"enabled={enabled.enabled} text={enabled.status_text!r}",
        )
        check(
            "勾选后状态文案就是「已开启」",
            enabled.status_text == autostart.TEXT_ENABLED,
            repr(enabled.status_text),
        )
        check(
            "勾选后 error 是 None",
            enabled.error is None,
            repr(enabled.error),
        )

        files = sorted(p.name for p in fake.iterdir())
        check(
            "启动文件夹里**恰好只有一个**我们的文件",
            files == [autostart.SHORTCUT_NAME],
            str(files),
        )

        link = fake / autostart.SHORTCUT_NAME
        info = autostart.read_shortcut(link)
        target, arguments = paths.autostart_target()
        check(
            "快捷方式的目标就是当前程序（pythonw.exe）",
            os.path.normcase(info.target) == os.path.normcase(target),
            f"actual={info.target} expected={target}",
        )
        check(
            "快捷方式的参数就是 run.py",
            os.path.normcase(info.arguments) == os.path.normcase(arguments),
            f"actual={info.arguments!r} expected={arguments!r}",
        )
        check(
            "快捷方式的工作目录是程序目录",
            os.path.normcase(info.working_dir) == os.path.normcase(str(ROOT)),
            f"actual={info.working_dir}",
        )

        requery = autostart.query_state(fake)
        check(
            "再查一次仍然「已开启」（判据是功能可用，不是文件在）",
            requery.enabled is True and requery.status_text == autostart.TEXT_ENABLED,
            f"enabled={requery.enabled} text={requery.status_text!r}",
        )

        disabled = autostart.disable(fake)
        check(
            "取消勾选后 enabled=False、文案「未开启」",
            disabled.enabled is False and disabled.status_text == autostart.TEXT_DISABLED,
            f"enabled={disabled.enabled} text={disabled.status_text!r}",
        )
        check(
            "取消勾选后文件消失了",
            list(fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )
        check(
            "取消勾选不算失败（error 是 None）",
            disabled.error is None,
            repr(disabled.error),
        )

        again = autostart.disable(fake)
        check(
            "**重复取消勾选（本来就没有）→ 幂等成功**",
            again.error is None
            and again.status_text == autostart.TEXT_DISABLED
            and again.enabled is False,
            f"error={again.error!r} text={again.status_text!r}",
        )
        check(
            "重复取消之后那里仍然是空的",
            list(fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )

        # 全流程都没在启动文件夹里留标记/状态文件
        check(
            "开→关全程没有出现状态/标记类文件",
            sorted(p.name for p in fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )


# ------------------------------------------------------------------ 4. 状态判定


def section_state_detection() -> None:
    section("§4 状态判定：'文件在' ≠ '功能能用'")
    with program_at_root():
        fake = _fresh_dir(FAKE_STARTUP)
        target, arguments = paths.autostart_target()

        # ① 指向别的程序（拿记事本当"别的程序"）
        autostart.enable(fake)
        link = fake / autostart.SHORTCUT_NAME
        other = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe")
        _make_shortcut(link, other, "", str(ROOT))
        state = autostart.query_state(fake)
        check(
            "指向别处 → **未开启**",
            state.enabled is False,
            f"enabled={state.enabled}",
        )
        check(
            "指向别处 → 文案是「未开启［它指向的是别的地方：…］」",
            state.status_text.startswith("未开启［它指向的是别的地方：")
            and state.status_text.endswith("］")
            and other.lower() in state.status_text.lower(),
            repr(state.status_text),
        )

        # ② 0 字节空壳（模拟"创建接口不报错但留下空壳"）
        link.write_bytes(b"")
        state = autostart.query_state(fake)
        check(
            "0 字节空壳 → **未开启**",
            state.enabled is False,
            f"enabled={state.enabled}",
        )
        check(
            "0 字节空壳 → 文案**逐字符**等于那一句",
            state.status_text == autostart.TEXT_BROKEN,
            repr(state.status_text),
        )
        check(
            "标准文案逐字符：未开启［启动文件夹里那个快捷方式是坏的（读不出目标），"
            "取消勾选再勾选一次即可重建］",
            autostart.TEXT_BROKEN
            == "未开启［启动文件夹里那个快捷方式是坏的（读不出目标），取消勾选再勾选一次即可重建］",
            autostart.TEXT_BROKEN,
        )

        # ③ 工作目录不符（目标与参数都对）
        _make_shortcut(link, target, arguments, str(ROOT / "data"))
        state = autostart.query_state(fake)
        check(
            "工作目录不符 → 仍然是**已开启**（功能能用）",
            state.enabled is True,
            f"enabled={state.enabled} text={state.status_text!r}",
        )
        check(
            "工作目录不符 → 文案是「已开启［工作目录对不上，重新开启即可修正］」",
            state.status_text == autostart.TEXT_WORKDIR,
            repr(state.status_text),
        )
        check(
            "标准文案逐字符：已开启［工作目录对不上，重新开启即可修正］",
            autostart.TEXT_WORKDIR == "已开启［工作目录对不上，重新开启即可修正］",
            autostart.TEXT_WORKDIR,
        )

        # ④ 修好之后回到干净的「已开启」
        _make_shortcut(link, target, arguments, str(ROOT))
        state = autostart.query_state(fake)
        check(
            "工作目录改回来 → 干干净净的「已开启」",
            state.enabled is True and state.status_text == autostart.TEXT_ENABLED,
            repr(state.status_text),
        )

        # ⑤ `.cmd` 回退形态也算开启，且文案带那一句
        fake2 = _fresh_dir(SCRATCH / "startup_cmd")
        (fake2 / autostart.FALLBACK_NAME).write_text(
            "@echo off\nstart \"\" " + autostart._command_line(target, arguments) + "\n",
            encoding="utf-8",
        )
        state = autostart.query_state(fake2)
        check(
            "只有 .cmd 时也算**已开启**",
            state.enabled is True,
            f"enabled={state.enabled} text={state.status_text!r}",
        )
        check(
            "回退方式的文案带「［回退方式：登录时会闪一下命令行窗口］」",
            state.status_text == f"{autostart.TEXT_ENABLED}{autostart.TEXT_FALLBACK}",
            repr(state.status_text),
        )
        check(
            "回退方式那句话**逐字符**对得上",
            autostart.TEXT_FALLBACK == "［回退方式：登录时会闪一下命令行窗口］",
            autostart.TEXT_FALLBACK,
        )

        # ⑥ 状态栏前缀
        check(
            "状态栏前缀是「开机自启：」（附录 B）",
            autostart.STATUS_PREFIX == "开机自启：",
            repr(autostart.STATUS_PREFIX),
        )
        check(
            "status_line() = 前缀 + 状态文字",
            autostart.status_line(state) == f"开机自启：{state.status_text}",
            repr(autostart.status_line(state)),
        )


def _make_shortcut(link: Path, target: str, arguments: str, working_dir: str) -> None:
    """用产品自己的脚本造一个快捷方式（**测的就是产品用的那条路**）。

    直接调 PowerShell 而不是用 Python 造假 `.lnk`：`.lnk` 是二进制格式，
    能造它的只有系统那一套；用别的方式造出来的文件未必是产品要读的那种。
    """
    args = [
        "-Path",
        str(link),
        "-Target",
        target,
        "-WorkingDirectory",
        working_dir,
    ]
    if arguments:
        args += ["-Arguments", arguments]
    completed = subprocess.run(
        [
            autostart._powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(autostart.make_shortcut_script()),
            *args,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:  # pragma: no cover - 造不出来就是环境问题
        raise RuntimeError(f"造快捷方式失败：{completed.stderr}")


# ------------------------------------------------------------------ 5. 只读路径


def section_readonly() -> None:
    section("§5 只读路径验证：打开设置窗口走不到写路径（§4.2 指定的方式）")

    with program_at_root():
        fake = _fresh_dir(FAKE_STARTUP)
        autostart.enable(fake)
        link = fake / autostart.SHORTCUT_NAME
        before = _sha(link)

        # **抛异常的桩**钉住写路径：只要 `query_state()` 碰到它就会当场炸出来。
        # 这不是"比对文件内容"，而是让"碰了写"变成一个可见的失败（§4.2）。
        calls: list[str] = []

        def _boom(*args, **kwargs):
            calls.append("called")
            raise PermissionError("只读路径不许写盘（自检桩）")

        saved = {
            "enable": autostart.enable,
            "disable": autostart.disable,
            "cleanup": autostart.cleanup_legacy_markers,
            "_write_shortcut": autostart._write_shortcut,
            "_write_fallback": autostart._write_fallback,
            "_remove": autostart._remove,
            "_write_text": autostart._write_text,
        }
        autostart.enable = _boom
        autostart.disable = _boom
        autostart.cleanup_legacy_markers = _boom
        autostart._write_shortcut = _boom
        autostart._write_fallback = _boom
        autostart._remove = _boom
        autostart._write_text = _boom
        try:
            failed = ""
            try:
                for _ in range(3):
                    autostart.query_state(fake)
            except PermissionError as exc:  # pragma: no cover - 走到了就说明写路径被碰了
                failed = str(exc)
            except Exception as exc:  # pragma: no cover
                failed = f"{type(exc).__name__}: {exc}"
        finally:
            autostart.enable = saved["enable"]
            autostart.disable = saved["disable"]
            autostart.cleanup_legacy_markers = saved["cleanup"]
            autostart._write_shortcut = saved["_write_shortcut"]
            autostart._write_fallback = saved["_write_fallback"]
            autostart._remove = saved["_remove"]
            autostart._write_text = saved["_write_text"]

        check(
            "query_state() 一次都没碰到写函数（抛异常的桩没被调用）",
            failed == "" and calls == [],
            f"failed={failed!r} calls={len(calls)}",
        )
        check(
            "连查 3 次之后快捷方式一个字节都没变",
            _sha(link) == before,
            f"before={before[:12]} after={_sha(link)[:12]}",
        )

        # 打开**真的设置窗口**也走不到写路径（这一条才是验收的原话）
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        assert app is not None

        saved2 = {
            "enable": autostart.enable,
            "disable": autostart.disable,
            "_write_shortcut": autostart._write_shortcut,
            "_write_fallback": autostart._write_fallback,
            "_remove": autostart._remove,
        }
        window_calls: list[str] = []

        def _window_boom(*args, **kwargs):
            window_calls.append("called")
            raise PermissionError("打开设置窗口不许写盘（自检桩）")

        autostart.enable = _window_boom
        autostart.disable = _window_boom
        autostart._write_shortcut = _window_boom
        autostart._write_fallback = _window_boom
        autostart._remove = _window_boom
        window_failed = ""
        config_before = _optional_sha(paths.config_path())
        # 界面查的是**真实**启动文件夹（它没有 `resolved` 形参）。
        # 自检不往那里写任何东西，所以这里只记下"打开窗口之前那里是什么样"，
        # 稍后比对"打开窗口之后它一个字节都没变"（§6：先快照、后比对）。
        real_startup = autostart.startup_dir()
        real_before = _dir_snapshot(real_startup)
        try:
            from deskpet.config import ConfigStore
            from deskpet.settings import SettingsWindow

            store = ConfigStore()
            store.load()
            dialog = SettingsWindow(store, current_pet="weedy", parent=None)
            dialog.reload_from_config()
            dialog.refresh_status()
            checkbox = dialog._widgets.get(settings_mod.AUTOSTART_KEY)
            text = checkbox.isChecked() if checkbox is not None else None
            expected_checked = autostart.query_state(real_startup).enabled
        except PermissionError as exc:  # pragma: no cover - 走到了就说明写路径被碰了
            window_failed = str(exc)
            expected_checked = None
        except Exception as exc:  # pragma: no cover
            window_failed = f"{type(exc).__name__}: {exc}"
            expected_checked = None
        finally:
            autostart.enable = saved2["enable"]
            autostart.disable = saved2["disable"]
            autostart._write_shortcut = saved2["_write_shortcut"]
            autostart._write_fallback = saved2["_write_fallback"]
            autostart._remove = saved2["_remove"]

        check(
            "打开设置窗口（含刷新状态）一次都没碰到写函数",
            window_failed == "" and window_calls == [],
            f"failed={window_failed!r} calls={len(window_calls)}",
        )
        check(
            "**打开窗口这个动作没有改动启动文件夹内容**（对整个目录做快照比对）",
            _dir_snapshot(real_startup) == real_before,
            f"before={real_before} after={_dir_snapshot(real_startup)}",
        )
        check(
            "打开窗口后那里仍然没有我们的自启项（自检不该给用户装上自启）",
            not (real_startup / autostart.SHORTCUT_NAME).exists()
            and not (real_startup / autostart.FALLBACK_NAME).exists(),
            str(sorted(p.name for p in real_startup.iterdir())),
        )
        check(
            "打开设置窗口也不该创建 config.json（读操作只读）",
            _optional_sha(paths.config_path()) == config_before,
            f"before={config_before} after={_optional_sha(paths.config_path())}",
        )
        check(
            "打开窗口后勾选态 = 系统里的真实状态（**不是**恒真的默认值）",
            text == expected_checked,
            f"checked={text} 真实状态={expected_checked}",
        )
        dialog.close()  # type: ignore[possibly-undefined]


def _dir_snapshot(directory: Path) -> dict[str, str]:
    """整个目录的快照：`文件名 -> 内容哈希`（比"看某个文件还在不在"硬，§6 原话）。"""
    snapshot: dict[str, str] = {}
    try:
        for entry in sorted(directory.iterdir()):
            if entry.is_file():
                try:
                    snapshot[entry.name] = _sha(entry)
                except OSError:  # pragma: no cover
                    snapshot[entry.name] = "(读不到)"
            else:
                snapshot[entry.name + "/"] = "(目录)"
    except OSError:  # pragma: no cover
        return snapshot
    return snapshot


def _optional_sha(path: Path) -> str | None:
    """文件存在就给哈希，不存在给 `None`（"不该被创建"也是一种判据）。"""
    try:
        return _sha(path) if path.is_file() else None
    except OSError:  # pragma: no cover
        return None


# ------------------------------------------------------------------ 6. 失败注入


def section_failure_injection() -> None:
    section("§6 失败注入：写失败必须报错、回滚，且两条原因都带出来")

    with program_at_root():
        # ① 快捷方式失败 + .cmd 也失败 → 两条原因都在
        fake = _fresh_dir(FAKE_STARTUP)
        original_script = autostart.make_shortcut_script
        original_write_text = autostart._write_text

        def _missing_script() -> Path:
            return SCRATCH / "definitely-not-here.ps1"

        def _refuse_write(path: Path, text: str) -> None:
            raise PermissionError("[Errno 13] Permission denied: " + str(path))

        autostart.make_shortcut_script = _missing_script
        autostart._write_text = _refuse_write
        try:
            state = autostart.enable(fake)
        finally:
            autostart.make_shortcut_script = original_script
            autostart._write_text = original_write_text

        check(
            "两条路都失败 → enabled=False",
            state.enabled is False,
            f"enabled={state.enabled}",
        )
        check(
            "两条路都失败 → error 同时带出两条尝试的原因",
            state.error is not None
            and "生成快捷方式" in state.error
            and "写回退脚本失败" in state.error,
            repr(state.error),
        )
        check(
            "两条路都失败 → 带出系统原文（Permission denied）",
            "Permission denied" in (state.error or ""),
            repr(state.error),
        )
        check(
            "两条路都失败 → 启动文件夹里什么都没留下",
            list(fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )

        # ② 界面上：勾选失败 → 勾选回滚 + 状态栏 `设置失败：<原因>`
        from deskpet.config import ConfigStore
        from deskpet.settings import AutostartOutcome, SettingsWindow

        store = ConfigStore()
        store.load()
        dialog = SettingsWindow(store, current_pet="weedy", parent=None)

        refused = AutostartOutcome(
            ok=False,
            enabled=False,
            status_text=autostart.TEXT_DISABLED,
            reason="删除自启项失败（C:/x/deskpet.lnk）：[Errno 13] Permission denied",
        )
        dialog.set_autostart_handler(lambda enabled: refused)
        checkbox = dialog._widgets[settings_mod.AUTOSTART_KEY]
        check(
            "失败的处理器装好时，初值填充不会触发它",
            checkbox.isChecked() is False,
            f"checked={checkbox.isChecked()}",
        )
        # 模拟用户勾上（setChecked 会走到 `_on_check`）
        checkbox.setChecked(True)
        check(
            "写失败 → **勾选回滚**（不是显示假成功）",
            checkbox.isChecked() is False,
            f"checked={checkbox.isChecked()}",
        )
        status = dialog._status_label.text()
        check(
            "写失败 → 状态栏是「设置失败：<系统原因>」",
            status.startswith(settings_mod.FAILED_PREFIX) and "Permission denied" in status,
            repr(status),
        )

        # ③ 成功的处理器：勾上之后状态栏回到 `开机自启：…`
        ok_outcome = AutostartOutcome(
            ok=True,
            enabled=True,
            status_text=autostart.TEXT_ENABLED,
            reason="",
        )
        dialog.set_autostart_handler(lambda enabled: ok_outcome)
        dialog._applying = True
        try:
            checkbox.setChecked(False)
        finally:
            dialog._applying = False
        checkbox.setChecked(True)
        status = dialog._status_label.text()
        check(
            "写成功 → 状态栏 `开机自启：已开启`",
            status == f"开机自启：{autostart.TEXT_ENABLED}",
            repr(status),
        )
        check(
            "写成功 → 勾选保持勾上",
            checkbox.isChecked() is True,
            f"checked={checkbox.isChecked()}",
        )

        # ④ 处理器抛异常也要报出来（不许静默）
        def _explode(enabled: bool) -> AutostartOutcome:
            raise RuntimeError("处理器自己炸了")

        dialog.set_autostart_handler(_explode)
        dialog._applying = True
        try:
            checkbox.setChecked(False)
        finally:
            dialog._applying = False
        checkbox.setChecked(True)
        status = dialog._status_label.text()
        check(
            "处理器抛异常 → 状态栏仍显示 `设置失败：…` 而不是假成功",
            status.startswith(settings_mod.FAILED_PREFIX) and "RuntimeError" in status,
            repr(status),
        )

        # ⑤ **没装处理器**时：勾选既不假装成功、也不崩
        dialog.set_autostart_handler(None)
        dialog._applying = True
        try:
            checkbox.setChecked(False)
        finally:
            dialog._applying = False
        checkbox.setChecked(True)
        status = dialog._status_label.text()
        check(
            "没装处理器 → 状态栏说失败（绝不假装成功）",
            status.startswith(settings_mod.FAILED_PREFIX),
            repr(status),
        )
        dialog.close()

        # ⑥ 删除失败（`disable` 里抛 PermissionError）→ **报错并带出原因**
        fake2 = _fresh_dir(SCRATCH / "startup_del")
        autostart.enable(fake2)
        original_remove = autostart._remove

        def _refuse_remove(path: Path) -> None:
            raise PermissionError("[WinError 5] 拒绝访问。: " + str(path))

        autostart._remove = _refuse_remove
        try:
            state = autostart.disable(fake2)
        finally:
            autostart._remove = original_remove

        check(
            "删不掉 → error **不是 None**（绝不静默吞掉）",
            state.error is not None,
            repr(state.error),
        )
        check(
            "删不掉 → 带出系统原文",
            "拒绝访问" in (state.error or "") or "WinError 5" in (state.error or ""),
            repr(state.error),
        )
        check(
            "删不掉 → 状态仍然是「已开启」（没删掉就是还开着）",
            state.enabled is True,
            f"enabled={state.enabled} text={state.status_text!r}",
        )
        check(
            "删不掉的失败也走 `设置失败：…` 那条界面路径",
            state.error is not None and state.error.startswith("删除自启项失败"),
            repr(state.error),
        )


# ------------------------------------------------------------------ 7. 启动文件夹里只有一个文件


def section_single_file() -> None:
    section("§7 「启动」文件夹里恰好只有一个我们的文件")
    with program_at_root():
        fake = _fresh_dir(FAKE_STARTUP)

        # 先手工造一个残留的 .cmd，再开启 → 清掉它，只留 .lnk
        leftover = fake / autostart.FALLBACK_NAME
        leftover.write_text("@echo off\nrem 残留\n", encoding="utf-8")
        state = autostart.enable(fake)
        files = sorted(p.name for p in fake.iterdir())
        check(
            "残留 .cmd + 开启 → 那里**恰好只有 deskpet.lnk**",
            files == [autostart.SHORTCUT_NAME],
            str(files),
        )
        check(
            "清掉残留下的状态是干净的「已开启」",
            state.status_text == autostart.TEXT_ENABLED,
            repr(state.status_text),
        )

        # 反向：先有 .lnk，再手工补一个 .cmd，然后关闭 → 两个都要没
        (fake / autostart.FALLBACK_NAME).write_text("@echo off\nrem 手工补的\n", encoding="utf-8")
        autostart.disable(fake)
        check(
            "关闭时 `.lnk` 与 `.cmd` **都**删掉",
            list(fake.iterdir()) == [],
            str([p.name for p in fake.iterdir()]),
        )

        # `.cmd` 回退那条路：**成功时也只留一个文件**
        fake2 = _fresh_dir(SCRATCH / "startup_fb")
        original_script = autostart.make_shortcut_script
        autostart.make_shortcut_script = lambda: SCRATCH / "definitely-not-here.ps1"
        try:
            state = autostart.enable(fake2)
        finally:
            autostart.make_shortcut_script = original_script
        files2 = sorted(p.name for p in fake2.iterdir())
        check(
            "回退生效时那里也**恰好只有一个**文件（deskpet.cmd）",
            files2 == [autostart.FALLBACK_NAME],
            str(files2),
        )
        check(
            "回退生效时状态是「已开启」+ 回退提示",
            state.enabled is True
            and state.status_text == f"{autostart.TEXT_ENABLED}{autostart.TEXT_FALLBACK}",
            repr(state.status_text),
        )
        check(
            "回退生效时 error 是 None（这是**成功**，不是失败）",
            state.error is None,
            repr(state.error),
        )
        # 回退项内容真的指着我们
        check(
            ".cmd 的内容里有我们的目标与参数",
            autostart._command_points_to_target(
                fake2 / autostart.FALLBACK_NAME, *paths.autostart_target()
            ),
            (fake2 / autostart.FALLBACK_NAME).read_text(encoding="utf-8").strip(),
        )

        # 再从回退切回快捷方式：回退项要被清掉
        state = autostart.enable(fake2)
        files3 = sorted(p.name for p in fake2.iterdir())
        check(
            "从回退切回快捷方式 → 只剩 .lnk（不留 .cmd）",
            files3 == [autostart.SHORTCUT_NAME],
            str(files3),
        )
        check(
            "切回来之后状态回到干净的「已开启」",
            state.status_text == autostart.TEXT_ENABLED,
            repr(state.status_text),
        )


# ------------------------------------------------------------------ 8. 遗留标记清理


def section_legacy_cleanup() -> None:
    section("§8 启动文件夹清理：遗留标记文件要被主动删掉")
    with program_at_root():
        fake = _fresh_dir(FAKE_STARTUP)
        autostart.enable(fake)
        before_state = autostart.query_state(fake)

        # 手工放一个旧版遗留的标记文件
        marker = fake / autostart.LEGACY_MARKERS[0]
        marker.write_text("legacy\n", encoding="utf-8")
        check(
            "手工放进去的标记文件确实在那儿",
            marker.is_file(),
            str(marker),
        )

        removed = autostart.cleanup_legacy_markers(fake)
        check(
            "程序**主动删掉**它",
            not marker.exists(),
            f"exists={marker.exists()} removed={removed}",
        )
        check(
            "返回的名单里有它",
            autostart.LEGACY_MARKERS[0] in removed,
            str(removed),
        )
        after_state = autostart.query_state(fake)
        check(
            "**不影响自启状态**",
            after_state.enabled == before_state.enabled
            and after_state.status_text == before_state.status_text,
            f"before={before_state.status_text!r} after={after_state.status_text!r}",
        )
        check(
            "清理之后那里仍然只有那一个自启项文件",
            sorted(p.name for p in fake.iterdir()) == [autostart.SHORTCUT_NAME],
            str([p.name for p in fake.iterdir()]),
        )

        # 幂等：没有标记文件时什么都不做、也不报错
        again = autostart.cleanup_legacy_markers(fake)
        check(
            "没有遗留文件时清理是幂等的空操作",
            again == [],
            str(again),
        )

        # **不做通配删除**：用户自己的启动项必须原封不动
        user_item = fake / "用户的启动项.lnk"
        user_item.write_bytes(b"\x00" * 16)
        autostart.cleanup_legacy_markers(fake)
        check(
            "清理**不碰**用户自己的启动项（不做通配删除）",
            user_item.is_file(),
            str(user_item),
        )
        autostart.disable(fake)
        check(
            "`disable()` 也只删我们那两个名字，用户自己的启动项留着",
            user_item.is_file() and not (fake / autostart.SHORTCUT_NAME).exists(),
            str([p.name for p in fake.iterdir()]),
        )

        # 每个阶段的文件清单（验收要求"从未出现状态/标记类文件"）
        final = sorted(p.name for p in fake.iterdir())
        check(
            "全程结束后那里只剩用户自己那个（我们的一样都没留）",
            final == [user_item.name],
            str(final),
        )
        check(
            "全程**从未**出现状态/标记类文件（清单里没有 LEGACY_MARKERS）",
            not any(name in final for name in autostart.LEGACY_MARKERS),
            str(final),
        )


def _disable_keeps_user_item(fake: Path, user_item: Path) -> bool:
    """（保留给手工排查用）关闭只删我们那两个名字。"""
    autostart.disable(fake)
    return user_item.is_file() and not (fake / autostart.SHORTCUT_NAME).exists()


# ------------------------------------------------------------------ 9. 源码级检查


def section_source() -> None:
    section("§9 源码级检查：读/写分开、不写死、`.ps1` 编码")

    code = _code_only("autostart.py")
    squashed = _squashed("autostart.py")

    check(
        "有 query_state / enable / disable 三个入口",
        all(
            f"def{name}" in squashed
            for name in ("query_state", "enable", "disable", "startup_dir")
        ),
        "query_state / enable / disable / startup_dir",
    )

    # **读状态绝不写盘**：`query_state` 的函数体里不许出现任何写调用。
    body = squashed_of(_function_body("autostart.py", "query_state"))
    forbidden = [
        "unlink(",
        "write_text(",
        "mkdir(",
        "_write_shortcut(",
        "_write_fallback(",
        "cleanup_legacy_markers(",
        "_remove(",
        "write_bytes(",
        "open(",
    ]
    hits = [name for name in forbidden if name in body]
    check(
        "query_state() 的函数体里**没有任何写调用**",
        hits == [],
        f"命中：{hits}",
    )
    check(
        "query_state() 调了只读的 read_shortcut()",
        # ⚠ `_code_only()` 用空格拼 token，所以 `read_shortcut(` 会变成
        # `read_shortcut (` —— 判带括号的调用要用 **squashed** 那种形式（§4.11）。
        "read_shortcut(" in squashed_of(body),
        "read_shortcut( 在 query_state 函数体里",
    )

    # 清理是**独立**函数，不是 query 的副作用
    check(
        "清理是一个独立函数（cleanup_legacy_markers）",
        "defcleanup_legacy_markers" in squashed,
    )
    check(
        "`disable()` 里**没有** except OSError: pass（删不掉必须报错）",
        "exceptOSError:pass" not in squashed,
    )

    # 启动文件夹的推导：向系统要 + %APPDATA% 第二道防线
    check(
        "向系统要启动文件夹（GetFolderPath）",
        "GetFolderPath" in _source_of("autostart.py"),
    )
    check(
        "有 %APPDATA% 推导的第二道防线 + 相对路径常量",
        # ⚠ `os.environ["APPDATA"]` 里的 `"APPDATA"` 是**字符串字面量**，
        # `_code_only()` 会把字符串整段剥掉，所以这一条必须查**原文**
        # （§4.11：按名字搜索前先想清楚那个名字住在代码里还是字符串里）。
        "APPDATA" in _source_of("autostart.py") and "STARTUP_RELATIVE" in code,
    )

    # 子进程结果走文件，不走标准输出解析
    check(
        "子进程结果**写文件再读回**（不解析 stdout）",
        "_result_file_path" in code
        and "_read_result_file" in code
        and "utf-8-sig" in _source_of("autostart.py"),
    )
    check(
        "结果文件**不放**启动文件夹（放临时目录）",
        "tempfile" in code and "mkstemp" in code,
    )

    # `.ps1` 必须显式在 tools/ 下（打包要声明）
    for name in ("make_shortcut.ps1", "read_shortcut.ps1"):
        path = ROOT / "tools" / name
        check(
            f"tools/{name} 存在",
            path.is_file(),
            str(path),
        )
        check(
            f"tools/{name} 是 **UTF-8 with BOM**",
            path.read_bytes()[:3] == BOM,
            f"前 3 字节={path.read_bytes()[:3]!r}",
        )
        check(
            f"tools/{name} 能被 PowerShell 正常执行（无乱码、无 ParserError）",
            _ps_parses(path),
            name,
        )

    # 两个脚本的路径只在 autostart 一处拼
    check(
        "两个 .ps1 的路径真值只在 autostart 一处",
        "make_shortcut.ps1" in _source_of("autostart.py")
        and "read_shortcut.ps1" in _source_of("autostart.py")
        and "make_shortcut.ps1" not in _source_of("settings.py")
        and "read_shortcut.ps1" not in _source_of("settings.py"),
    )

    # 界面**不碰**启动文件夹
    settings_code = _squashed("settings.py")
    check(
        "settings.py 里没有直接操作启动文件夹/快捷方式",
        "WScript" not in settings_code
        and "SHORTCUT_NAME" not in settings_code
        and "startup_dir()" not in settings_code,
    )
    check(
        "settings.py 通过处理器走（set_autostart_handler）",
        "defset_autostart_handler" in settings_code,
    )

    # app.py：每个新窗口都要重装（task12/13 同一条教训）
    app_code = _squashed("app.py")
    check(
        "SettingsController.open() 里**每个新窗口都重装**自启处理器",
        "dialog.set_autostart_handler(self._autostart_handler)" in app_code,
    )
    check(
        "装配层装了处理器（settings_controller.set_autostart_handler）",
        "settings_controller.set_autostart_handler(" in app_code,
    )
    check(
        "清理挂在'用户明确动作'上（打开设置窗口），不是启动路径",
        "autostart_controller.open_hook()" in app_code,
    )

    # `开机自启` 那一行不再带 `（暂未开放）`
    from deskpet.settings import FIELDS, FieldKind

    row = next(f for f in FIELDS if f.kind is FieldKind.CHECK)
    check(
        "`开机自启` 那一行 implemented=True（去掉「（暂未开放）」）",
        row.implemented is True,
        f"implemented={row.implemented}",
    )
    check(
        "`开机自启` 那一行**仍然没有配置键**（它是系统状态）",
        row.key == "",
        f"key={row.key!r}",
    )

    # logger 有自启留痕
    check(
        "logger.py 有自启留痕函数（两条尝试的原因都落日志）",
        "deflog_autostart_outcome" in _squashed("logger.py"),
    )
    check(
        "autostart.py 真的调用了它",
        "log_autostart_outcome(" in squashed,
        "（用 squashed 形式判，见 `squashed_of` 的说明）",
    )
    check(
        "两条尝试的原因都进了日志（同一函数里出现多次）",
        squashed.count("log_autostart_outcome(") >= 3,
        f"出现 {squashed.count('log_autostart_outcome(')} 次",
    )

    # 诊断文案不许写没验证过的承诺（§4.2）
    #
    # ⚠ 注意**不能**拿 `重新开启即可修正` 去搜全文：task14 §4 的**状态文案表**
    # 明文要求 `已开启［工作目录对不上，重新开启即可修正］` 这一句逐字符照做，
    # 它是**产品契约**，不是"没验证过的承诺"。被禁的是**诊断提示**
    # （`HINT_BLOCKED` 那段"最可能的原因 / 逃生门"）里出现这种承诺
    # ——task14 §4 的"诊断文案"那一行说的正是那里。
    hint = _constant_value("autostart.py", "HINT_BLOCKED")
    check(
        "逃生门提示里**没有**「重新开启即可修正」这种没验证过的承诺",
        "重新开启即可修正" not in hint and "重新开启" not in hint,
        repr(hint),
    )
    check(
        "逃生门提示存在（安全软件 / 受控文件夹访问）",
        "受控文件夹访问" in hint and "安全软件" in hint,
    )


def _constant_value(name: str, constant: str) -> str:
    """取一个模块级字符串常量的值（**真读值，不搜文本**）。

    比"在源码里 `in` 一下"可靠得多：带 `#:` 注释的常量常常在**注释里**
    出现同样的措辞（§4.2 那句就正好是这样），搜文本会把注释当代码。
    """
    module = __import__(f"deskpet.{name[:-3]}", fromlist=[constant])
    return str(getattr(module, constant, ""))


def _function_body(module_name: str, func_name: str) -> str:
    """精确取一个**模块级**函数的函数体源码（用 `ast` 定边界，再剥注释与字符串）。

    ⚠ 为什么不用"在拼好的代码里找下一个 `def`"：`_code_only()` 已经丢掉了
    缩进信息，靠字符串找边界会把**后面那些函数**一起圈进来——第一版就是
    这么写的，于是 `query_state` 的"函数体"里出现了 `enable()` 里的
    `_write_shortcut(`，断言报了一堆假 FAIL（**根因不在产品代码里**）。

    `ast` 给的是**行号**，用行号去切**原始源码**是准的。
    """
    import ast

    source = _source_of(module_name)
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            start = node.lineno - 1
            end = node.end_lineno or node.lineno
            return code_only_text("\n".join(lines[start:end]))
    return ""


def code_only_text(source: str) -> str:
    """把一段**源码文本**剥掉注释与字符串（`_code_only()` 的文本版）。"""
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _ps_parses(path: Path) -> bool:
    """问 PowerShell 一句"这个脚本能不能解析"（**不执行**）。

    含中文的 `.ps1` 缺 BOM 时，PowerShell 5.1 会按系统 ANSI 解码 →
    `ParserError`。这一条就是 task14 §6 那条验收的自动化版本。

    ⚠ `[ref]$errors` 的变量**必须先存在**：PowerShell 对不存在的变量做 `[ref]`
    会直接报 `[ref] cannot be applied to a variable that does not exist.`
    ——那样"解析失败"与"命令自己写错了"就分不出来（§4.3：失败路径自己也会失败）。
    """
    script = (
        "$ErrorActionPreference='Stop';"
        "$errors = $null;"
        "$tokens = $null;"
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{path}',"
        "[ref]$tokens, [ref]$errors);"
        "if ($errors.Count -gt 0) { $errors[0].Message; exit 1 } else { 'OK'; exit 0 }"
    )
    completed = subprocess.run(
        [
            autostart._powershell_executable(),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    return completed.returncode == 0


def main() -> int:
    section("task14 自检（offscreen）")
    SCRATCH.mkdir(parents=True, exist_ok=True)
    try:
        section_startup_dir()
        section_self_location()
        section_roundtrip()
        section_state_detection()
        section_readonly()
        section_failure_injection()
        section_single_file()
        section_legacy_cleanup()
        section_source()
    finally:
        _LINES.append("")
    return _finish()


if __name__ == "__main__":
    sys.exit(main())




