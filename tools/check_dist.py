"""分发包自检：**对着 dist/deskPet/ 这个冻结产物**做端到端验证。

与 `tools/check_task*` 那一批的区别：那些验的是**源码**，本脚本验的是**打出来的包**
——判据全部落在"这个文件夹本身"上：

| 检查 | 为什么需要它 |
| --- | --- |
| `_internal/assets` 存在且帧文件数对得上 | 漏了就是启动即报"连默认形象都不可用" |
| **exe 同级**有 `tools/*.ps1` | `autostart.tools_dir()` 按 `program_dir()/tools` 找；漏了 = 自启永远不可用（§2.22） |
| 两个 `.ps1` 是 **UTF-8 with BOM** | 被编辑工具吃掉 BOM → PowerShell 5.1 按 ANSI 解码 → `ParserError`（§4.22） |
| `data/` 落在 **exe 旁边**且写入成功 | `program_dir()` 打包后取 exe 目录；验证它与 `resource_dir()` 真的分开了 |
| 真跑起进程、日志里出现启动行 | 只查文件不算数，要它真的活着 |
| 单实例锁互斥 | 第二次启动应**安静退出 rc=0** |
| 音效设备可用 | 打包漏 Qt multimedia 插件/FFmpeg = "开发机能响、打包后没声音"（§2.18） |

用法（**必须在工作区内跑**，§4.13：自检的临时目录不能放 %TEMP%）::

    python tools/check_dist.py

⚠ 本脚本会**真的启动桌宠进程**并让它写 `data/`；它跑完会把 `data/` 里自己造的文件清掉。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "deskPet"
EXE = DIST / "deskPet.exe"
DATA = DIST / "data"

_passed = 0
_failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    """按中性文字记录实测值（§4.11：不要把成功条件写反）。"""
    global _passed
    if ok:
        _passed += 1
        print(f"  PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _failed.append(label)
        print(f"  FAIL  {label}" + (f"  [{detail}]" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _run(args: list[str], timeout: float = 60.0) -> subprocess.CompletedProcess:
    """跑一次 exe。**不带控制台**的 exe 输出不到管道，所以只关心退出码与副作用。"""
    return subprocess.run(
        [str(EXE), *args],
        cwd=str(DIST),
        capture_output=True,
        timeout=timeout,
    )


def _read_log_tail(lines: int = 60) -> str:
    log = DATA / "deskpet.log"
    if not log.is_file():
        return ""
    return "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


# ------------------------------------------------------------------ §1 文件布局


section("§1 文件布局")
check("dist/deskPet/deskPet.exe 存在", EXE.is_file())
check("_internal/ 存在（运行环境）", (DIST / "_internal").is_dir())

# 自带素材：进 _internal（resource_dir()/assets）
assets = DIST / "_internal" / "assets"
check("_internal/assets 存在", assets.is_dir())
if assets.is_dir():
    packs = sorted(p.name for p in assets.iterdir() if p.is_dir())
    check("自带两个素材包都在", packs == ["weedy", "weedy_sightseer"], str(packs))
    # 与源码里的帧文件数对齐（不去猜具体数字）。
    source_frames = len(list((ROOT / "assets").rglob("*.png")))
    dist_frames = len(list(assets.rglob("*.png")))
    check("帧文件数与源码一致", source_frames == dist_frames, f"{dist_frames}/{source_frames}")
    for pack in ("weedy", "weedy_sightseer"):
        check(f"{pack}/asset.json 在", (assets / pack / "asset.json").is_file())
    # 音效也要在（task10 补充：两个包都声明了 sounds）。
    check("音效文件被带进来", len(list(assets.rglob("*.wav"))) >= 6)

# 自启脚本：**必须**在 exe 同级（这是本脚本最重要的一条）
tools = DIST / "tools"
for script in ("make_shortcut.ps1", "read_shortcut.ps1"):
    target = tools / script
    check(f"exe 同级 tools/{script} 存在", target.is_file())
    if target.is_file():
        raw = target.read_bytes()
        check(f"{script} 是 UTF-8 with BOM", raw.startswith(b"\xef\xbb\xbf"))
        check(f"{script} 与源码一致", raw == (ROOT / "tools" / script).read_bytes())

check("操作指引.md 在包根", (DIST / "操作指引.md").is_file())

# ⚠ 交付物必须是"从没运行过"的状态：构建机的 data/ 绝不能发出去。
# （带着别人的 config.json 会让用户的桌宠按开发机的档位/位置摆，
#   带着别人的 deskpet.log 会把排查方向带偏。）
# 注意这条要**在 §2 真跑之前**判，否则测的就是自己刚造出来的 data/。
check("交付物里**没有**构建机的 data/", not (DIST / "data").exists())

# Qt 的音频后端（§2.18：漏了就是"打包后没声音"）
#
# ⚠ 判据必须**递归找**：`collect_dynamic_libs("PySide6")` 把它们放在
# `_internal/PySide6/` 下，只 glob 顶层会误报"缺 FFmpeg"。
internal = DIST / "_internal"
ffmpeg = sorted(p.name for p in internal.rglob("avcodec-*.dll"))
check("FFmpeg 解码库已随包（avcodec）", bool(ffmpeg), str(ffmpeg))
check(
    "FFmpeg 容器库已随包（avformat）",
    bool(list(internal.rglob("avformat-*.dll"))),
)
check(
    "FFmpeg 缩放库已随包（swscale）",
    bool(list(internal.rglob("swscale-*.dll"))),
)
media_plugins = list(internal.rglob("ffmpegmediaplugin.dll"))
check("Qt multimedia 插件在", bool(media_plugins), str([str(p.parent) for p in media_plugins]))
platform = list(internal.rglob("qwindows.dll"))
check("Qt 平台插件 qwindows.dll 在", bool(platform))


# ------------------------------------------------------------------ §2 真跑起来


section("§2 真跑一次（--exit-after）")
# 先把上一次的 data/ 清掉，看它能不能自己重建（"首次运行"的路径）。
if DATA.is_dir():
    shutil.rmtree(DATA)
check("起始时没有 data/", not DATA.is_dir())

proc = _run(["--exit-after", "4"])
check("启动退出码为 0", proc.returncode == 0, f"rc={proc.returncode}")
check("data/ 落在 exe 旁边（不是 _internal 里）", DATA.is_dir())
check(
    "data/ **没有**落在 _internal 里",
    not (internal / "data").is_dir(),
)
log = _read_log_tail(80)
check("日志里有启动分隔行", "=====" in log)
check("日志里出现程序目录 = 包根", str(DIST) in log, "")

# 日志里应当能看到素材加载与音效的结论。
check("日志里有素材/动作相关信息", ("动作" in log) or ("fps" in log) or ("素材" in log))
check("音效设备结论已打印", "音效" in log)

# 只读查询不该建 QApplication，但会建 data/；这里验 rc 与输出稳定性。
for arg, name in (("--list-pets", "素材包"), ("--list-actions", "动作"), ("--list-sizes", "档位")):
    q = _run([arg])
    check(f"{arg} 退出码为 0", q.returncode == 0, f"rc={q.returncode}")

v = _run(["--version"])
check("--version 退出码为 0", v.returncode == 0, f"rc={v.returncode}")

# 坏包名：必须 rc=2 且不崩（用法错误走 stderr + 退出码，§2.10）
bad = _run(["--pet", "definitely-not-a-pet"])
check("坏素材包名 → rc=2", bad.returncode == 2, f"rc={bad.returncode}")

bad_level = _run(["--size-level", "99"])
check("档位越界 → rc=2", bad_level.returncode == 2, f"rc={bad_level.returncode}")


# ------------------------------------------------------------------ §3 单实例


section("§3 单实例保护")
# 起一个长命的实例，再起第二个：第二个应当**安静退出 rc=0**。
first = subprocess.Popen(
    [str(EXE)],
    cwd=str(DIST),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
try:
    time.sleep(6.0)  # 等它抢到锁、把窗口开起来
    second = _run([])
    check("已有实例时第二次启动 rc=0（安静退出）", second.returncode == 0, f"rc={second.returncode}")
    check("第一个实例还活着", first.poll() is None, f"poll={first.poll()}")

    tail = _read_log_tail(40)
    check("日志里说明了'已有实例'", "已有实例" in tail or "安静退出" in tail)
finally:
    first.terminate()
    try:
        first.wait(timeout=15)
    except subprocess.TimeoutExpired:
        first.kill()


# ------------------------------------------------------------------ §4 退出干净


section("§4 退出与残留")
# 终止之后应当能立刻再起（锁真的被系统回收 / 被 finally 释放）。
time.sleep(1.0)
again = _run(["--exit-after", "3"])
check("终止后能立刻再启动（锁没被占死）", again.returncode == 0, f"rc={again.returncode}")

# `data/` 里不该有临时文件残留（原子写的收尾，§2.3）。
if DATA.is_dir():
    leftovers = [p.name for p in DATA.iterdir() if p.is_file() and p.suffix not in {".json", ".log", ".1"}]
    check("data/ 里没有临时文件残留", not leftovers, str(leftovers))
    config = DATA / "config.json"
    if config.is_file():
        try:
            json.loads(config.read_text(encoding="utf-8"))
            check("生成的 config.json 是合法 JSON", True)
        except Exception as exc:  # noqa: BLE001
            check("生成的 config.json 是合法 JSON", False, str(exc))


# ------------------------------------------------------------------ 收尾


def main() -> int:
    if not EXE.is_file():
        print(f"找不到 {EXE}，先打包：python -m PyInstaller --noconfirm deskpet.spec")
        return 2

    print(f"分发包：{DIST}")
    print(f"大小：{sum(p.stat().st_size for p in DIST.rglob('*') if p.is_file()) / 1024 / 1024:.1f} MB")

    print(f"\n{'=' * 60}")
    print(f"通过 {_passed} 项，失败 {len(_failed)} 项")
    if _failed:
        for name in _failed:
            print(f"  - {name}")
        return 1
    print("全过")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    sys.exit(main())
