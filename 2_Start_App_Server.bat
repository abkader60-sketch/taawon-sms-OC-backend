@echo off
title SMS FastAPI Backend
echo Starting the FastAPI Application Server...
echo.

cd /d "D:\Security Management System (SMS)"
uvicorn main:app --reload

pause