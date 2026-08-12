"""Schüler-Queue-Steuerung: Nächster, Skip, Disconnect(-All), Reset/Clear, Finish,
Buch-Alert freigeben."""

from __future__ import annotations

from datetime import datetime

from fastapi import HTTPException

from ..hub import get_hub
from ..sessions import (
    assign_student_to_helper,
    broadcast_printer_displays,
    end_student,
    teardown_students,
)
from ..state import get_state
from ._deps import (
    _EMPTY_CONTEXT_BODY,
    ContextIdBody,
    NextStudentRequest,
    StudentRef,
    host_router,
)


@host_router.post("/api/next-student")
async def next_student(body: NextStudentRequest) -> dict:
    helper_token = body.helper_token.strip()
    state = get_state()
    hub = get_hub()

    helper = state.helper_sessions.get(helper_token)
    if not helper:
        raise HTTPException(404, "Unbekannter Helper-Token")
    if helper.student_id is not None:
        raise HTTPException(409, "Helfer hat bereits einen aktiven Schüler")

    # „Nächster" zieht aus der Klasse, an die der Helfer gebunden ist; ohne
    # Bindung aus dem aktiven Kontext (Kompat).
    student = state.next_pending(helper.context_id)
    if not student:
        raise HTTPException(404, "Keine Schüler in der Queue")

    # Zuweisung + `loading`-Push an den Scanner (verbirgt die Queue, während
    # der Schüler geladen wird) zentral in `assign_student_to_helper`.
    await assign_student_to_helper(state, hub, helper, student)

    return {
        "ok": True,
        "student_id": student.student_id,
        "name": f"{student.lastname}, {student.firstname}",
    }


@host_router.post("/api/skip")
async def skip_student(body: StudentRef) -> dict:
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status in ("done", "skipped"):
        raise HTTPException(409, f"Schüler bereits als {student.status} markiert")

    # Setzt Queue-Status, löst Helfer und entwertet eine Modus-B-Session hart.
    await end_student(state, hub, student_id, queue_status="skipped", session_state="revoked")
    return {"ok": True}


@host_router.post("/api/disconnect")
async def disconnect_student(body: StudentRef) -> dict:
    """Schüler von Helfer/Schüler-Session trennen und auf 'Wartend' zurücksetzen.

    Anders als /api/skip wird der Schüler NICHT übersprungen, sondern bleibt als
    `pending` in der Queue (kann erneut zugeordnet werden). Für `pending`-Schüler
    ohne Verbindung ist es ein harmloser No-op.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()
    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status in ("done", "skipped"):
        raise HTTPException(409, f"Schüler ist {student.status}")
    await end_student(state, hub, student_id, queue_status="pending", session_state="revoked")
    return {"ok": True}


@host_router.post("/api/disconnect-all")
async def disconnect_all(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Alle aktiven Verbindungen (Modus A + B) eines Klassen-Kontexts trennen,
    Schüler zurück auf 'Wartend'. `context_id` optional im Body — fehlt er,
    aktiver Kontext (Kompat)."""
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    active_ids = [s.student_id for s in ctx.queue if s.status == "active"]
    for sid in active_ids:
        await end_student(
            state, hub, sid, queue_status="pending", session_state="revoked", broadcast=False
        )
    # Einmal am Ende broadcasten statt pro Schüler (sonst N Snapshots).
    if active_ids:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(active_ids)}


