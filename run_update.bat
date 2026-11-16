@echo off
rem Daily incremental update, run by Windows Task Scheduler (or by double-click).
rem Runs src\update.py with the project's virtual environment and appends
rem all output to logs\update.log.

rem Work in the folder of this file, whatever folder the task starts in.
cd /d "%~dp0"
if not exist logs mkdir logs

rem Write the log in UTF-8, not the Windows code page.
set PYTHONUTF8=1

echo ==== %date% %time% ==== >> logs\update.log
".venv\Scripts\python.exe" src\update.py >> logs\update.log 2>&1
set exit_code=%errorlevel%
echo exit code: %exit_code% >> logs\update.log
echo. >> logs\update.log

rem Pass the exit code on, so Task Scheduler can show whether the run failed.
exit /b %exit_code%
