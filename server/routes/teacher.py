"""Lehrkraft-Statusansicht (`/teacher`) — Live-Fortschritt genau einer
Modus-B-Klasse für ein eigenes Lehrkraft-Gerät. Host-Endpunkte für QR-Minten,
Autorisieren und Trennen; öffentliche, token-authentifizierte Endpunkte für
die der Lehrkraft erlaubten Aktionen (`pending <-> skipped` sowie das
informative „Leihschein entgegengenommen"-Flag). Die Lehrkraft selbst
verbindet sich unauthentifiziert (per Cookie) via `/ws/teacher?token=...`
(s. routes/ws.py) und sieht vor der Host-Freischaltung ausschließlich den
Registrierungscode — keine Klassen-/Schülerdaten (PLAN
docs/teacher_status_page_plan.md)."""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request

from ..hub import get_hub
from ..sessions import (
    gen_registration_code,
    make_qr_data_url,
    revoke_teacher_session,
    send_teacher_update,
)
from ..state import TeacherSession, get_state
from ._deps import (
    TeacherAuthorizeRequest,
    TeacherDisconnectRequest,
    TeacherSlipCollectedRequest,
    TeacherStatusRequest,
    _base_url,
    host_router,
    router,
)

log = logging.getLogger(__name__)

# Lange, kryptografisch zufällige Zugangs-Credentials (analog
# `StudentSessionB.session_token`) — 48 Hex-Zeichen, in der URL nie eine
# Klasse/einen Schülernamen preisgebend.
_TOKEN_BYTES = 24


def _new_teacher_token() -> str:
    return secrets.token_hex(_TOKEN_BYTES)


@host_router.get("/api/teacher/qr")
async def teacher_qr(request: Request, context_id: str = "") -> dict:
    """Neuen QR für die Lehrkraft-Ansicht EINER Klasse minten. Eine noch nicht
    autorisierte bestehende Session dieser Klasse wird dabei ersetzt (ihre WS
    wird geschlossen); eine bereits autorisierte Session blockiert das Minten
    — der Host muss zuerst explizit trennen (PLAN: „Ein neuer QR ersetzt eine
    noch nicht autorisierte Session; für eine autorisierte Session braucht der
    Host eine sichtbare Aktion 'Lehrkraft trennen'")."""
    context_id = context_id.strip()
    if not context_id:
        raise HTTPException(400, "context_id fehlt")
    state = get_state()
    if context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")

    existing = state.teacher_session_for_context(context_id)
    if existing is not None:
        if existing.authorized:
            raise HTTPException(
                409, "Lehrkraft bereits verbunden — zuerst trennen"
            )
        await revoke_teacher_session(state, existing, reason="neuer QR gemintet")

    session = TeacherSession(
        token=_new_teacher_token(),
        context_id=context_id,
        registration_code=gen_registration_code(),
    )
    state.teacher_sessions[session.token] = session
    # Ohne diesen Broadcast bliebe die Lehrkraft-Kachel im Klassen-Tab beim
    # Host so lange auf dem alten Stand (kein Code sichtbar), bis irgendeine
    # ANDERE Aktion zufällig einen Snapshot auslöst — der QR-Minten-Response
    # selbst aktualisiert nur den QR-Dialog, nicht den gecachten Host-State.
    await get_hub().broadcast_host(state.state_snapshot())
    url = f"{_base_url(request)}/teacher?token={session.token}"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/teacher/authorize")
async def teacher_authorize(body: TeacherAuthorizeRequest) -> dict:
    """Host bestätigt den am Lehrkraft-Gerät angezeigten Registrierungscode
    im selben Klassen-Tab — erst danach liefert die WS Klassen-/Schülerdaten."""
    context_id = body.context_id.strip()
    code = body.registration_code.strip().upper()
    if not context_id or not code:
        raise HTTPException(400, "context_id und registration_code erforderlich")
    state = get_state()
    session = state.teacher_session_for_context(context_id)
    if session is None or session.authorized or session.registration_code != code:
        raise HTTPException(404, "Kein passender Code für diese Klasse (oder bereits autorisiert)")
    session.authorized = True
    await send_teacher_update(state, session)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.post("/api/teacher/disconnect")
