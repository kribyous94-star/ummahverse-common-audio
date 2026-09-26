$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'bootstrap.bat')
if ($LASTEXITCODE -ne 0) { throw "Installation échouée (code $LASTEXITCODE)." }
