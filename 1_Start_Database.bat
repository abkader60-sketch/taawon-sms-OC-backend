@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  SMS Database Server - Smart Startup Script (v2)
REM  Checks for running instances, stale locks, and starts cleanly
REM  v2 fix: capture pg_ctl exit code into a variable BEFORE the
REM          if/else block, since errorlevel can be reset by other
REM          commands inside the block.
REM ============================================================

title SMS Database Server
color 0A

set "PG_BIN=D:\Security Management System (SMS)\Db\postgresql17.9-binaries\pgsql\bin"
set "PG_DATA=D:\Security Management System (SMS)\Db\postgresql17.9-binaries\pgsql\data"
set "PG_LOG=!PG_DATA!\logfile.txt"
set "LOCK_FILE=!PG_DATA!\postmaster.pid"

echo.
echo ============================================================
echo   SMS Database Server - Health Check and Startup
echo ============================================================
echo.

REM --- Step 1: Check if port 5432 is already in use ---
echo [1/4] Checking if port 5432 is already in use...
netstat -ano | findstr ":5432" | findstr "LISTENING" >nul
if !errorlevel!==0 (
    echo       --^> Port 5432 is ALREADY in use.
    echo.
    echo  *** PostgreSQL appears to be running already. ***
    echo  *** No action needed - skip to running 2_Start_App_Server.bat ***
    echo.
    echo ============================================================
    pause
    exit /b 0
)
echo       --^> Port 5432 is free. Good.
echo.

REM --- Step 2: Check for any leftover postgres.exe processes ---
echo [2/4] Checking for leftover postgres.exe processes...
tasklist | findstr /i "postgres.exe" >nul
if !errorlevel!==0 (
    echo       --^> WARNING: Found leftover postgres.exe processes.
    echo.
    echo  *** Something is wrong. Open PowerShell and run: ***
    echo  ***   Get-Process postgres ^| Stop-Process -Force ***
    echo  *** Then run this bat file again. ***
    echo.
    echo ============================================================
    pause
    exit /b 1
)
echo       --^> No leftover processes. Good.
echo.

REM --- Step 3: Check for and remove stale lock file ---
echo [3/4] Checking for stale lock file...
if exist "!LOCK_FILE!" (
    echo       --^> Found stale lock file. Removing it...
    del "!LOCK_FILE!" >nul 2>&1
    if exist "!LOCK_FILE!" (
        echo       --^> ERROR: Could not delete the lock file.
        echo.
        echo  *** Try deleting manually: ***
        echo  *** !LOCK_FILE! ***
        echo.
        echo ============================================================
        pause
        exit /b 1
    )
    echo       --^> Lock file removed. Good.
) else (
    echo       --^> No stale lock file. Good.
)
echo.

REM --- Step 4: Start the database ---
echo [4/4] Starting PostgreSQL...
echo.
"!PG_BIN!\pg_ctl.exe" -D "!PG_DATA!" -l "!PG_LOG!" start
set "PG_RESULT=!errorlevel!"

echo.
if "!PG_RESULT!"=="0" (
    echo ============================================================
    echo   SUCCESS: PostgreSQL is now running on port 5432.
    echo.
    echo   NEXT STEPS:
    echo     1. Run 3_Verify_Database.bat   ^(optional - check tables^)
    echo     2. Run 2_Start_App_Server.bat  ^(start the FastAPI app^)
    echo     3. Open index.html in your browser
    echo ============================================================
) else (
    echo ============================================================
    echo   ERROR: PostgreSQL failed to start. Exit code: !PG_RESULT!
    echo   Check the log file for details:
    echo   !PG_LOG!
    echo ============================================================
)

echo.
pause
endlocal
