$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) {
    throw 'Application non installée. Lancer scripts\bootstrap.bat ou scripts\bootstrap.ps1.'
}
& $Python -m ummahverse_audio gui
if ($LASTEXITCODE -ne 0) { throw "L'interface a échoué (code $LASTEXITCODE)." }
