@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Cineverse's virtual environment was not found.
    echo Check that the .venv folder exists in this project.
    pause
    exit /b 1
)

echo Starting Cineverse. Keep this window open while using the website.
".venv\Scripts\python.exe" run.py

echo.
echo Cineverse stopped. Review any error above, then press any key to close.
pause >nul
