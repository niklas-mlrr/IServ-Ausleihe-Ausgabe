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

REM SumatraPDF pruefen (Silent-Print fuer Leihscheine); nur installieren wenn fehlend.
where SumatraPDF >nul 2>nul && goto sumatra_ok
if exist "C:\Program Files\SumatraPDF\SumatraPDF.exe" goto sumatra_ok
if exist "C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe" goto sumatra_ok
echo [INFO] SumatraPDF nicht gefunden - installiere via winget ...
winget install SumatraPDF.SumatraPDF --silent --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo [WARNUNG] SumatraPDF-Installation fehlgeschlagen.
  echo           Manuell installieren: https://www.sumatrapdfreader.org/
  echo           Leihschein-Druck faellt auf win-default zurueck ^(kein Silent-Print^).
) else (
  echo [INFO] SumatraPDF installiert.
)
:sumatra_ok

echo Starte Ausleihe-Ausgabe-Server (HTTPS, Port aus .env, Default 3443) ...
echo Host:  https://localhost:3443/host
echo Beenden mit Strg+C.
echo.
call uv run python -m server.main
echo.
echo Server beendet.
pause
endlocal
