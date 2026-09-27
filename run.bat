@echo off
rem ---------------------------------------------------------------------------
rem  Lance l'interface graphique (Windows).
rem
rem  Ce fichier delegue a run.ps1, qui porte toute la logique : choix de
rem  l'environnement installe, FFmpeg local, fichiers temporaires dans
rem  .cache\workspace. Les arguments sont transmis tels quels :
rem
rem      run.bat                         ouvre l'interface
rem      run.bat -Cli extract --help     passe la main a la ligne de commande
rem
rem  Se lance aussi par double-clic depuis l'explorateur.
rem ---------------------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%run.ps1"

if not exist "%SCRIPT%" (
    echo Erreur : run.ps1 est introuvable a cote de run.bat.
    pause
    exit /b 1
)

if not exist "%ROOT%.venv\Scripts\python.exe" if not exist "%ROOT%.venv-gui\Scripts\python.exe" (
    echo Application non installee.
    echo Lancez d'abord : install.bat
    pause
    exit /b 1
)

set "PS="
where pwsh >nul 2>nul && set "PS=pwsh"
if not defined PS where powershell >nul 2>nul && set "PS=powershell"
if not defined PS (
    echo Erreur : ni pwsh ni powershell n'ont ete trouves.
    pause
    exit /b 1
)

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo L'interface s'est terminee avec le code %CODE%.
    pause
)
exit /b %CODE%
