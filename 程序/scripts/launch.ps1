$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$ProjectRoot = Split-Path -Parent $Root
Set-Location $Root

function Test-Python([string]$Exe) {
    if ([string]::IsNullOrWhiteSpace($Exe)) { return $false }
    if (!(Test-Path -LiteralPath $Exe -PathType Leaf)) { return $false }
    try {
        & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 9)" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Find-Python {
    $Candidates = New-Object System.Collections.Generic.List[string]

    # Portable package runtime.
    $runtime = Join-Path $ProjectRoot "runtime\python.exe"
    if (Test-Path -LiteralPath $runtime -PathType Leaf) { $Candidates.Add($runtime) }

    # Prefer normal per-user CPython installs. Do NOT use py.exe.
    foreach ($V in @("314","313","312","311")) {
        if ($env:LOCALAPPDATA) {
            $Candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\Python\Python$V\python.exe"))
        }
        if ($env:ProgramFiles) {
            $Candidates.Add((Join-Path $env:ProgramFiles "Python$V\python.exe"))
        }
    }

    # Registry installs.
    foreach ($Base in @(
        "HKCU:\Software\Python\PythonCore",
        "HKLM:\Software\Python\PythonCore",
        "HKLM:\Software\WOW6432Node\Python\PythonCore"
    )) {
        if (Test-Path $Base) {
            Get-ChildItem $Base -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    $P=(Get-ItemProperty ($_.PSPath + "\InstallPath") -ErrorAction Stop)."(default)"
                    if ($P) { $Candidates.Add((Join-Path $P "python.exe")) }
                } catch {}
            }
        }
    }

    # PATH python.exe last. Windows Store stubs will fail Test-Python.
    try {
        Get-Command python.exe -All -ErrorAction SilentlyContinue |
            ForEach-Object { $Candidates.Add($_.Source) }
    } catch {}

    foreach ($P in ($Candidates | Select-Object -Unique)) {
        if (Test-Python $P) { return $P }
    }
    return $null
}

$Python = Find-Python
if (!$Python) {
    Write-Host "[ERROR] Python 3.11+ was not found."
    Write-Host "Install normal CPython 3.11+ or use the portable package that ships runtime\python.exe."
    exit 2
}
Write-Host "[OK] Python: $Python"

$engineExe = Join-Path $Root "engine\pikafish.exe"
$engineNet = Join-Path $Root "engine\pikafish.nnue"
if (!(Test-Path $engineExe) -or !(Test-Path $engineNet)) {
    Write-Host ""
    Write-Host "Pikafish engine is missing in this folder."
    Write-Host ""
    Write-Host "  [x] engine\pikafish.exe"
    Write-Host "  [x] engine\pikafish.nnue"
    Write-Host ""
    Write-Host "The launcher can download the official offline engine:"
    Write-Host "  Source   : github.com/official-pikafish/Pikafish"
    Write-Host "  Size     : about 51 MB"
    Write-Host "  Install  : $Root\engine\"
    Write-Host ""
    $Answer = Read-Host "Download and install now? [Y/N]"
    if ($Answer -match "^[Yy]") {
        & (Join-Path $PSScriptRoot "setup_engine.ps1") -AcceptNetworkTerms
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host "AI features need Pikafish. No weak fallback will be used."
    }
}

& $Python -X utf8 (Join-Path $Root "启动.pyw")
exit $LASTEXITCODE
