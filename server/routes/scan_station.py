"""Scan-Station (`/scan-station`) — festes Scan-Gerät für Schüler ohne Handy.

Aufbau und Pairing-Fluss sind der Spiegel des Drucker-Displays
(`routes/drucker_display.py`): Die Station selbst verbindet sich
unauthentifiziert via `/ws/scan-station` (s. routes/ws.py) und zeigt vorab nur
ihren Registrierungs-Code; erst nach der Freischaltung durch den Host (Name
eintragen) nimmt sie Zettel-Codes an.

Zusätzlich hängt hier der Druck des Zettels: `POST /api/scan-station/print-sheet`
reiht einen Auftrag in dieselbe Druckerwarteschlange ein wie ein
Host-Leihschein (`kind="station_sheet"`), damit er auf dem Drucker-Display
gewohnt mit Klasse/Name und Host-Symbol erscheint. Der Zettel selbst wird lokal
gebaut (`server/scan_station.py`) — kein Schreibzugriff auf IServ.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..print_queue import PrintJob, slip_name
from ..sessions import (
    activate_station_student,
    allowed_printers_for,
    make_qr_data_url,
    persist_scan_stations,
    release_station_student,
    send_scan_station_update,
)
from ..state import get_state
from ._deps import (
    ScanStationActivateRequest,
    ScanStationEnableRequest,
    ScanStationForgetRequest,
    ScanStationInputModeRequest,
    ScanStationLabelRequest,
    ScanStationPrintRequest,
    ScanStationReleaseRequest,
    ScanStationThemeRequest,
    StudentRef,
    _base_url,
    host_router,
    require_host,
    router,
)

log = logging.getLogger(__name__)

_WEB_DIR = Path(__file__).parent.parent.parent / "web"


def _valid_token(token: str) -> bool:
    """12 Hex-Zeichen — dasselbe Tokenformat wie beim Drucker-Display."""
    return len(token) == 12 and all(c in "0123456789abcdef" for c in token)


def _station_or_404(station_id: str, *, authorized_only: bool = False):
    state = get_state()
    station = state.scan_stations.get(station_id)
    if station is None or (authorized_only and not station.authorized):
        raise HTTPException(404, "Scan-Station nicht gefunden oder nicht freigeschaltet")
    return state, station


async def _broadcast(state) -> None:
    from ..hub import get_hub

    await get_hub().broadcast_host(state.state_snapshot())


@router.get("/scan-station", response_model=None)
async def scan_station_page(request: Request) -> FileResponse | RedirectResponse:
    """Seite für die Scan-Station. Ohne ``?token=`` → Redirect auf einen frischen
    Token (in der URL persistiert, sodass ein Reload dieselbe Session
    wiederverwendet und kein neuer Code entsteht). Mit Token → HTML ausliefern."""
    token = (request.query_params.get("token") or "").strip().lower()
    if not token or not _valid_token(token):
        token = uuid.uuid4().hex[:12]
        return RedirectResponse(f"/scan-station?token={token}", status_code=307)
    return FileResponse(str(_WEB_DIR / "scan-station.html"))


@router.post("/api/scan-station/departed")
async def scan_station_departed(request: Request) -> dict:
    """Vom Stationsgerät beim **Entladen der Seite** via ``navigator.sendBeacon``
    gerufen (Spiegel von `/api/drucker-display/departed`). Räumt die Session
    auf: autorisiert → ``ws=None`` (grauer Punkt), nicht autorisiert → Session
    entfernen. Ein noch angemeldeter Schüler wird dabei freigegeben, damit sein
    Worker-Context nicht an der verwaisten Station hängen bleibt.

    Idempotent und unauthentifiziert wie die Station selbst — der Token steht
    ohnehin öffentlich in deren URL; ein Fremdaufruf würde lediglich diese eine
    Station trennen (sie verbindet sich ggf. neu)."""
    token = (request.query_params.get("token") or "").strip().lower()
    if not token or not _valid_token(token):
        return {"ok": False}
    state = get_state()
    station = state.scan_stations.get(token)
    if station is None:
        return {"ok": False}
    ws = station.ws
    # ws-Referenz VOR dem Freigeben/Schließen lösen: damit schickt
    # `release_station_student` nichts mehr auf den scheidenden Socket, und der
    # `finally`-Block des WS-Handlers macht nichts doppelt
    # (`station.ws is websocket` ist dann False).
    station.ws = None
    await release_station_student(state, station, reason="station navigated away")
    if not station.authorized:
        state.scan_stations.pop(token, None)
    if ws is not None:
        try:
            await ws.close(code=1001, reason="station navigated away")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    await _broadcast(state)
    return {"ok": True}


@host_router.get("/api/scan-station/qr")
async def scan_station_qr(request: Request, station_id: str | None = None) -> dict:
    """QR, mit dem ein Gerät die Stationsseite öffnet. Ohne ``station_id`` die
    Basis-URL (frischer Token — „+"-Reiter), mit ``station_id`` die URL inkl.
    ``?token=`` einer konkreten Station (QR-Button im Reiter)."""
    if station_id:
        _state, station = _station_or_404(station_id)
        url = f"{_base_url(request)}/scan-station?token={station.station_id}"
    else:
        url = f"{_base_url(request)}/scan-station"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/scan-station/enable")
async def scan_station_enable(body: ScanStationEnableRequest) -> dict:
    """Eine Scan-Station durch Eingabe eines Namens freischalten. Danach nimmt
    sie Zettel-Codes an; vorher zeigt sie nur ihren Registrierungs-Code."""
    state, station = _station_or_404(body.station_id)
    station.authorized = True
    station.label = (body.label or "").strip()
    persist_scan_stations(state)
    await send_scan_station_update(state, station)
    await _broadcast(state)
    return {"ok": True, "station_id": station.station_id, "label": station.label}


@host_router.post("/api/scan-station/label")
async def scan_station_label(body: ScanStationLabelRequest) -> dict:
    """Stationsname setzen (leer = kein Name). Erscheint im Host-Reiter und als
    Überschrift auf der Station."""
    state, station = _station_or_404(body.station_id, authorized_only=True)
    station.label = (body.label or "").strip()
    persist_scan_stations(state)
    await send_scan_station_update(state, station)
    await _broadcast(state)
    return {"ok": True, "label": station.label}


@host_router.post("/api/scan-station/theme")
async def scan_station_theme(body: ScanStationThemeRequest) -> dict:
    """Darstellung auf der Station setzen (``'light'`` oder ``'dark'``)."""
    state, station = _station_or_404(body.station_id, authorized_only=True)
    theme = (body.theme or "").strip().lower()
    if theme not in ("light", "dark"):
        raise HTTPException(400, "theme muss 'light' oder 'dark' sein")
    station.theme = theme
    persist_scan_stations(state)
    await send_scan_station_update(state, station)
    await _broadcast(state)
    return {"ok": True, "theme": station.theme}


@host_router.post("/api/scan-station/input-mode")
async def scan_station_input_mode(body: ScanStationInputModeRequest) -> dict:
    """Eingabeart auf der Station setzen — ``'camera'`` (Kamera-Scanner) oder
    ``'manual'`` (Tastatur-/Handscanner tippt in ein Eingabefeld), wie im
    Helferclient. Wirkt wie das Theme: der gesetzte Wert überschreibt die
    lokale Wahl an der Station."""
    state, station = _station_or_404(body.station_id, authorized_only=True)
    mode = (body.input_mode or "").strip().lower()
    if mode not in ("camera", "manual"):
        raise HTTPException(400, "input_mode muss 'camera' oder 'manual' sein")
    station.input_mode = mode
    persist_scan_stations(state)
    await send_scan_station_update(state, station)
    await _broadcast(state)
    return {"ok": True, "input_mode": station.input_mode}


@host_router.post("/api/scan-station/release")
async def scan_station_release(body: ScanStationReleaseRequest) -> dict:
    """Den an einer Station angemeldeten Schüler vom Host aus freigeben — die
    Station fällt sofort auf „Zettel-Code scannen" zurück. Idempotent."""
    state, station = _station_or_404(body.station_id)
    released = await release_station_student(state, station, reason="host")
    await _broadcast(state)
    return {"ok": True, "released": released}


@host_router.post("/api/scan-station/release-student")
async def scan_station_release_student(body: StudentRef) -> dict:
    """Spiegel von `/api/scan-station/release`, aber vom Schüler aus statt von
    der Station aus adressiert — für den „Trennen"-Knopf im Now-Serving-
    Kästchen (`web/host-render.js`), der nur die `student_id` kennt, nicht
    die interne `station_id`. Kein Fehler, wenn der Schüler gerade an keiner
    Station angemeldet ist (idempotent, der Button ist ohnehin nur sichtbar,
    solange `station_name` gesetzt ist)."""
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    state = get_state()
    station = state.find_station_by_student(body.student_id)
    if station is None:
        return {"ok": True, "released": False}
    released = await release_station_student(state, station, reason="host")
    await _broadcast(state)
    return {"ok": True, "released": released}


@host_router.post("/api/scan-station/forget")
async def scan_station_forget(body: ScanStationForgetRequest) -> dict:
    """Eine Scan-Station **verbieten** (endgültig): Token auf die Bannliste,
    Session entfernt, Station bekommt eine ``forbidden``-Nachricht, dann wird
    die WS geschlossen. Ein Reload mit demselben Token bleibt gesperrt; eine neu
    geöffnete Station (frischer Token) ist erlaubt. Nicht reaktivierbar."""
    state, station = _station_or_404(body.station_id)
    await release_station_student(state, station, reason="forbidden")
    state.scan_stations.pop(station.station_id, None)
    state.banned_scan_station_tokens.add(station.station_id)
    persist_scan_stations(state)
    if station.ws is not None:
        try:
            from ..hub import get_hub

            await get_hub().send_websocket(station.ws, {"type": "forbidden"})
            await station.ws.close(code=4009, reason="Station verboten")
        except Exception:  # noqa: BLE001 — Schließen darf Endpoint nicht crashen
            pass
    await _broadcast(state)
    return {"ok": True}


@host_router.post("/api/scan-station/print-sheet")
async def scan_station_print_sheet(
    body: ScanStationPrintRequest, sid: str = Depends(require_host)
) -> dict:
    """Zettel für die Scan-Station drucken (Barcode + Bücherliste zum Abhaken).

    Läuft durch dieselbe Druckerwarteschlange wie ein Host-Leihschein
    (`role="host"`), erscheint auf dem Drucker-Display also gewohnt mit
    Klasse/Name und Host-Symbol — ist aber über `kind="station_sheet"` klar
    davon unterschieden und setzt keinen Leihschein-Status.

    Spiegel von `/api/print-loan-slip` (routes/slips.py): nicht-blockierend,
    Status/Ergebnis kommen via WS an diesen `sid`. Der Zettel-Code selbst wird
    erst beim Bauen des PDFs vergeben (stabil pro Schüler) — ein Nachdruck
    trägt denselben Barcode.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    state = get_state()
    if not state.settings.printers:
        raise HTTPException(400, "Kein Drucker konfiguriert")
    student = state.find_student(body.student_id)
    if student is None:
        raise HTTPException(404, "Schüler nicht in einer geöffneten Klasse")

    pool_ids = {p.id for p in state.settings.printers}
    if body.printers is not None:
        selected_ids = {pid for pid in body.printers if pid in pool_ids}
        if not selected_ids:
            raise HTTPException(400, "Bitte mindestens einen Drucker auswählen")
        allowed = selected_ids
    else:
        allowed = allowed_printers_for(state, body.student_id)
        if allowed is not None and not (allowed & pool_ids):
            raise HTTPException(400, "Kein erlaubter Drucker im Pool für diese Klasse")

    job = PrintJob.create(
        role="host",
        kind="station_sheet",
        student_id=body.student_id,
        pages=None,  # der Zettel ist einseitig
        name=slip_name(student.lastname, student.firstname, student.form),
        host_sid=sid,
        allowed_printers=allowed,
        reactivate_station_code=body.reactivate_old_code,
    )
    await state.print_queue.enqueue(job)
    return {"ok": True, "queued": True, "job_id": job.id}


@host_router.post("/api/scan-station/activate")
async def scan_station_activate(body: ScanStationActivateRequest) -> dict:
    """Schüler für den Zettel-/Stations-Fluss aktivieren OHNE einen Zettel zu
    drucken (Knopf „Erstellen" im Pairing-Kasten, Gegenstück zu „Erstellen
    und Drucken" im Druck-Dialog). Status/Code/Fortschritt identisch zu
    `/api/scan-station/print-sheet` (`activate_station_student` teilt sich
    die Logik mit `print_station_sheet_for`), aber synchron und ohne
    Druckauftrag — der physische Zettel kann jederzeit später über den
    Nachdruck-Knopf im „Aktuell in Ausgabe"-Kästchen gedruckt werden.
    `reactivate_old_code` s. `ScanStationActivateRequest`.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    state = get_state()
    student = state.find_student(body.student_id)
    if student is None:
        raise HTTPException(404, "Schüler nicht in einer geöffneten Klasse")
    return await activate_station_student(
        state, body.student_id, reactivate_old_code=body.reactivate_old_code
    )
