@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   FusionTTS - setup
echo ========================================
echo.
echo MANIFIESTO (se ejecuta SOLO si respondes s):
echo.
echo [1] venv del app (venv\):
echo     python -m venv venv + pip install -r requirements.txt
echo     Necesita: Python 3.11.x en PATH + INTERNET
echo     (fastapi, uvicorn, httpx, faster-whisper, onnxruntime, ...)
echo.
echo [2] TTS server: NO instala nada.
echo     Usa ..\OmniVoice\venv (carpeta sibling, ~5 GB, creada por el
echo     setup de OmniVoice). Si no existe: solo una advertencia
echo     (el app funciona, el TTS no).
echo.
echo [3] settings.json: si no existe, copia settings.example.json.
echo     Si tts_server_python esta vacio y existe el venv de OmniVoice,
echo     lo rellena con la ruta (imprime el cambio). No toca el resto.
echo.
echo [4] Opcional (pregunta [s/n]): pre-descarga del modelo ASR
echo     Systran/faster-whisper-medium (~1.5 GB, INTERNET).
echo     Si no, se descarga solo en el primer uso de voz.
echo.
echo NO incluido: LLM (llama-server + modelo GGUF en :8080, lo gestionas tu).
echo.

set /p "ANSWER=Ejecutar setup? [s/n] "
if /i not "%ANSWER%"=="s" (
    echo Cancelado, no se toco nada.
    exit /b 0
)

echo.
echo [1/4] venv del app...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: python no esta en PATH.
    exit /b 1
)
if not exist "venv\Scripts\python.exe" (
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: no se pudo crear el venv.
        exit /b 1
    )
)
venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: pip install --upgrade pip fallo (internet?).
    exit /b 1
)
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install -r requirements.txt fallo (internet?).
    exit /b 1
)
echo   OK: venv del app listo.
echo.

echo [2/4] TTS server (OmniVoice sibling)...
if exist "..\OmniVoice\venv\Scripts\python.exe" (
    echo   OK: ..\OmniVoice\venv encontrado.
) else (
    echo   WARNING: ..\OmniVoice\venv no existe. El app funciona, pero el TTS no.
)
echo.

echo [3/4] settings.json...
if not exist "settings.json" (
    copy /Y "settings.example.json" "settings.json" >nul
    echo   settings.json creado desde settings.example.json.
)
venv\Scripts\python.exe -c "import json,os; t=os.path.abspath(os.path.join('..','OmniVoice','venv','Scripts','python.exe')); p='settings.json'; d=json.load(open(p,encoding='utf-8')); v=d.get('tts_server_python') or ''; (d.update(tts_server_python=t), json.dump(d,open(p,'w',encoding='utf-8'),indent=2,ensure_ascii=False), print('  patch: tts_server_python =', t)) if (not v and os.path.exists(t)) else print('  tts_server_python: sin cambios')"
echo.

echo [4/4] Pre-descarga del modelo ASR (opcional)...
set /p "ASR=Descargar Systran/faster-whisper-medium ahora (~1.5 GB, internet)? [s/n] "
if /i "%ASR%"=="s" (
    venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('Systran/faster-whisper-medium', device='cpu')"
    if errorlevel 1 (
        echo   WARNING: la pre-descarga fallo; se bajara en el primer uso de voz.
    ) else (
        echo   OK: modelo ASR descargado.
    )
) else (
    echo   Se descargara solo en el primer uso de voz.
)
echo.

echo ========================================
echo   Setup completado!
echo ========================================
echo.
echo Siguiente: start.bat
echo Recordatorio: el LLM debe estar corriendo en :8080 (llama-server) antes de usar el chat.
echo.
pause
