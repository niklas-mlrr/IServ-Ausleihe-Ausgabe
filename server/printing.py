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
import re
import secrets
import shutil
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

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
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = _SUMATRA_CANDIDATES + (
        os.path.join(local, "SumatraPDF", "SumatraPDF.exe"),
        os.path.join(local, "Programs", "SumatraPDF", "SumatraPDF.exe"),
    )
    for cand in candidates:
        if cand and Path(cand).is_file():
            return cand
    return None


def _write_pdf(data: bytes, output_dir: Path, *, prefix: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Sekunden-genauer Timestamp kann bei zwei Drucken in derselben Sekunde
    # kollidieren → Mikrosekunden + 4 Hex-Zeichen Suffix machen den Dateinamen
    # eindeutig, ohne den Prefix/Dir-Logik zu verändern.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S%f")
    suffix = secrets.token_hex(2)
    path = output_dir / f"{prefix}_{ts}_{suffix}.pdf"
    path.write_bytes(data)
    return path


# PowerShell-Vorspann: erzwingt UTF-8 auf stdout, damit Umlaute auf deutschem
# Windows (Default cp850/cp1252) nicht als Mojibake durchgereicht werden.
_PS_UTF8_PREFIX = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8;"


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Subprozess async ausführen, (returncode, stderr/stdout) zurückgeben.

    Decode stdout als UTF-8 mit ``errors="replace"`` — Backends geben i. d. R.
    UTF-8 (lp/lpstat auf macOS/Linux, SumatraPDF); bei PowerShell wird das
    OutputEncoding zusätzlich vorab auf UTF-8 gesetzt (siehe _PS_UTF8_PREFIX),
    sodass cp850/cp1252-Umlaute auf deutschem Windows nicht kaputtgehen.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, (out or b"").decode("utf-8", errors="replace").strip()


async def _print_lp(tmp_pdf: Path, printer_name: str | None, pages: str | None = None) -> dict:
    if not shutil.which("lp"):
        raise RuntimeError("lp (CUPS) nicht gefunden — Backend 'lp' nicht verfügbar")
    cmd = ["lp"]
    if printer_name:
        cmd += ["-d", printer_name]
    if pages:
        cmd += ["-o", f"page-ranges={pages}"]
    cmd.append(str(tmp_pdf))
    rc, out = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"lp fehlgeschlagen (rc={rc}): {out}")
    # CUPS meldet auf stdout "request id is <dest>-<id>" — die Job-ID brauchen
    # wir, um später per `lpstat -o` das physische Druckende zu erkennen. Liefert
    # lp keine ID (exotische CUPS-Variante), fällt `job_handle` auf None → dann
    # gilt der Auftrag sofort als „gespoolt = gedruckt" (kein Polling möglich).
    job_id = None
    m = re.search(r"request id is (\S+)", out or "")
    if m:
        job_id = m.group(1).rstrip(".")
    handle = {"kind": "cups", "job_id": job_id} if job_id else None
    return {
        "ok": True,
        "backend": "lp",
        "detail": out or "an Drucker gesendet",
        "job_handle": handle,
    }


async def _print_sumatra(
    tmp_pdf: Path,
    printer_name: str | None,
    sumatra_path: str | None,
    pages: str | None = None,
) -> dict:
    exe = _find_sumatra(sumatra_path)
    if not exe:
        raise FileNotFoundError("SumatraPDF nicht gefunden")
    if printer_name:
        cmd = [exe, "-print-to", printer_name, "-silent"]
    else:
        cmd = [exe, "-print-to-default", "-silent"]
    if pages:
        # SumatraPDF: Seitenbereich via -print-settings (z. B. "1" oder "1-2").
        cmd += ["-print-settings", pages]
    cmd.append(str(tmp_pdf))
    rc, out = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"SumatraPDF fehlgeschlagen (rc={rc}): {out}")
    # SumatraPDF setzt den Print-Job-Namen auf den Dateinamen (Basename) — der
    # `mkstemp`-Prefix `leihschein_<student_id>_` macht ihn eindeutig. Daran
    # erkennen wir per `Get-PrintJob` das physische Druckende. Ohne expliziten
    # Drucker (`-print-to-default`) merken wir uns None und lösen den
    # Standarddrucker erst beim Pollen auf.
    handle = {"kind": "win", "printer": printer_name, "doc": tmp_pdf.name}
    return {
        "ok": True,
        "backend": "sumatra",
        "detail": "an Drucker gesendet",
        "job_handle": handle,
    }


