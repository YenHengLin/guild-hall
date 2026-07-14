@echo off
cd /d "%~dp0"
echo ============================================
echo    Guild Hall - starting up ...
echo ============================================

REM ---- first run: install frontend deps ----
if not exist "web\node_modules" (
  echo [first run] installing frontend deps, please wait ...
  pushd "%~dp0web"
  call npm install
  popd
)

REM ---- backend on port 8848 ----
echo [1/2] backend  -> http://localhost:8848
start "Guild Backend 8848" cmd /k "cd /d %~dp0 && python -m uvicorn app.main:app --port 8848 --reload"

REM ---- frontend on port 5173 ----
echo [2/2] frontend -> http://localhost:5173
start "Guild Frontend 5173" cmd /k "cd /d %~dp0web && npm run dev"

REM ---- wait, then open browser ----
echo Waiting for services to boot ...
timeout /t 6 >nul
start "" "http://localhost:5173"

echo.
echo Done. If the browser did not open, go to http://localhost:5173
echo To stop: close the two console windows, or run stop.bat
pause
