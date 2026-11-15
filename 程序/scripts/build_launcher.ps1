$ErrorActionPreference = "Stop"
$programRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $programRoot
$source = Join-Path $programRoot "启动器\启动器.cs"
$icon = Join-Path $programRoot "assets\app.ico"
$output = Join-Path $projectRoot "象棋指导棋.exe"

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (!(Test-Path -LiteralPath $csc)) {
    $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (!(Test-Path -LiteralPath $csc)) { throw "找不到 .NET Framework 4.x 的 csc.exe" }
if (!(Test-Path -LiteralPath $source)) { throw "找不到启动器源码：$source" }
if (!(Test-Path -LiteralPath $icon)) { throw "找不到图标：$icon" }

& $csc /nologo /target:winexe /optimize+ /win32icon:"$icon" /out:"$output" "$source"
if ($LASTEXITCODE -ne 0) { throw "启动器编译失败，退出码 $LASTEXITCODE" }
if (!(Test-Path -LiteralPath $output)) { throw "编译后没有生成：$output" }
Write-Host "[OK] 已生成 $output"
