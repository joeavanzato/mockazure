@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-mockazure.ps1" %*
set "launcher_exit=%ERRORLEVEL%"
if not "%launcher_exit%"=="0" (
  echo.
  echo MockAzure startup failed. See the error above.
  pause
)
exit /b %launcher_exit%
