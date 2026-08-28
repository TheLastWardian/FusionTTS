@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
    echo ERROR: venv no existe. Ejecuta setup.bat primero.
    pause
    exit /b 1
)
venv\Scripts\python.exe launcher.py
