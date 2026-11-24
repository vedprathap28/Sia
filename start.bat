@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\activate.bat" (echo Run setup.bat first. & pause & exit /b 1)
if not exist ".env" (echo .env missing - run setup.bat and add your API keys. & pause & exit /b 1)
start "SIA backend"  cmd /k "call venv\Scripts\activate.bat && python -m backend.main"
start "SIA frontend" cmd /k "cd frontend && npm run dev"
timeout /t 6 >nul
start "" http://localhost:5173
