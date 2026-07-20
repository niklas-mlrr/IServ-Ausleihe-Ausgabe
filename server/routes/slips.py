"""Leihschein-Druck (read-only PDF-Abruf + lokaler Druck), Drucker-Auswahl und
die GATED Buchung (`/api/commit-book`)."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

from ..config import get_config
from ..hub import get_hub
from ..print_queue import PrintJob, slip_name
from ..sessions import allowed_printers_for, handle_commit
from ..state import get_state
from ._deps import (
    CommitBookRequest,
    PrinterAddRequest,
    PrinterDuplexRequest,
    PrinterReactivateRequest,
    PrinterRemoveRequest,
    PrinterReorderRequest,
    PrintLoanSlipRequest,
    host_router,
    require_host,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Leihschein-Druck (read-only PDF-Abruf + lokaler Druck)
# ---------------------------------------------------------------------------


@host_router.post("/api/print-loan-slip")
async def print_loan_slip(body: PrintLoanSlipRequest, sid: str = Depends(require_host)) -> dict:
    """Leihschein eines Schülers holen (read-only) und lokal drucken — über die
    interne Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight).

    Kein Schreibzugriff auf IServ — `get_loan_slip_pdf` ist ein reiner GET, das
    Drucken passiert am Laptop/Macbook (siehe server/printing.py).

    Der Endpoint enqueued den Auftrag und **blockiert** bis der Worker ihn
    abgearbeitet hat (gedruckt/fehlgeschlagen) — die HTTP-Antwort ist Rückversicherung
    für den Fall, dass der Host-WS gerade nicht live ist. Das Live-Popup
    (Position / „wird gedruckt" / „gedruckt") läuft parallel via WS und erscheint
    nur an diesem Host (`sid`), nicht an allen eingeloggt-Verbundenen.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    # Leerer Drucker-Pool → Druck verweigern (Auftrag würde sonst endlos in der
    # Warteschlange hängen, da der Scheduler nichts verteilt).
    state = get_state()
    if not state.settings.printers:
        raise HTTPException(400, "Kein Drucker konfiguriert")
    # Drucker-Allowlist der Klasse des Schülers (Snapshot zum Enqueue-Zeitpunkt).
    # `None` = alle Pool-Drucker; eine Menge beschränkt auf diese Drucker. Ist die
    # Menge explizit, aber kein erlaubter Drucker im Pool (z. B. alle entfernt),
    # verweigern: sonst hinge der Auftrag endlos in der Warteschlange.
    allowed = allowed_printers_for(state, student_id)
    if allowed is not None:
        pool_ids = {p.id for p in state.settings.printers}
        if not (allowed & pool_ids):
            raise HTTPException(400, "Kein erlaubter Drucker im Pool für diese Klasse")
    # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur, wenn der
    # Host-Toggle gesetzt ist.
    pages = None if body.second_page else "1"

    student = state.find_student(student_id)
    name = slip_name(
        student.lastname if student else None,
        student.firstname if student else None,
        student.form if student else None,
    )
    job = PrintJob.create(
        role="host",
        student_id=student_id,
        pages=pages,
        name=name,
        host_sid=sid,
        allowed_printers=allowed,
    )
    await state.print_queue.enqueue(job)
    # Bis der Worker den Auftrag finalisiert hat (physisch gedruckt / fehl-
    # geschlagen). `done` wird im Worker gesetzt, sobald der Kopf abgearbeitet ist.
    await job.done.wait()
    res = dict(job.result or {})
    res.pop("job_handle", None)  # internes OS-Handle nicht nach außen reichen
    if not res.get("ok"):
        # 502 wie vor der Queue-Umstellung (Contract erhalten); das Live-Popup
        # läuft parallel via WS (`print_result` mit ok:false → toast-warn).
        raise HTTPException(502, res.get("msg") or res.get("detail") or "Druck fehlgeschlagen")
    return res


@host_router.get("/api/printers")
async def get_printers() -> dict:
    """Dem Host-Gerät bekannte Drucker + den konfigurierten Pool für die
    Einstellungen. Rein lesend (lpstat/Get-Printer, lokales System — kein
    IServ-/DB-Zugriff). Liefert `printers` (Geräteliste), `default`, `backend`
    und zusätzlich `pool` (konfigurierte Drucker mit Live-Last) sowie
    `waiting` (zentrale Warteschlange).
    """
    from ..printing import list_printers

    cfg = get_config()
    state = get_state()
    info = await list_printers(cfg.print_backend)
    info["pool"] = state.print_queue.pool_printers(state.settings.printers)
    info["waiting"] = state.print_queue.pool_summary()["waiting"]
    return info


# ---------------------------------------------------------------------------
# Drucker-Pool verwalten (Einstellungen-Dialog) — In-Memory + Persistenz
# ---------------------------------------------------------------------------


def _persist_printers(state) -> None:
    """Aktuellen Pool atomar nach `data/printers.json` wegschreiben (non-fatal
    — Schreibfehler crashen den Endpoint nicht)."""
    from ..printer_store import save as save_printers

    try:
        save_printers(state.settings.printers)
    except Exception:  # noqa: BLE001 — Persistenz darf Endpoint nicht crashen
        log.exception("Speichern der Drucker-Einstellungen fehlgeschlagen")


