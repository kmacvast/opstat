@echo off
REM Build a standalone opstat.exe for Windows.
REM Output: releases\opstat-windows-x86_64.exe (or arm64)
setlocal

cd /d "%~dp0\.."

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: python not found on PATH
  exit /b 1
)

python -m pip install --upgrade pip wheel
python -m pip install "pyinstaller>=6.0"
python scripts\build_opstat.py %*

echo.
echo Binary ready under releases\. Example:
echo   releases\opstat-windows-x86_64.exe --help
endlocal
