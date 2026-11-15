param(
    [string]$Name = "象棋指导棋_免安装版",
    [string]$PythonHome = "C:\Program Files\Python314"
)

$ErrorActionPreference = "Stop"
$programRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $programRoot
$outRoot = Join-Path $projectRoot "build"
$pkg = Join-Path $outRoot $Name
$zip = Join-Path $outRoot ($Name + ".zip")

function Copy-Tree([string]$Source, [string]$Destination, [string[]]$ExcludeDirs = @(), [string[]]$ExcludeFiles = @()) {
    if (!(Test-Path -LiteralPath $Source)) { throw "找不到源目录：$Source" }
    $roboArgs = @($Source, $Destination, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    if ($ExcludeDirs.Count -gt 0) { $roboArgs += "/XD"; $roboArgs += $ExcludeDirs }
    if ($ExcludeFiles.Count -gt 0) { $roboArgs += "/XF"; $roboArgs += $ExcludeFiles }
    & robocopy.exe @roboArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "复制失败：$Source -> $Destination（robocopy $LASTEXITCODE）" }
}

if (!(Test-Path -LiteralPath (Join-Path $PythonHome "python.exe"))) {
    throw "找不到便携运行时源：$PythonHome\python.exe"
}
$venvSite = Join-Path $programRoot ".recognition-venv\Lib\site-packages"
if (!(Test-Path -LiteralPath $venvSite)) {
    throw "找不到识别依赖：$venvSite（请先在本机安装图片识别）"
}

if (Test-Path -LiteralPath $pkg) { Remove-Item -LiteralPath $pkg -Recurse -Force }
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Force -Path $pkg | Out-Null

Write-Host "1/5 复制便携 Python 运行时..."
$runtime = Join-Path $pkg "runtime"
Copy-Tree $PythonHome $runtime @(
    (Join-Path $PythonHome "Doc"),
    (Join-Path $PythonHome "include"),
    (Join-Path $PythonHome "libs"),
    (Join-Path $PythonHome "Scripts"),
    (Join-Path $PythonHome "Lib\site-packages"),
    "__pycache__"
) @("*.pyc")

Write-Host "2/5 复制识别依赖..."
Copy-Tree $venvSite (Join-Path $runtime "Lib\site-packages") @("__pycache__") @("*.pyc")

Write-Host "3/5 复制程序文件..."
$appDir = Join-Path $pkg "程序"
Copy-Tree $programRoot $appDir @(
    (Join-Path $programRoot ".recognition-venv"),
    (Join-Path $programRoot "data"),
    (Join-Path $programRoot "engine\px0-gpu"),
    (Join-Path $programRoot ".pytest_cache"),
    "__pycache__"
) @("*.pyc")

# 只保留运行所需的大师库数据。
$dataDir = Join-Path $appDir "data"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
foreach ($file in @("master_book.json", "master_book.sqlite")) {
    $source = Join-Path $programRoot "data\$file"
    if (!(Test-Path -LiteralPath $source)) { throw "缺少数据文件：$source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $dataDir $file) -Force
}

Copy-Item -LiteralPath (Join-Path $projectRoot "象棋指导棋.exe") -Destination $pkg -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "README.md") -Destination (Join-Path $pkg "README.md") -Force
Copy-Item -LiteralPath (Join-Path $projectRoot "THIRD_PARTY_NOTICES.md") -Destination $pkg -Force

$readme = @'
象棋指导棋 · 免安装版（Windows 64 位）
=====================================

一、怎么开始
1. 把整个文件夹解压到可写目录（桌面、下载目录都可以，不要放 Program Files）。
2. 双击“象棋指导棋.exe”。不需要安装 Python、引擎或识别模型。
3. 如果 Windows 提示“未知发布者”，点“更多信息 → 仍要运行”。
   杀毒软件若误报 Pikafish/Px0，请把整个文件夹加入信任。

二、功能说明
- 人机对局、双人、局域网联机、悔棋、翻转、创造模式
- Pikafish 与 Px0 CPU 双引擎、候选着、推演、复盘
- 图片导入残局、投屏跟随（需自行安装 ToDesk 等手机投屏）
- 大师棋谱 SQLite 库已内置
- API 评议/代走可用，但需要填写你自己的 API Key

