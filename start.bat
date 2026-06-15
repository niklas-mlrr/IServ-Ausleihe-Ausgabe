@echo off
REM ====================================================================
REM  Ausleihe-Ausgabe - Server starten (Windows)
REM  Erststart? Zuerst setup.bat ausfuehren.
REM ====================================================================
setlocal
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 ( echo [FEHLER] 'uv' nicht gefunden - bitte setup.bat ausfuehren. & pause & exit /b 1 )
if not exist ".env" ( echo [FEHLER] .env fehlt - bitte setup.bat ausfuehren. & pause & exit /b 1 )

echo Starte Ausleihe-Ausgabe-Server (HTTPS, Port aus .env, Default 3443) ...
echo Leitstand:  https://localhost:3443/leitstand.html
echo Beenden mit Strg+C.
echo.
call uv run python -m server.main
echo.
echo Server beendet.
pause
endlocal
