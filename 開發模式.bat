@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-project.ps1" -Development
if errorlevel 1 pause

