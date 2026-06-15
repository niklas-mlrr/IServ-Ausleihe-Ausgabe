"""Modus-B-Session-Lebenszyklus (Live-Ausgabe) + gemeinsame Scan-Logik.

Sicherheitsmodell (PLAN §3): Der `session_token` ist der einzige
Daten-Zugangs-Credential (lang, kryptografisch zufällig). Der 4-stellige
`pairing_code` dient nur der menschlich vermittelten Zuordnung am Leitstand und
gewährt für sich genommen NIE Datenzugriff. Schülerdaten fließen erst nach
Leitstand-Bestätigung (`state == "paired"`). Beim Abschluss/Abbruch/Timeout wird
der Token hart entwertet: Worker-Context zu, WebSocket zu, Token aus dem RAM.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import secrets
from datetime import datetime

import qrcode

from .config import get_config
from .hub import get_hub
from .ratelimit import join_limiter
from .state import AppState, DisplaySession, StudentSessionB, get_state

log = logging.getLogger(__name__)

# Gut ablesbares Alphabet (keine 0/O/1/I) für den iPad-Registrierungscode.
_REG_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# ---------------------------------------------------------------------------
# Token-/Code-Erzeugung
# ---------------------------------------------------------------------------

def gen_session_token() -> str:
    """~256 bit Zufall — der eigentliche Zugangs-Credential."""
    return secrets.token_urlsafe(32)


def gen_join_secret() -> str:
    return secrets.token_urlsafe(16)


def gen_registration_code() -> str:
    return "".join(secrets.choice(_REG_ALPHABET) for _ in range(4))


def gen_pairing_code(state: AppState) -> str:
    """4-stelliger Code, eindeutig unter den aktiven pending-Sessions."""
    for _ in range(100):
        code = f"{secrets.randbelow(10000):04d}"
        if not state.code_in_use(code):
            return code
    raise RuntimeError("Kein freier Pairing-Code verfügbar")


def make_qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# Gemeinsame Scan-Logik (genutzt von /ws/scanner UND /ws/student)
# ---------------------------------------------------------------------------

async def handle_scan(state: AppState, student_id: int, barcode: str) -> dict:
    """Barcode an die Playwright-Worker-Session des Schülers geben.

    Bleibt read-only/staged (kein Submit) — siehe automation/worker.py."""
    worker_session = state.student_worker_sessions.get(student_id)
    if not worker_session:
        return {"status": "error", "msg": "Worker-Session nicht bereit"}
    try:
        return await worker_session.submit_barcode(barcode)
    except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
        log.exception("submit_barcode fehlgeschlagen")
        return {"status": "error", "msg": str(e)}


async def handle_commit(state: AppState, student_id: int, barcode: str) -> dict:
    """Barcode tatsächlich BUCHEN (Enter auf der Counter-Seite).

    Erste Prüfung ist das Gate: ohne `allow_booking` wird der Worker NICHT
    berührt (kein Enter, kein Produktionskontakt). Der Aufruf dieses Pfads ist
    zusätzlich auf den Leitstand-Endpoint `/api/commit-book` (+ confirm)
    beschränkt — Buchung nur nach Freigabe Niklas + Lukas (CLAUDE.md / PLAN §6).
    """
    if not get_config().allow_booking:
        return {"status": "blocked", "msg": "Buchung gesperrt (ALLOW_BOOKING=false)"}
    worker_session = state.student_worker_sessions.get(student_id)
    if not worker_session:
        return {"status": "error", "msg": "Worker-Session nicht bereit"}
    try:
        return await worker_session.commit_barcode(barcode)
    except Exception as e:  # noqa: BLE001
        log.exception("commit_barcode fehlgeschlagen")
        return {"status": "error", "msg": str(e)}


def release_worker(state: AppState, worker) -> None:
    """Worker-Context nach Abschluss zurück in den Pool (statt ihn zu verlieren).

    Fällt auf reines Schließen zurück, falls kein Pool verfügbar ist.
    """
    pool = state.worker_pool
    if pool is not None and hasattr(pool, "release"):
        asyncio.create_task(pool.release(worker))
    else:
        asyncio.create_task(worker.close())


# ---------------------------------------------------------------------------
# Modus-B-Session-Lebenszyklus
# ---------------------------------------------------------------------------

def create_student_session(state: AppState) -> StudentSessionB:
    session = StudentSessionB(
        session_token=gen_session_token(),
        pairing_code=gen_pairing_code(state),
    )
    state.student_sessions[session.session_token] = session
    log.info("Modus-B-Session angelegt (Code %s)", session.pairing_code)
    return session


async def invalidate_session(
    state: AppState, session: StudentSessionB, new_state: str, *, reason: str = ""
) -> None:
    """Harter Zugriffsentzug: Worker zu, WS zu, Token aus dem RAM (PLAN §3.2)."""
    if session.state in ("completed", "expired", "revoked"):
        return
    session.state = new_state  # type: ignore[assignment]

    # Worker-Context zurück in den Pool (falls vorhanden).
    if session.student_id is not None:
        worker = state.student_worker_sessions.pop(session.student_id, None)
        if worker:
            release_worker(state, worker)

    # Schüler-WS informieren und schließen.
    ws = session.ws
    session.ws = None
    if ws is not None:
        try:
            await ws.send_json({"type": "closed", "reason": new_state})
        except Exception:
            pass
        try:
            await ws.close(code=4006)
        except Exception:
            pass

    # Token endgültig entwerten.
    state.student_sessions.pop(session.session_token, None)
    log.info("Modus-B-Session %s… → %s (%s)", session.session_token[:6], new_state, reason)


async def end_student(
    state: AppState,
    hub,
    student_id: int,
    *,
    queue_status: str,
    session_state: str,
) -> None:
    """Schüler beenden (Abschluss/Skip/Abbruch) für Modus A UND B.

    Setzt den Queue-Status, löst die Helfer-Zuordnung (Modus A) und entwertet
    eine etwaige Modus-B-Session hart. Schließt in jedem Fall den Worker-Context.
    """
    student = state.find_student(student_id)
    if student:
        student.status = queue_status  # type: ignore[assignment]
        old_helper = student.assigned_helper
        student.assigned_helper = None
        if old_helper and old_helper in state.helper_sessions:
            state.helper_sessions[old_helper].student_id = None

    session = state.find_session_by_student(student_id)
    if session:
        await invalidate_session(state, session, session_state, reason=queue_status)
    else:
        worker = state.student_worker_sessions.pop(student_id, None)
        if worker:
            release_worker(state, worker)

    await hub.broadcast_leitstand(state.state_snapshot())


async def load_and_push_paired_student(
    state: AppState, hub, session: StudentSessionB, student, info: dict
) -> None:
    """Nach erfolgreichem Pairing: Worker öffnen und Schülerinfo ans Handy pushen."""
    if state.worker_pool:
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
            state.student_worker_sessions[student.student_id] = worker_session
        except Exception as e:  # noqa: BLE001
            log.exception("Worker-Session (Modus B) für %d fehlgeschlagen", student.student_id)
            if session.ws is not None:
                try:
                    await session.ws.send_json(
                        {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."}
                    )
                except Exception:
                    pass

    if session.ws is not None:
        try:
            await session.ws.send_json(
                {
                    "type": "student_info",
                    "student": info,
                    "payment_overridden": session.payment_overridden,
                }
            )
        except Exception:
            pass
    await hub.broadcast_leitstand(state.state_snapshot())


# ---------------------------------------------------------------------------
# iPad-Display
# ---------------------------------------------------------------------------

async def send_display_update(state: AppState, display: DisplaySession) -> None:
    """Aktuellen Zustand an ein Display schicken: Reg-Code, QR oder 'geschlossen'."""
    if display.ws is None:
        return
    try:
        if not display.authorized:
            msg = {
                "type": "registration",
                "code": display.registration_code,
                "display_id": display.display_id,
            }
        elif state.modus_b_open and state.modus_b_join_qr:
            msg = {"type": "qr", "qr": state.modus_b_join_qr}
        else:
            msg = {"type": "closed"}
        await display.ws.send_json(msg)
    except Exception:
        display.ws = None


async def broadcast_displays(state: AppState) -> None:
    for display in list(state.displays.values()):
        await send_display_update(state, display)


# ---------------------------------------------------------------------------
# Timeout-Sweeper (harter Zugriffsentzug bei Inaktivität)
# ---------------------------------------------------------------------------

async def sweep_expired_sessions() -> None:
    """Hintergrund-Loop: pending/paired Sessions nach TTL hart entwerten."""
    cfg = get_config()
    hub = get_hub()
    while True:
        await asyncio.sleep(30)
        join_limiter.sweep()  # Rate-Limit-Buckets aufräumen (kein unbegrenztes Wachstum)
        state = get_state()
        now = datetime.now()
        expired: list[StudentSessionB] = []
        for session in list(state.student_sessions.values()):
            if session.state == "pending_pairing":
                age = (now - session.created_at).total_seconds()
                if age > cfg.pending_pairing_ttl_s:
                    expired.append(session)
            elif session.state == "paired":
                idle = (now - session.last_activity).total_seconds()
                if idle > cfg.paired_idle_ttl_s:
                    expired.append(session)
        for session in expired:
            sid = session.student_id
            if sid is not None:
                await end_student(state, hub, sid, queue_status="pending", session_state="expired")
            else:
                await invalidate_session(state, session, "expired", reason="timeout")
        if expired:
            await hub.broadcast_leitstand(state.state_snapshot())
