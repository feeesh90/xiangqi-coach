$ErrorActionPreference = 'Stop'
$recognitionRoot = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent $recognitionRoot
Set-Location -LiteralPath $recognitionRoot

# Portable package: use the bundled runtime if present.
$runtime = Join-Path $projectRoot 'runtime\python.exe'
if (Test-Path -LiteralPath $runtime -PathType Leaf) {
    & $runtime -X utf8 (Join-Path $PSScriptRoot 'setup_recognition.py')
    exit $LASTEXITCODE
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    & $pythonCommand.Source -X utf8 (Join-Path $PSScriptRoot 'setup_recognition.py')
} else {
    & py -3 -X utf8 (Join-Path $PSScriptRoot 'setup_recognition.py')
}
exit $LASTEXITCODE
