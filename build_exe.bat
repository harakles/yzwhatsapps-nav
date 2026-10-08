@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Once run.bat ile kurulumu yapin.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --windowed --name WhatsAppTabirTarayici ^
    --collect-data playwright --collect-data sv_ttk --collect-submodules wpfilter app.py
echo.
echo Hazir: dist\WhatsAppTabirTarayici\WhatsAppTabirTarayici.exe
pause
