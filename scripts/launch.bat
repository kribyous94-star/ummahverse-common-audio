@echo off
setlocal
set "ROOT=%~dp0.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Application non installee. Lancer scripts\bootstrap.bat.
  exit /b 1
)
"%PY%" -m ummahverse_audio gui
if errorlevel 1 (
  echo Echec du lancement de l'interface.
  pause
  exit /b 1
)
