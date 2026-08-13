"""Drucker-Scanner (`/drucker-scan`) — Scan-Gerät neben einem oder mehreren
Druckern, mit dem ein Scan-Station-Schüler (Schülerauslöser) seinen
Leihschein-Druckauftrag selbst auslöst. Host-Endpunkte für QR, Pairing
(Enable), Name/Theme/Eingabeart und Forget. Spiegel von
`routes/drucker_display.py` — nur ohne Drucker-Zuweisung (die liegt beim
Drucker-Display, s. `PrinterDisplaySession.assigned_scanner_ids`).

Das Gerät selbst verbindet sich unauthentifiziert via `/ws/drucker-scan` (s.
routes/ws.py) und zeigt vorab nur den Registrierungs-Code — die Scan-
Auswertung läuft dort, das Ergebnis wird aber ausschließlich auf den
zugeordneten Drucker-Display(s) angezeigt (der Scanner selbst bleibt stumm)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..sessions import make_qr_data_url, persist_printer_scanners, send_printer_scanner_update
from ..state import get_state
from ._deps import (
    PrinterScannerEnableRequest,
    PrinterScannerForgetRequest,
    PrinterScannerInputModeRequest,
    PrinterScannerLabelRequest,
    PrinterScannerThemeRequest,
    _base_url,
    host_router,
    router,
)

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent.parent.parent / "web"


@router.get("/drucker-scan", response_model=None)
async def printer_scanner_page(request: Request) -> FileResponse | RedirectResponse:
    """Seite für den Drucker-Scanner. Ohne ``?token=`` → Redirect auf einen
    frischen Token (in der URL persistiert, sodass Reload dieselbe Session
    wiederverwendet und kein neuer Code erzeugt wird). Mit Token → HTML ausliefern."""
    token = (request.query_params.get("token") or "").strip().lower()
    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token):
        token = uuid.uuid4().hex[:12]
        return RedirectResponse(f"/drucker-scan?token={token}", status_code=307)
    return FileResponse(str(_WEB_DIR / "drucker-scan.html"))


@router.post("/api/drucker-scan/departed")
async def printer_scanner_departed(request: Request) -> dict:
    """Vom Drucker-Scanner beim **Entladen der Seite** via ``navigator.sendBeacon``
    gerufen. Spiegel von `routes/drucker_display.py::printer_display_departed`."""
    token = (request.query_params.get("token") or "").strip().lower()
    if not token or len(token) != 12 or any(c not in "0123456789abcdef" for c in token):
        return {"ok": False}
    state = get_state()
    d = state.printer_scanners.get(token)
    if d is None:
        return {"ok": False}
    ws = d.ws
    d.ws = None
    if not d.authorized:
        state.printer_scanners.pop(token, None)
    if ws is not None:
        try:
            await ws.close(code=1001, reason="scanner navigated away")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.get("/api/drucker-scan/qr")
async def printer_scanner_qr(request: Request, scanner_id: str | None = None) -> dict:
    """QR, mit dem ein Gerät die Drucker-Scanner-Seite (`/drucker-scan`)
    öffnet. Spiegel von `routes/drucker_display.py::printer_display_qr`."""
    if scanner_id:
        state = get_state()
        scanner = state.printer_scanners.get(scanner_id)
        if not scanner:
            raise HTTPException(404, "Drucker-Scanner nicht gefunden")
        url = f"{_base_url(request)}/drucker-scan?token={scanner.scanner_id}"
    else:
        url = f"{_base_url(request)}/drucker-scan"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/drucker-scan/enable")
async def printer_scanner_enable(body: PrinterScannerEnableRequest) -> dict:
    """Einen Drucker-Scanner durch Eingabe eines Namens freischalten. Spiegel
    von `routes/drucker_display.py::printer_display_enable`."""
    state = get_state()
    scanner = state.printer_scanners.get(body.scanner_id)
    if not scanner:
        raise HTTPException(404, "Drucker-Scanner nicht gefunden")
    scanner.authorized = True
    scanner.label = (body.label or "").strip()
    persist_printer_scanners(state)
    await send_printer_scanner_update(state, scanner)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "scanner_id": scanner.scanner_id, "label": scanner.label}


@host_router.post("/api/drucker-scan/label")
async def printer_scanner_label(body: PrinterScannerLabelRequest) -> dict:
    """Scanner-Name setzen (leer = kein Name)."""
    state = get_state()
    scanner = state.printer_scanners.get(body.scanner_id)
    if not scanner or not scanner.authorized:
        raise HTTPException(404, "Drucker-Scanner nicht gefunden oder nicht freigeschaltet")
    scanner.label = (body.label or "").strip()
    persist_printer_scanners(state)
    await send_printer_scanner_update(state, scanner)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "label": scanner.label}


@host_router.post("/api/drucker-scan/theme")
async def printer_scanner_theme(body: PrinterScannerThemeRequest) -> dict:
    """Darstellung auf dem Scanner setzen (``'light'`` oder ``'dark'``)."""
    state = get_state()
    scanner = state.printer_scanners.get(body.scanner_id)
    if not scanner or not scanner.authorized:
        raise HTTPException(404, "Drucker-Scanner nicht gefunden oder nicht freigeschaltet")
    theme = (body.theme or "").strip().lower()
    if theme not in ("light", "dark"):
        raise HTTPException(400, "theme muss 'light' oder 'dark' sein")
    scanner.theme = theme
    persist_printer_scanners(state)
    await send_printer_scanner_update(state, scanner)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "theme": scanner.theme}


@host_router.post("/api/drucker-scan/input-mode")
async def printer_scanner_input_mode(body: PrinterScannerInputModeRequest) -> dict:
    """Eingabeart auf dem Scanner setzen — vom Host vorgegeben, kein lokaler
    Umschalter am Gerät (Spiegel von `routes/scan_station.py::
    scan_station_input_mode`)."""
    state = get_state()
    scanner = state.printer_scanners.get(body.scanner_id)
    if not scanner or not scanner.authorized:
        raise HTTPException(404, "Drucker-Scanner nicht gefunden oder nicht freigeschaltet")
    input_mode = (body.input_mode or "").strip().lower()
    if input_mode not in ("camera", "manual"):
        raise HTTPException(400, "input_mode muss 'camera' oder 'manual' sein")
    scanner.input_mode = input_mode
    persist_printer_scanners(state)
    await send_printer_scanner_update(state, scanner)
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "input_mode": scanner.input_mode}


@host_router.post("/api/drucker-scan/forget")
async def printer_scanner_forget(body: PrinterScannerForgetRequest) -> dict:
    """Einen Drucker-Scanner **verbieten** (endgültig). Spiegel von
    `routes/drucker_display.py::printer_display_forget`."""
    state = get_state()
    scanner = state.printer_scanners.pop(body.scanner_id, None)
    if not scanner:
        raise HTTPException(404, "Drucker-Scanner nicht gefunden")
    state.banned_printer_scanner_tokens.add(scanner.scanner_id)
    # Aus etwaigen Display-Zuweisungen entfernen, sonst zeigt das Display eine
    # verwaiste (nie mehr sichtbare) Box; `printer_display_assign_scanners`
    # filtert das zwar auch beim Rendern, aber die Persistenz soll sauber
    # bleiben.
    for d in state.printer_displays.values():
        if d.assigned_scanner_ids is not None and scanner.scanner_id in d.assigned_scanner_ids:
            d.assigned_scanner_ids = [
                sid for sid in d.assigned_scanner_ids if sid != scanner.scanner_id
            ]
    persist_printer_scanners(state)
    from ..sessions import persist_printer_displays

    persist_printer_displays(state)
    if scanner.ws is not None:
        try:
            from ..hub import get_hub

            await get_hub().send_websocket(scanner.ws, {"type": "forbidden"})
            await scanner.ws.close(code=4009, reason="Scanner verboten")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}
