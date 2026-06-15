from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Cookie, HTTPException, Request, Response

from ..config import get_config
from ..hub import get_hub
from ..ratelimit import join_limiter
from ..sessions import (
    broadcast_displays,
    create_student_session,
    end_student,
    gen_join_secret,
    handle_commit,
    invalidate_session,
    load_and_push_paired_student,
    make_qr_data_url,
    send_display_update,
)
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
    # secure=True: Cookie nur über HTTPS (der Server läuft ausschließlich über TLS).
    response.set_cookie("session_id", sid, httponly=True, samesite="lax", secure=True)
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
    qr_data_url = make_qr_data_url(url)

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

    # Setzt Queue-Status, löst Helfer und entwertet eine Modus-B-Session hart.
    await end_student(state, hub, int(student_id), queue_status="skipped", session_state="revoked")
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

    await end_student(state, hub, int(student_id), queue_status="done", session_state="completed")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Leihschein-Druck (read-only PDF-Abruf + lokaler Druck)
# ---------------------------------------------------------------------------

@router.post("/api/print-loan-slip")
async def print_loan_slip(body: dict, session_id: str = Cookie(default=None)) -> dict:
    """Leihschein eines Schülers holen (read-only) und lokal drucken.

    Kein Schreibzugriff auf IServ — `get_loan_slip_pdf` ist ein reiner GET, das
    Drucken passiert am Laptop/Macbook (siehe server/printing.py).
    """
    _require_leitstand(session_id)
    student_id = body.get("student_id")
    if student_id is None:
        raise HTTPException(400, "student_id fehlt")
    variant = str(body.get("variant", "student")).strip() or "student"

    state = get_state()
    cfg = get_config()
    try:
        pdf = await state.iserv.get_loan_slip_pdf(int(student_id), variant=variant)
    except Exception as e:
        log.exception("Leihschein-PDF für %s konnte nicht geladen werden", student_id)
        raise HTTPException(502, f"IServ-Fehler beim Leihschein: {e}")

    from ..printing import print_pdf
    try:
        result = await print_pdf(
            pdf,
            backend=cfg.print_backend,
            printer_name=cfg.printer_name,
            sumatra_path=cfg.sumatra_path,
            output_dir=cfg.print_output_dir,
            label=f"leihschein_{int(student_id)}",
        )
    except Exception as e:
        log.exception("Druck des Leihscheins fehlgeschlagen (Backend %s)", cfg.print_backend)
        raise HTTPException(500, f"Druck fehlgeschlagen: {e}")

    log.info("Leihschein gedruckt: student_id=%s backend=%s", student_id, result.get("backend"))
    return result


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


@router.post("/api/commit-book")
async def commit_book(body: dict, session_id: str = Cookie(default=None)) -> dict:
    """Einen Barcode tatsächlich BUCHEN (Enter auf der IServ-Counter-Seite).

    Dreifach gesperrt: Leitstand-Auth + `confirm:true` + Server-Flag
    `allow_booking`. Default `ALLOW_BOOKING=false` → gesperrt; `handle_commit`
    berührt den Worker dann gar nicht erst. Nur für den freigegebenen
    Buchungstest (Niklas + Lukas, CLAUDE.md / PLAN §6).
    """
    _require_leitstand(session_id)              # Gate 2: Leitstand-Bestätigung
    cfg = get_config()
    if not cfg.allow_booking:                   # Gate 1: Server-Flag
        raise HTTPException(403, "Buchung gesperrt (ALLOW_BOOKING=false)")
    if not bool(body.get("confirm")):           # Gate 3: bewusster Extra-Schritt
        raise HTTPException(400, "confirm:true erforderlich")

    student_id = body.get("student_id")
    if student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = int(student_id)

    state = get_state()
    hub = get_hub()
    barcode = str(body.get("barcode", "")).strip() or _last_scan_for(state, student_id)
    if not barcode:
        raise HTTPException(400, "Kein Barcode (weder übergeben noch gestaged)")

    result = await handle_commit(state, student_id, barcode)
    await hub.broadcast_leitstand(state.state_snapshot())
    return {"ok": result.get("status") in ("booked", "unknown"), "barcode": barcode, **result}


# ---------------------------------------------------------------------------
# Modus B — Live-Ausgabe
# ---------------------------------------------------------------------------

