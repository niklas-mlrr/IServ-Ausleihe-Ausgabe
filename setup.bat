@echo off
REM ====================================================================
REM  Ausleihe-Ausgabe - Erstinstallation (Windows-Laptop der Ausleihe)
REM  Einmalig ausfuehren. Danach genuegt start.bat.
REM ====================================================================
setlocal
cd /d "%~dp0"

echo.
echo === Ausleihe-Ausgabe: Erstinstallation ===
echo.

REM 'uv' verwaltet auch die Python-Umgebung selbst (laedt bei Bedarf automatisch
REM eine passende Python-Version herunter) - Node.js wird in diesem Projekt nicht
REM gebraucht. Einzige externe Abhaengigkeit ist 'uv' selbst; die installieren
REM wir automatisch, falls sie fehlt.
where uv >nul 2>nul
if errorlevel 1 (
  echo [INFO] 'uv' wurde nicht gefunden - installiere automatisch ...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
  where uv >nul 2>nul
  if errorlevel 1 (
    echo [FEHLER] Automatische 'uv'-Installation fehlgeschlagen.
    echo   Manuell installieren: https://docs.astral.sh/uv/getting-started/installation/
    echo   z.B. in PowerShell:  irm https://astral.sh/uv/install.ps1 ^| iex
    echo   Danach dieses Fenster schliessen und setup.bat erneut ausfuehren.
    echo.
    pause
    exit /b 1
  )
  echo [INFO] 'uv' installiert.
)

REM 'uv sync' installiert alle Abhaengigkeiten aus pyproject.toml - darunter
REM 'pymupdf' (PyMuPDF, native Wheel) fuer die lokale Leihschein-Bearbeitung
REM (Klasse auf dem Leihschein korrigieren). Danach pruefen wir, dass sich das
REM native Modul wirklich importieren laesst, damit ein kaputtes Wheel hier
REM auffaellt und nicht erst beim Drucken.
echo [1/3] Python-Umgebung + Abhaengigkeiten (uv sync) ...
call uv sync
if errorlevel 1 ( echo [FEHLER] uv sync fehlgeschlagen. & pause & exit /b 1 )
call uv run python -c "import fitz" >nul 2>nul
if errorlevel 1 ( echo [FEHLER] PyMuPDF ^(pymupdf^) konnte nicht importiert werden. & pause & exit /b 1 )

echo [2/3] Playwright-Browser (Chromium) ...
call uv run playwright install chromium
if errorlevel 1 ( echo [FEHLER] playwright install fehlgeschlagen. & pause & exit /b 1 )

echo [3/3] .env pruefen ...
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo   .env aus Vorlage erstellt - bitte jetzt ISERV_* und HOST_PASSWORD eintragen!
) else (
  echo   .env vorhanden.
)

echo.
echo Fertig. Fuer Silent-Print den USB-Drucker als Standarddrucker setzen
echo und ggf. SumatraPDF installieren (siehe docs\deployment.md).
echo Start danach mit:  start.bat
echo.
pause
endlocal
