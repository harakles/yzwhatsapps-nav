@echo off
chcp 65001 >nul
title WhatsApp Tabir Tarayici
cd /d "%~dp0"

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
    echo Sanal ortam olusturuluyor...
    %PY% -m venv .venv
    if errorlevel 1 goto :nopython
)

if not exist ".venv\.kurulum_tamam" (
    echo Gerekli paketler kuruluyor ^(ilk calistirmada birkac dakika surebilir^)...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :piperror
    echo ok> ".venv\.kurulum_tamam"
)

where claude >nul 2>nul
if errorlevel 1 if not exist "%USERPROFILE%\.local\bin\claude.exe" (
    echo.
    echo [UYARI] Claude Code bulunamadi. Claude aboneliginizle kullanmak icin PowerShell'de:
    echo     irm https://claude.ai/install.ps1 ^| iex
    echo ardindan bir kez "claude" yazip abonelik hesabinizla giris yapin.
    echo.
    pause
)

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:nopython
echo Python bulunamadi. https://www.python.org/downloads/ adresinden Python 3.10+ kurun
echo (kurulumda "Add python.exe to PATH" kutusunu isaretleyin).
pause
exit /b 1

:piperror
echo Paket kurulumu basarisiz oldu. Internet baglantinizi kontrol edip tekrar deneyin.
pause
exit /b 1
