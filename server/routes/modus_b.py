"""Modus B — Live-Ausgabe: öffnen/schließen, QR/Display, Schüler-Join (öffentlich)
und Pairing am Host."""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime

from fastapi import HTTPException, Request

from ..hub import get_hub
from ..ratelimit import join_limiter
from ..sessions import (
    broadcast_displays,
    create_student_session,
    gen_join_secret,
    invalidate_session,
    load_and_push_paired_student,
    make_qr_data_url,
    send_display_update,
)
from ..state import get_state
from ._deps import (
    DisplayAuthorizeRequest,
    StudentJoinRequest,
    StudentPairRequest,
    _base_url,
    host_router,
    router,
)

log = logging.getLogger(__name__)


@host_router.post("/api/modus-b/open")
async def modus_b_open(request: Request) -> dict:
    """Live-Ausgabe öffnen: allgemeines Join-Secret + QR erzeugen und an iPads pushen."""
    state = get_state()
    state.modus_b_open = True
    # Frisches Join-Secret bei jedem Öffnen → alte Screenshots/QRs aus einer
    # früheren Ausgabe werden ungültig. Innerhalb einer Ausgabe bleibt es konstant
    # (rotiert NICHT mehr pro Zuordnung, 2026-06-18).
    state.modus_b_join_secret = gen_join_secret()
    state.modus_b_join_url = f"{_base_url(request)}/student?j={state.modus_b_join_secret}"
    state.modus_b_join_qr = make_qr_data_url(state.modus_b_join_url)

    await broadcast_displays(state)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "join_url": state.modus_b_join_url, "qr": state.modus_b_join_qr}


@host_router.post("/api/modus-b/close")
async def modus_b_close() -> dict:
    """Live-Ausgabe schließen: Join-Secret entwerten, offene pending-Sessions revoken.

    Bereits gepairte (aktive) Sessions laufen weiter, bis sie regulär abgeschlossen
    werden.
    """
    state = get_state()
    hub = get_hub()
    state.modus_b_open = False
    state.modus_b_join_secret = None
    state.modus_b_join_url = None
    state.modus_b_join_qr = None

    for sess in list(state.student_sessions.values()):
        if sess.state == "pending_pairing":
            await invalidate_session(state, sess, "revoked", reason="ausgabe-geschlossen")

    await broadcast_displays(state)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.get("/api/modus-b/qr")
async def modus_b_qr() -> dict:
    """QR/URL für den Host nachladen (z. B. nach Reconnect)."""
    state = get_state()
    return {
        "open": state.modus_b_open,
        "join_url": state.modus_b_join_url,
        "qr": state.modus_b_join_qr,
    }


@host_router.get("/api/display/qr")
async def display_qr(request: Request) -> dict:
    """QR, mit dem ein iPad die QR-Display-Seite (`/qr-display`) öffnet.

    Anders als der Schüler-Join-QR (`modus_b_join_qr`) zeigt dieser QR nur auf
    die statische Display-Seite — keine Schülerdaten, kein Join-Secret. Die
    LAN-IP-Korrektur aus `_base_url` macht den QR für das iPad erreichbar.
    """
    url = f"{_base_url(request)}/qr-display"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/display/authorize")
async def display_authorize(body: DisplayAuthorizeRequest) -> dict:
    """iPad-Display per Registrierungscode autorisieren (Registrierung am Host)."""
    code = body.registration_code.strip().upper()
    if not code:
        raise HTTPException(400, "registration_code fehlt")
    state = get_state()
    display = next(
        (d for d in state.displays.values() if d.registration_code == code and not d.authorized),
        None,
    )
    if not display:
        raise HTTPException(404, "Kein Display mit diesem Code (oder bereits autorisiert)")
    display.authorized = True
    await send_display_update(state, display)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id}


