@echo off
rem ---------------------------------------------------------------------------
rem  Installation complete, entierement contenue dans ce dossier (Windows).
rem
rem  Ce fichier delegue a install.ps1, qui porte toute la logique : une seule
rem  implementation, donc aucun risque de divergence entre les deux points
rem  d'entree. Les arguments sont transmis tels quels :
rem
rem      install.bat
rem      install.bat -WithFfmpeg
rem      install.bat -Clean -NoGui
rem
rem  Tout ce qui est installe vit sous ce dossier : supprimer le dossier
rem  desinstalle tout. Aucun droit administrateur n'est necessaire.
rem ---------------------------------------------------------------------------
setlocal
set "ROOT=%~dp0"
set "SCRIPT=%ROOT%install.ps1"

if not exist "%SCRIPT%" (
    echo Erreur : install.ps1 est introuvable a cote de install.bat.
    exit /b 1
)

set "PS="
where pwsh >nul 2>nul && set "PS=pwsh"
if not defined PS where powershell >nul 2>nul && set "PS=powershell"
if not defined PS (
    echo Erreur : ni pwsh ni powershell n'ont ete trouves.
    echo PowerShell est fourni avec Windows ; verifiez votre PATH, ou lancez
    echo directement :  install.ps1
    exit /b 1
)

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
    echo.
    echo L'installation a echoue ^(code %CODE%^).
)
exit /b %CODE%
