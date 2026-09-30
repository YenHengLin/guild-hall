@echo off
echo Stopping Guild Hall services ...

REM ---- 1) close the two titled console windows (and their process trees) ----
REM    /T kills the child uvicorn/node too; /F forces it. This also CLOSES the
REM    console windows that start.bat opened (the old stop.bat left them behind).
taskkill /FI "WINDOWTITLE eq Guild Hall Backend*"  /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Guild Hall Frontend*" /T /F >nul 2>&1

REM ---- 2) fallback: kill whatever still holds the ports (started by hand etc.) ----
REM    Use taskkill /T to kill the WHOLE process TREE, not just the main process.
REM    The backend spawns claude/node children that INHERIT the listening socket;
REM    killing only the parent leaves orphans holding the socket -> zombie LISTEN
REM    that keeps the port bound, so the next start fails with bind error 10048.
REM    Then verify the port is actually released (zombie sockets clear with a delay).
powershell -NoProfile -Command "foreach ($p in 8848,5173) { $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($c) { $c.OwningProcess | Select-Object -Unique | ForEach-Object { taskkill /F /T /PID $_ 2>$null | Out-Null; Write-Host ('  killed port ' + $p + ' tree (PID ' + $_ + ')') } } else { Write-Host ('  port ' + $p + ' already free') } }; foreach ($p in 8848,5173) { for ($i=0; $i -lt 10; $i++) { if (-not (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue)) { break }; Start-Sleep -Milliseconds 500 }; if (Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue) { Write-Host ('  WARNING: port ' + $p + ' still held (zombie socket) - reboot may be needed') } else { Write-Host ('  port ' + $p + ' confirmed free') } }"

echo Done.
timeout /t 2 >nul
