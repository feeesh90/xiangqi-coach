function Find-XiangqiPython {
    $Candidates = @()
    $runtime = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "runtime\python.exe"
    if (Test-Path -LiteralPath $runtime -PathType Leaf) { $Candidates += $runtime }
    foreach ($V in @("314","313","312","311")) {
        if ($env:LOCALAPPDATA) {
            $Candidates += (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$V\python.exe")
        }
    }
    try { $Candidates += (Get-Command python.exe -All -ErrorAction SilentlyContinue | ForEach-Object {$_.Source}) } catch {}
    foreach ($P in ($Candidates | Select-Object -Unique)) {
        if (!(Test-Path -LiteralPath $P -PathType Leaf)) { continue }
        try {
            & $P -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 9)" 2>$null
            if ($LASTEXITCODE -eq 0) { return $P }
        } catch {}
    }
    throw "Python 3.11+ not found. This project intentionally does not use the Windows py launcher."
}

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $env:USERPROFILE ".xiangqi-coach-data"
$repo = Join-Path $cache "Chinese-Chess-Practical-Dataset"
New-Item -ItemType Directory -Force $cache | Out-Null

if (!(Test-Path $repo)) {
    Write-Host "Downloading CCPD master-game dataset..."
    if (Get-Command git.exe -ErrorAction SilentlyContinue) {
        git clone --depth 1 "https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset.git" $repo
        if ($LASTEXITCODE -ne 0) { throw "git clone failed" }
    } else {
        $zip = Join-Path $cache "ccpd.zip"
        Invoke-WebRequest "https://github.com/Yvonne761/Chinese-Chess-Practical-Dataset/archive/refs/heads/main.zip" -OutFile $zip
        Expand-Archive -Force $zip $cache
        Move-Item -Force (Join-Path $cache "Chinese-Chess-Practical-Dataset-main") $repo
    }
}
Push-Location $root
try {
    $Python = Find-XiangqiPython
    & $Python scripts\import_ccpd.py $repo
    if ($LASTEXITCODE -ne 0) { throw "CCPD import failed" }
} finally { Pop-Location }
Write-Host "MASTER_BOOK_READY"
