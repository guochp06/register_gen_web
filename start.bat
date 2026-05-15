@echo off
setlocal

REM Switch to the directory where this script is located
cd /d "%~dp0"

echo Starting Register Description Tool...
echo.

REM Start backend
echo Starting Backend...
cd backend
if not exist venv\Scripts\activate.bat (
    if exist venv (
        echo Found existing venv from another platform, removing...
        rmdir /s /q venv
    )
    echo Creating Python virtual environment...
    python -m venv venv || goto :error
)
call venv\Scripts\activate.bat
pip install -q -r requirements.txt || goto :error
start "Backend Server" cmd /k "uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
cd ..

REM Start frontend
echo Starting Frontend...
cd frontend
if not exist node_modules (
    echo Installing npm dependencies...
    npm install || goto :error
)
start "Frontend Server" cmd /k "npm run dev"
cd ..

echo.
echo Register Description Tool started!
echo Backend: http://localhost:8000
echo Frontend: http://localhost:5173
echo API Docs: http://localhost:8000/docs
echo.
echo Close the two server windows to stop.
pause
goto :eof

:error
echo ERROR: Failed to start. See message above.
pause
exit /b 1
