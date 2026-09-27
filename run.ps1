<#
.SYNOPSIS
    Lance l'interface graphique (Windows), en gardant tout dans le dossier du projet.

.DESCRIPTION
    Choisit l'environnement installé par install.ps1 (.venv, ou .venv-gui si
    l'interface a dû être installée séparément), pointe FFmpeg vers tools\ffmpeg s'il
    y a été téléchargé, et place les fichiers temporaires de traitement dans
    .cache\workspace\ — de sorte que supprimer ce dossier ne laisse rien derrière.

.PARAMETER Cli
    Passe la main à la ligne de commande plutôt qu'à l'interface. Les arguments
    restants sont transmis à `python -m ummahverse_audio`.

.EXAMPLE
    .\run.ps1
.EXAMPLE
    .\run.ps1 -Cli extract --input-a A.wav --input-b B.wav --out-dir sorties
#>
[CmdletBinding()]
param(
    [switch] $Cli,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root      = $PSScriptRoot
$Venv      = Join-Path $Root '.venv'
$VenvGui   = Join-Path $Root '.venv-gui'
$FfmpegDir = Join-Path $Root 'tools\ffmpeg'

function Stop-WithError([string] $Message) {
    Write-Host "Erreur : $Message" -ForegroundColor Red
    exit 1
}

# --------------------------------------------------------------------------- #
# Environnement à utiliser
# --------------------------------------------------------------------------- #
$guiPython  = Join-Path $VenvGui 'Scripts\python.exe'
$mainPython = Join-Path $Venv 'Scripts\python.exe'

if ((-not $Cli) -and (Test-Path $guiPython)) {
    $Python = $guiPython          # interface installée dans un second environnement
} elseif (Test-Path $mainPython) {
    $Python = $mainPython
} else {
    Stop-WithError "Application non installée.`nLancez : .\install.ps1   (ou install.bat)"
}

# --------------------------------------------------------------------------- #
# Confinement : rien en dehors du dossier
# --------------------------------------------------------------------------- #
if (-not $env:UMMAHVERSE_WORKSPACE) {
    $env:UMMAHVERSE_WORKSPACE = Join-Path $Root '.cache\workspace'
}
New-Item -ItemType Directory -Force -Path $env:UMMAHVERSE_WORKSPACE | Out-Null
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONPATH, Env:PYTHONHOME -ErrorAction SilentlyContinue

$localFfmpeg  = Join-Path $FfmpegDir 'ffmpeg.exe'
$localFfprobe = Join-Path $FfmpegDir 'ffprobe.exe'
if ((Test-Path $localFfmpeg) -and (Test-Path $localFfprobe)) {
    $env:UMMAHVERSE_FFMPEG  = $localFfmpeg
    $env:UMMAHVERSE_FFPROBE = $localFfprobe
}

# --------------------------------------------------------------------------- #
# Lancement
# --------------------------------------------------------------------------- #
Push-Location $Root
try {
    if ($Cli) {
        & $Python -m ummahverse_audio @Arguments
        exit $LASTEXITCODE
    }

    & $Python -c 'import PySide6' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Stop-WithError ("PySide6 n'est pas installé : l'interface graphique est indisponible.`n" +
                        "Relancez « .\install.ps1 », ou utilisez la CLI : .\run.ps1 -Cli --help")
    }

    & $Python -m ummahverse_audio gui @Arguments
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
