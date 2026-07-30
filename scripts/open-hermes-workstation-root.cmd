@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Hermes Workstation - ROOT maintenance

where wsl.exe >nul 2>&1
if errorlevel 1 (
    echo ERROR: wsl.exe is not available on this Windows host.
    pause
    exit /b 1
)

echo.
echo This opens a LOCAL ROOT shell in the isolated Hermes-Workstation distro.
echo Use it only for operator maintenance such as apt package installation.
echo It does not grant sudo or root access to hermes-worker or the Hermes plugin.
echo.
choice /C YN /N /M "Open the Hermes-Workstation ROOT console? [Y/N] "
if errorlevel 2 (
    echo Cancelled.
    exit /b 1
)

wsl.exe --distribution Hermes-Workstation --user root --cd /home/hermes-worker/workspace
set "HERMES_WSL_EXIT=%ERRORLEVEL%"
if not "%HERMES_WSL_EXIT%"=="0" (
    echo.
    echo ERROR: Hermes-Workstation could not be opened. Run this script as the
    echo Windows user that owns the distro and verify it with: wsl.exe --list --verbose
    pause
)
exit /b %HERMES_WSL_EXIT%
