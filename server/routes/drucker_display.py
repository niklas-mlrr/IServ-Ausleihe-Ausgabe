"""Drucker-Display (`/drucker-display`) — Warteschlangen-Anzeige für einen
Bildschirm neben den Druckern. Host-Endpunkte für QR, Pairing (Authorize) und
Drucker-Zuweisung (Assign). Das Display selbst verbindet sich unauthentifiziert
via `/ws/drucker-display` (s. routes/ws.py) und zeigt vorab nur den
Registrierungs-Code — Schülerdaten kommen erst nach Pairing + Zuweisung."""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from ..sessions import make_qr_data_url, send_printer_display_update
from ..state import get_state
from ._deps import (
    PrinterDisplayAssignRequest,
    PrinterDisplayAuthorizeRequest,
    PrinterDisplayForgetRequest,
    _base_url,
    host_router,
)

log = logging.getLogger(__name__)


@host_router.get("/api/drucker-display/qr")
async def printer_display_qr(request: Request) -> dict:
    """QR, mit dem ein Gerät die Drucker-Display-Seite (`/drucker-display`)
    öffnet. Analog `/api/display/qr` (iPad), eigener Endpoint für klare Trennung
    und eigene URL. Liefert `{url, qr}` (PNG-Data-URL)."""
    url = f"{_base_url(request)}/drucker-display"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/drucker-display/authorize")
async def printer_display_authorize(body: PrinterDisplayAuthorizeRequest) -> dict:
    """Ein Drucker-Display über seinen Registrierungs-Code freischalten
    (Pairing). Danach kann der Host Druckerkapazitäten zuordnen (Assign). Analog
    `/api/display/authorize`, plus `send_printer_display_update` (zeigt nun die
    Queue-Sicht statt der Nummer, sobald zugewiesen)."""
    state = get_state()
    code = (body.registration_code or "").strip().upper()
    if not code:
        raise HTTPException(400, "registration_code fehlt")
    display = next(
        (
            d
            for d in state.printer_displays.values()
            if d.registration_code == code and not d.authorized
        ),
        None,
    )
    if not display:
        raise HTTPException(404, "Kein wartendes Drucker-Display mit diesem Code")
    display.authorized = True
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id}


@host_router.post("/api/drucker-display/assign")
async def printer_display_assign(body: PrinterDisplayAssignRequest) -> dict:
    """Zugewiesene Pool-Drucker für ein Drucker-Display setzen. `printer_ids=None`
    = alle Pool-Drucker (Default); explizite (auch leere) Liste = Teilmenge.
    Verwaiste IDs (Drucker nachträglich aus dem Pool entfernt) werden heraus-
    gefiltert. Push an das Display + Host-Snapshot (Liste der Displays folgt)."""
    state = get_state()
    display = state.printer_displays.get(body.display_id)
    if not display or not display.authorized:
        raise HTTPException(404, "Drucker-Display nicht gefunden oder nicht freigeschaltet")
    if body.printer_ids is None:
        display.assigned_printer_ids = None
    else:
        pool_ids = {p.id for p in state.settings.printers}
        display.assigned_printer_ids = {pid for pid in body.printer_ids if pid in pool_ids}
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "assigned_printer_ids": display.assigned_printer_ids}


@host_router.post("/api/drucker-display/forget")
async def printer_display_forget(body: PrinterDisplayForgetRequest) -> dict:
    """Ein Drucker-Display abmelden: Session entfernen und WebSocket schließen.
    Der `finally`-Block des WS-Handlers übernimmt das Aufräumen beim Schließen
    (idempotent per `.pop(..., None)`); hier wird zusätzlich der Host-Snapshot
    sofort aktualisiert, damit die Display-Liste im Host sofort schrumpft."""
    state = get_state()
    display = state.printer_displays.pop(body.display_id, None)
    if not display:
        raise HTTPException(404, "Drucker-Display nicht gefunden")
    # WS schließen → löst den `finally`-Block im WS-Handler aus (Trennung).
    if display.ws is not None:
        try:
            await display.ws.close(code=4009, reason="Vom Host abgemeldet")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}
