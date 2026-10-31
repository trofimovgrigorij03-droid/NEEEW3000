@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m PyInstaller --noconfirm --clean --onefile --windowed --name PORT_BOT_CHILIAD port_bot.py
if errorlevel 1 ( echo Сборка не удалась. Сначала install.bat & pause & exit /b 1 )
copy /Y config.json dist\config.json >nul
copy /Y README_RU.txt dist\README_RU.txt >nul
echo ГОТОВО: dist\PORT_BOT_CHILIAD.exe
pause
