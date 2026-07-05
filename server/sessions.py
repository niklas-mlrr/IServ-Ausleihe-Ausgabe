"""Modus-B-Session-Lebenszyklus (Live-Ausgabe) + gemeinsame Scan-Logik.

Sicherheitsmodell (PLAN §3): Der `session_token` ist der einzige
Daten-Zugangs-Credential (lang, kryptografisch zufällig). Der 4-stellige
`pairing_code` dient nur der menschlich vermittelten Zuordnung am Host und
gewährt für sich genommen NIE Datenzugriff. Schülerdaten fließen erst nach
Host-Bestätigung (`state == "paired"`). Beim Abschluss/Abbruch/Timeout wird
der Token hart entwertet: Worker-Context zu, WebSocket zu, Token aus dem RAM.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import logging
import secrets
from datetime import datetime

import qrcode

from .book_order import get_book_order_for_form
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


def expected_isbns_from_info(info: dict) -> set[str]:
    """ISBN-Menge der Bücher, die zu diesem Schüler gehören (Anmeldung + bereits
    ausgeliehen). Grundlage für die Vorab-Prüfung „gehört dieses Buch zu dir?"."""
    return {b["isbn"] for b in info.get("books", []) if b.get("isbn")}


def booking_isbn_sets_from_info(info: dict) -> tuple[set[str], set[str]]:
    """Zerlegt die Buchliste in (vorgemerkt, ausgeliehen) — für die Buchungs-
    Vorabprüfung (Freigabe 2026-07-02).

    `vorgemerkt` = bestellt UND von der Reihe ist noch KEIN Buch auf den Schüler
    ausgeliehen (genau die buchbaren ISBNs — `get_student_info` setzt den Status
    einer Reihe auf „ausgeliehen", sobald ein Exemplar verliehen ist).
    `ausgeliehen` = Reihe bereits auf den Schüler ausgeliehen (nur für die
    Fehlermeldung „Reihe schon ausgeliehen").
    """
    vormerk: set[str] = set()
    lent: set[str] = set()
    for b in info.get("books", []):
        isbn = b.get("isbn")
        if not isbn:
            continue
        if b.get("status") == "vorgemerkt":
            vormerk.add(isbn)
        elif b.get("status") == "ausgeliehen":
            lent.add(isbn)
    return vormerk, lent


async def evaluate_scan_for_booking(
    state: AppState, vormerk_isbns: set[str], lent_isbns: set[str], barcode: str
) -> dict:
    """Buchungs-Vorabprüfung (read-only) VOR jedem Eintippen ins Feld.

    Freigabe 2026-07-02: Gebucht (Enter) wird nur, wenn ALLE Bedingungen erfüllt
    sind — sonst wird der Barcode gar nicht erst ins Feld gefüllt.

      1. Buch im Lager: `available and not distributed and not deleted`.
      2. Schüler hat das Buch bestellt UND von der Reihe ist noch keins auf ihn
         ausgeliehen (= ISBN ∈ vormerk_isbns).

    Streng bei Unsicherheit: fehlender API-Client, noch nicht geladene Buchliste
    oder ein Lookup-Fehler → `ok=False` (NICHT buchen). Bewusst strenger als eine
    reine „gehört das Buch zu dir?"-Prüfung: da wir bei Erfolg automatisch Enter
    drücken (Buchung gegen Produktion), muss die Vorabprüfung sicher sein.

    Gibt `{"ok": True, "isbn", "title", "code"}` bei Buchbarkeit, sonst
    `{"ok": False, "status", "msg", ...}`. Reiner Read-Pfad.
    """
    if state.iserv is None:
        return {"ok": False, "status": "error", "msg": "Kein IServ-Client"}
    if not vormerk_isbns and not lent_isbns:
        # Buchliste noch nicht geladen → keine sichere Aussage möglich, nicht buchen.
        return {
            "ok": False,
            "status": "not_ready",
            "msg": "Buchliste noch nicht geladen — bitte erneut scannen",
        }
    try:
        book = await state.iserv.get_book_by_code(barcode)
    except Exception as e:  # noqa: BLE001 — bei Lookup-Fehler NICHT buchen
        log.warning("Buch-Lookup für %s fehlgeschlagen: %s", barcode, e)
        return {"ok": False, "status": "error", "msg": f"Buch-Lookup fehlgeschlagen: {e}"}

    if book is None:
        return {"ok": False, "status": "unknown_book", "msg": "Buch unbekannt"}

    isbn = book["isbn"]
    title = book.get("title") or isbn

    # Ausgemustert-Prüfung ZUERST — noch vor der Anmeldeprüfung, damit ein
    # ausgemustertes Buch immer als solches erkannt wird, auch wenn der Schüler
    # es gar nicht bestellt hat. Muss am Scanner (rot) und am Host sichtbar sein.
    if book["deleted"]:
        return {
            "ok": False,
            "status": "book_deleted",
            "msg": f"Buch ausgemustert: {title}",
            "isbn": isbn,
            "title": title,
        }

    # Bedingung 2: bestellt UND Reihe noch nicht ausgeliehen.
    if isbn not in vormerk_isbns:
        if isbn in lent_isbns:
            return {
                "ok": False,
                "status": "series_already_lent",
                "msg": f"Reihe bereits ausgeliehen: {title}",
                "isbn": isbn,
                "title": title,
            }
        return {
            "ok": False,
            "status": "not_enrolled",
            "msg": f"Nicht bestellt: {title}",
            "isbn": isbn,
            "title": title,
        }

    # Bedingung 1: Buch im Lager.
    if book["distributed"] or not book["available"]:
        return {
            "ok": False,
            "status": "not_in_stock",
            "msg": f"Nicht im Lager (verliehen): {title}",
            "isbn": isbn,
            "title": title,
        }

    return {"ok": True, "isbn": isbn, "title": title, "code": book["code"]}


