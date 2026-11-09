@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
set "IRON_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "IRON_PYTHON=.venv\Scripts\python.exe"
if "%~1"=="" (
  "%IRON_PYTHON%" tools\native_campaign.py --help
) else (
  "%IRON_PYTHON%" tools\native_campaign.py %*
)
exit /b %errorlevel%
