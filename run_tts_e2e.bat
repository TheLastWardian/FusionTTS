@echo off
cd /d "%~dp0"
venv\Scripts\python.exe tts_e2e_check.py %*
pause
