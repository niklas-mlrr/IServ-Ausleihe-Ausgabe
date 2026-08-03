"""Drucker-Display (`/drucker-display`) — Warteschlangen-Anzeige für einen
Bildschirm neben den Druckern. Host-Endpunkte für QR, Pairing (Authorize) und
Drucker-Zuweisung (Assign). Das Display selbst verbindet sich unauthentifiziert
via `/ws/drucker-display` (s. routes/ws.py) und zeigt vorab nur den
Registrierungs-Code — Schülerdaten kommen erst nach Pairing + Zuweisung."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..sessions import make_qr_data_url, send_printer_display_update
from ..state import get_state
from ._deps import (
    PrinterDisplayAssignRequest,
    PrinterDisplayEnableRequest,
    PrinterDisplayForgetRequest,
    PrinterDisplayLabelRequest,
    PrinterDisplayThemeRequest,
    _base_url,
    host_router,
    router,
)

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent.parent.parent / "web"


@router.get("/drucker-display", response_model=None)
async def printer_display_page(request: Request) -> FileResponse | RedirectResponse:
    """Seite für das Drucker-Display. Ohne ``?token=`` → Redirect auf einen
    frischen Token (in der URL persistiert, sodass Reload dieselbe Session
    wiederverwendet und kein neuer Code erzeugt wird). Mit Token → HTML ausliefern."""
    token = (request.query_params.get("token") or "").strip().lower()
    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token):
        token = uuid.uuid4().hex[:12]
        return RedirectResponse(f"/drucker-display?token={token}", status_code=307)
    return FileResponse(str(_WEB_DIR / "drucker-display.html"))


@host_router.get("/api/drucker-display/qr")
async def printer_display_qr(request: Request, display_id: str | None = None) -> dict:
    """QR, mit dem ein Gerät die Drucker-Display-Seite (`/drucker-display`)
    öffnet. Ohne ``display_id`` die Basis-URL (öffnet ein neu zugewiesenes
    Display mit frischem Token — „+"-Reiter). Mit ``display_id`` die URL inkl.
    ``?token=`` für ein konkretes Display, sodass ein Reload dieselbe Session
    wiederverwendet (QR-Button im autorisierten Panel). Liefert `{url, qr}`."""
    if display_id:
        state = get_state()
        display = state.printer_displays.get(display_id)
        if not display:
            raise HTTPException(404, "Drucker-Display nicht gefunden")
        url = f"{_base_url(request)}/drucker-display?token={display.display_id}"
    else:
        url = f"{_base_url(request)}/drucker-display"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/drucker-display/enable")
async def printer_display_enable(body: PrinterDisplayEnableRequest) -> dict:
    """Ein Drucker-Display durch Eingabe eines Namens freischalten (autorisieren).
    Der Registrierungs-Code wird nur auf dem Display + im Host-Reiter gezeigt
    (visuelle Zuordnung), nicht mehr am Host getippt — der Name ersetzt die
    Code-Eingabe. Danach kann der Host Druckerkapazitäten zuordnen (Assign).
    Push an das Display (zeigt nun die Queue-Sicht) + Host-Snapshot."""
    state = get_state()
    display = state.printer_displays.get(body.display_id)
    if not display:
        raise HTTPException(404, "Drucker-Display nicht gefunden")
    display.authorized = True
    display.label = (body.label or "").strip()
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id, "label": display.label}


@host_router.post("/api/drucker-display/assign")
async def printer_display_assign(body: PrinterDisplayAssignRequest) -> dict:
    """Zugewiesene Pool-Drucker für ein Drucker-Display setzen. `printer_ids=None`
    = alle Pool-Drucker (Default); explizite (auch leere) Liste = geordnete
    Teilmenge (Reihenfolge = Display-Reihenfolge). Verwaiste IDs (Drucker
    nachträglich aus dem Pool entfernt) werden herausgefiltert, Duplikate
    entfernt (erste Vorkommen gewinnt). Push an das Display + Host-Snapshot."""
    state = get_state()
    display = state.printer_displays.get(body.display_id)
    if not display:
        raise HTTPException(404, "Drucker-Display nicht gefunden")
    if body.printer_ids is None:
        display.assigned_printer_ids = None
    else:
        pool_ids = {p.id for p in state.settings.printers}
        seen: set[str] = set()
        ordered: list[str] = []
        for pid in body.printer_ids:
            if pid in pool_ids and pid not in seen:
                seen.add(pid)
                ordered.append(pid)
        display.assigned_printer_ids = ordered
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "assigned_printer_ids": display.assigned_printer_ids}


@host_router.post("/api/drucker-display/label")
async def printer_display_label(body: PrinterDisplayLabelRequest) -> dict:
    """Display-Name setzen (leer = kein Name). Der Name erscheint im Reiter
    (sobald gesetzt) und als Überschrift auf dem Display. Push an das Display
    (überschreibt die Default-Überschrift) + Host-Snapshot."""
    state = get_state()
    display = state.printer_displays.get(body.display_id)
    if not display or not display.authorized:
        raise HTTPException(404, "Drucker-Display nicht gefunden oder nicht freigeschaltet")
    display.label = (body.label or "").strip()
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "label": display.label}


@host_router.post("/api/drucker-display/theme")
async def printer_display_theme(body: PrinterDisplayThemeRequest) -> dict:
    """Darstellung auf dem Display setzen (``'light'`` oder ``'dark'``). Das
    Display wendet das Theme via ``data-theme`` am Wurzelelement an. Push an
    das Display + Host-Snapshot (Schieberegler-Stand folgt)."""
    state = get_state()
    display = state.printer_displays.get(body.display_id)
    if not display or not display.authorized:
        raise HTTPException(404, "Drucker-Display nicht gefunden oder nicht freigeschaltet")
    theme = (body.theme or "").strip().lower()
    if theme not in ("light", "dark"):
        raise HTTPException(400, "theme muss 'light' oder 'dark' sein")
    display.theme = theme
    await send_printer_display_update(state, display)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "theme": display.theme}


@host_router.post("/api/drucker-display/forget")
async def printer_display_forget(body: PrinterDisplayForgetRequest) -> dict:
    """Ein Drucker-Display **verbieten** (endgültig): Token wird auf die Bannliste
    gesetzt, Session entfernt und das Display erhält eine ``forbidden``-Nachricht
    („vom Betreuer gesperrt"), dann wird die WS geschlossen. Ein Reload mit
    demselben Token bleibt gesperrt; ein neu geöffnetes Display (frischer Token)
    ist erlaubt. Nicht reaktivierbar."""
    state = get_state()
    display = state.printer_displays.pop(body.display_id, None)
    if not display:
        raise HTTPException(404, "Drucker-Display nicht gefunden")
    # Token verbieten — künftige Verbindungen mit diesem Token werden gesperrt.
    state.banned_printer_display_tokens.add(display.display_id)
    # Display über die Sperre informieren (falls gerade verbunden), dann WS
    # schließen. Der `finally`-Block des WS-Handlers setzt nur noch ws=None
    # (Session ist hier schon entfernt).
    if display.ws is not None:
        try:
            from ..hub import get_hub

            await get_hub().send_websocket(display.ws, {"type": "forbidden"})
            await display.ws.close(code=4009, reason="Display verboten")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}
