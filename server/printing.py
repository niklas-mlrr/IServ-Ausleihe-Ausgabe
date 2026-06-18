"""Leihschein-Druck: plattformübergreifende Silent-Print-Abstraktion.

Der Server holt das Leihschein-PDF read-only über die ausleihe-api
(`get_loan_slip_pdf`, reiner GET) und übergibt die Bytes hier zum Drucken.
Das ist eine **lokale** Aktion am Laptop/Macbook — kein Schreibzugriff auf die
IServ-Produktion (CLAUDE.md / PLAN §6).

Backends:
  - ``file``        PDF nach `print_output_dir` schreiben, NICHT drucken
                    (Default auf Nicht-Desktop-Umgebungen; dev-sicher).
  - ``lp``          CUPS `lp` (macOS / Linux): nativ, ideal für USB-Drucker.
  - ``sumatra``     Windows: SumatraPDF `-print-to[-default] -silent`.
  - ``win-default`` Windows-Fallback ohne Sumatra: `os.startfile(path, "print")`.
  - ``auto``        Windows→sumatra/win-default · macOS→lp · sonst→file.

Konfiguration über `server.config.Config` (env: PRINT_BACKEND, PRINTER_NAME,
SUMATRA_PATH, PRINT_OUTPUT_DIR).
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Übliche SumatraPDF-Installationspfade unter Windows (Fallback-Suche).
_SUMATRA_CANDIDATES = (
    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
)


def resolve_backend(backend: str) -> str:
    """`auto` in ein konkretes Backend auflösen (plattformabhängig)."""
    if backend != "auto":
        return backend
    system = platform.system()
    if system == "Windows":
        return "sumatra"  # mit win-default als Laufzeit-Fallback
    if system == "Darwin":
        return "lp"
    return "file"  # Linux/headless (z. B. der Dev-VPS): kein physischer Druck


def _find_sumatra(sumatra_path: str | None) -> str | None:
    if sumatra_path and Path(sumatra_path).is_file():
        return sumatra_path
    on_path = shutil.which("SumatraPDF") or shutil.which("SumatraPDF.exe")
    if on_path:
        return on_path
    for cand in _SUMATRA_CANDIDATES:
        if Path(cand).is_file():
            return cand
    return None


def _write_pdf(data: bytes, output_dir: Path, *, prefix: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"{prefix}_{ts}.pdf"
    path.write_bytes(data)
    return path


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Subprozess async ausführen, (returncode, stderr/stdout) zurückgeben."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, (out or b"").decode(errors="replace").strip()


async def _print_lp(tmp_pdf: Path, printer_name: str | None) -> dict:
    if not shutil.which("lp"):
        raise RuntimeError("lp (CUPS) nicht gefunden — Backend 'lp' nicht verfügbar")
    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", printer_name]
    cmd.append(str(tmp_pdf))
    rc, out = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"lp fehlgeschlagen (rc={rc}): {out}")
    return {"ok": True, "backend": "lp", "detail": out or "an Drucker gesendet"}


async def _print_sumatra(
    tmp_pdf: Path, printer_name: str | None, sumatra_path: str | None
) -> dict:
    exe = _find_sumatra(sumatra_path)
    if not exe:
        raise FileNotFoundError("SumatraPDF nicht gefunden")
    if printer_name:
        cmd = [exe, "-print-to", printer_name, "-silent", str(tmp_pdf)]
    else:
        cmd = [exe, "-print-to-default", "-silent", str(tmp_pdf)]
    rc, out = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"SumatraPDF fehlgeschlagen (rc={rc}): {out}")
    return {"ok": True, "backend": "sumatra", "detail": "an Drucker gesendet"}


def _print_win_default(tmp_pdf: Path) -> dict:
    # os.startfile gibt es nur unter Windows; druckt über das verknüpfte
    # PDF-Programm (öffnet ggf. kurz dessen Fenster).
    os.startfile(str(tmp_pdf), "print")  # type: ignore[attr-defined]  # noqa: S606
    return {"ok": True, "backend": "win-default", "detail": "an Standard-PDF-Handler gesendet"}


def cleanup_stale_print_tempfiles(max_age_h: float = 6.0) -> int:
    """Liegengebliebene Leihschein-Temp-PDFs aus dem System-Temp-Verzeichnis räumen.

    Das `win-default`-Backend (`os.startfile(..., "print")`) kann seine Temp-Datei
    nicht löschen, weil der verknüpfte PDF-Handler sie evtl. noch braucht — über
    einen Ausgabetag sammeln sich so `leihschein_*.pdf` an. Beim Serverstart
    (siehe app.lifespan) räumen wir alte Reste best-effort weg. Gibt die Anzahl
    der gelöschten Dateien zurück."""
    tmp = Path(tempfile.gettempdir())
    cutoff = datetime.now().timestamp() - max_age_h * 3600
    removed = 0
    for p in tmp.glob("leihschein_*.pdf"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    if removed:
        log.info("%d liegengebliebene Leihschein-Temp-PDF(s) entfernt", removed)
    return removed


async def print_pdf(
    data: bytes,
    *,
    backend: str = "auto",
    printer_name: str | None = None,
    sumatra_path: str | None = None,
    output_dir: Path | str = "automation/out/loan_slips",
    label: str = "leihschein",
) -> dict:
    """PDF-Bytes drucken (oder im `file`-Backend speichern).

    Gibt ein dict `{ok, backend, detail, [path]}` zurück. Wirft bei harten
    Fehlern eine Exception (vom Aufrufer in eine HTTP-Antwort zu wandeln).
    """
    resolved = resolve_backend(backend)
    out_dir = Path(output_dir)

    # `file`: nur speichern, nichts drucken.
    if resolved == "file":
        path = _write_pdf(data, out_dir, prefix=label)
        log.info("Leihschein gespeichert (Backend 'file'): %s", path)
        return {"ok": True, "backend": "file", "detail": f"gespeichert: {path}", "path": str(path)}

    # Sonst: in eine Temp-Datei schreiben und an das Druck-Backend übergeben.
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", prefix=f"{label}_")
    tmp_pdf = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        if resolved == "lp":
            return await _print_lp(tmp_pdf, printer_name)

        if resolved in ("sumatra", "win-default"):
            if resolved == "sumatra":
                try:
                    return await _print_sumatra(tmp_pdf, printer_name, sumatra_path)
                except FileNotFoundError:
                    log.warning("SumatraPDF nicht gefunden — Fallback auf win-default")
            return _print_win_default(tmp_pdf)

        raise ValueError(f"Unbekanntes Druck-Backend: {resolved!r}")
    finally:
        # Bei lp/sumatra ist der Druckjob nach Rückkehr i. d. R. gespoolt; die
        # Temp-Datei kann weg. win-default braucht sie evtl. noch kurz — daher
        # nur best-effort löschen.
        if resolved != "win-default":
            try:
                tmp_pdf.unlink(missing_ok=True)
            except Exception:
                pass
