@echo off
setlocal
cd /d "%~dp0"
echo ============================================
echo           SIA - one-time setup
echo ============================================
echo.

where python >nul 2>nul || (echo [ERROR] Python 3.11-3.13 is not on PATH. & pause & exit /b 1)
where npm    >nul 2>nul || (echo [ERROR] Node.js 18+ is not on PATH. & pause & exit /b 1)

if not exist "venv\Scripts\activate.bat" (
    echo [1/6] Creating virtual environment...
    python -m venv venv || (echo [ERROR] could not create venv & pause & exit /b 1)
) else (
    echo [1/6] Virtual environment found.
)
call venv\Scripts\activate.bat

echo [2/6] Installing Python packages (first time takes a while: Whisper/torch)...
python -m pip install --upgrade pip >nul
pip install -r backend\requirements.txt || (echo [ERROR] pip install failed & pause & exit /b 1)

echo [3/6] Installing the Playwright browser...
python -m playwright install chromium

echo [4/6] Installing frontend packages...
pushd frontend
call npm install || (echo [ERROR] npm install failed & popd & pause & exit /b 1)
popd

echo [5/6] Checking ffmpeg (needed for local Whisper speech-to-text)...
where ffmpeg >nul 2>nul && (echo       ffmpeg found.) || (echo [WARN] ffmpeg not found. Install it ^(winget install ffmpeg^) or set SARVAM_API_KEY in .env.)

echo [6/6] Checking .env...
if not exist ".env" (
    copy .env.example .env >nul
    echo [ACTION] .env created - open it and add GROQ_API_KEY and OPENAI_API_KEY.
) else (
    echo       .env found.
)

echo.
echo Setup complete. Start SIA with:  start.bat
pause