三、和开发机的差异（重要）
1. 本包不包含 GPU 加速的 Px0 GPU 引擎（仅 NVIDIA 显卡可用，约 1.3GB）。
   默认使用 Pikafish；也可手动切 Px0 CPU。棋力不降级，速度取决于你的 CPU。
   有 NVIDIA 显卡的高级用户可运行 程序\scripts\setup_px0_gpu.py 自行补齐。
2. API Key 不随包提供，需要自己填写。
3. 局域网仅限同一局域网，双方同版本；需要专用网络并同意一次防火墙授权。
4. 投屏跟随需要自己安装 ToDesk 或类似投屏软件。
5. 识别在 CPU 上运行；极老 CPU（缺 AVX2）或缺少 VC++ 运行库时可能失败。
6. Px0 CPU 是按 Ryzen 7 5700X 编译的，其他 CPU 不保证兼容，失败会退回 Pikafish。
7. 棋力、指导速度、执方、API 设置保存在你自己的用户目录，不随包携带。
8. 仅支持 64 位 Intel/AMD Windows 10/11；建议中文 Windows。
9. 没有自动更新；以后更新请重新下载新包。

四、排错
- 启动不了：运行“程序\启动（控制台诊断）.bat”，会显示完整错误。
- 启动日志：程序\data\启动失败.log
- 引擎日志：程序\data\logs\engine.log
- 设置位置：%USERPROFILE%\.xiangqi-coach\

更完整说明见同目录 README.md。
'@
$readmePath = Join-Path $pkg "使用说明.txt"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($readmePath, $readme, $utf8Bom)

Write-Host "4/5 校验便携运行时..."
$runtimePython = Join-Path $runtime "python.exe"
& $runtimePython -X utf8 -c "import sys, tkinter, PIL, cv2, onnxruntime, numpy; print('RUNTIME_OK', sys.version.split()[0], cv2.__version__, onnxruntime.__version__)"
if ($LASTEXITCODE -ne 0) { throw "便携运行时自检失败" }
& $runtimePython -X utf8 -c "import sys; sys.path.insert(0, r'$appDir'); import rules, coach, engine_manager, screen_follow.process, master_book; print('APP_IMPORT_OK')"
if ($LASTEXITCODE -ne 0) { throw "便携包程序导入失败" }

# 清理运行产物，保证发布物干净。
Get-ChildItem -LiteralPath (Join-Path $appDir "data\logs") -Force -File -ErrorAction SilentlyContinue | Remove-Item -Force
foreach ($junk in @((Join-Path $appDir "data\启动失败.log"), (Join-Path $appDir "启动器错误.log"))) {
    if (Test-Path -LiteralPath $junk) { Remove-Item -LiteralPath $junk -Force }
}
Get-ChildItem -LiteralPath $runtime -Recurse -Force -Directory -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -eq "__pycache__" } |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$rawSize = [math]::Round(((Get-ChildItem -LiteralPath $pkg -Recurse -Force -File | Measure-Object Length -Sum).Sum) / 1GB, 2)
Write-Host "5/5 打包 ZIP（解压后约 $rawSize GB）..."
$zipScript = @'
import pathlib, sys, zipfile, os
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
if out.exists(): out.unlink()
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for path in sorted(root.rglob("*")):
        arc = pathlib.Path(root.name) / path.relative_to(root)
        name = str(arc).replace(os.sep, "/")
        if path.is_dir():
            z.writestr(name + "/", b"")
        else:
            z.write(path, name)
print("ZIP_DONE", out.stat().st_size)
'@
$zipScriptPath = Join-Path $outRoot "_make_zip.py"
[System.IO.File]::WriteAllText($zipScriptPath, $zipScript, (New-Object System.Text.UTF8Encoding($false)))
& $runtimePython -X utf8 $zipScriptPath $pkg $zip
if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $zip)) { throw "ZIP 打包失败" }
Remove-Item -LiteralPath $zipScriptPath -Force
if (!(Test-Path -LiteralPath $zip)) { throw "没有生成 ZIP" }
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash
$zipSize = [math]::Round((Get-Item -LiteralPath $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "PACKAGE_READY"
Write-Host "ZIP   : $zip"
Write-Host "SIZE  : $zipSize MB"
Write-Host "SHA256: $hash"
