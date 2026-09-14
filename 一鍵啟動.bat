@echo off
setlocal
cd /d "%~dp0"
rem Do not pass PowerShell 7 module paths into Windows PowerShell 5.1.
set "PSModulePath="
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1" %*
set "launcherExit=%errorlevel%"
if not "%launcherExit%"=="0" (
  echo.
  echo Project launch failed. Review the message above and logs in the logs folder.
  if not "%IOT_LAUNCHER_NO_PAUSE%"=="1" pause
)
endlocal & exit /b %launcherExit%
