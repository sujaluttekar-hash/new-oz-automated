@echo off
REM Scheduled launcher - no pause, logs to file
cd /d "%~dp0"
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set STAMP=%%i
python -u ozonetel_cdr_pull.py > "logs\cdr_%STAMP%.log" 2>&1
powershell -NoProfile -Command "Get-ChildItem logs\cdr_*.log | Sort-Object LastWriteTime -Descending | Select-Object -Skip 30 | Remove-Item"