@router.post("/api/modus-b/open")
async def modus_b_open(request: Request, session_id: str = Cookie(default=None)) -> dict:
    """Live-Ausgabe öffnen: allgemeines Join-Secret + QR erzeugen und an iPads pushen."""
    _require_leitstand(session_id)
    state = get_state()
    state.modus_b_open = True
    state.modus_b_join_secret = gen_join_secret()
    state.modus_b_join_url = f"{_base_url(request)}/student.html?j={state.modus_b_join_secret}"
    state.modus_b_join_qr = make_qr_data_url(state.modus_b_join_url)

    await broadcast_displays(state)
    await get_hub().broadcast_leitstand(state.state_snapshot())
    return {"ok": True, "join_url": state.modus_b_join_url, "qr": state.modus_b_join_qr}


@router.post("/api/modus-b/close")
async def modus_b_close(session_id: str = Cookie(default=None)) -> dict:
    """Live-Ausgabe schließen: Join-Secret entwerten, offene pending-Sessions revoken.

    Bereits gepairte (aktive) Sessions laufen weiter, bis sie regulär abgeschlossen
    werden.
    """
    _require_leitstand(session_id)
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
    await hub.broadcast_leitstand(state.state_snapshot())
    return {"ok": True}


@router.get("/api/modus-b/qr")
async def modus_b_qr(session_id: str = Cookie(default=None)) -> dict:
    """QR/URL für den Leitstand nachladen (z. B. nach Reconnect)."""
    _require_leitstand(session_id)
    state = get_state()
    return {
        "open": state.modus_b_open,
        "join_url": state.modus_b_join_url,
        "qr": state.modus_b_join_qr,
    }


@router.post("/api/display/authorize")
async def display_authorize(body: dict, session_id: str = Cookie(default=None)) -> dict:
    """iPad-Display per Registrierungscode autorisieren (Registrierung am Leitstand)."""
    _require_leitstand(session_id)
    code = str(body.get("registration_code", "")).strip().upper()
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
    await get_hub().broadcast_leitstand(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id}


@router.post("/api/student/join")
async def student_join(body: dict, request: Request) -> dict:
    """Öffentlich (per allgemeinem QR erreichbar): neue Schüler-Session anlegen.

    Verlangt das aktuelle Join-Secret aus dem QR. Liefert den langen
    session_token (Zugang) + den 4-stelligen Pairing-Code (Zuordnung am Leitstand).
    """
    # DoS-Schutz: pro-IP gedrosselt, noch vor jeder Prüfung (auch Falsch-Secret-Floods).
    ip = request.client.host if request.client else "?"
    if not join_limiter.hit(ip):
        raise HTTPException(429, "Zu viele Anfragen — bitte kurz warten")

    state = get_state()
    secret = str(body.get("join_secret", "")).strip()
    if not state.modus_b_open or not state.modus_b_join_secret:
        raise HTTPException(403, "Live-Ausgabe ist geschlossen")
    if secret != state.modus_b_join_secret:
        raise HTTPException(403, "Ungültiger oder abgelaufener QR")

    session = create_student_session(state)
    await get_hub().broadcast_leitstand(state.state_snapshot())
    return {"session_token": session.session_token, "pairing_code": session.pairing_code}


@router.post("/api/student/pair")
async def student_pair(body: dict, session_id: str = Cookie(default=None)) -> dict:
    """Leitstand ordnet einen 4-stelligen Code einem Schüler zu (Doppel-Bestätigung)."""
    _require_leitstand(session_id)
    state = get_state()
    hub = get_hub()

    code = str(body.get("pairing_code", "")).strip()
    student_id = body.get("student_id")
    override = bool(body.get("override_payment", False))
    if not code or student_id is None:
        raise HTTPException(400, "pairing_code und student_id erforderlich")
    student_id = int(student_id)

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
        info = await state.iserv.get_student_info(student_id)
    except Exception as e:
        log.exception("Schülerinfo (Pairing) für %d fehlgeschlagen", student_id)
        raise HTTPException(502, f"IServ-Fehler: {e}")

    # O6: nicht bezahlt → Leitstand muss explizit freigeben.
    if not info.get("paid") and not override:
        raise HTTPException(
            409,
            detail={
                "reason": "unpaid",
                "amount_open": info.get("amount_open"),
                "msg": "Schüler nicht bezahlt",
            },
        )

    # Binden — ab jetzt gilt der session_token als freigegeben.
    session.student_id = student_id
    session.state = "paired"
    session.paired_at = _now()
    session.last_activity = _now()
    session.payment_overridden = bool(not info.get("paid") and override)
    student.status = "active"

    await hub.broadcast_leitstand(state.state_snapshot())
    asyncio.create_task(load_and_push_paired_student(state, hub, session, student, info))
    return {"ok": True, "student_id": student_id}


# ---------------------------------------------------------------------------
# Interne Helpers
# ---------------------------------------------------------------------------

def _now():
    from datetime import datetime
    return datetime.now()


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
