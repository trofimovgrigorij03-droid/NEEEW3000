@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
if errorlevel 1 ( echo Не удалось установить зависимости. Нужен Python 3.10+. & pause & exit /b 1 )
echo Готово. Запуск: py port_bot.py
pause
