@echo off
echo Stopping Guild Hall services (ports 8848 / 5173) ...
powershell -NoProfile -Command "foreach ($p in 8848,5173) { $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue; if ($c) { $c.OwningProcess | Select-Object -Unique | ForEach-Object { Stop-Process -Id $_ -Force; Write-Host ('  stopped port ' + $p + ' (PID ' + $_ + ')') } } else { Write-Host ('  port ' + $p + ' not running') } }"
echo Done.
timeout /t 2 >nul
