@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap-project.ps1"
if errorlevel 1 (
  echo.
  echo Bootstrap failed. Review the message above, then press any key to close.
  pause >nul
)
endlocal
