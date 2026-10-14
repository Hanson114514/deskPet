# make_shortcut.ps1 —— 生成一个 .lnk 快捷方式（task14 的开机自启项）。
#
# ⚠ 本文件**必须**是 UTF-8 with BOM：里面含非 ASCII 字符，PowerShell 5.1
#   在没有 BOM 时按系统 ANSI 解码 → 中文变乱码 → 连语法都过不去 →
#   报一堆 ParserError，而调用方只看到最后一步的 "Permission denied"（agent.md §4.3）。
#
# 用法：
#   powershell -File make_shortcut.ps1 -Path <lnk> -Target <目标> [-Arguments <参数>]
#              [-WorkingDirectory <工作目录>] [-Description <说明>] [-Result <结果文件>]
#
# 结果**写进文件**（UTF-8），不靠标准输出：子进程输出的编码随环境而变，
# 猜错会把唯一的线索毁掉（agent.md §4.3）。
# 结果文件的格式与 read_shortcut.ps1 **完全一致**（逐行 `键=值`，第一个 `=` 之后全算值）：
#   STATUS=OK | ERROR
#   TARGET=<写进去的目标>
#   ERROR=<系统给的原因>     （STATUS=ERROR 时才有值）

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Target,
    [string]$Arguments = "",
    [string]$WorkingDirectory = "",
    [string]$Description = "",
    [string]$Result = ""
)

$ErrorActionPreference = "Stop"

function Write-Lines {
    param([string[]]$Lines)
    if ([string]::IsNullOrEmpty($Result)) { return }
    $text = ($Lines -join "`r`n")
    # 无 BOM 的 UTF-8 更好读；父进程用 utf-8-sig 读，两种写法都能吃下。
    [System.IO.File]::WriteAllText($Result, $text, (New-Object System.Text.UTF8Encoding($false)))
}

try {
    if (-not (Test-Path -LiteralPath $Target)) {
        throw "目标不存在：$Target"
    }

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $Target
    if (-not [string]::IsNullOrEmpty($Arguments)) { $shortcut.Arguments = $Arguments }
    if (-not [string]::IsNullOrEmpty($WorkingDirectory)) { $shortcut.WorkingDirectory = $WorkingDirectory }
    if (-not [string]::IsNullOrEmpty($Description)) { $shortcut.Description = $Description }
    $shortcut.Save()

    # 立刻读回一次：Save() 不报错但留下空壳的情况必须当场发现（否则就是"假成功"）。
    $check = $shell.CreateShortcut($Path)
    if ([string]::IsNullOrEmpty($check.TargetPath)) {
        throw "写完之后读回目标为空（$Path）"
    }
    Write-Lines @("STATUS=OK", "TARGET=$($check.TargetPath)")
    exit 0
}
catch {
    Write-Lines @("STATUS=ERROR", "ERROR=$($_.Exception.Message)")
    exit 1
}
