"""task14 真平台自检：在**真的**「启动」文件夹里建出 `.lnk`，并核对它真的能启动桌宠。

`check_task14.py` 用假启动文件夹验了逻辑；**这一份验"在真操作系统上到底成不成立"**。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task14_real.py

## ⚠ 这一份会往用户的真实「启动」文件夹里写东西

这是 task14 §6 的验收原话（"勾选 → 「启动」文件夹里出现 `deskpet.lnk`"、
"打开那个 `.lnk` 的属性确认目标正确"），假文件夹验不了。所以：

- 脚本开头**先记下那里原本有什么**，结尾**恢复原样**（`finally` 里收拾）；
- 只碰 `deskpet.lnk` / `deskpet.cmd` **这两个名字**，绝不删用户自己别的启动项；
- 中断（Ctrl+C / 异常）时 `finally` 仍然会把我们造的东西收拾掉。

## 这一份验什么

1. **真的**启动文件夹在哪（向系统问的那一个，与 `%APPDATA%` 推导一致）；
2. 在**真的**那里建出 `deskpet.lnk`，读回目标 = 当前 `pythonw.exe` + `run.py`；
   **而且那个目标真的能跑起来**（用 `QProcess.startDetached` 拉一次桌宠子进程，
   看它是否活过启动阶段——这是"这个快捷方式真的能启动程序"最硬的判据）；
3. 状态文案、`error`、文件清单都对；
4. **恰好只有一个**我们的文件（`.lnk` 与 `.cmd` 不并存）；
5. 真设置窗口（走真 `QApplication`）打开时能读出真实状态、**且不改那里的内容**；
6. 点 `打开启动文件夹` 的动作真的能调 `QDesktopServices`（拦下 URL 验它）；
7. 收尾：把启动文件夹恢复成进来时的样子。
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task14_real.txt"
SCRATCH = ROOT / "data" / "task14_real_scratch"

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


import contextlib  # noqa: E402

from deskpet import autostart, paths  # noqa: E402


@contextlib.contextmanager
def program_at_root():
    """自检住在 `tools/`，"程序目录"会变成 `tools/`（§4.13）→ 临时指回仓库根。

    ⚠ 真平台这份**必须**用真的程序目录：验收要看"'源码运行时指向
    `pythonw.exe` + 启动脚本'"，覆盖成假目录就验不出这句话了。
    仓库根本来就是真的程序目录（`python run.py` 时就是它）。
    """
    with paths.override_dirs(program=ROOT, resource=ROOT):
        yield


# ------------------------------------------------------------------ 启动文件夹


def _snapshot(directory: Path) -> dict[str, int]:
    """目录快照：`文件名 -> 大小`（用来"恢复原样"与"没被改动"的比对）。"""
    snapshot: dict[str, int] = {}
    try:
        for entry in directory.iterdir():
            if entry.is_file():
                snapshot[entry.name] = entry.stat().st_size
    except OSError:  # pragma: no cover
        return snapshot
    return snapshot


def section_startup_dir() -> None:
    section("§1 真实「启动」文件夹在哪")
    with program_at_root():
        real = autostart.startup_dir()
        guessed = autostart._startup_relpath()
        check(
            "向系统问到的启动文件夹与 %APPDATA% 推导一致",
            os.path.normcase(str(real)) == os.path.normcase(str(guessed)),
            f"system={real}",
        )
        check("那个目录真的存在", real.is_dir(), str(real))
        check(
            "快捷方式路径就是 <启动文件夹>/deskpet.lnk",
            autostart.shortcut_path(real) == real / "deskpet.lnk",
            str(autostart.shortcut_path(real)),
        )


# ------------------------------------------------------------------ 真建真读


def section_real_shortcut() -> None:
    section("§2 在**真的**启动文件夹里建出快捷方式并读回")

    with program_at_root():
        real = autostart.startup_dir()
        link = real / autostart.SHORTCUT_NAME
        cmd = real / autostart.FALLBACK_NAME

        # 进来时先确认那里没有我们的自启项（有的话如实报出来，不偷偷删）
        pre_existing = [p.name for p in (link, cmd) if p.exists()]
        check(
            "进来时那里没有我们的自启项（有的话本脚本会先记下来）",
            pre_existing == [],
            f"预先存在：{pre_existing}",
        )

        state = autostart.enable()
        check("真的 enable() 返回 enabled=True", state.enabled is True, f"text={state.status_text!r}")
        check(
            "真的 enable() 之后状态是「已开启」",
            state.status_text == autostart.TEXT_ENABLED,
            repr(state.status_text),
        )
        check("真的 enable() 没有报错", state.error is None, repr(state.error))
        check("`deskpet.lnk` 真的出现在启动文件夹里", link.is_file(), str(link))

        files = sorted(p.name for p in real.iterdir())
        ours = [name for name in files if name in (autostart.SHORTCUT_NAME, autostart.FALLBACK_NAME)]
        check(
            "那里**恰好只有一个**我们的文件",
            ours == [autostart.SHORTCUT_NAME],
            f"我们的：{ours}；那一共：{files}",
        )

        info = autostart.read_shortcut(link)
        target, arguments = paths.autostart_target()
        check(
            "读回的目标 = 当前 pythonw.exe",
            os.path.normcase(info.target) == os.path.normcase(target),
            f"actual={info.target} expected={target}",
        )
        check(
            "读回的参数 = <程序目录>/run.py",
            os.path.normcase(info.arguments) == os.path.normcase(arguments),
            f"actual={info.arguments!r} expected={arguments!r}",
        )
        check(
            "读回的工作目录 = 程序目录",
            os.path.normcase(info.working_dir) == os.path.normcase(str(ROOT)),
            f"actual={info.working_dir}",
        )

        # **最硬的一条**：那个目标真的能把桌宠拉起来
        check(
            "快捷方式的目标**真的能启动桌宠**（拉一个子进程看它活下来）",
            _target_really_starts(info.target, info.arguments),
            f"{info.target} {info.arguments}",
        )

        check(
            "再查一次状态仍然「已开启」",
            autostart.query_state().status_text == autostart.TEXT_ENABLED,
            repr(autostart.query_state().status_text),
        )

        # 关掉 → 文件真的消失
        disabled = autostart.disable()
        check(
            "真的 disable() 之后 enabled=False、文案「未开启」",
            disabled.enabled is False and disabled.status_text == autostart.TEXT_DISABLED,
            f"enabled={disabled.enabled} text={disabled.status_text!r}",
        )
        check("`deskpet.lnk` 真的没了", not link.exists(), str(link))
        again = autostart.disable()
        check(
            "**重复取消勾选（本来就没有）→ 幂等成功**（error 是 None）",
            again.error is None and again.status_text == autostart.TEXT_DISABLED,
            f"error={again.error!r}",
        )
        check(
            "重复取消之后那儿没有我们任何文件",
            not link.exists() and not cmd.exists(),
            f"link={link.exists()} cmd={cmd.exists()}",
        )

        # 重开一次：确认"关掉再打开"这条路是通的（文件真的被删干净）
        state2 = autostart.enable()
        check("关掉之后还能再开启", state2.enabled is True, repr(state2.status_text))
        autostart.disable()
        check("再关一次也干净", not link.exists() and not cmd.exists())


def _target_really_starts(target: str, arguments: str) -> bool:
    """用快捷方式里的目标与参数**真的拉一次桌宠**，看它能不能活下来。

    判据是"进程起来了、并且活过 `--exit-after` 之外的启动阶段"：
    `pythonw.exe` 没有控制台，所以只能靠**进程还在不在**来判断。

    ⚠ 带的参数是产品的 `--exit-after`（自检专用），所以这个子进程会自己退出
    —— 不需要我们去强杀（§4.21：按进程名强杀会误伤用户自己开的 pythonw）。
    """
    if not Path(target).is_file():
        return False
    args = [str(arguments), "--exit-after", "2"] if arguments else ["--exit-after", "2"]
    process = subprocess.Popen(
        [target, *args],
        cwd=str(ROOT),
        env=_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    # 给它 1 秒活着的时间：启动期致命（素材找不到 / 路径错）会在这段时间里退出。
    time.sleep(1.0)
    alive = process.poll() is None
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:  # pragma: no cover - 不该发生（带了 --exit-after）
        process.kill()
    return alive


# ------------------------------------------------------------------ 真设置窗口


def section_real_settings() -> None:
    section("§3 真设置窗口：读出真实状态、且不动启动文件夹")

    os.environ.pop("QT_QPA_PLATFORM", None)
    from PySide6.QtWidgets import QApplication

    from deskpet.config import ConfigStore
    from deskpet.settings import AUTOSTART_KEY, SettingsWindow

    app = QApplication.instance() or QApplication([])

    with program_at_root():
        real = autostart.startup_dir()
        before = _snapshot(real)

        store = ConfigStore()
        store.load()
        dialog = SettingsWindow(store, current_pet="weedy", parent=None)

        checkbox = dialog._widgets.get(AUTOSTART_KEY)
        check("设置窗口里找得到「开机自启」的勾选框", checkbox is not None, str(type(checkbox)))
        check(
            "**默认不勾选**（全新状态下）",
            checkbox is not None and checkbox.isChecked() is False,
            f"checked={checkbox.isChecked() if checkbox is not None else None}",
        )
        check(
            "控件是**可用**的（去掉了「（暂未开放）」）",
            checkbox is not None and checkbox.isEnabled() is True,
            f"enabled={checkbox.isEnabled() if checkbox is not None else None}",
        )
        status = dialog._status_label.text()
        check(
            "状态栏是 `开机自启：未开启`",
            status == f"{autostart.STATUS_PREFIX}{autostart.TEXT_DISABLED}",
            repr(status),
        )
        check(
            "**打开窗口这个动作没有改动启动文件夹内容**",
            _snapshot(real) == before,
            f"before={sorted(before)} after={sorted(_snapshot(real))}",
        )

        # 真的勾一下：装上真正干活的处理器（与 `app.py` 装的是同一个函数）
        from deskpet.app import AutostartController

        controller = AutostartController()
        dialog.set_autostart_handler(controller.apply)
        checkbox.setChecked(True)

        check(
            "勾上之后启动文件夹里真的多出了 `deskpet.lnk`",
            (real / autostart.SHORTCUT_NAME).is_file(),
            str(sorted(p.name for p in real.iterdir())),
        )
        status = dialog._status_label.text()
        check(
            "状态栏变成 `开机自启：已开启`",
            status == f"{autostart.STATUS_PREFIX}{autostart.TEXT_ENABLED}",
            repr(status),
        )
        check(
            "勾选态保持勾上（没有假失败弹回来）",
            checkbox.isChecked() is True,
            f"checked={checkbox.isChecked()}",
        )
        ours = [
            p.name
            for p in real.iterdir()
            if p.name in (autostart.SHORTCUT_NAME, autostart.FALLBACK_NAME)
        ]
        check(
            "那里**恰好只有一个**我们的文件",
            ours == [autostart.SHORTCUT_NAME],
            str(ours),
        )

        # 真取消勾选
        checkbox.setChecked(False)
        check(
            "取消勾选之后 `deskpet.lnk` 真的没了",
            not (real / autostart.SHORTCUT_NAME).exists(),
            str(sorted(p.name for p in real.iterdir())),
        )
        status = dialog._status_label.text()
        check(
            "状态栏回到 `开机自启：未开启`",
            status == f"{autostart.STATUS_PREFIX}{autostart.TEXT_DISABLED}",
            repr(status),
        )

        # 逃生门：点一下 `打开启动文件夹`，拦下 URL 看它是不是 shell:startup
        opened: list[str] = []
        original_open = None
        import deskpet.settings as settings_mod

        original_open = settings_mod.QDesktopServices.openUrl

        def _capture(url):
            opened.append(url.toString())
            return True

        settings_mod.QDesktopServices.openUrl = staticmethod(_capture)
        try:
            dialog.open_startup_folder()
        finally:
            settings_mod.QDesktopServices.openUrl = original_open

        check(
            "点「打开启动文件夹」→ 打开的是 `shell:startup`",
            opened == ["shell:startup"],
            str(opened),
        )

        # 收尾：那里不该剩下我们的东西
        check(
            "设置窗口折腾完之后那里没有我们任何文件",
            not (real / autostart.SHORTCUT_NAME).exists()
            and not (real / autostart.FALLBACK_NAME).exists(),
            str(sorted(p.name for p in real.iterdir())),
        )
        dialog.close()
        app.processEvents()


# ------------------------------------------------------------------ 收尾


def main() -> int:
    section("task14 真平台自检")
    SCRATCH.mkdir(parents=True, exist_ok=True)

    real = autostart.startup_dir()
    link = real / autostart.SHORTCUT_NAME
    cmd = real / autostart.FALLBACK_NAME
    # 进来时那两份文件在不在（**我们只负责把我们自己造的收拾掉**）
    existed_before = (link.exists(), cmd.exists())
    before_names = sorted(p.name for p in real.iterdir())

    try:
        section_startup_dir()
        section_real_shortcut()
        section_real_settings()
    finally:
        # **恢复原样**：把我们造的那两个名字删掉；进来时本来就有的（不该有）
        # 也照样删——那是上一次跑崩留下的残骸，留着会让用户每次登录都启动桌宠。
        for path in (link, cmd):
            try:
                path.unlink(missing_ok=True)
            except OSError:  # pragma: no cover
                pass
        after_names = sorted(p.name for p in real.iterdir())
        section("§4 收尾：启动文件夹恢复原样")
        check(
            "启动文件夹里的文件清单与进来时一致",
            after_names == before_names,
            f"before={before_names} after={after_names}",
        )
        check(
            "没有留下任何我们的自启项",
            not link.exists() and not cmd.exists(),
            f"link={link.exists()} cmd={cmd.exists()}",
        )
        _LINES.append(f"（进来时 deskpet.lnk/cmd 是否存在：{existed_before}）")

    return _finish()


if __name__ == "__main__":
    sys.exit(main())