async def teacher_disconnect(body: TeacherDisconnectRequest) -> dict:
    """Lehrkraft-Session einer Klasse explizit trennen (autorisiert oder
    nicht) — die sichtbare Host-Aktion, die einen versehentlichen QR-Klick
    von einem echten Trennen einer laufenden Ansicht unterscheidet."""
    context_id = body.context_id.strip()
    if not context_id:
        raise HTTPException(400, "context_id fehlt")
    state = get_state()
    session = state.teacher_session_for_context(context_id)
    if session is None:
        raise HTTPException(404, "Keine Lehrkraft-Session für diese Klasse")
    await revoke_teacher_session(state, session, reason="vom Host getrennt")
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


def _authorized_session(state, token: str) -> TeacherSession:
    token = token.strip()
    if not token:
        raise HTTPException(400, "token fehlt")
    session = state.teacher_sessions.get(token)
    if session is None or not session.authorized:
        raise HTTPException(403, "Ungültige oder nicht autorisierte Lehrkraft-Session")
    return session


@router.post("/api/teacher/skip")
async def teacher_skip(body: TeacherStatusRequest) -> dict:
    """Einzige der Lehrkraft erlaubte Aktion: einen wartenden Schüler ihrer
    Klasse als abwesend markieren (`pending -> skipped`). Rein lokaler
    Runtime-State-Wechsel — kein IServ-Write, keine Playwright-Aktion. `active`
    und `done` bleiben strikt hostgesteuert (nicht über diesen Endpunkt
    erreichbar, s. PLAN „Lehrer-Ablauf und Rechte")."""
    state = get_state()
    session = _authorized_session(state, body.token)
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    found = state.find_student_with_ctx(body.student_id)
    if found is None or found[0].id != session.context_id:
        raise HTTPException(404, "Schüler nicht in dieser Klasse")
    student = found[1]
    if student.status != "pending":
        raise HTTPException(409, f"Nur wartende Schüler überspringbar (Status: {student.status})")
    student.status = "skipped"
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


@router.post("/api/teacher/undo-skip")
async def teacher_undo_skip(body: TeacherStatusRequest) -> dict:
    """Rücknahme von `teacher_skip`: `skipped -> pending`, wieder wartend."""
    state = get_state()
    session = _authorized_session(state, body.token)
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    found = state.find_student_with_ctx(body.student_id)
    if found is None or found[0].id != session.context_id:
        raise HTTPException(404, "Schüler nicht in dieser Klasse")
    student = found[1]
    if student.status != "skipped":
        raise HTTPException(
            409, f"Nur übersprungene Schüler zurücksetzbar (Status: {student.status})"
        )
    student.status = "pending"
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


@router.post("/api/teacher/slip-collected")
async def teacher_set_slip_collected(body: TeacherSlipCollectedRequest) -> dict:
    """Die Lehrkraft markiert, ob sie den (unterschriebenen) Leihschein eines
    Schülers ihrer Klasse entgegengenommen hat — rein informatives Flag
    (`QueueStudent.slip_collected`), ändert nie `status`. Die Klassenoption
    `done_collected` und der Status `done` müssen aktiv sein; außerdem muss für
    den Schüler bereits ein Leihschein gedruckt worden sein (`slip_printed`)."""
    state = get_state()
    session = _authorized_session(state, body.token)
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    found = state.find_student_with_ctx(body.student_id)
    if found is None or found[0].id != session.context_id:
        raise HTTPException(404, "Schüler nicht in dieser Klasse")
    ctx, student = found
    if not ctx.done_collected:
        raise HTTPException(409, "Leihschein-Einsammeln für diese Klasse nicht aktiviert")
    if student.status != "done":
        raise HTTPException(
            409,
            f"Leihschein erst bei abgeschlossenem Schüler erfassbar (Status: {student.status})",
        )
    if not student.slip_printed:
        raise HTTPException(409, "Für diesen Schüler wurde noch kein Leihschein gedruckt")
    student.slip_collected = bool(body.collected)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "slip_collected": student.slip_collected}
