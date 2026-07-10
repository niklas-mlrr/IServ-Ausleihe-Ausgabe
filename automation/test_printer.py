"""Standalone-Druckertest für den Ausleihe-Laptop (Windows).

Läuft direkt auf dem Laptop (nicht über den Server):
  uv run python automation/test_printer.py

Testet:
  1. Verfügbare Windows-Drucker auflisten (PowerShell)
  2. Minimales Test-PDF mit Pillow erzeugen
  3. Druck via SumatraPDF (bevorzugt) oder win-default

Kein IServ-Zugriff, keine Buchung — rein lokaler Druckertest.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path


# Test-PDF mit Pillow erzeugen (Pillow ist via qrcode[pil] verfügbar).
def _make_test_pdf() -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (595, 842), color="white")
    draw = ImageDraw.Draw(img)
    lines = [
        "Drucker-Test",
        "HP LaserJet Pro P1102",
        "",
        "IServ Ausleihe-Ausgabe",
        "Wenn dieser Druck erscheint,",
        "funktioniert der Print-Pfad.",
    ]
    y = 300
    for line in lines:
        draw.text((80, y), line, fill="black")
        y += 40
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


def list_printers() -> list[str]:
    """Drucker auflisten und Namen als Liste zurückgeben."""
    print("\n--- Windows-Drucker ---")
    names: list[str] = []
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-Printer | Select-Object Name, PrinterStatus, Default | Format-Table -AutoSize",
            ],
            text=True,
            timeout=10,
        )
        print(out.strip())
        # Namen aus der Tabelle extrahieren (Zeile 3+, erste Spalte)
        for line in out.splitlines()[2:]:
            name = line.strip()
            if name:
                names.append(name.split("  ")[0].strip())
    except Exception as e:
        print(f"  PowerShell fehlgeschlagen: {e}")
        try:
            out = subprocess.check_output(
                ["wmic", "printer", "get", "Name,Default,PrinterStatus"],
                text=True,
                timeout=10,
            )
            print(out.strip())
        except Exception as e2:
            print(f"  wmic fehlgeschlagen: {e2}")
            print("  (Drucker-Auflistung nicht möglich)")
    return names


def _find_sumatra() -> str | None:
    import shutil

    local = os.environ.get("LOCALAPPDATA", "")
    candidates = (
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
        os.path.join(local, "SumatraPDF", "SumatraPDF.exe"),
        os.path.join(local, "Programs", "SumatraPDF", "SumatraPDF.exe"),
    )
    found = shutil.which("SumatraPDF") or shutil.which("SumatraPDF.exe")
    if found:
        return found
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


async def _install_sumatra_winget() -> str | None:
    """SumatraPDF via winget installieren; gibt Pfad zurück wenn erfolgreich."""
    print("  -> Installiere SumatraPDF via winget ...")
    proc = await asyncio.create_subprocess_exec(
        "winget",
        "install",
        "SumatraPDF.SumatraPDF",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    output = (out or b"").decode(errors="replace").strip()
    if proc.returncode == 0:
        print("  SumatraPDF installiert.")
        return _find_sumatra()
    print(f"  winget fehlgeschlagen (rc={proc.returncode}): {output}")
    print("  Manuell installieren: https://www.sumatrapdfreader.org/")
    return None


async def _test_sumatra(pdf_path: Path, printer_name: str | None) -> bool:
    exe = _find_sumatra()
    if not exe:
        print("  SumatraPDF nicht gefunden — versuche automatische Installation ...")
        exe = await _install_sumatra_winget()
    if not exe:
        return False
    print(f"  SumatraPDF: {exe}")
    if printer_name:
        cmd = [exe, "-print-to", printer_name, "-silent", str(pdf_path)]
    else:
        cmd = [exe, "-print-to-default", "-silent", str(pdf_path)]
    print(f"  Befehl: {' '.join(cmd)}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    rc = proc.returncode or 0
    output = (out or b"").decode(errors="replace").strip()
    if rc == 0:
        print("  OK (rc=0) — Druckjob an Warteschlange übergeben.")
        return True
    else:
        print(f"  FEHLER rc={rc}: {output}")
        return False


async def _test_powershell(pdf_path: Path, printer_name: str | None) -> bool:
    """PowerShell-Fallback: Start-Process -Verb PrintTo (benötigt PDF-Standardprogramm)."""
    print("  Fallback: PowerShell Start-Process -Verb PrintTo")
    if printer_name:
        # PowerShell single-quoted string: einfache Anführungszeichen werden
        # durch Verdopplung escaped — schließt Command-Injection über den
        # Druckernamen (aus sys.argv[1]) aus.
        safe_printer = printer_name.replace("'", "''")
        ps = (
            f"Start-Process -FilePath '{pdf_path}' "
            f"-Verb PrintTo -ArgumentList '{safe_printer}' -Wait"
        )
    else:
        ps = f"Start-Process -FilePath '{pdf_path}' -Verb Print -Wait"
    proc = await asyncio.create_subprocess_exec(
        "powershell",
        "-NoProfile",
        "-Command",
        ps,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    rc = proc.returncode or 0
    output = (out or b"").decode(errors="replace").strip()
    if rc == 0:
        print("  OK — Druckjob via PowerShell übergeben.")
        return True
    print(f"  FEHLER rc={rc}: {output}")
    return False


def _test_win_default(pdf_path: Path) -> bool:
    print("  Fallback: os.startfile(..., 'print')")
    try:
        os.startfile(str(pdf_path), "print")  # type: ignore[attr-defined]
        print("  OK — Standard-PDF-Handler geöffnet.")
        return True
    except Exception as e:
        print(f"  FEHLER: {e}")
        print("  -> Kein Standard-PDF-Viewer gesetzt oder kein 'print'-Verb registriert.")
        print("     Lösung: SumatraPDF installieren (https://www.sumatrapdfreader.org/)")
        return False


async def main() -> None:
    printer_name: str | None = None
    if len(sys.argv) > 1:
        printer_name = sys.argv[1]
        print(f"Druckername aus Argument: {printer_name!r}")
    else:
        print("Kein Druckername angegeben → Standarddrucker wird verwendet.")
        print('Tipp: uv run python automation/test_printer.py "HP LaserJet Pro P1102"')

    known_printers = list_printers()

    if printer_name and known_printers and printer_name not in known_printers:
        # Partielle Übereinstimmung suchen (case-insensitive)
        needle = printer_name.lower()
        suggestions = [n for n in known_printers if needle in n.lower() or n.lower() in needle]
        print(f"\n[WARNUNG] Druckername {printer_name!r} nicht in der Liste gefunden!")
        if suggestions:
            print(f"  Meintest du: {suggestions[0]!r}  ?")
            print("  -> Skript mit diesem Namen erneut aufrufen:")
            print(f'     uv run python automation/test_printer.py "{suggestions[0]}"')
        else:
            print("  Bitte exakten Namen aus der Tabelle oben verwenden.")
        print()

    print("\n--- Test-PDF erzeugen ---")
    pdf_bytes = _make_test_pdf()
    print(f"  {len(pdf_bytes)} Bytes erzeugt.")

    # Temp-Datei; bei win-default erst nach Druck löschbar.
    fd, tmp = tempfile.mkstemp(suffix=".pdf", prefix="druckertest_")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pdf_bytes)
        print(f"  Temp-Datei: {tmp_path}")

        print("\n--- Drucktest via SumatraPDF ---")
        ok = await _test_sumatra(tmp_path, printer_name)

        if not ok:
            print("\n--- Drucktest via PowerShell ---")
            ok = await _test_powershell(tmp_path, printer_name)

        if not ok:
            print("\n--- Drucktest via win-default ---")
            ok = _test_win_default(tmp_path)

        print()
        if ok:
            print("Ergebnis: Druckjob übergeben. Bitte Drucker prüfen.")
        else:
            print("Ergebnis: FEHLGESCHLAGEN. Treiber und Drucker-Verbindung prüfen.")
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass  # win-default hält die Datei evtl. noch


if __name__ == "__main__":
    if sys.platform != "win32":
        print("WARNUNG: Dieses Skript ist für Windows gedacht.")
        print("Auf Linux/macOS fehlt SumatraPDF und os.startfile.")
    asyncio.run(main())
