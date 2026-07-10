"""Leihschein-Druck (read-only PDF-Abruf + lokaler Druck), Drucker-Auswahl und
die GATED Buchung (`/api/commit-book`)."""

from __future__ import annotations

import logging

from fastapi import HTTPException

from ..config import get_config
from ..hub import get_hub
from ..sessions import handle_commit
from ..state import get_state
from ._deps import CommitBookRequest, PrinterRequest, PrintLoanSlipRequest, host_router

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Leihschein-Druck (read-only PDF-Abruf + lokaler Druck)
# ---------------------------------------------------------------------------


@host_router.post("/api/print-loan-slip")
async def print_loan_slip(body: PrintLoanSlipRequest) -> dict:
    """Leihschein eines Schülers holen (read-only) und lokal drucken.

    Kein Schreibzugriff auf IServ — `get_loan_slip_pdf` ist ein reiner GET, das
    Drucken passiert am Laptop/Macbook (siehe server/printing.py).
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur, wenn der
    # Host-Toggle gesetzt ist.
    pages = None if body.second_page else "1"

    from ..sessions import print_loan_slip_for

    state = get_state()
    try:
        return await print_loan_slip_for(state, student_id, pages=pages)
    except Exception as e:
        log.exception("Leihschein-Druck für %s fehlgeschlagen", student_id)
        raise HTTPException(502, f"Leihschein-Druck fehlgeschlagen: {e}") from e


@host_router.get("/api/printers")
async def get_printers() -> dict:
    """Dem Host-Gerät bekannte Drucker für die Auswahl im Einstellungen-Dialog.

    Rein lesend (lpstat/Get-Printer, lokales System — kein IServ-/DB-Zugriff).
    """
    from ..printing import list_printers

    cfg = get_config()
    state = get_state()
    info = await list_printers(cfg.print_backend)
    info["current"] = state.printer_name_override or cfg.printer_name
    info["env_default"] = cfg.printer_name
    return info


@host_router.post("/api/printer")
async def set_printer(body: PrinterRequest) -> dict:
    """Einstellungen-Dialog: Leihschein-Drucker wählen.

    Setzt nur den In-Memory-Override im Serverstate (leer = zurück auf
    .env/Systemstandard) — kein IServ-/DB-Zugriff, nichts wird persistiert.
    """
    state = get_state()
    name = body.printer.strip()
    state.printer_name_override = name or None
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "printer": state.printer_name_override}


# ---------------------------------------------------------------------------
# Buchung (GATED — nur freigegebener Buchungstest, PLAN §6)
# ---------------------------------------------------------------------------


def _last_scan_for(state, student_id: int) -> str:
    """Zuletzt gestageter Barcode des Schülers (Modus B Session oder Modus A Helfer)."""
    sess = state.find_session_by_student(student_id)
    if sess and sess.last_scan:
        return sess.last_scan
    helper = state.find_helper_for_student(student_id)
    if helper and helper.last_scan:
        return helper.last_scan
    return ""


@host_router.post("/api/commit-book")
async def commit_book(body: CommitBookRequest) -> dict:
    """Einen Barcode tatsächlich BUCHEN (Enter auf der IServ-Counter-Seite).

    Dreifach gesperrt: Host-Auth + `confirm:true` + Server-Flag
    `allow_booking`. Default `ALLOW_BOOKING=false` → gesperrt; `handle_commit`
    berührt den Worker dann gar nicht erst. Nur für den freigegebenen
    Buchungstest (Niklas + Lukas, CLAUDE.md / PLAN §6).

    `confirm` ist im Model bewusst `bool = False` (KEIN Pflichtfeld) — ein
    Pflichtfeld würde bei fehlendem/falschem `confirm` schon während der
    Pydantic-Validierung mit 422 abbrechen, BEVOR Gate 1 (`allow_booking`)
    geprüft wird. Das würde die geforderte Reihenfolge "403 vor 400"
    (CLAUDE.md / PLAN §6) verletzen. Mit Default bleibt die Validierung immer
    erfolgreich; die eigentliche confirm-Prüfung (Gate 3) bleibt unten im
    Funktionsrumpf, NACH Gate 1.
    """
    # Gate 2 (Host-Auth) läuft bereits vorab als Dependency (require_host auf
    # host_router) — FastAPI löst Dependencies immer vor dem Funktionskörper
    # auf, die Reihenfolge Gate2 -> Gate1 -> Gate3 bleibt damit erhalten.
    cfg = get_config()
    if not cfg.allow_booking:  # Gate 1: Server-Flag
        raise HTTPException(403, "Buchung gesperrt (ALLOW_BOOKING=false)")
    if not body.confirm:  # Gate 3: bewusster Extra-Schritt
        raise HTTPException(400, "confirm:true erforderlich")

    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id

    state = get_state()
    hub = get_hub()
    barcode = body.barcode.strip() or _last_scan_for(state, student_id)
    if not barcode:
        raise HTTPException(400, "Kein Barcode (weder übergeben noch gestaged)")

    result = await handle_commit(state, student_id, barcode)
    await hub.broadcast_host(state.state_snapshot())
    # Nur "booked" gilt als Erfolg. "unknown" (Selektoren unverifiziert) darf
    # KEINE Buchung vortäuschen — der Host muss dann manuell prüfen.
    return {"ok": result.get("status") == "booked", "barcode": barcode, **result}
