<#
.SYNOPSIS
    Installation complète et entièrement contenue dans ce dossier (Windows).

.DESCRIPTION
    Tout ce que ce script crée vit sous la racine du projet :

      .venv\              environnement Python principal (moteur, CLI, tests, interface)
      .venv-gui\          second environnement, créé seulement si l'interface ne peut
                          pas être installée dans le premier
      .cache\pip\         cache de téléchargement pip — rien dans %LOCALAPPDATA%\pip
      .cache\workspace\   fichiers temporaires de traitement, utilisés par run.ps1
      tools\ffmpeg\       ffmpeg.exe et ffprobe.exe locaux, avec -WithFfmpeg
      .install-state      trace de ce qui a été installé

    Supprimer le dossier du projet supprime la totalité de l'installation. Aucun
    paquet système n'est installé, aucun droit administrateur n'est demandé, rien
    n'est écrit dans votre profil utilisateur.

.PARAMETER Python
    Interpréteur à utiliser pour l'environnement principal (chemin vers python.exe).

.PARAMETER WithFfmpeg
    Télécharge une construction de FFmpeg dans tools\ffmpeg au lieu d'utiliser celle
    du système.

.PARAMETER NoGui
    N'installe pas PySide6 (ligne de commande seule).

.PARAMETER Clean
    Repart de zéro : supprime .venv, .venv-gui, .cache et tools avant d'installer.

.PARAMETER Offline
    Interdit tout accès réseau ; échoue si un paquet manque.

.PARAMETER PurgeUserCache
    Supprime aussi le cache pip du profil utilisateur, hors du dossier.

.EXAMPLE
    .\install.ps1
.EXAMPLE
    .\install.ps1 -WithFfmpeg -Clean