@router.post("/api/student/join")
async def student_join(body: StudentJoinRequest, request: Request) -> dict:
    """Öffentlich (per allgemeinem QR erreichbar): neue Schüler-Session anlegen.

    Verlangt das aktuelle Join-Secret aus dem QR. Liefert den langen
    session_token (Zugang) + den 4-stelligen Pairing-Code (Zuordnung am Host).
    """
    # DoS-Schutz: pro-IP gedrosselt, noch vor jeder Prüfung (auch Falsch-Secret-Floods).
    # request.client None (z. B. bei Test-Clients ohne Peer-Info) würde sonst alle
    # Anfragen in einen "?"-Bucket werfen und einen gemeinsamen Limit-Kontingent
    # teilen — lieber hart abweisen, bevor der Limiter gerufen wird.
    if request.client is None:
        raise HTTPException(400, "Client-Info nicht verfügbar")
    if not join_limiter.hit(request.client.host):
        raise HTTPException(429, "Zu viele Anfragen — bitte kurz warten")

    state = get_state()
    secret = body.join_secret.strip()
    if not state.modus_b_open or not state.modus_b_join_secret:
        raise HTTPException(403, "Live-Ausgabe ist geschlossen")
    # Konstantzeit-Vergleich — kein Short-Circuit-Timing-Leak wie bei `!=`.
    if not secrets.compare_digest(secret, str(state.modus_b_join_secret or "")):
        raise HTTPException(403, "Ungültiger oder abgelaufener QR")

    try:
        session = create_student_session(state)
    except RuntimeError:
        # Pairing-Code-Raum (4-stellig) erschöpft — sehr viele gleichzeitig Wartende.
        raise HTTPException(503, "Zu viele gleichzeitige Wartende — bitte gleich erneut scannen") from None
    await get_hub().broadcast_host(state.state_snapshot())
    return {"session_token": session.session_token, "pairing_code": session.pairing_code}


@host_router.post("/api/student/pair")
async def student_pair(body: StudentPairRequest) -> dict:
    """Host ordnet einen 4-stelligen Code einem Schüler zu (Doppel-Bestätigung)."""
    state = get_state()
    hub = get_hub()

    code = body.pairing_code.strip()
    student_id = body.student_id
    override = body.override_payment
    if not code or student_id is None:
        raise HTTPException(400, "pairing_code und student_id erforderlich")

    session = state.find_session_by_code(code)
    if not session:
        raise HTTPException(404, "Code unbekannt oder abgelaufen")

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status not in ("pending",):
        raise HTTPException(409, f"Schüler nicht verfügbar (Status: {student.status})")
    if state.find_session_by_student(student_id):
        raise HTTPException(409, "Schüler hat bereits eine Live-Session")

    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schülerinfo (Pairing) für %d fehlgeschlagen", student_id)
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    # Re-Check nach dem await (TOCTOU): während des IServ-Calls könnte eine
    # parallele Anfrage denselben Code/Schüler gebunden oder die Session
    # entwertet haben. Erneut prüfen, bevor wir verbindlich binden.
    if session.state != "pending_pairing" or state.find_session_by_code(code) is not session:
        raise HTTPException(409, "Code zwischenzeitlich vergeben oder abgelaufen")
    if student.status not in ("pending",):
        raise HTTPException(409, f"Schüler nicht verfügbar (Status: {student.status})")
    if state.find_session_by_student(student_id):
        raise HTTPException(409, "Schüler hat bereits eine Live-Session")

    # O6: nicht bezahlt → Host muss explizit freigeben. Genauso bei
    # ausstehendem Ermäßigungs-/Befreiungsnachweis (Antrag gestellt, aber
    # unentschieden) — der Host muss den Schüler bewusst freigeben. Beide
    # Blocker werden gesammelt und in einem einzigen Bestätigungs-Dialog
    # angezeigt; `override_payment` hebt alle Blocker auf einmal auf.
    # Nicht angemeldete Schüler haben keinen Bezahl-/Nachweis-Status → keine
    # Nachfrage, sie werden direkt gepaart.
    blockers = []
    if info.get("enrolled"):
        if not info.get("paid"):
            blockers.append({"kind": "unpaid", "amount_open": info.get("amount_open")})
        if info.get("remission_pending") or info.get("exemption_pending"):
            blockers.append({
                "kind": "nachweis",
                "remission": bool(info.get("remission_pending")),
                "exemption": bool(info.get("exemption_pending")),
            })
    if blockers and not override:
        raise HTTPException(
            409,
            detail={
                "reason": "blocked",
                "blockers": blockers,
                "msg": "Schüler-Status erfordert Freigabe",
            },
        )

    # Binden — ab jetzt gilt der session_token als freigegeben.
    session.student_id = student_id
    session.state = "paired"
    session.paired_at = datetime.now()
    session.last_activity = datetime.now()
    session.payment_overridden = bool(not info.get("paid") and override)
    student.status = "active"

    # Join-Secret ist konstant (PLAN §3, 2026-06-18) → kein Rotieren mehr.
    # Der QR bleibt unverändert; bereits angezeigte Displays brauchen kein Update.

    await hub.broadcast_host(state.state_snapshot())
    session.load_task = asyncio.create_task(
        load_and_push_paired_student(state, hub, session, student, info)
    )
    return {"ok": True, "student_id": student_id}


# Modus-A-Schülerladen liegt jetzt zentral in sessions.load_and_push_helper_student.
