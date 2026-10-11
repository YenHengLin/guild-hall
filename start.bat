@echo off
cd /d "%~dp0"
echo Guild Hall starting up ...

REM ---- first run: install frontend deps ----
if not exist "web\node_modules" (
  echo [first run] installing frontend deps, please wait ...
  pushd "%~dp0web"
  call npm install
  popd
)

REM ---- backend + frontend run in two MINIMIZED, titled windows ----
REM    titled so stop.bat can close the windows themselves (not just the ports)
REM    no --reload: dispatch jobs often edit guild-hall's own .py files, which would
REM    make --reload restart the very backend running the dispatch (self-eviction).
REM    Restart manually after changing backend code.
start "Guild Hall Backend" /min cmd /k "cd /d %~dp0 && python -m uvicorn app.main:app --port 8848"
start "Guild Hall Frontend" /min cmd /k "cd /d %~dp0web && npm run dev"

echo Waiting for services to boot ...
timeout /t 6 >nul
start "" "http://localhost:5173"

REM remote access (Tailscale) is configured in config.yaml; the frontend window prints the URL

REM no pause -> this launcher window closes itself (one less window to babysit).
REM Two minimized consoles remain in the taskbar; run stop.bat to close both.