async def process_scan(
    state: AppState,
    student_id: int,
    vormerk_isbns: set[str],
    lent_isbns: set[str],
    barcode: str,
) -> dict:
    """Vollständige Scan-Verarbeitung, gemeinsam für Scanner (Modus A) und
    Schüler (Modus B). Returnt das scan_result-Payload (ohne `type`/`barcode`).

    Ablauf (Freigabe 2026-07-02):
      1. Buchungs-Vorabprüfung (read-only). Nicht erfüllt → Feld wird NICHT
         berührt, Grund zurückmelden.
      2. Erfüllt UND `ALLOW_BOOKING=true` → tatsächlich buchen (Enter).
      3. Erfüllt, aber Gate aus (Default) → nur stagen (fill, kein Enter) —
         Standardbetrieb bleibt read-only, bis explizit scharfgeschaltet.
    """
    decision = await evaluate_scan_for_booking(state, vormerk_isbns, lent_isbns, barcode)
    if not decision["ok"]:
        if decision["status"] == "book_deleted":
            student = state.find_student(student_id)
            await get_hub().broadcast_host({
                "type": "book_deleted_alert",
                "barcode": barcode,
                "isbn": decision.get("isbn"),
                "title": decision.get("title"),
                "student": f"{student.lastname}, {student.firstname}" if student else None,
            })
        return {
            "status": decision["status"],
            "msg": decision["msg"],
            "isbn": decision.get("isbn"),
        }
    if get_config().allow_booking:
        result = await handle_commit(state, student_id, barcode)
    else:
        result = await handle_scan(state, student_id, barcode)
    result.setdefault("isbn", decision.get("isbn"))
    return result


