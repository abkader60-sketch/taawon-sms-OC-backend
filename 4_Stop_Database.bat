@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  SMS Database Server - Clean Shutdown
REM ============================================================

title SMS Database - Stop
color 0E

set "PG_BIN=D:\Security Management System (SMS)\Db\postgresql17.9-binaries\pgsql\bin"
set "PG_DATA=D:\Security Management System (SMS)\Db\postgresql17.9-binaries\pgsql\data"

echo.
echo ============================================================
echo   SMS Database Server - Clean Shutdown
echo ============================================================
echo.

REM --- Check if anything is on port 5432 first ---
netstat -ano | findstr ":5432" | findstr "LISTENING" >nul
if not !errorlevel!==0 (
    echo  PostgreSQL does not appear to be running. Nothing to stop.
    echo.
    echo ============================================================
    pause
    exit /b 0
)

echo  Stopping PostgreSQL gracefully...
echo.
"!PG_BIN!\pg_ctl.exe" -D "!PG_DATA!" stop -m fast
set "PG_RESULT=!errorlevel!"

echo.
if "!PG_RESULT!"=="0" (
    echo ============================================================
    echo   SUCCESS: PostgreSQL has been stopped cleanly.
    echo ============================================================
) else (
    echo ============================================================
    echo   WARNING: pg_ctl returned exit code !PG_RESULT!.
    echo   The server may still be running. Check Task Manager
    echo   or run 1_Start_Database.bat to see current state.
    echo ============================================================
)

echo.
pause
endlocal
