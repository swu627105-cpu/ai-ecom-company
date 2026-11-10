@echo off
setlocal
cd /d "%~dp0"
if not exist "resources\python\python.exe" (
  echo Please extract the complete APP folder before starting.
  pause
  exit /b 1
)
start "" "%~dp0AI-Ecom-Company.exe"
