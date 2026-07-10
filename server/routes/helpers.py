"""Helfer verwalten: anlegen (QR), an Klassen-Kontext binden, entfernen."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

from fastapi import HTTPException, Request

from ..hub import get_hub
from ..sessions import end_student, make_qr_data_url
from ..state import HelperSession, get_state
from ._deps import AddHelperRequest, SetHelperClassRequest, _base_url, host_router


@host_router.post("/api/add-helper")
async def add_helper(body: AddHelperRequest, request: Request) -> dict:
    name = body.name.strip() or "Helfer"
    token = str(uuid.uuid4()).replace("-", "")[:16]
    state = get_state()
    # Optionale Bindung an einen Klassen-Kontext (Helfer bedient genau diese
    # Klasse; „Nächster" zieht aus ihrer Queue). Ohne context_id später per
    # /api/helper/{token}/class setzbar.
    context_id = str(body.context_id or "").strip() or None
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    if context_id is None:
        # Kein Kontext explizit gewählt: erster Reiter (Host-Reihenfolge,
        # also am weitesten links) mit tatsächlich Wartenden — ein neuer
        # Helfer soll dort einsteigen, wo schon gestaut ist, statt unbenutzt
        # in einer leeren Klasse zu landen.
        context_id = next(
            (cid for cid in state.contexts if state.pending_count(cid) > 0),
            None,
        )
    state.helper_sessions[token] = HelperSession(token=token, name=name, context_id=context_id)
    url = f"{_base_url(request)}/scan?token={token}"
    qr_data_url = make_qr_data_url(url)

    await get_hub().broadcast_host(get_state().state_snapshot())
    return {"ok": True, "token": token, "url": url, "qr": qr_data_url}


@host_router.post("/api/helper/{token}/class")
async def set_helper_class(token: str, body: SetHelperClassRequest) -> dict:
    """Helfer an einen Klassen-Kontext binden (`context_id`) oder lösen
    (`context_id=null`). Rein transient — kein IServ-/DB-Zugriff."""
    state = get_state()
    helper = state.helper_sessions.get(token)
    if not helper:
        raise HTTPException(404, "Unbekannter Token")
    context_id = body.context_id
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    helper.context_id = context_id
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": helper.context_id}


@host_router.delete("/api/helper/{token}")
async def remove_helper(token: str) -> dict:
    state = get_state()
    hub = get_hub()
    helper = state.helper_sessions.get(token)
    if not helper:
        raise HTTPException(404, "Unbekannter Token")
    # Vollständige Cleanup-Reihenfolge analog invalidate_session / disconnect:
    # 1. laufenden Lade-Task canceln (sonst leakt der Worker-Context, falls er
    #    noch in open_student steckt),
    # 2. aktiven Schüler des Helfers beenden → Worker zu + Queue zurück auf
    #    pending (Modus A) bzw. Session revoked (Modus B via end_student),
    # 3. WS schließen,
    # 4. Helper aus der Map nehmen.
    # Reihenfolge 1 vor 2 stellt sicher, dass end_student's eigener cancel+
    # await denselben Task nicht doppelt canceln muss (idempotent, aber klarer).
    if helper.load_task is not None and not helper.load_task.done():
        helper.load_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await helper.load_task
        helper.load_task = None
    # Ein noch laufender Grace-Teardown-Task (Verbindung kürzlich getrennt)
    # wird hiermit ebenfalls cancelt — sonst hinge er bis zu 3 s als No-op
    # im Raum (die Re-Checks in _deferred_end machen ihn ohnehin unschädlich,
    # aber sauber ist, ihn deterministisch abzuräumen).
    if helper.end_task is not None and not helper.end_task.done():
        helper.end_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await helper.end_task
        helper.end_task = None
    if helper.student_id is not None:
        await end_student(
            state, hub, helper.student_id,
            queue_status="pending", session_state="revoked",
        )
    if helper.ws:
        try:
            await helper.ws.close()
        except Exception:
            pass
    state.helper_sessions.pop(token, None)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True}
