@echo off
setlocal
pushd "%~dp0.." || exit /b 1
where ffmpeg >nul 2>nul || (echo ffmpeg requis dans PATH & popd & exit /b 1)
where ffprobe >nul 2>nul || (echo ffprobe requis dans PATH & popd & exit /b 1)
if not exist "pyproject.toml" (
  echo Claude doit creer pyproject.toml et le module ummahverse_audio avant installation.
  popd
  exit /b 1
)
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)
if errorlevel 1 (popd & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (popd & exit /b 1)
".venv\Scripts\python.exe" -m pip install -e ".[dev,gui]"
if errorlevel 1 (popd & exit /b 1)
popd
echo Installation terminee. Lancez scripts\launch.bat.
