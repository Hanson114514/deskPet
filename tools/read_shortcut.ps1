# read_shortcut.ps1 —— 读回一个 .lnk 的目标（task14 的状态判定依据）。
#
# ⚠ 本文件**必须**是 UTF-8 with BOM（原因同 make_shortcut.ps1：§4.3）。
#
# 用法：
#   powershell -File read_shortcut.ps1 -Path <lnk> -Result <结果文件>
#
# 结果文件（UTF-8）逐行是 `键=值`：
#   STATUS=OK | MISSING | BROKEN | ERROR
#   TARGET=<目标路径>        （STATUS=OK 时才有值）
#   ARGUMENTS=<启动参数>
#   WORKINGDIR=<工作目录>
#   ERROR=<系统给的原因>     （STATUS=ERROR 时才有值）
#
# ⚠ 为什么不让 Python 直接读 .lnk：shell link 是二进制格式，没有标准库支持；
#   `WScript.Shell` 是系统自带的、唯一稳定的读法（不引入任何依赖）。

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [string]$Result = ""
)

$ErrorActionPreference = "Stop"

function Write-Lines {
    param([string[]]$Lines)
    if ([string]::IsNullOrEmpty($Result)) { return }
    $text = ($Lines -join "`r`n")
    [System.IO.File]::WriteAllText($Result, $text, (New-Object System.Text.UTF8Encoding($false)))
}

try {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Lines @("STATUS=MISSING")
        exit 0
    }

    if ((Get-Item -LiteralPath $Path).Length -eq 0) {
        # 0 字节：创建接口没报错但留下空壳 —— 与"读不出目标"同一种结论。
        Write-Lines @("STATUS=BROKEN")
        exit 0
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $target = $shortcut.TargetPath
    if ([string]::IsNullOrEmpty($target)) {
        Write-Lines @("STATUS=BROKEN")
        exit 0
    }

    Write-Lines @(
        "STATUS=OK",
        "TARGET=$target",
        "ARGUMENTS=$($shortcut.Arguments)",
        "WORKINGDIR=$($shortcut.WorkingDirectory)"
    )
    exit 0
}
catch {
    Write-Lines @("STATUS=ERROR", "ERROR=$($_.Exception.Message)")
    exit 1
}