async def list_printers(backend: str = "auto") -> dict:
    """Dem Gerät bekannte Drucker auflisten (für die Druckerauswahl am Host).

    Rein lesend: Windows via `Get-Printer` (PowerShell), lp/CUPS via `lpstat`.
    Im `file`-Backend (headless) gibt es keinen Druckdienst — leere Liste.
    Gibt `{printers, default, backend}` zurück; Fehler werden geschluckt
    (dann eben keine Auswahl, der Druck läuft weiter über den Default).
    """
    resolved = resolve_backend(backend)
    printers: list[str] = []
    default: str | None = None
    try:
        if resolved in ("sumatra", "win-default"):
            rc, out = await _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _PS_UTF8_PREFIX + "Get-Printer | Select-Object -ExpandProperty Name",
                ]
            )
            if rc == 0:
                printers = [ln.strip() for ln in out.splitlines() if ln.strip()]
            rc, out = await _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    _PS_UTF8_PREFIX + "(Get-CimInstance Win32_Printer -Filter 'Default=TRUE').Name",
                ]
            )
            if rc == 0 and out.strip():
                default = out.strip().splitlines()[0].strip()
        elif resolved == "lp" and shutil.which("lpstat"):
            rc, out = await _run(["lpstat", "-e"])
            if rc == 0:
                printers = [ln.strip() for ln in out.splitlines() if ln.strip()]
            rc, out = await _run(["lpstat", "-d"])
            # Format: "system default destination: <name>" (oder "no system default …")
            if rc == 0 and ":" in out:
                default = out.split(":", 1)[1].strip() or None
    except Exception:
        log.warning("Druckerliste konnte nicht ermittelt werden", exc_info=True)
    return {"printers": printers, "default": default, "backend": resolved}


def _print_win_default(tmp_pdf: Path) -> dict:
    # os.startfile gibt es nur unter Windows; druckt über das verknüpfte
    # PDF-Programm (öffnet ggf. kurz dessen Fenster).
    os.startfile(str(tmp_pdf), "print")  # type: ignore[attr-defined]  # noqa: S606
    # Kein zuverlässiges Job-Handle (keine Job-ID vom verknüpften PDF-Handler) →
    # kein OS-Polling; der Worker wertet den Auftrag sofort als „gedruckt"
    # (dokumentierter „gespoolt = gedruckt"-Fallback für win-default).
    return {
        "ok": True,
        "backend": "win-default",
        "detail": "an Standard-PDF-Handler gesendet",
        "job_handle": None,
    }


_COMPLETION_POLL_S = 0.7
_COMPLETION_TIMEOUT_S = 90.0


async def _resolve_default_printer_win() -> str | None:
    """Standarddrucker unter Windows ermitteln (PowerShell, rein lesend).

    Für den Polling-Pfad, wenn SumatraPDF mit `-print-to-default` aufgerufen
    wurde und wir keinen Druckernamen haben — `Get-PrintJob` braucht ihn aber.
    Gibt None zurück, wenn sich der Standarddrucker nicht ermitteln lässt.
    """
    rc, out = await _run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PS_UTF8_PREFIX + "(Get-CimInstance Win32_Printer -Filter 'Default=TRUE').Name",
        ]
    )
    if rc == 0 and out.strip():
        return out.strip().splitlines()[0].strip() or None
    return None


async def await_print_completion(
    handle: dict | None, *, timeout_s: float = _COMPLETION_TIMEOUT_S
) -> bool:
    """Auf das **physische** Druckende warten (OS-Queue-Polling).

    ``handle`` kommt aus ``print_pdf``-Result (`job_handle`). Rückgabe:
    ``True`` = Auftrag ist aus der OS-Warteschlange verschwunden (auf Papier
    fertig oder abgebrochen); ``False`` = Timeout (wir werten dann trotzdem
    als „fertig", damit die interne Warteschlange nicht blockiert — der
    physische Druck läuft am OS ohnehin weiter).

    ``handle is None`` (Backends ``file``/``win-default``) → sofort ``True``
    (kein Polling möglich / nötig).
    """
    if not handle:
        return True
    kind = handle.get("kind")
    if kind == "cups":
        return await _await_cups(handle.get("job_id"), timeout_s=timeout_s)
    if kind == "win":
        return await _await_win(handle.get("printer"), handle.get("doc"), timeout_s=timeout_s)
    # Unbekanntes Handle → kein Polling, sofort fertig.
    return True


