from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from ..hub import get_hub
from ..state import get_state

log = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/leitstand")
async def ws_leitstand(websocket: WebSocket, session_id: str | None = Cookie(default=None)) -> None:
    state = get_state()
    if not session_id or session_id not in state.leitstand_session_ids:
        await websocket.close(code=4003, reason="Nicht authentifiziert")
        return

    await websocket.accept()
    state.leitstand_ws_connections.append(websocket)
    try:
        await websocket.send_json(state.state_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.leitstand_ws_connections.remove(websocket)
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
    helper.ws = websocket

    # Schüler bereits zugewiesen? Info sofort schicken.
    if helper.student_id is not None:
        student = state.find_student(helper.student_id)
        if student and state.iserv:
            try:
                info = await state.iserv.get_student_info(helper.student_id)
                await websocket.send_json({"type": "student_info", "student": info})
            except Exception as e:
                await websocket.send_json({"type": "error", "msg": str(e)})
        elif student is None:
            await websocket.send_json({"type": "waiting", "msg": "Warte auf Schüler-Zuweisung"})
    else:
        await websocket.send_json({"type": "waiting", "msg": "Warte auf Schüler-Zuweisung"})

    await hub.broadcast_leitstand(state.state_snapshot())

    try:
        while True:
            raw = await websocket.receive_json()
            if raw.get("type") != "scan":
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

            worker_session = state.student_worker_sessions.get(student_id)
            if worker_session:
                try:
                    result = await worker_session.submit_barcode(barcode)
                    await websocket.send_json({"type": "scan_result", "barcode": barcode, **result})
                except Exception as e:
                    log.exception("submit_barcode fehlgeschlagen")
                    await websocket.send_json({
                        "type": "scan_result",
                        "barcode": barcode,
                        "status": "error",
                        "msg": str(e),
                    })
            else:
                await websocket.send_json({
                    "type": "scan_result",
                    "barcode": barcode,
                    "status": "error",
                    "msg": "Worker-Session nicht bereit",
                })

            await hub.broadcast_leitstand(state.state_snapshot())

    except WebSocketDisconnect:
        pass
    finally:
        helper.ws = None
        try:
            await hub.broadcast_leitstand(state.state_snapshot())
        except Exception:
            pass
