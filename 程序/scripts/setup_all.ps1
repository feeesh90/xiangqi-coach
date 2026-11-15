$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    Write-Host "1/2 Installing official Pikafish..."
    & .\scripts\setup_engine.ps1 -AcceptNetworkTerms
    if ($LASTEXITCODE -ne 0) { throw "Engine setup failed" }

    Write-Host ""
    Write-Host "2/2 Building offline master-game database..."
    & .\scripts\setup_master_book.ps1
    if ($LASTEXITCODE -ne 0) { throw "Master book setup failed" }

    Write-Host ""
    Write-Host "ALL_OFFLINE_COMPONENTS_READY"
    Write-Host "Now double-click 启动（控制台诊断）.bat"
} finally {
    Pop-Location
}
