# -*- mode: python ; coding: utf-8 -*-
"""deskPet 打包配置（PyInstaller onedir 便携版）。

设计要点（每一条都对应 agent.md 里的一个坑）：

1. **onedir 而不是 onefile**：`paths.program_dir()` 打包后取 `sys.executable` 的父目录，
   用户数据 `data/` 就落在 exe 旁边（可写、可备份）。onefile 会把程序目录变成
   临时解包目录，`data/` 跟着乱跑。

2. **`assets/` 与 `tools/*.ps1` 必须显式声明**（agent.md §2.22 末段）：
   `tools/make_shortcut.ps1` / `read_shortcut.ps1` 是**外部文件**，
   `autostart.tools_dir()` 按 `<程序目录>/tools` 找它们。漏掉的症状是
   "打包后开机自启永远显示不可用"，而源码运行完全看不出来。

3. **`tools/` 走 `datas`（落进 `_internal/`），构建后再复制一份到 exe 同级**：
   `tools_dir()` 取的是 `program_dir()/tools` = **exe 同级**的 `tools/`，
   所以真正生效的是构建后复制的那一份；`datas` 这一份是冗余但无害的保险。

4. **Qt 的 multimedia 插件与 FFmpeg 动态库不能少**（agent.md §2.18 末行）：
   漏掉的表现是"开发机能响、打包后没声音"。PySide6 的官方 hook 会收，
   本 spec 再用 `collect_dynamic_libs` 显式兜一道底。

5. **`main.py` 是唯一入口**：它的 import 写在顶层，静态分析才收得全模块
   （`run.py` 那种运行期改 `sys.path` 的写法收不全）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).resolve()

# ---------------------------------------------------------------- 随包资源

# ⚠ 为什么这里**只有**两个 `.ps1`，而 `assets/` 与手册走 COLLECT 之后的复制：
#
# `datas` 的落点是 `COLLECT` 的输出目录，而 PyInstaller 6.x 的 onedir 会
# 把所有 datas/binaries 一并塞进 `_internal/`（exe 同级只剩 exe 自己）。
# 但 `paths.resource_dir()` 打包后返回的正是 `_internal/`，
# 而 `autostart.tools_dir()` 返回的却是 `paths.program_dir()/tools`
# = **exe 同级**的 `tools/`。两者落点不同，必须分开伺候：
#
#   - `assets/` → 只需在 `_internal/` 下（`resource_dir()/assets`）→ 走 `datas`；
#   - `tools/*.ps1` → **必须在 exe 同级**的 `tools/` → COLLECT 之后复制过去。
#
# 漏掉后者的症状是"打包后开机自启永远显示不可用"，而源码运行完全看不出来
# （agent.md §2.22 末段）。
datas = [
    # 自带素材（约 60 MB，1683 个文件）——必须整包带走，否则启动就报
    # "连默认形象都不可用"。
    (str(ROOT / "assets"), "assets"),
]

# FFmpeg 动态库（avcodec / avutil / swscale …）：音频解码的实际执行者。
binaries = collect_dynamic_libs("PySide6")

# 不随包带的东西：开发期自检脚本、抽帧工具、素材源、文档截图。
# （自检脚本是开发工具，最终用户不需要；带上去只会让包变大、
#   还会让 tools/ 里混进一堆 .py，与 `autostart` 只看两个 .ps1 的约定无关。）

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        # 显式点名：这些模块由运行期字符串/信号间接用到，
        # 静态分析与 PySide6 hook 都未必能推出来。
        "deskpet",
        "PySide6.QtMultimedia",
        "PySide6.QtNetwork",
        "PySide6.QtSvg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 明确排除：没用到就别带（省体积，也少一处能出问题的地方）。
    excludes=[
        "tkinter",
        "unittest",
        "pydoc_data",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore",
        "PySide6.QtQuick",
        "PySide6.QtQml",
        "PySide6.QtDesigner",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebSockets",
        "PySide6.QtWebChannel",
        "PySide6.QtSerialPort",
        "PySide6.QtSensors",
        "PySide6.QtSpatialAudio",
        "PySide6.QtTextToSpeech",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="deskPet",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # **不带控制台**：桌宠是常驻托盘类的小程序，弹出黑框很难看；
    # 而且 `logger` 的输出本来就全部落 `data/deskpet.log`（§4.10 最后一条）。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="deskPet",
)


# ---------------------------------------------------------------- 收官：补齐 exe 同级文件
#
# `COLLECT` 之后 `dist/deskPet/` 里只有 exe 与 `_internal/`，而有两样东西
# **必须待在 exe 同级**：
#
#   1. `tools/*.ps1` —— `autostart.tools_dir()` = `program_dir()/tools`；
#   2. `操作指引.md`  —— 给人看的，放在 `_internal/` 里等于没有。
#
# 这一段的每一句都在构建日志里说话（`print`），构建完的目录结构可当场核对。

import shutil  # noqa: E402  （spec 里只能这么写，PyInstaller 不预处理 import）

DIST = ROOT / "dist" / "deskPet"
if not DIST.is_dir():
    raise SystemExit(f"打包失败：没找到输出目录 {DIST}")

# 两个自启脚本（**这是 task14 §2.22 明写的打包待办**）。
tools_out = DIST / "tools"
tools_out.mkdir(exist_ok=True)
for script in ("make_shortcut.ps1", "read_shortcut.ps1"):
    source = ROOT / "tools" / script
    if not source.is_file():
        raise SystemExit(f"打包失败：缺 {source}")
    shutil.copy2(source, tools_out / script)
    print(f"[deskpet.spec] 已复制 {script} → {tools_out}")

# 用户手册。
manual = ROOT / "操作指引.md"
if not manual.is_file():
    raise SystemExit(f"打包失败：缺 {manual}")
shutil.copy2(manual, DIST / manual.name)
print(f"[deskpet.spec] 已复制 {manual.name} → {DIST}")

# `_internal/` 里那份 assets 是 `resource_dir()/assets` 的唯一来源，**必须存在**。
if not (DIST / "_internal" / "assets").is_dir():
    raise SystemExit(f"打包失败：{DIST / '_internal' / 'assets'} 不存在，自带素材没进去")

# ⚠ **必须删掉构建机自己的 `data/`**。
#
# 交付物必须是"从没运行过"的状态：带着构建机的 `deskpet.log` 发出去，
# 用户第一次打开会看到别人的日志，排查问题时会被彻底误导；
# 带着 `config.json` 更糟——那是**开发机的档位与位置**，用户的桌宠会按它摆。
# 这一条在 agent.md §6 里写成了硬要求（"构建机留在 data/ 的日志必须删掉再发"），
# 所以在这里**自动执行**，不靠人记得。
stale = DIST / "data"
if stale.is_dir():
    shutil.rmtree(stale)
    print(f"[deskpet.spec] 已删除构建机残留的 {stale}")
if (DIST / "data").exists():
    raise SystemExit(f"打包失败：{stale} 仍然存在（交付物不许带构建机的用户数据）")

print(f"[deskpet.spec] 打包完成：{DIST}")