@host_router.post("/api/reset-queue")
async def reset_queue(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Queue-Status eines Klassen-Kontexts zurücksetzen: ALLE Schüler auf 'pending'.

    Trennt aktive Verbindungen (wie disconnect) und setzt zusätzlich
    `done`/`skipped`-Schüler zurück auf `pending`. Die Schüler bleiben in der
    Queue (kein Neuladen der Klasse). `context_id` optional — fehlt er, aktiver
    Kontext (Kompat).
    """
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    changed = [s.student_id for s in ctx.queue if s.status != "pending"]
    for sid in changed:
        await end_student(
            state, hub, sid, queue_status="pending", session_state="revoked", broadcast=False
        )
    # Einmal am Ende broadcasten statt pro Schüler (sonst N Snapshots).
    if changed:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(changed)}


@host_router.post("/api/clear-queue")
async def clear_queue(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Queue eines Klassen-Kontexts komplett LEEREN: alle Schüler entfernen.

    Anders als `/api/reset-queue` (setzt nur den Status zurück) wird die Queue
    hier ganz geleert. Laufende Live-Sessions der Schüler dieses Kontexts werden
    sauber beendet und Helfer-Zuordnungen gelöst. Der Kontext (Tab) bleibt
    bestehen — nur seine Queue wird leer. `context_id` optional — fehlt er,
    aktiver Kontext (Kompat).
    """
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    count = len(ctx.queue)
    student_ids = {s.student_id for s in ctx.queue}
    await teardown_students(state, hub, student_ids, reason="queue-leeren")
    ctx.queue = []
    # book_order/Katalog bleiben (Klasse/Tab bleibt offen, nur Queue leer).
    await hub.broadcast_host(state.state_snapshot())
    # Queue geleert → aktive Modus-B-Schüler dieser Klasse weg, kann die
    # Schülerauftrag-Bedingung eines Drucker-Displays kippen.
    await broadcast_printer_displays(state)
    return {"ok": True, "count": count}


@host_router.post("/api/finish")
async def finish_student(body: StudentRef) -> dict:
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")

    await end_student(state, hub, student_id, queue_status="done", session_state="completed")
    return {"ok": True}


@host_router.post("/api/finish-signed")
async def finish_signed_student(body: StudentRef) -> dict:
    """Bestätigung des unterschriebenen Leihscheins für einen Modus-B-Schüler.

    Wie der Helfer-Button darf dieser Pfad nur den explizit aktivierten
    Unterschriftenmodus abschließen; der normale Host-Abschluss bleibt davon
    unabhängig verfügbar.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()
    found = state.find_student_with_ctx(student_id)
    session = state.find_session_by_student(student_id)
    if (
        found is None
        or not found[0].done_signed
        or found[1].status != "active"
        or not found[1].slip_signing
        or session is None
        or not session.loan_slip_mode
    ):
        raise HTTPException(409, "Schüler ist nicht im Unterschriften-Modus")
    await end_student(state, hub, student_id, queue_status="done", session_state="completed")
    if state.helper_sessions:
        await hub.broadcast_queue_size(state)
    return {"ok": True}


@host_router.post("/api/clear-book-alert")
async def clear_book_alert(body: StudentRef) -> dict:
    """Blockierendes Ausgemustert-Hinweis-Modal am Schüler-Client (Modus B)
    oder an einer Scan-Station freigeben — beide Clients haben dafür bewusst
    keinen Schließen-Button (Freigabe nur durch den Host). Wird das Buch am
    Helfer-Scanner (Modus A) gemeldet, gibt es keine Client-Session dazu —
    dann räumt dieser Call nur das Host-Kästchen auf."""
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id

    state = get_state()
    session = state.find_session_by_student(student_id)
    if session is not None and session.book_alert_open:
        session.book_alert_open = False
        session.book_alert_payload = None
        if session.ws is not None:
            await get_hub().send_websocket(session.ws, {"type": "book_alert_clear"})

    station = state.find_station_by_student(student_id)
    if station is not None and station.book_alert_open:
        station.book_alert_open = False
        # Das 30-s-Leerlauf-TTL stand während der offenen Meldung still
        # (s. `sessions.expired_scan_stations`) — hier erst wieder scharf
        # stellen, sonst würde die stillstehende Zeit sofort nachgeholt.
        station.last_activity = datetime.now()
        if station.ws is not None:
            await get_hub().send_websocket(station.ws, {"type": "book_alert_clear"})

    await get_hub().broadcast_host(
        {"type": "book_alert", "student_id": student_id, "cleared": True}
    )
    return {"ok": True}