async def _await_cups(job_id: str | None, *, timeout_s: float) -> bool:
    """CUPS: `lpstat -o` pollen, bis die `job_id` nicht mehr auftaucht."""
    if not job_id or not shutil.which("lpstat"):
        return True
    deadline = _monotonic() + timeout_s
    while _monotonic() < deadline:
        rc, out = await _run(["lpstat", "-o"])
        if rc == 0 and job_id not in (out or ""):
            return True
        await asyncio.sleep(_COMPLETION_POLL_S)
    log.warning("CUPS-Completion-Polling Timeout für Job %s — werte als fertig", job_id)
    return True


async def _await_win(printer: str | None, doc: str | None, *, timeout_s: float) -> bool:
    """Windows: `Get-PrintJob` pollen, bis kein Job mehr mit unserem
    DocumentName (Basename) in der Druckerwarteschlange liegt."""
    if not doc:
        return True
    name = printer or await _resolve_default_printer_win()
    if not name:
        log.warning("Windows-Completion-Polling: Drucker nicht ermittelbar — werte als fertig")
        return True
    # DocumentName per `-like` matchen: SumatraPDF setzt i. d. R. den Dateinamen
    # als Job-Namen; `-like "*<doc>*"` ist robust gegen Pfad-/Erweiterungs-Drift.
    pattern = f"*{doc}*"
    deadline = _monotonic() + timeout_s
    while _monotonic() < deadline:
        cmd = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _PS_UTF8_PREFIX
            + (
                f"@(Get-PrintJob -PrinterName '{name}' | "
                f"Where-Object {{ $_.DocumentName -like '{pattern}' }}).Count"
            ),
        ]
        rc, out = await _run(cmd)
        if rc == 0 and (out or "").strip() in ("0", ""):
            return True
        await asyncio.sleep(_COMPLETION_POLL_S)
    log.warning("Windows-Completion-Polling Timeout für Doc %s — werte als fertig", doc)
    return True


# ---- OS-Druckstatus single-shot (für den Print-Queue-Tracker) ------------
# `read_job_state` liefert je gesendetem OS-Job seinen aktuellen Zustand in
# einem Poll: ``absent`` (Job weg = physisch fertig/abgebrochen → finalisieren),
# ``printing`` (OS druckt aktiv → „wird gedruckt"), ``spooled`` (an OS gesendet,
# aber noch nicht aktiv am Drucker → „gesendet, wartet"). Der Print-Queue-Tracker
# (`print_queue._track_job`) ruft das zyklisch auf und treibt daraus den Job-
# Status, statt ihn logisch per Slot-Beförderung zu schätzen.

JobState = Literal["absent", "spooled", "printing"]


async def read_job_state(handle: dict | None) -> JobState:
    """Aktuellen OS-Druckstatus eines gesendeten Jobs einmal lesen (single shot,
    nicht blockierend). ``handle`` aus ``print_pdf``-Result (`job_handle`).

    ``handle is None`` (Backends ``file``/``win-default``) → ``absent`` (kein
    OS-Job nachverfolgbar → der Tracker wertet sofort als fertig; entspricht dem
    bisherigen „gespoolt = gedruckt"-Fallback). Unbekanntes Handle → ``absent``.
    """
    if not handle:
        return "absent"
    kind = handle.get("kind")
    if kind == "cups":
        return await _read_cups_job_state(handle.get("job_id"))
    if kind == "win":
        # Standarddrucker (SumatraPDF `-print-to-default`) einmal auflösen und
        # am Handle cachen, damit wiederholte Polls nicht jedes Mal per PowerShell
        # neu auflösen.
        printer = handle.get("printer")
        if not printer:
            printer = await _resolve_default_printer_win()
            if printer:
                handle["printer"] = printer
        return await _read_win_job_state(printer, handle.get("doc"))
    return "absent"


async def _read_cups_job_state(job_id: str | None) -> JobState:
    """CUPS: `lpstat -o` einmal auswerten. Job-ID nicht mehr im Output →
    ``absent``; Zeile mit ``active`` (Rang = druckt gerade) → ``printing``;
    sonst → ``spooled``. ``lpstat`` fehlt / keine Job-ID → ``absent`` (nicht
    nachverfolgbar = wie bisher sofort fertig). Best-effort, am Dev-VPS ohne
    CUPS nicht getestet."""
    if not job_id or not shutil.which("lpstat"):
        return "absent"
    try:
        rc, out = await _run(["lpstat", "-o"])
    except Exception:  # noqa: BLE001 — niemals werfen, Tracker sonst tot
        return "spooled"
    if rc != 0:
        return "spooled"
    out = out or ""
    if job_id not in out:
        return "absent"
    for ln in out.splitlines():
        if job_id in ln and "active" in ln.lower():
            return "printing"
    return "spooled"


