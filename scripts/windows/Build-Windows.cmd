@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0..\.."
where py >nul 2>nul
if errorlevel 1 (
  python scripts\build-windows.py %*
) else (
  py -3 scripts\build-windows.py %*
)
set "result=%errorlevel%"
popd
if not "%result%"=="0" pause
exit /b %result%
