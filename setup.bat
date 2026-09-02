@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   FusionTTS - setup
echo ========================================
echo.
echo MANIFEST - runs only if you answer s:
echo.
echo   Requires: Windows 10/11, Python 3.11 in PATH, git in PATH, INTERNET.
echo.
echo   [1] app venv + requirements ............... ~1 GB   (PyPI)
echo   [2] clone OmniVoice to ..\OmniVoice ....... ~100 MB (GitHub) - skipped if present
echo   [3] OmniVoice venv: torch cu121 + package
echo       + TTS server deps ..................... ~5 GB
echo   [4] TTS model k2-fsa/OmniVoice ............ ~3 GB   (HuggingFace) - skipped if in cache
echo   [5] ASR model faster-whisper-medium ....... ~1.4 GB (HuggingFace) - optional, will ask
echo   [6] settings.json: created from settings.example.json if missing;
echo       fills omnivoice_dir / tts_server_python. Nothing else is touched.
echo.

set /p "ANSWER=Run setup? [s/n] "
if /i not "%ANSWER%"=="s" (
    echo Cancelled, nothing was done.
    exit /b 0
)

echo.
echo [0/6] Prerequisites...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: python not found in PATH.
    echo Install Python 3.11 from python.org and check "Add python.exe to PATH".
    exit /b 1
)
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: git not found in PATH.
    echo Install git, e.g.: winget install --id Git.Git
    exit /b 1
)
echo   OK: python and git found.
echo.

echo [1/6] App venv...
if not exist "venv\Scripts\python.exe" (
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: could not create the app venv.
        exit /b 1
    )
)
venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 (
    echo ERROR: pip upgrade failed, check internet.
    exit /b 1
)
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: pip install -r requirements.txt failed, check internet.
    exit /b 1
)
echo   OK: app venv ready.
echo.

echo [2/6] OmniVoice repo...
if exist "..\OmniVoice\pyproject.toml" (
    echo   OK: ..\OmniVoice already exists, skipped.
) else (
    git clone https://github.com/k2-fsa/OmniVoice.git ..\OmniVoice
    if errorlevel 1 (
        echo ERROR: git clone failed, check internet.
        exit /b 1
    )
    echo   OK: repo cloned to ..\OmniVoice.
)
echo.

echo [3/6] OmniVoice venv...
if exist "..\OmniVoice\venv\Scripts\python.exe" (
    echo   OK: ..\OmniVoice\venv already exists, skipped.
) else (
    if not exist "..\OmniVoice\pyproject.toml" (
        echo ERROR: ..\OmniVoice repo is missing.
        exit /b 1
    )
    pushd ..\OmniVoice
    python -m venv venv
    if errorlevel 1 (
        popd
        echo ERROR: could not create ..\OmniVoice\venv.
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip
    if errorlevel 1 (
        popd
        echo ERROR: pip upgrade failed, check internet.
        exit /b 1
    )
    echo   Installing torch cu121, big download, this takes a while...
    venv\Scripts\python.exe -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
    if errorlevel 1 (
        popd
        echo ERROR: torch install failed, check internet.
        exit /b 1
    )
    echo   Installing the OmniVoice package...
    venv\Scripts\python.exe -m pip install -e .
    if errorlevel 1 (
        popd
        echo ERROR: OmniVoice package install failed, check internet.
        exit /b 1
    )
    echo   Installing TTS server deps...
    venv\Scripts\python.exe -m pip install -r "%~dp0tts-server\requirements.txt"
    if errorlevel 1 (
        popd
        echo ERROR: TTS server deps install failed, check internet.
        exit /b 1
    )
    popd
    echo   OK: OmniVoice venv ready.
)
echo.

echo [4/6] TTS model k2-fsa/OmniVoice...
set "HFCACHE=%USERPROFILE%\.cache\huggingface\hub"
if exist "%HFCACHE%\models--k2-fsa--OmniVoice" (
    echo   OK: model already in the HF cache, skipped.
) else (
    if not exist "..\OmniVoice\venv\Scripts\python.exe" (
        echo ERROR: ..\OmniVoice\venv is missing, cannot download the model.
        exit /b 1
    )
    echo   Downloading ~3 GB, this takes a while...
    ..\OmniVoice\venv\Scripts\python.exe -c "from huggingface_hub import snapshot_download; snapshot_download('k2-fsa/OmniVoice')"
    if errorlevel 1 (
        echo ERROR: TTS model download failed, check internet.
        exit /b 1
    )
    echo   OK: TTS model downloaded.
)
echo.

echo [5/6] ASR model, optional...
set /p "ASR=Pre-download the ASR model Systran/faster-whisper-medium, ~1.4 GB, now? [s/n] "
if /i "%ASR%"=="s" (
    venv\Scripts\python.exe -c "from faster_whisper import WhisperModel; WhisperModel('Systran/faster-whisper-medium', device='cpu')"
    if errorlevel 1 (
        echo   WARNING: pre-download failed, it will download on first voice use.
    ) else (
        echo   OK: ASR model downloaded.
    )
) else (
    echo   Skipped, it will download on first voice use.
)
echo.

echo [6/6] settings.json...
if not exist "settings.json" (
    copy /Y "settings.example.json" "settings.json" >nul
    echo   Created from settings.example.json.
)
venv\Scripts\python.exe -c "import json,os; p='settings.json'; d=json.load(open(p,encoding='utf-8')); ov=(d.get('omnivoice_dir') or '').strip() or os.path.abspath(os.path.join('..','OmniVoice')); t=os.path.join(ov,'venv','Scripts','python.exe'); ch=[]; (not (d.get('omnivoice_dir') or '').strip()) and (d.__setitem__('omnivoice_dir', ov), ch.append('omnivoice_dir='+ov)); (not (d.get('tts_server_python') or '').strip() and os.path.exists(t)) and (d.__setitem__('tts_server_python', os.path.abspath(t)), ch.append('tts_server_python='+os.path.abspath(t))); json.dump(d,open(p,'w',encoding='utf-8'),indent=2,ensure_ascii=False); [print('  patch:', c) for c in ch] if ch else print('  omnivoice_dir / tts_server_python: no changes')"
echo.

echo ========================================
echo   Setup complete
echo ========================================
echo.
echo   Next:
echo     1. Start your LLM on port 8080, e.g. llama-server
echo     2. start.bat
echo     3. Open http://localhost:8000
echo.
pause
