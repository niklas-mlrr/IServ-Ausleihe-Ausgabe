@echo off
cd /d "%~dp0"

set NODE=%~dp0node\node.exe
set PYTHON=%~dp0python\python.exe

echo Starting Barcode Server...
start "Barcode Server" "%NODE%" server\server.js

timeout /t 2 /nobreak >nul

echo Starting Desktop Client...
"%PYTHON%" client\client.py %*
