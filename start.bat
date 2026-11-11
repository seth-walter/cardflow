@echo off
rem CardFlow launcher for Windows: sets up on first run, then starts the app.
cd /d "%~dp0"

if not exist venv\Scripts\python.exe (
    echo First run: creating a virtual environment...
    python -m venv venv || (echo Python was not found. Install it from python.org and tick "Add to PATH". & pause & exit /b 1)
)

venv\Scripts\python.exe -m pip install --quiet --upgrade pip
venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || (echo Installing requirements failed. & pause & exit /b 1)

if not exist .env (
    copy .env.example .env >nul
    echo Created .env from .env.example - fill it in ^(see README^), then run start.bat again.
    notepad .env
    pause
    exit /b 0
)

venv\Scripts\python.exe -m streamlit run CardFlow.py
pause
