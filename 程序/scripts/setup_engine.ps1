[CmdletBinding()]
param(
    [switch]$AcceptNetworkTerms
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$engineDir = Join-Path $root "engine"
New-Item -ItemType Directory -Force $engineDir | Out-Null

if (!$AcceptNetworkTerms) {
    Write-Host ""
    Write-Host "Pikafish engine is GPLv3, but its NNUE/network may have separate upstream terms."
    Write-Host "Read 文档\THIRD_PARTY_NOTICES.md first."
    Write-Host ""
    throw "If you accept the upstream network terms for your intended use, rerun with -AcceptNetworkTerms"
}

$url = "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-09-06/Pikafish.2026-09-06.7z"
$archive = Join-Path $env:TEMP "Pikafish.2026-09-06.7z"
$expected = "41952BBFE2520FACEB5902C69E6AB4845CC999841D2B49A95CC1BE7867A25E5B"

Write-Host "Downloading official Pikafish 2026-09-06..."
Write-Host "Total size: about 51 MB. The progress bar shows bytes written to disk (not uploaded)."
Invoke-WebRequest -Uri $url -OutFile $archive
$actual = (Get-FileHash -Algorithm SHA256 $archive).Hash
if ($actual -ne $expected) {
    throw "Release archive SHA256 mismatch.`nExpected: $expected`nActual:   $actual"
}

$tmp = Join-Path $env:TEMP ("xiangqi-pikafish-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force $tmp | Out-Null

$seven = Get-Command 7z.exe -ErrorAction SilentlyContinue
if ($seven) {
    & $seven.Source x -y "-o$tmp" $archive | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "7-Zip extraction failed." }
} else {
    & tar.exe -xf $archive -C $tmp
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot extract the official .7z. Install 7-Zip, then rerun this script."
    }
}

$exe = Get-ChildItem -Recurse $tmp -File |
    Where-Object {
        $_.Name -match "Pikafish.*Windows.*x86-64.*universal.*\.exe$" -or
        $_.Name -eq "Pikafish-Windows-x86-64-universal.exe"
    } | Select-Object -First 1

if (!$exe) {
    $exe = Get-ChildItem -Recurse $tmp -File |
        Where-Object { $_.Name -match "Pikafish.*Windows.*\.exe$" } |
        Select-Object -First 1
}

$net = Get-ChildItem -Recurse $tmp -File |
    Where-Object { $_.Name -eq "pikafish.nnue" } |
    Select-Object -First 1

if (!$exe) { throw "The official archive did not contain a Windows Pikafish executable." }
if (!$net) { throw "The official archive did not contain pikafish.nnue." }

Copy-Item -Force $exe.FullName (Join-Path $engineDir "pikafish.exe")
Copy-Item -Force $net.FullName (Join-Path $engineDir "pikafish.nnue")

Write-Host ""
Write-Host "PIKAFISH_OFFLINE_READY"
Write-Host "Engine: $($exe.Name)"
Write-Host "Network: $($net.Name)"
Write-Host ""
Write-Host "Now run:"
Write-Host "  启动（控制台诊断）.bat"
