@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\launcher.py
) else (
  python tools\launcher.py
)
pause