async def _read_win_job_state(printer: str | None, doc: str | None) -> JobState:
    """Windows/SumatraPDF: `Get-PrintJob` für den Drucker, Treffer per
    DocumentName `-like` (SumatraPDF setzt den Dateinamen als Job-Namen), davon
    `JobStatus` lesen. Kein Treffer → ``absent``; JobStatus enthält ``Printing``
    → ``printing``; sonst (Spooling/Pending/Sent/…) → ``spooled``. Fehler /
    Drucker nicht ermittelbar → ``spooled`` (niemals ``absent``, damit der
    Tracker nicht aus einem Lesefehler heraus vorzeitig finalisiert; das
    Tracker-Timeout sichert den Endzustand ab).

    ``printer`` sollte vom Aufrufer (``read_job_state``) bereits aufgelöst und
    am Handle gecacht sein; hier verbleibt nur der defensive ``None``-Check."""
    if not doc or not printer:
        return "spooled"
    pattern = f"*{doc}*"
    cmd = [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        _PS_UTF8_PREFIX
        + (
            f"@(Get-PrintJob -PrinterName '{printer}' | "
            f"Where-Object {{ $_.DocumentName -like '{pattern}' }} | "
            f"Select-Object -ExpandProperty JobStatus)"
        ),
    ]
    try:
        rc, out = await _run(cmd)
    except Exception:  # noqa: BLE001
        return "spooled"
    if rc != 0:
        return "spooled"
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    if not lines:
        return "absent"
    for st in lines:
        if "printing" in st.lower():
            return "printing"
    return "spooled"


def _monotonic() -> float:
    """Monotone Zeit (Wanduhr-Sprünge sollen das Polling-Deadline nicht
    verfälschen)."""
    return time.monotonic()


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
    pages: str | None = None,
) -> dict:
    """PDF-Bytes drucken (oder im `file`-Backend speichern).

    `pages` schränkt den Druck auf einen Seitenbereich ein (z. B. ``"1"`` nur
    erste Seite, ``"1-2"`` beide). ``None`` druckt alle Seiten. Unterstützt von
    den Backends ``lp`` und ``sumatra``; ``file`` speichert immer das ganze PDF
    und ``win-default`` kann nicht einschränken (druckt alle Seiten).

    Gibt ein dict `{ok, backend, detail, [path]}` zurück. Wirft bei harten
    Fehlern eine Exception (vom Aufrufer in eine HTTP-Antwort zu wandeln).
    """
    resolved = resolve_backend(backend)
    out_dir = Path(output_dir)

    # `pages` validieren (z. B. "1" oder "1-2"); None bedeutet „alle Seiten".
    if pages is not None and not re.fullmatch(r"\d+(?:-\d+)?", pages):
        return {
            "ok": False,
            "backend": resolved,
            "detail": f"ungültiger Seitenbereich: {pages!r} (erwartet z. B. '1' oder '1-2')",
        }

    # `file`: nur speichern, nichts drucken.
    if resolved == "file":
        path = _write_pdf(data, out_dir, prefix=label)
        log.info("Leihschein gespeichert (Backend 'file'): %s", path)
        # Kein physischer Drucker → kein Polling; der Worker wertet den Auftrag
        # sofort als „gedruckt" (Dev-VPS / `save_pdf_locally`-Sicherheitsnetz).
        return {
            "ok": True,
            "backend": "file",
            "detail": f"gespeichert: {path}",
            "path": str(path),
            "job_handle": None,
        }

    # Sonst: in eine Temp-Datei schreiben und an das Druck-Backend übergeben.
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf", prefix=f"{label}_")
    tmp_pdf = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)

        if resolved == "lp":
            return await _print_lp(tmp_pdf, printer_name, pages)

        if resolved in ("sumatra", "win-default"):
            if resolved == "sumatra":
                try:
                    return await _print_sumatra(tmp_pdf, printer_name, sumatra_path, pages)
                except FileNotFoundError:
                    log.warning("SumatraPDF nicht gefunden — Fallback auf win-default")
            if pages:
                log.warning(
                    "Backend 'win-default' kann keinen Seitenbereich (%s) wählen — "
                    "es werden alle Seiten gedruckt",
                    pages,
                )
            # `os.startfile` ist synchron und kann das Event-Loop für hunderte
            # ms blockieren (Windows-PDF-Handler-Aufruf) → in einen Thread
            # auslagern.
            return await asyncio.to_thread(_print_win_default, tmp_pdf)

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
