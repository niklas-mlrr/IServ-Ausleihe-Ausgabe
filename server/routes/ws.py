from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from ..hub import get_hub
from ..sessions import (
    advance_helper,
    check_scanned_book,
    end_student,
    expected_isbns_from_info,
    gen_registration_code,
    handle_scan,
    print_loan_slip_for,
    send_display_update,
)
from ..state import DisplaySession, get_state

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/host")
async def ws_host(websocket: WebSocket, session_id: str | None = Cookie(default=None)) -> None:
    state = get_state()
    from ..config import get_config
    if not state.is_host_session_valid(session_id, get_config().host_session_ttl_s):
        await websocket.close(code=4003, reason="Nicht authentifiziert")
        return

    await websocket.accept()
    state.host_ws_connections.append(websocket)
    try:
        await websocket.send_json(state.state_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.host_ws_connections.remove(websocket)
        except ValueError:
            pass


@router.websocket("/ws/scanner/{token}")
async def ws_scanner(websocket: WebSocket, token: str) -> None:
    state = get_state()
    hub = get_hub()

    if token not in state.helper_sessions:
        await websocket.close(code=4004, reason="Ungültiger Token")
        return

    await websocket.accept()
    helper = state.helper_sessions[token]
    # Reconnect (Seite erneut geöffnet): die alte Verbindung sauber schließen,
    # statt sie verwaist offen zu lassen.
    old_ws = helper.ws
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass
    helper.ws = websocket

    # Schüler bereits zugewiesen? Info sofort schicken.
    if helper.student_id is not None:
        student = state.find_student(helper.student_id)
        if student and state.iserv:
            try:
                info = await state.iserv.get_student_info(helper.student_id, state.selected_schoolyear)
                info["form"] = student.form
                helper.expected_isbns = expected_isbns_from_info(info)
                await websocket.send_json({"type": "student_info", "student": info})
            except Exception as e:
                await websocket.send_json({"type": "error", "msg": str(e)})
        elif student is None:
            await websocket.send_json({"type": "waiting", "msg": "Warte auf Schüler-Zuweisung", "queue_size": state.pending_count()})
    else:
        await websocket.send_json({"type": "waiting", "msg": "Warte auf Schüler-Zuweisung", "queue_size": state.pending_count()})

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            raw = await websocket.receive_json()
            mtype = raw.get("type")

            if mtype == "next":
                # Aktuellen Schüler abschließen (kein Browser-Submit) und
                # nächsten Wartenden auf diesen Helfer setzen.
                await advance_helper(state, hub, helper)
                continue

            if mtype == "print":
                # Leihschein des aktuell zugewiesenen Schülers drucken.
                # Read-only PDF-Abruf + lokaler Druck (kein IServ-Submit).
                if helper.student_id is None:
                    await websocket.send_json({"type": "print_result", "ok": False, "msg": "Kein Schüler zugewiesen"})
                    continue
                try:
                    result = await print_loan_slip_for(state, helper.student_id)
                    await websocket.send_json({"type": "print_result", **result})
                except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
                    log.exception("Leihschein-Druck (Scanner) fehlgeschlagen")
                    await websocket.send_json({"type": "print_result", "ok": False, "msg": str(e)})
                continue

            if mtype != "scan":
                continue

            barcode = str(raw.get("value", "")).strip()
            if not barcode:
                continue

            helper.last_scan = barcode
            log.info("Scan von Helper %s: %s", token, barcode)

            student_id = helper.student_id
            if student_id is None:
                await websocket.send_json({
                    "type": "scan_result",
                    "barcode": barcode,
                    "status": "error",
                    "msg": "Kein Schüler zugewiesen",
                })
                continue

            # Vorab-Prüfung (read-only): gehört das Buch zum zugewiesenen Schüler?
            check = await check_scanned_book(state, helper.expected_isbns, barcode)
            if not check.get("ok"):
                await websocket.send_json({
                    "type": "scan_result",
                    "barcode": barcode,
                    "status": check["status"],
                    "msg": check["msg"],
                })
                await hub.broadcast_host(state.state_snapshot())
                continue

            result = await handle_scan(state, student_id, barcode)
            await websocket.send_json({"type": "scan_result", "barcode": barcode, **result})
            await hub.broadcast_host(state.state_snapshot())

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn es noch unsere Verbindung ist — bei einem
        # Reconnect hat die neue Verbindung helper.ws bereits übernommen
        # (analog ws_student), sonst würde der alte Disconnect sie wegräumen.
        if helper.ws is websocket:
            helper.ws = None
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modus B — iPad-Display (nur QR, keine Schülerdaten)
# ---------------------------------------------------------------------------

@router.websocket("/ws/display")
async def ws_display(websocket: WebSocket) -> None:
    state = get_state()
    hub = get_hub()

    await websocket.accept()
    display = DisplaySession(display_id=uuid.uuid4().hex[:12], registration_code=gen_registration_code())
    state.displays[display.display_id] = display
    display.ws = websocket
    await send_display_update(state, display)  # zeigt zunächst den Registrierungscode
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Display sendet nichts Inhaltliches; receive dient der Trennungserkennung.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.displays.pop(display.display_id, None)
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Modus B — Schüler-Session (Pairing-Gate + Scan)
# ---------------------------------------------------------------------------

@router.websocket("/ws/student/{session_token}")
async def ws_student(websocket: WebSocket, session_token: str) -> None:
    state = get_state()
    hub = get_hub()

    session = state.student_sessions.get(session_token)
    if not session or session.state not in ("pending_pairing", "paired"):
        # Ungültiger/entwerteter Token → neutrale "Vorgang abgeschlossen"-Seite.
        # accept() VOR close(), damit der Browser den 4006-Code zuverlässig
        # erhält (sonst sieht er nur 1006 und kann Token-Tod nicht erkennen).
        await websocket.accept()
        await websocket.close(code=4006, reason="Session ungültig")
        return

    await websocket.accept()
    # Reconnect: vorherige Verbindung derselben Session sauber schließen.
    old_ws = session.ws
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass
    session.ws = websocket
    session.last_activity = datetime.now()

    if session.state == "pending_pairing":
        await websocket.send_json({"type": "pending", "pairing_code": session.pairing_code})
    elif session.state == "paired" and session.student_id is not None:
        # Reconnect nach Pairing: Schülerinfo erneut senden.
        try:
            info = await state.iserv.get_student_info(session.student_id, state.selected_schoolyear)
            qs = state.find_student(session.student_id)
            info["form"] = qs.form if qs else ""
            session.expected_isbns = expected_isbns_from_info(info)
            await websocket.send_json({
                "type": "student_info",
                "student": info,
                "payment_overridden": session.payment_overridden,
            })
        except Exception as e:
            await websocket.send_json({"type": "error", "msg": str(e)})

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            raw = await websocket.receive_json()
            session.last_activity = datetime.now()
            mtype = raw.get("type")

            if mtype == "scan":
                barcode = str(raw.get("value", "")).strip()
                if not barcode:
                    continue
                if session.state != "paired" or session.student_id is None:
                    await websocket.send_json({
                        "type": "scan_result",
                        "barcode": barcode,
                        "status": "error",
                        "msg": "Noch nicht freigegeben",
                    })
                    continue
                session.last_scan = barcode
                # Vorab-Prüfung (read-only): gehört das gescannte Buch zu diesem
                # Schüler? Wenn nicht, gar nicht erst an den Worker stagen.
                check = await check_scanned_book(state, session.expected_isbns, barcode)
                if not check.get("ok"):
                    await websocket.send_json({
                        "type": "scan_result",
                        "barcode": barcode,
                        "status": check["status"],
                        "msg": check["msg"],
                    })
                    await hub.broadcast_host(state.state_snapshot())
                    continue
                result = await handle_scan(state, session.student_id, barcode)
                await websocket.send_json({"type": "scan_result", "barcode": barcode, **result})
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "finish":
                # Schüler schließt selbst ab → harter Zugriffsentzug.
                if session.student_id is not None:
                    await end_student(
                        state, hub, session.student_id,
                        queue_status="done", session_state="completed",
                    )
                else:
                    from ..sessions import invalidate_session
                    await invalidate_session(state, session, "completed", reason="self-finish")
                break

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn es noch unsere Verbindung ist.
        if session.ws is websocket:
            session.ws = None
        try:
            await hub.broadcast_host(state.state_snapshot())
        except Exception:
            pass
