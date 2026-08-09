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
    DisplayDisconnectRequest,
    HelperJoinRequest,
    StudentDismissRequest,
    StudentJoinRequest,
    StudentPairRequest,
    StudentRef,
    _base_url,
    host_router,
    router,
)

log = logging.getLogger(__name__)


@host_router.post("/api/modus-b/open")
async def modus_b_open(request: Request) -> dict:
    """Live-Ausgabe öffnen: allgemeines Join-Secret + QR erzeugen und an iPads pushen."""
    state = get_state()
    # Reopening rotates the join secret.  Pending sessions created with the
    # previous QR must be revoked as well; otherwise their old pairing code
    # remains a valid capability even though its QR is obsolete.
    for session in list(state.student_sessions.values()):
        if session.state == "pending_pairing":
            await invalidate_session(state, session, "revoked", reason="ausgabe-neu-geoeffnet")
    state.modus_b_open = True
    state.modus_b_paused = False
    # Frisches Join-Secret bei jedem Öffnen → alte Screenshots/QRs aus einer
    # früheren Ausgabe werden ungültig. Innerhalb einer Ausgabe bleibt es konstant
    # über alle Zuordnungen hinweg.
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
    state.modus_b_paused = False
    state.modus_b_join_secret = None
    state.modus_b_join_url = None
    state.modus_b_join_qr = None

    for sess in list(state.student_sessions.values()):
        if sess.state == "pending_pairing":
            await invalidate_session(state, sess, "revoked", reason="ausgabe-geschlossen")

    await broadcast_displays(state)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.post("/api/modus-b/pause")
async def modus_b_pause() -> dict:
    """QR-Anzeige auf den autorisierten iPad-Displays pausieren bzw.
    fortsetzen. Schüler-Sessions und der allgemeine Join-Secret bleiben dabei
    unverändert bestehen."""
    state = get_state()
    if not state.modus_b_open:
        raise HTTPException(409, "Live-Ausgabe ist geschlossen")
    state.modus_b_paused = not state.modus_b_paused
    await broadcast_displays(state)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "paused": state.modus_b_paused}


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
    """iPad-Display durch Klick auf einen Eintrag der Host-Freischalt-Liste
    autorisieren (`display_id`, wie `printer_display_enable`) — kein Tippen
    des Registrierungscodes mehr nötig, der Host wählt aus den aktuell
    verbundenen, noch unautorisierten Displays."""
    display_id = body.display_id.strip()
    if not display_id:
        raise HTTPException(400, "display_id fehlt")
    state = get_state()
    display = state.displays.get(display_id)
    if not display or display.authorized:
        raise HTTPException(404, "Kein Display mit dieser ID (oder bereits autorisiert)")
    display.authorized = True
    await send_display_update(state, display)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id}


@host_router.post("/api/display/disconnect")
async def display_disconnect(body: DisplayDisconnectRequest) -> dict:
    """Ein verbundenes iPad-Display auf Host-Anforderung trennen.

    Die Session wird vor dem WebSocket-Close aus dem State entfernt. Das
    verhindert, dass ein parallel laufender Cleanup-Pfad sie erneut als
    verbunden meldet. Das Display bekommt zuerst ein explizites Frame, damit
    die öffentliche QR-Seite ihren automatischen Reconnect für diesen
    absichtlichen Close abschaltet; ein Reload kann anschließend bewusst eine
    neue Registrierung starten.
    """
    display_id = body.display_id.strip()
    if not display_id:
        raise HTTPException(400, "display_id fehlt")
    state = get_state()
    display = state.displays.pop(display_id, None)
    if not display:
        raise HTTPException(404, "Kein Display mit dieser ID")

    websocket = display.ws
    display.ws = None
    if websocket is not None:
        await get_hub().send_websocket(websocket, {"type": "disconnected"})
        try:
            await websocket.close(code=4009, reason="Vom Host getrennt")
        except Exception:  # noqa: BLE001 — Cleanup darf den Host-Request nicht crashen
            log.debug("iPad-Display %s konnte nicht geschlossen werden", display_id)

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display_id}


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
        raise HTTPException(
            503, "Zu viele gleichzeitige Wartende — bitte gleich erneut scannen"
        ) from None
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
    # Live-Ausgabe für die Klasse des Schülers muss eingeschaltet sein — sonst
    # ist der Modus-B-Kasten in deren Klassenansicht ausgeblendet und eine
    # Zuordnung darf nicht möglich sein (defensive Absicherung des
    # client-seitigen Gates, s. host-render.js Queue-Pairing-Button).
    owning = state.find_student_with_ctx(student_id)
    if owning is not None and not owning[0].live_ausgabe:
        raise HTTPException(403, "Live-Ausgabe für diese Klasse deaktiviert")
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
            blockers.append(
                {
                    "kind": "nachweis",
                    "remission": bool(info.get("remission_pending")),
                    "exemption": bool(info.get("exemption_pending")),
                }
            )
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

    # Join-Secret ist konstant (PLAN §3): Zuordnung rotiert es nicht.
    # Der QR bleibt unverändert; bereits angezeigte Displays brauchen kein Update.

    await hub.broadcast_host(state.state_snapshot())
    session.load_task = asyncio.create_task(
        load_and_push_paired_student(state, hub, session, student, info)
    )
    return {"ok": True, "student_id": student_id}