async def handle_commit(state: AppState, student_id: int, barcode: str) -> dict:
    """Barcode tatsächlich BUCHEN (Enter auf der Counter-Seite).

    Erste Prüfung ist das Gate: ohne `allow_booking` wird der Worker NICHT
    berührt (kein Enter, kein Produktionskontakt). Der Aufruf dieses Pfads ist
    zusätzlich auf den Host-Endpoint `/api/commit-book` (+ confirm)
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


async def print_loan_slip_for(
    state: AppState,
    student_id: int,
    *,
    variant: str = "student-always_school-auto",
    pages: str | None = "1",
) -> dict:
    """Leihschein eines Schülers holen (read-only GET) und lokal drucken.

    Geholt wird stets der 2-seitige Beleg (Seite 1 = immer gedruckt, Seite 2 =
    Schüler-Leihschein). `pages` wählt den zu druckenden Bereich: ``"1"`` nur die
    erste Seite (Default), ``None`` beide Seiten.

    Gemeinsame Orchestrierung für den Host-Endpoint (`/api/print-loan-slip`)
    und den Scanner (WS `print`). Kein Schreibzugriff auf IServ — `get_loan_slip_pdf`
    ist ein reiner GET, das Drucken passiert lokal am Laptop/Macbook
    (siehe server/printing.py).

    Gibt `{ok, backend, detail, [path]}` zurück oder wirft bei Fehlern eine
    Exception (vom Aufrufer in eine Client-Antwort zu wandeln).
    """
    from .printing import print_pdf

    cfg = get_config()
    pdf = await state.iserv.get_loan_slip_pdf(student_id, variant=variant)
    result = await print_pdf(
        pdf,
        backend=cfg.print_backend,
        printer_name=state.printer_name_override or cfg.printer_name,
        sumatra_path=cfg.sumatra_path,
        output_dir=cfg.print_output_dir,
        label=f"leihschein_{student_id}",
        pages=pages,
    )
    log.info(
        "Leihschein gedruckt: student_id=%s backend=%s pages=%s",
        student_id, result.get("backend"), pages or "alle",
    )
    return result


def release_worker(state: AppState, worker) -> None:
    """Worker-Context nach Abschluss zurück in den Pool (statt ihn zu verlieren).

    Fällt auf reines Schließen zurück, falls kein Pool verfügbar ist. Die
    Release-Coroutine wird als Task mit starkem Reference gehalten — CPython's
    asyncio führt Tasks nur in einem WeakSet, ein fire-and-forget-Task kann
    sonst mid-Coroutine GC'd werden und der Context bleibt für immer aus dem
    Pool draußen (bei WORKER_CONTEXTS=2 reicht das zweimal zum stillen Drain).
    """
    pool = state.worker_pool
    coro = pool.release(worker) if (pool is not None and hasattr(pool, "release")) else worker.close()
    t = asyncio.create_task(coro)
    _release_tasks.add(t)
    t.add_done_callback(_release_tasks.discard)


# Starke Referenzen auf in-flight Release-Tasks — verhindert GC vor Completion.
_release_tasks: set[asyncio.Task] = set()


def set_worker_session(state: AppState, student_id: int, worker_session) -> None:
    """Worker-Session eines Schülers registrieren — vorhandene zuvor freigeben.

    Ohne diese Freigabe würde ein Überschreiben (z. B. zwei `open_student`-Läufe
    für denselben Schüler) den alten Context aus dem Pool verlieren — bei nur
    wenigen Contexts (Default 2) sind so nach kurzer Zeit alle weg.
    """
    old = state.student_worker_sessions.get(student_id)
    if old is not None and old is not worker_session:
        release_worker(state, old)
    state.student_worker_sessions[student_id] = worker_session


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

    # In-flight Lade-Task abbrechen — sonst leakt der Worker-Context, wenn
    # open_student noch in load_card steckt (s. end_student / worker.py).
    # Der Await erzwingt, dass die CancelledError den Task tatsächlich trifft
    # (bzw. der Task über den Stale-Guard in load_and_push_paired_student
    # sauber zurückkehrt), BEVOR wir unten den Worker poppen — sonst raced das
    # pop() gegen das nachträgliche set_worker_session und der Context wird
    # trotzdem als orphan für den toten student_id registriert. Kein Lock
    # wird hier gehalten → kein Deadlock-Risiko.
    if session.load_task is not None and not session.load_task.done():
        session.load_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await session.load_task
    session.load_task = None

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
    # Token niemals loggen — auch nicht als Präfix (PLAN §3.7). Stattdessen
    # ein nicht-reversibler 8-Zeichen-Hash als Korrelationshandle.
    token_handle = hashlib.sha256(session.session_token.encode()).hexdigest()[:8]
    log.info("Modus-B-Session %s → %s (%s)", token_handle, new_state, reason)


async def end_student(
    state: AppState,
    hub,
    student_id: int,
    *,
    queue_status: str,
    session_state: str,
    broadcast: bool = True,
) -> None:
    """Schüler beenden (Abschluss/Skip/Abbruch) für Modus A UND B.

    Setzt den Queue-Status, löst die Helfer-Zuordnung (Modus A) und entwertet
    eine etwaige Modus-B-Session hart. Schließt in jedem Fall den Worker-Context.

    `broadcast=False` unterdrückt den Host-Snapshot-Push — für Batch-Aufrufe
    (disconnect-all/reset-queue), die am Ende einmal selbst broadcasten.
    """
    student = state.find_student(student_id)
    if student:
        student.status = queue_status  # type: ignore[assignment]
        old_helper = student.assigned_helper
        student.assigned_helper = None
        if old_helper and old_helper in state.helper_sessions:
            h = state.helper_sessions[old_helper]
            # In-flight Lade-Task abbrechen, damit ein noch laufendes
            # open_student seinen Worker-Context zurückgibt (BaseException-
            # Handler in worker.py). Sonst leakt der Context, weil er erst
            # nach open_student in student_worker_sessions registriert wird
            # und pop() unten Nothing fände — Pool läuft unter schnellem
            # „Weiter"-Klicken leer.
            #
            # WICHTIG: student_id wird VOR dem Await auf None gesetzt, damit
            # der Stale-Guard in load_and_push_helper_student (der
            # helper.student_id gegen den ursprünglich zugewiesenen student_id
            # prüft) den nach open_student noch synchronen set_worker_session-
            # Aufruf überspringt und den Context selbst schließt. Der Await
            # selbst ist sicher (kein Lock gehalten) — der Task wartet nur auf
            # open_student (BaseException-Handler fängt Cancel) oder ist schon
            # darüber hinaus und läuft via Stale-Guard leer.
            if h.load_task is not None and not h.load_task.done():
                h.load_task.cancel()
            h.student_id = None
            h.expected_isbns = set()
            h.vormerk_isbns = set()
            h.lent_isbns = set()
            if h.load_task is not None and not h.load_task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await h.load_task
            h.load_task = None
            # Scanner sonst ohne jede Rückmeldung mit dem alten (getrennten)
            # Schüler stehen — der Helfer sieht dann weder Trennung noch neuen
            # Wartezustand ("Alle Verbindungen trennen" wirkte sonst nur am Host).
            await hub.send_scanner(old_helper, {
                "type": "waiting",
                "msg": "Warte auf Schüler-Zuweisung",
                "queue_size": state.pending_count(),
            })

    session = state.find_session_by_student(student_id)
    if session:
        await invalidate_session(state, session, session_state, reason=queue_status)
    else:
        worker = state.student_worker_sessions.pop(student_id, None)
        if worker:
            release_worker(state, worker)

    if broadcast:
        await hub.broadcast_host(state.state_snapshot())


async def load_and_push_helper_student(state: AppState, hub, student, helper) -> None:
    """Modus A: Schülerinfo laden, an den Scanner pushen, Worker-Context öffnen.

    Reihenfolge bewusst: erst `student_info` an den Scanner (sofort sichtbar),
    dann der (langsamere) Worker-Aufbau.
    """
    # Identität festhalten, bevor wir awaiten — end_student/skip kann während
    # open_student helper.student_id auf None (oder einen neuen Schüler) setzen.
    assigned_student_id = student.student_id
    try:
        info = await state.iserv.get_student_info(student.student_id, state.selected_schoolyear)
    except Exception as e:  # noqa: BLE001
        log.exception("Schülerinfo für %d konnte nicht geladen werden", student.student_id)
        await hub.send_scanner(helper.token, {"type": "error", "msg": f"IServ-Fehler: {e}"})
        return

    info["form"] = getattr(student, "form", "")
    info["book_order"] = await get_book_order_for_form(state, info["form"])
    helper.expected_isbns = expected_isbns_from_info(info)
    helper.vormerk_isbns, helper.lent_isbns = booking_isbn_sets_from_info(info)
    await hub.send_scanner(helper.token, {"type": "student_info", "student": info})
    await hub.broadcast_host(state.state_snapshot())

    if state.worker_pool:
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Worker-Session für Schüler %d fehlgeschlagen", student.student_id)
            await hub.send_scanner(
                helper.token,
                {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."},
            )
            return
        # Stale-Guard: end_student/skip kann während open_student gelaufen sein
        # und helper.student_id auf None gesetzt haben. CancelledError trifft
        # erst am nächsten await — aber zwischen open_student-Return und
        # set_worker_session gibt es KEIN await (synchroner Aufruf). Ohne
        # diesen Guard würde der Task den Context für einen student_id
        # registrieren, dessen end_student schon gelaufen ist → Worker-Orphan
        # unter totalem student_id (Pool-Slot belegt, niemand poppt ihn jemals).
        if helper.student_id != assigned_student_id:
            log.info(
                "Stale load_and_push_helper_student für %d — Helfer nicht mehr "
                "zugewiesen (helper.student_id=%r), Context zurück.",
                assigned_student_id, helper.student_id,
            )
            try:
                await worker_session.close()
            except Exception:
                log.exception("Schließen des stale Worker-Contexts fehlgeschlagen")
            return
        set_worker_session(state, student.student_id, worker_session)


async def advance_helper(state: AppState, hub, helper) -> dict:
    """Helfer auf den nächsten Wartenden setzen.

    Schließt den aktuellen Schüler ab (`end_student` → Worker-Context zu, KEIN
    Browser-Submit/keine Buchung) und lädt den nächsten Pending aus der Queue.
    """
    if helper.student_id is not None:
        await end_student(
            state, hub, helper.student_id,
            queue_status="done", session_state="completed",
        )

    student = state.next_pending()
    if not student:
        await hub.send_scanner(helper.token, {"type": "waiting", "msg": "Warteschlange leer", "queue_size": state.pending_count()})
        return {"ok": False, "reason": "empty"}

    student.status = "active"
    student.assigned_helper = helper.token
    helper.student_id = student.student_id
    await hub.broadcast_host(state.state_snapshot())
    helper.load_task = asyncio.create_task(
        load_and_push_helper_student(state, hub, student, helper)
    )
    return {"ok": True, "student_id": student.student_id}


async def load_and_push_paired_student(
    state: AppState, hub, session: StudentSessionB, student, info: dict
) -> None:
    """Nach erfolgreichem Pairing: Schülerinfo SOFORT ans Handy pushen, Worker danach.

    `info` ist bereits im Endpoint geladen — das Handy kann seine Bestellliste
    also unmittelbar rendern. Das Öffnen der Playwright-Worker-Session
    (`open_student` → Browser-Navigation, mehrere Sekunden) blockiert die
    Handy-Anzeige NICHT mehr; es läuft im Anschluss. Scannt der Schüler, bevor
    der Worker bereit ist, meldet `handle_scan` sauber „Worker nicht bereit"
    (der Schüler liest ohnehin erst die Liste → Worker ist rechtzeitig da).
    """
    # Identität + Session-State festhalten — invalidate_session kann während
    # open_student die Session auf "revoked" setzen und aus student_sessions
    # poppen. Ohne Stale-Gard registriert der Task danach den Context für einen
    # student_id, der schon nicht mehr zur Session gehört → Worker-Orphan.
    paired_student_id = student.student_id
    info["form"] = getattr(student, "form", "")
    info["book_order"] = await get_book_order_for_form(state, info["form"])
    session.expected_isbns = expected_isbns_from_info(info)
    session.vormerk_isbns, session.lent_isbns = booking_isbn_sets_from_info(info)
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

    if state.worker_pool:
        try:
            worker_session = await state.worker_pool.open_student(
                student.student_id,
                f"{student.lastname}, {student.firstname}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Worker-Session (Modus B) für %d fehlgeschlagen", student.student_id)
            if session.ws is not None:
                try:
                    await session.ws.send_json(
                        {"type": "error", "msg": f"Playwright-Fehler: {e}. Buchung manuell."}
                    )
                except Exception:
                    pass
            await hub.broadcast_host(state.state_snapshot())
            return
        # Stale-Gard: invalidate_session/Modus-B-Close kann während open_student
        # gelaufen sein (session.state != "paired" oder student_id gezogen).
        # Dann Context selbst schließen — set_worker_session würde sonst einen
        # Orphan unter totalem student_id registrieren.
        if session.student_id != paired_student_id or session.state != "paired":
            log.info(
                "Stale load_and_push_paired_student für %d — Session nicht mehr "
                "paired (state=%r, student_id=%r), Context zurück.",
                paired_student_id, session.state, session.student_id,
            )
            try:
                await worker_session.close()
            except Exception:
                log.exception("Schließen des stale Worker-Contexts (Modus B) fehlgeschlagen")
            await hub.broadcast_host(state.state_snapshot())
            return
        set_worker_session(state, student.student_id, worker_session)

    await hub.broadcast_host(state.state_snapshot())


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
            msg = {"type": "qr", "qr": state.modus_b_join_qr, "url": state.modus_b_join_url}
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
        # Einzelne Iteration darf den Loop nie töten — eine flüchtige Exception
        # (z. B. transienter IServ-Fehler in end_student) würde sonst den
        # Sweeper dauerhaft killen → unbegrenztes Session-Wachstum.
        try:
            join_limiter.sweep()  # Rate-Limit-Buckets aufräumen (kein unbegrenztes Wachstum)
            state = get_state()
            state.sweep_host_sessions(cfg.host_session_ttl_s)  # abgelaufene Host-Logins entfernen
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
            # broadcast=False — einmal am Ende bündeln (wie /api/disconnect-all),
            # sonst N Snapshots pro Sweep.
            for session in expired:
                sid = session.student_id
                if sid is not None:
                    await end_student(
                        state, hub, sid,
                        queue_status="pending", session_state="expired",
                        broadcast=False,
                    )
                else:
                    await invalidate_session(state, session, "expired", reason="timeout")
        except asyncio.CancelledError:
            raise  # Shutdown — Loop sauber beenden.
        except Exception:
            log.exception("Sweeper iteration fehlgeschlagen (non-fatal)")
            continue
        if expired:
            await hub.broadcast_host(state.state_snapshot())
