@echo off
setlocal EnableDelayedExpansion

REM ============================================================
REM  SMS - Initialise / Migrate Database Schema
REM
REM  Calls the FastAPI endpoint /api/v1/admin/init-db which:
REM    - creates any missing tables (Roles, Role_Permissions,
REM      Attachments, Notifications)
REM    - adds the new 'email' column to Site_Access_ID if missing
REM    - seeds Roles, Role_Permissions, and any missing App_Users
REM
REM  All operations are IDEMPOTENT and ADDITIVE - existing data
REM  (applications, history, users) is NOT touched.
REM
REM  Run this:
REM    - the very first time after upgrading to v0.3
REM    - any time main.py changes the schema
REM    - any time you suspect the schema is out of sync
REM ============================================================

title SMS - Initialise Database Schema
color 0B

echo.
echo ============================================================
echo   SMS - Initialise / Migrate Database Schema (additive)
echo ============================================================
echo.

REM Make sure the API is up - quick check on port 8000
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not !errorlevel!==0 (
    echo  ERROR: FastAPI server does not appear to be running on port 8000.
    echo.
    echo  Please start it first:  2_Start_App_Server.bat
    echo.
    echo ============================================================
    pause
    exit /b 1
)

echo  Calling /api/v1/admin/init-db ...
echo.

curl -s -X POST http://127.0.0.1:8000/api/v1/admin/init-db
set "RC=!errorlevel!"

echo.
echo.
if "!RC!"=="0" (
    echo ============================================================
    echo   Done. New tables and seeds are in place.
    echo   Existing applications, users, and history are untouched.
    echo ============================================================
) else (
    echo ============================================================
    echo   curl returned exit code !RC! - check the server logs.
    echo ============================================================
)

echo.
pause
endlocal
