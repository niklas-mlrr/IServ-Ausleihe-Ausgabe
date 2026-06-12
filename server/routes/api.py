from __future__ import annotations

import asyncio
import base64
import io
import logging
import uuid

import qrcode
from fastapi import APIRouter, Cookie, HTTPException, Request, Response

from ..config import get_config
from ..hub import get_hub
from ..state import HelperSession, get_state

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _require_leitstand(session_id: str | None = Cookie(default=None)) -> str:
    state = get_state()
    if not session_id or session_id not in state.leitstand_session_ids:
        raise HTTPException(403, "Nicht eingeloggt")
    return session_id


def _base_url(request: Request) -> str:
    return f"https://{request.headers.get('host', 'localhost')}"


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.post("/api/login")
async def login(body: dict, response: Response) -> dict:
    cfg = get_config()
    if body.get("password") != cfg.leitstand_password:
        raise HTTPException(403, "Falsches Passwort")
    sid = str(uuid.uuid4())
    get_state().leitstand_session_ids.add(sid)
    response.set_cookie("session_id", sid, httponly=True, samesite="lax")
    return {"ok": True}


@router.post("/api/logout")
async def logout(response: Response, session_id: str | None = Cookie(default=None)) -> dict:
    if session_id:
        get_state().leitstand_session_ids.discard(session_id)
    response.delete_cookie("session_id")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Klassen
# ---------------------------------------------------------------------------

@router.get("/api/classes")
async def get_classes(session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    state = get_state()
    try:
        classes = await state.iserv.get_class_names()
    except Exception as e:
        log.exception("IServ-Klassen konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}")
    return {"classes": classes}


# ---------------------------------------------------------------------------
# Queue-Aufbau
# ---------------------------------------------------------------------------

@router.post("/api/select-class")
async def select_class(body: dict, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    form = body.get("form", "").strip()
    if not form:
        raise HTTPException(400, "form fehlt")
    state = get_state()
    hub = get_hub()
    try:
        students = await state.iserv.get_students_for_form(form)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}")

    from ..state import QueueStudent
    state.active_form = form
    state.queue = [
        QueueStudent(
            student_id=s["student_id"],
            lastname=s["lastname"],
            firstname=s["firstname"],
            form=form,
        )
        for s in students
    ]
    await hub.broadcast_leitstand(state.state_snapshot())
    return {"ok": True, "count": len(state.queue)}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@router.get("/api/state")
async def get_state_endpoint(session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    return get_state().state_snapshot()


# ---------------------------------------------------------------------------
# Helfer verwalten
# ---------------------------------------------------------------------------

@router.post("/api/add-helper")
async def add_helper(body: dict, request: Request, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    name = body.get("name", "Helfer").strip() or "Helfer"
    token = str(uuid.uuid4()).replace("-", "")[:16]
    state = get_state()
    state.helper_sessions[token] = HelperSession(token=token, name=name)
    url = f"{_base_url(request)}/scan.html?token={token}"

    # QR-Code als PNG-Daten-URL generieren
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    qr_data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    await get_hub().broadcast_leitstand(get_state().state_snapshot())
    return {"ok": True, "token": token, "url": url, "qr": qr_data_url}


@router.delete("/api/helper/{token}")
async def remove_helper(token: str, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    state = get_state()
    helper = state.helper_sessions.pop(token, None)
    if not helper:
        raise HTTPException(404, "Unbekannter Token")
    if helper.ws:
        try:
            await helper.ws.close()
        except Exception:
            pass
    await get_hub().broadcast_leitstand(state.state_snapshot())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Schüler-Queue-Steuerung
# ---------------------------------------------------------------------------

@router.post("/api/next-student")
async def next_student(body: dict, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    helper_token = body.get("helper_token", "").strip()
    state = get_state()
    hub = get_hub()

    helper = state.helper_sessions.get(helper_token)
    if not helper:
        raise HTTPException(404, "Unbekannter Helper-Token")
    if helper.student_id is not None:
        raise HTTPException(409, "Helfer hat bereits einen aktiven Schüler")

    student = state.next_pending()
    if not student:
        raise HTTPException(404, "Keine Schüler in der Queue")

    student.status = "active"
    student.assigned_helper = helper_token
    helper.student_id = student.student_id

    await hub.broadcast_leitstand(state.state_snapshot())
    asyncio.create_task(_load_and_push_student(state, hub, student, helper))

    return {"ok": True, "student_id": student.student_id,
            "name": f"{student.lastname}, {student.firstname}"}


@router.post("/api/skip")
async def skip_student(body: dict, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    student_id = body.get("student_id")
    if student_id is None:
        raise HTTPException(400, "student_id fehlt")
    state = get_state()
    hub = get_hub()

    student = state.find_student(int(student_id))
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status in ("done", "skipped"):
        raise HTTPException(409, f"Schüler bereits als {student.status} markiert")

    # Ggf. Worker-Session schließen
    _cleanup_student_session(state, int(student_id))

    old_helper_token = student.assigned_helper
    student.status = "skipped"
    student.assigned_helper = None
    if old_helper_token and old_helper_token in state.helper_sessions:
        state.helper_sessions[old_helper_token].student_id = None

    await hub.broadcast_leitstand(state.state_snapshot())
    return {"ok": True}


@router.post("/api/finish")
async def finish_student(body: dict, session_id: str = Cookie(default=None)) -> dict:
    _require_leitstand(session_id)
    student_id = body.get("student_id")
    if student_id is None:
        raise HTTPException(400, "student_id fehlt")
    state = get_state()
    hub = get_hub()

    student = state.find_student(int(student_id))
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")

    _cleanup_student_session(state, int(student_id))

    old_helper_token = student.assigned_helper
    student.status = "done"
    student.assigned_helper = None
    if old_helper_token and old_helper_token in state.helper_sessions:
        state.helper_sessions[old_helper_token].student_id = None

    await hub.broadcast_leitstand(state.state_snapshot())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Interne Helpers
# ---------------------------------------------------------------------------

def _cleanup_student_session(state, student_id: int) -> None:
    session = state.student_worker_sessions.pop(student_id, None)
    if session:
        asyncio.create_task(session.close())


async def _load_and_push_student(state, hub, student, helper) -> None:
    """Im Hintergrund: Schülerinfo laden, Worker-Session öffnen, Scanner informieren."""
    try:
        info = await state.iserv.get_student_info(student.student_id)
    except Exception as e:
        log.exception("Schülerinfo für %d konnte nicht geladen werden", student.student_id)
        await hub.send_scanner(helper.token, {"type": "error", "msg": f"IServ-Fehler: {e}"})
        return

    if state.worker_pool:
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
            state.student_worker_sessions[student.student_id] = worker_session
        except Exception as e:
            log.exception("Worker-Session für Schüler %d fehlgeschlagen", student.student_id)
            await hub.send_scanner(
                helper.token,
                {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."},
            )

    await hub.send_scanner(helper.token, {"type": "student_info", "student": info})
    await hub.broadcast_leitstand(state.state_snapshot())