@host_router.post("/api/student/dismiss")
async def student_dismiss(body: StudentDismissRequest) -> dict:
    """Host verwirft einen wartenden Pairing-Code ohne Zuordnung — die
    pending-Session wird revokierte, der Code verschwindet aus der Modus-B-Liste.

    Anwendungsfall: ein Schüler hat die Seite nach Abschluss neu geladen und
    per Re-Join einen neuen Code ausgelöst. Statt ihn zuzuordnen, räumt der
    Host ihn hier ab. `invalidate_session` sendet dem Schüler-Client ein
    ``closed``-Frame VOR dem Close 4006 — der Client geht auf den Done-Screen
    (``finished=true``) und verbindet sich NICHT neu (kein Re-Join-Loop).
    """
    code = body.pairing_code.strip()
    if not code:
        raise HTTPException(400, "pairing_code fehlt")
    state = get_state()
    session = state.find_session_by_code(code)
    if not session:
        raise HTTPException(404, "Code unbekannt oder abgelaufen")
    await invalidate_session(state, session, "revoked", reason="host-dismissed")
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.post("/api/helper-scan/start")
async def helper_scan_start(body: StudentRef, request: Request) -> dict:
    """Host erzeugt für einen übersprungenen oder abwesenden Schüler einen
    Einmal-QR, mit dem ein Helfer die Bücher des Schülers stellvertretend
    einscannen kann.

    Der QR trägt ein einmaliges Helfer-Secret (`/student?h=<secret>`). Beim
    Scannen bindet `POST /api/student/helper-join` eine Modus-B-Session direkt
    an den Schüler (ohne den sonst nötigen Pairing-Schritt). Pro Schüler ist
    nur ein gültiger QR erlaubt — ein neuer Aufruf ersetzt den alten.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status not in ("skipped", "absent"):
        raise HTTPException(409, f"Schüler nicht übersprungen/abwesend (Status: {student.status})")

    # Nur ein gültiger QR pro Schüler — ein neuer ersetzt den alten.
    for secret, (sid, _created) in list(state.helper_scan_secrets.items()):
        if sid == student_id:
            del state.helper_scan_secrets[secret]

    secret = gen_join_secret()
    state.helper_scan_secrets[secret] = (student_id, datetime.now())
    url = f"{_base_url(request)}/student?h={secret}"
    return {"ok": True, "url": url, "qr": make_qr_data_url(url)}


@router.post("/api/student/helper-join")
async def student_helper_join(body: HelperJoinRequest) -> dict:
    """Öffentlich (per Einmal-QR des Hosts erreichbar): bindet eine Modus-B-
    Session direkt an den übersprungenen/abwesenden Schüler und liefert den
    session_token.

    Das Secret ist eine Einmal-Capability: es wird beim ersten Aufruf aus dem
    State gepoppt. Bewusst KEIN `modus_b_open`-Check — der Host hat den QR
    gezielt erzeugt (der Button ist ohnehin nur bei offener Ausgabe sichtbar).
    Kein Payment-Gate: der Host entscheidet bewusst, für den Schüler zu scannen.
    """
    secret = body.helper_secret.strip()
    state = get_state()
    hub = get_hub()
    entry = state.helper_scan_secrets.pop(secret, None)
    if entry is None:
        raise HTTPException(403, "Ungültiger oder abgelaufener QR")
    student_id, _created = entry

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status not in ("skipped", "absent"):
        raise HTTPException(
            409, f"Schüler nicht mehr übersprungen/abwesend (Status: {student.status})"
        )
    if state.find_session_by_student(student_id):
        raise HTTPException(409, "Schüler hat bereits eine Live-Session")

    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schülerinfo (Helfer-Scan) für %d fehlgeschlagen", student_id)
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    # Binden — ab jetzt gilt der session_token als freigegeben (analog
    # student_pair, ohne Payment-Override).
    session = create_student_session(state)
    session.student_id = student_id
    session.state = "paired"
    session.paired_at = datetime.now()
    session.last_activity = datetime.now()
    session.payment_overridden = False
    # Abwesend + Bücher durch Helfer eingescant → Host „Fertig (abwesend)",
    # Lehrkraft „Leihschein & Bücherstapel entgegengenommen" (s. state.py).
    student.helper_scanned = True
    student.status = "active"

    await hub.broadcast_host(state.state_snapshot())
    session.load_task = asyncio.create_task(
        load_and_push_paired_student(state, hub, session, student, info)
    )
    return {"session_token": session.session_token}


# Modus-A-Schülerladen liegt jetzt zentral in sessions.load_and_push_helper_student.
