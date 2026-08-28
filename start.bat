@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv no existe. Ejecuta setup.bat primero.
    pause
    exit /b 1
)
venv\Scripts\python.exe launcher.py
if errorlevel 1 (
    echo.
    echo El launcher termino con error. Revisa los mensajes de arriba.
    pause
)