#>
[CmdletBinding()]
param(
    [string] $Python,
    [switch] $WithFfmpeg,
    [switch] $NoGui,
    [switch] $Clean,
    [switch] $Offline,
    [switch] $PurgeUserCache
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root      = $PSScriptRoot
$Venv      = Join-Path $Root '.venv'
$VenvGui   = Join-Path $Root '.venv-gui'
$Cache     = Join-Path $Root '.cache'
$PipCache  = Join-Path $Cache 'pip'
$Workspace = Join-Path $Cache 'workspace'
$Tools     = Join-Path $Root 'tools'
$FfmpegDir = Join-Path $Tools 'ffmpeg'
$State     = Join-Path $Root '.install-state'

# Construction de FFmpeg pour -WithFfmpeg (archive « essentials », statique).
$FfmpegUrl = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'

function Write-Step([string] $Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok   ([string] $Message) { Write-Host "  [ok] $Message" -ForegroundColor Green }
function Write-Warn ([string] $Message) { Write-Host "  [!]  $Message" -ForegroundColor Yellow }
function Stop-WithError([string] $Message) { Write-Host "Erreur : $Message" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------------------- #
# Confinement : tout ce que pip écrit reste dans le dossier
# --------------------------------------------------------------------------- #
$env:PIP_CACHE_DIR = $PipCache
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
$env:PYTHONNOUSERSITE = '1'
Remove-Item Env:PYTHONPATH, Env:PYTHONHOME, Env:PIP_TARGET, Env:PIP_USER -ErrorAction SilentlyContinue

$PipFlags = @('--disable-pip-version-check')
if ($Offline) { $PipFlags += '--no-index' }

# --------------------------------------------------------------------------- #
# Nettoyage préalable
# --------------------------------------------------------------------------- #
if ($Clean) {
    Write-Step 'Suppression de l''installation précédente'
    foreach ($path in @($Venv, $VenvGui, $Cache, $Tools, $State,
                        (Join-Path $Root '.pytest_cache'))) {
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
    Get-ChildItem -Path (Join-Path $Root 'src'), (Join-Path $Root 'tests') `
        -Filter '__pycache__' -Recurse -Directory -ErrorAction SilentlyContinue |
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Get-ChildItem -Path (Join-Path $Root 'src') -Filter '*.egg-info' -Directory `
        -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
    Write-Ok 'dossier remis à l''état initial'
}

New-Item -ItemType Directory -Force -Path $PipCache, $Workspace | Out-Null

# --------------------------------------------------------------------------- #
# Choix de l'interpréteur Python
# --------------------------------------------------------------------------- #
function Get-PythonVersion([string] $Exe) {
    try { (& $Exe -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null) } catch { $null }
}

function Test-PythonUsable([string] $Exe) {
    try {
        & $Exe -c 'import sys, venv, ensurepip; raise SystemExit(0 if (3,10) <= sys.version_info[:2] < (4,0) else 1)' 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Get-PythonCandidates {
    $found = [System.Collections.Generic.List[string]]::new()
    # « py -0p » liste les interpréteurs connus du lanceur Windows, du plus récent au
    # plus ancien ; c'est la source la plus fiable sur ce système.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($version in @('3.13', '3.12', '3.11', '3.10', '3')) {
            try {
                $path = (& py "-$version" -c 'import sys; print(sys.executable)' 2>$null)
                if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path $path)) { $found.Add($path) }
            } catch { }
        }
    }
    foreach ($name in @('python', 'python3')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $found.Add($command.Source) }
    }
    return ($found | Select-Object -Unique)
}

Write-Step 'Recherche d''un interpréteur Python'
if ($Python) {
    if (-not (Test-Path $Python)) { Stop-WithError "-Python : « $Python » est introuvable." }
    $candidates = @((Resolve-Path $Python).Path)
} else {
    $candidates = @(Get-PythonCandidates)
}
if (-not $candidates -or $candidates.Count -eq 0) {
    Stop-WithError "Aucun interpréteur Python trouvé.`nInstallez Python 3.10+ : winget install --id Python.Python.3.12"
}

$Interpreter = $candidates | Where-Object { Test-PythonUsable $_ } | Select-Object -First 1
if (-not $Interpreter) {
    Write-Host 'Interpréteurs examinés :' -ForegroundColor Yellow
    foreach ($candidate in $candidates) {
        Write-Host ("  {0} (Python {1})" -f $candidate, (Get-PythonVersion $candidate))
    }
    Stop-WithError 'Aucun interpréteur Python 3.10+ utilisable (module venv requis).'
}
Write-Ok ("Python {0} — {1}" -f (Get-PythonVersion $Interpreter), $Interpreter)

# --------------------------------------------------------------------------- #
# FFmpeg
# --------------------------------------------------------------------------- #
function Install-LocalFfmpeg {
    if ($Offline) { Stop-WithError '-WithFfmpeg est incompatible avec -Offline.' }
    Write-Step 'Téléchargement de FFmpeg dans tools\ffmpeg'
    Write-Warn "source externe : $FfmpegUrl — vérifiez que cela correspond à votre politique"
    New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null
    $archive = Join-Path $Cache 'ffmpeg.zip'
    $staging = Join-Path $Cache 'ffmpeg-extract'
    try {
        Invoke-WebRequest -Uri $FfmpegUrl -OutFile $archive -UseBasicParsing
    } catch {
        Stop-WithError "Téléchargement de FFmpeg impossible : $($_.Exception.Message)"
    }
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    Expand-Archive -Path $archive -DestinationPath $staging -Force
    foreach ($name in @('ffmpeg.exe', 'ffprobe.exe')) {
        $binary = Get-ChildItem -Path $staging -Filter $name -Recurse -File |
                  Select-Object -First 1
        if (-not $binary) { Stop-WithError "$name absent de l'archive téléchargée." }
        Copy-Item $binary.FullName (Join-Path $FfmpegDir $name) -Force
    }
    Remove-Item -Recurse -Force $staging, $archive -ErrorAction SilentlyContinue
    Write-Ok ((& (Join-Path $FfmpegDir 'ffmpeg.exe') -version 2>$null | Select-Object -First 1))
}

Write-Step 'Vérification de FFmpeg'
$FfmpegSource = 'absent'
$localFfmpeg = Join-Path $FfmpegDir 'ffmpeg.exe'
$localFfprobe = Join-Path $FfmpegDir 'ffprobe.exe'
if ($WithFfmpeg) {
    Install-LocalFfmpeg
    $FfmpegSource = 'local'
} elseif ((Test-Path $localFfmpeg) -and (Test-Path $localFfprobe)) {
    Write-Ok 'ffmpeg local déjà présent dans tools\ffmpeg'
    $FfmpegSource = 'local'
} elseif ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and
          (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Ok ((& ffmpeg -version 2>$null | Select-Object -First 1) + ' (système)')
    $FfmpegSource = 'système'
} else {
    Write-Warn 'ffmpeg/ffprobe introuvables.'
    Write-Warn '  soit : winget install --id Gyan.FFmpeg  (puis rouvrir le terminal)'
    Write-Warn '  soit : .\install.ps1 -WithFfmpeg        (copie locale, rien hors du dossier)'
}

# --------------------------------------------------------------------------- #
# Environnements Python
# --------------------------------------------------------------------------- #
function New-Venv([string] $Interpreter, [string] $Target) {
    if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
    & $Interpreter -m venv $Target
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Création de l'environnement échouée ($Target)." }
    $python = Join-Path $Target 'Scripts\python.exe'
    & $python -m pip install @PipFlags --quiet --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { Stop-WithError "Mise à jour de pip impossible dans $Target." }
    return $python
}

function Install-Package([string] $VenvPython, [string] $Extras) {
    Push-Location $Root
    try {
        & $VenvPython -m pip install @PipFlags --quiet -e ".[$Extras]"
        return ($LASTEXITCODE -eq 0)
    } finally { Pop-Location }
}

Write-Step 'Environnement principal (.venv)'
$VenvPython = New-Venv $Interpreter $Venv
if (-not (Install-Package $VenvPython 'dev')) {
    Stop-WithError 'Installation du moteur et des tests impossible.'
}
Write-Ok 'moteur, CLI et tests installés'

$GuiVenv = ''
if (-not $NoGui) {
    Write-Step 'Interface graphique (PySide6)'
    if (Install-Package $VenvPython 'dev,gui') {
        $GuiVenv = $Venv
        Write-Ok 'interface installée dans .venv'
    } else {
        Write-Warn ("PySide6 ne s'installe pas pour Python {0}." -f (Get-PythonVersion $Interpreter))
        Write-Warn 'recherche d''un autre interpréteur pour un second environnement dédié…'
        foreach ($candidate in $candidates) {
            if ($candidate -eq $Interpreter) { continue }
            if (-not (Test-PythonUsable $candidate)) { continue }
            Write-Step ("Essai avec Python {0} — {1}" -f (Get-PythonVersion $candidate), $candidate)
            $guiPython = New-Venv $candidate $VenvGui
            if (Install-Package $guiPython 'gui') {
                $GuiVenv = $VenvGui
                Write-Ok ("interface installée dans .venv-gui (Python {0})" -f (Get-PythonVersion $candidate))
                break
            }
            Remove-Item -Recurse -Force $VenvGui -ErrorAction SilentlyContinue
        }
        if (-not $GuiVenv) {
            Write-Warn 'Aucun interpréteur disponible ne permet d''installer PySide6.'
            Write-Warn 'La CLI reste pleinement utilisable ; l''interface graphique ne le sera pas.'
        }
    }
} else {
    Write-Ok 'interface graphique ignorée (-NoGui)'
}

# --------------------------------------------------------------------------- #
# État de l'installation, relu par run.ps1
# --------------------------------------------------------------------------- #
@(
    "installed_at=$([DateTime]::Now.ToString('s'))"
    "python=$Interpreter"
    "python_version=$(Get-PythonVersion $Interpreter)"
    'venv=.venv'
    "gui_venv=$(if ($GuiVenv) { Split-Path $GuiVenv -Leaf } else { '' })"
    "ffmpeg=$FfmpegSource"
) | Set-Content -Path $State -Encoding UTF8

# --------------------------------------------------------------------------- #
# Vérification
# --------------------------------------------------------------------------- #
Write-Step 'Vérification'
& $VenvPython -c 'import ummahverse_audio, numpy, scipy; print(f"  moteur {ummahverse_audio.__version__}, numpy {numpy.__version__}, scipy {scipy.__version__}")'
if ($LASTEXITCODE -ne 0) { Stop-WithError "Le moteur ne s'importe pas." }
if ($GuiVenv) {
    $guiPython = Join-Path $GuiVenv 'Scripts\python.exe'
    & $guiPython -c 'import PySide6; print(f"  PySide6 {PySide6.__version__}")'
    if ($LASTEXITCODE -ne 0) { Write-Warn 'PySide6 est installé mais ne s''importe pas.' }
}

if ($PurgeUserCache) {
    Write-Step 'Suppression du cache pip personnel (hors du dossier)'
    $userCache = Join-Path $env:LOCALAPPDATA 'pip'
    if (Test-Path $userCache) { Remove-Item -Recurse -Force $userCache }
    Write-Ok "$userCache supprimé"
}

Write-Host ''
Write-Host "Installation terminée. Tout est dans : $Root" -ForegroundColor Cyan
Write-Host '  interface         : .\run.ps1   (ou run.bat)'
Write-Host '  ligne de commande : .\.venv\Scripts\python.exe -m ummahverse_audio --help'
Write-Host '  tests             : .\.venv\Scripts\python.exe -m pytest -q'
if ($FfmpegSource -eq 'absent') {
    Write-Host ''
    Write-Host 'FFmpeg manque encore : installez-le avant de traiter un fichier.' -ForegroundColor Yellow
}
Write-Host ''
Write-Host 'Pour tout désinstaller : supprimez ce dossier. Rien n''est écrit ailleurs.'
