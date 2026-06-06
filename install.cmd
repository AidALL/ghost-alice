@echo off
setlocal
chcp 65001 >nul 2>nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%install.ps1"

where pwsh.exe >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
    exit /b %ERRORLEVEL%
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
exit /b %ERRORLEVEL%
