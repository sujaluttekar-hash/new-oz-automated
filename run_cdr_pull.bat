@echo off
cd /d "%~dp0"
python -u ozonetel_cdr_pull.py %*
echo.
pause
