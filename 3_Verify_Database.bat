@echo off
title SMS Database Console
echo Accessing id_system Database...
echo.

"D:\Security Management System (SMS)\Db\postgresql17.9-binaries\pgsql\bin\psql.exe" -U postgres -d id_system

pause