async def _after_pool_change(state, *, wake: bool = False) -> dict:
    """Nach einer Pool-Mutation: persistieren, ggf. Scheduler wecken (wartende
    Aufträge verteilen), alle Hosts über den neuen Snapshot informieren."""
    _persist_printers(state)
    if wake:
        state.print_queue.wake()
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "pool": state.print_queue.pool_printers(state.settings.printers)}


@host_router.post("/api/printers/add")
async def add_printer(body: PrinterAddRequest) -> dict:
    """Einen Drucker zum Pool hinzufügen. `name=None` fügt den Standarddrucker
    hinzu (falls noch nicht vorhanden); benannte Drucker nur, wenn das Gerät
    sie meldet und sie noch nicht im Pool sind. Duplex-Default `one_sided`.
    """
    from ..printing import list_printers

    cfg = get_config()
    state = get_state()
    name = body.name
    # name=None → Standarddrucker; sonst normalisieren.
    if name is not None:
        name = name.strip() or None
    # Doppelte Einträge vermeiden (Standarddrucker name=None bzw. gleicher Name).
    def _matches(p) -> bool:
        return (p.name is None and name is None) or (p.name == name and name is not None)

    if any(_matches(p) for p in state.settings.printers):
        raise HTTPException(409, "Drucker bereits im Pool")
    # Benannte Drucker müssen das Gerät aktuell melden (read-only Prüfung).
    if name is not None:
        info = await list_printers(cfg.print_backend)
        if name not in (info.get("printers") or []):
            raise HTTPException(400, f"Drucker „{name}“ am Gerät nicht gefunden")
    from ..state import PrinterConfig

    state.settings.printers.append(PrinterConfig(name=name))
    return await _after_pool_change(state, wake=True)


@host_router.post("/api/printers/remove")
async def remove_printer(body: PrinterRemoveRequest) -> dict:
    """Einen Drucker aus dem Pool entfernen. Drucker mit aktiven Druckaufträgen
    (Last > 0) können nicht entfernt werden (→ 400)."""
    state = get_state()
    pid = body.id.strip()
    printer = next((p for p in state.settings.printers if p.id == pid), None)
    if printer is None:
        raise HTTPException(404, "Drucker nicht gefunden")
    slots = state.print_queue.slots.get(pid)
    if slots and slots.load > 0:
        raise HTTPException(400, "Drucker noch beschäftigt — bitte warten")
    state.settings.printers = [p for p in state.settings.printers if p.id != pid]
    return await _after_pool_change(state)


@host_router.post("/api/printers/duplex")
async def set_printer_duplex(body: PrinterDuplexRequest) -> dict:
    """Duplex-Modus eines Druckers setzen (nur gespeichert, nicht ans Backend
    weitergereicht — Backends können Duplex CLI-seitig nicht zuverlässig)."""
    from ..state import DUPLEX_MODES

    if body.duplex not in DUPLEX_MODES:
        raise HTTPException(400, f"Unbekannter Duplex-Modus: {body.duplex}")
    state = get_state()
    pid = body.id.strip()
    printer = next((p for p in state.settings.printers if p.id == pid), None)
    if printer is None:
        raise HTTPException(404, "Drucker nicht gefunden")
    printer.duplex = body.duplex  # type: ignore[assignment]
    return await _after_pool_change(state)


@host_router.post("/api/printers/reorder")
async def reorder_printers(body: PrinterReorderRequest) -> dict:
    """Neue Reihenfolge aller Drucker (bestimmt Verteilungspriorität — linkester
    freier Drucker zuerst). Die übergebenen IDs müssen genau den aktuellen Pool
    abdecken (kein Hinzufügen/Entfernen hierfür)."""
    state = get_state()
    by_id = {p.id: p for p in state.settings.printers}
    if set(body.ids) != set(by_id) or len(body.ids) != len(by_id):
        raise HTTPException(400, "IDs passen nicht zum aktuellen Pool")
    state.settings.printers = [by_id[i] for i in body.ids]
    return await _after_pool_change(state, wake=True)


@host_router.post("/api/printers/reactivate")
async def reactivate_printer(body: PrinterReactivateRequest) -> dict:
    """Einen als hängend markierten Drucker wieder für neue Aufträge zulassen
    („Wieder aktivieren"-Button in den Drucker-Einstellungen, sichtbar sobald
    der Drucker nach Inaktivität fehlerhaft wurde). Entfernt die Marke und weckt
    den Scheduler, damit wartende Aufträge wieder dorthin dispatcht werden.
    Rein in-memory — kein Persistenz-/IServ-Zugriff."""
    state = get_state()
    pid = body.id.strip()
    printer = next((p for p in state.settings.printers if p.id == pid), None)
    if printer is None:
        raise HTTPException(404, "Drucker nicht gefunden")
    if not state.print_queue.reactivate(pid):
        raise HTTPException(400, "Drucker ist nicht fehlerhaft")
    return await _after_pool_change(state, wake=True)


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
