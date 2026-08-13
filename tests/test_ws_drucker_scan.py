"""Tests für den Drucker-Scanner (`/ws/drucker-scan`) und die davon abhängige
Aufteilung des Scan-Station-`print_mode`-Handlers (`/ws/scan-station`) in
„student" (Schülerauslöser via Drucker-Scanner) vs. „auto"-Fallback.

Fährt beide WS-Endpunkte über echten `TestClient.websocket_connect`, Aufbau
gespiegelt von `tests/test_ws_scan_station.py`. PRODUKTIONSSCHUTZ: `state.iserv`
ist ein reiner In-Memory-Fake, kein echter Playwright/Browser/Netzwerk.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import server.hub as hub_module
import server.routes.ws as ws_module
import server.sessions as sessions_module
from server.print_queue import PrintJob
from server.state import AppState, PrinterConfig, PrinterDisplaySession, QueueStudent

_TOKEN = "abc123def456"
_STATION_TOKEN = "aaa111aaa222"


class _FakeIServ:
    """Read-only In-Memory-Fake — liefert Bücherstatus + Buch-Lookup."""

    def __init__(self) -> None:
        # student_id -> Liste offener/ausgeliehener Buchzeilen.
        self.books_by_student: dict[int, list[dict]] = {}
        # code -> book dict für get_book_by_code.
        self.book_by_code: dict[str, dict] = {}

    async def get_student_info(self, student_id, schoolyear):
        return {
            "student_id": student_id,
            "books": [dict(b) for b in self.books_by_student.get(student_id, [])],
            "current_books": [],
        }

    async def get_book_by_code(self, code):
        return self.book_by_code.get(code)


def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _recv_until(ws, mtype: str, cap: int = 20) -> dict:
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") == mtype:
            return msg
    raise AssertionError(f"Nachricht vom Typ {mtype!r} nicht innerhalb {cap} Frames erhalten")


@pytest.fixture
def scan_env(monkeypatch):
    """Frische AppState in ws.py/hub.py/sessions.py; ein Schüler mit
    vergebenem Zettel-Code, ohne offene vorgemerkte Bücher (bereit zum
    Drucken). Gibt (state, student, code)."""
    state = AppState()
    state.iserv = _FakeIServ()
    monkeypatch.setattr(ws_module, "get_state", lambda: state)
    monkeypatch.setattr(hub_module, "get_state", lambda: state)
    monkeypatch.setattr(sessions_module, "get_state", lambda: state)
    ctx = state.open_context("Klasse 10a")
    student = QueueStudent(student_id=1, lastname="Muster", firstname="Max", form="Klasse 10a")
    ctx.queue.append(student)
    code = state.allocate_station_code(1)
    return state, student, code


def _connect_authorized_scanner(client, state):
    ws = client.websocket_connect(f"/ws/drucker-scan?token={_TOKEN}").__enter__()
    _recv_until(ws, "registration")
    state.printer_scanners[_TOKEN].authorized = True
    state.printer_scanners[_TOKEN].label = "Drucker 1"
    return ws


# ---- Pairing ----------------------------------------------------------


def test_missing_token_is_rejected(client, scan_env):
    with pytest.raises(Exception), client.websocket_connect("/ws/drucker-scan") as ws:  # noqa: B017
        ws.receive_json()


def test_fresh_scanner_gets_registration_code_only(client, scan_env):
    state, _student, _code = scan_env
    with client.websocket_connect(f"/ws/drucker-scan?token={_TOKEN}") as ws:
        msg = _recv_until(ws, "registration")
        assert msg["code"] == state.printer_scanners[_TOKEN].registration_code


def test_banned_token_is_refused(client, scan_env):
    state, _student, _code = scan_env
    state.banned_printer_scanner_tokens.add(_TOKEN)
    with client.websocket_connect(f"/ws/drucker-scan?token={_TOKEN}") as ws:
        assert ws.receive_json()["type"] == "forbidden"
    assert _TOKEN not in state.printer_scanners


def test_unauthorized_scanner_ignores_scan(client, scan_env):
    state, _student, code = scan_env
    with client.websocket_connect(f"/ws/drucker-scan?token={_TOKEN}") as ws:
        _recv_until(ws, "registration")
        ws.send_json({"type": "scan", "code": code})
        # Kein Frame kommt zurück (der Scanner ist ohnehin stumm) — der
        # relevante Beleg ist, dass gar keine Klassifikation stattfand.
        time.sleep(0.1)
        assert state.printer_scanners[_TOKEN].last_scan_status is None


# ---- Scan-Klassifikation -----------------------------------------------


def test_ready_scan_enqueues_job_and_sets_print_mode(client, scan_env):
    state, student, code = scan_env
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": code})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "ready")
        scanner = state.printer_scanners[_TOKEN]
        assert scanner.last_scan_payload["lastname"] == "Muster"
        # Klassen-Präfix „Klasse " abgeschnitten (Mirror print_queue.slip_name)
        # — die Scanner-Karte zeigt nur „10a", nicht „Klasse 10a".
        assert scanner.last_scan_payload["form"] == "10a"
        assert student.print_mode is True
        job_states = state.print_queue.print_job_states()
        assert job_states.get(1) == "waiting"
        # `job_id` im Payload — der Drucker-Display-Client braucht sie, um das
        # Namens-Kästchen von der Scanner-Karte an die echte Position in der
        # Warteschlange/auf einem Drucker "reisen" zu lassen (s. web/
        # drucker-display.js::revertScannerAndFlip).
        job_id = scanner.last_scan_payload["job_id"]
        assert job_id
        assert state.print_queue.active_job_id_for_student(1) == job_id
    finally:
        ws.__exit__(None, None, None)


def test_already_waiting_scan_does_not_duplicate_job(client, scan_env):
    state, _student, code = scan_env
    job = PrintJob.create(role="student", student_id=1, pages="1", name="Muster, Max (Klasse 10a)")
    asyncio.run(state.print_queue.enqueue(job))
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": code})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "already")
        payload = state.printer_scanners[_TOKEN].last_scan_payload
        assert payload["job_status"] == "waiting"
        assert payload["job_id"] == job.id
        # Kein zweiter Job für denselben Schüler.
        assert len(state.print_queue.waiting) == 1
    finally:
        ws.__exit__(None, None, None)


def test_already_printed_scan(client, scan_env):
    state, student, code = scan_env
    student.slip_printed = True
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": code})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "already")
        payload = state.printer_scanners[_TOKEN].last_scan_payload
        assert payload["job_status"] == "printed"
        # Kein laufender Auftrag mehr (nur der Druck-Marker) — job_id ist None.
        assert payload["job_id"] is None
    finally:
        ws.__exit__(None, None, None)


def test_pending_books_scan(client, scan_env):
    state, _student, code = scan_env
    state.iserv.books_by_student[1] = [
        {"isbn": "978-1", "title": "Green Line 6", "status": "vorgemerkt"}
    ]
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": code})
        assert _wait_until(
            lambda: state.printer_scanners[_TOKEN].last_scan_status == "pending_books"
        )
        job_states = state.print_queue.print_job_states()
        assert 1 not in job_states
    finally:
        ws.__exit__(None, None, None)


def test_unreadable_code_is_unknown(client, scan_env):
    state, _student, _code = scan_env
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": "totally-unrelated-code"})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "unknown")
    finally:
        ws.__exit__(None, None, None)


def test_done_student_code_is_unknown(client, scan_env):
    state, student, code = scan_env
    student.status = "done"
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": code})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "unknown")
    finally:
        ws.__exit__(None, None, None)


def test_book_code_resolves_owning_station_student(client, scan_env):
    """Buchcode statt Schülercode: der Scanner löst über `loaned_to_id` den
    Schüler auf, sofern dieser einen aktiven Zettel-Code hat."""
    state, _student, _code = scan_env
    state.iserv.book_by_code["0015166"] = {
        "isbn": "978-1", "title": "Green Line 6", "loaned_to_id": 1,
    }
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": "0015166"})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "ready")
    finally:
        ws.__exit__(None, None, None)


def test_book_code_of_student_without_station_code_is_unknown(client, scan_env):
    state, _student, _code = scan_env
    ctx = state.active_context
    other = QueueStudent(student_id=2, lastname="Andere", firstname="Anna", form="Klasse 10a")
    ctx.queue.append(other)  # kein allocate_station_code für student 2
    state.iserv.book_by_code["9999999"] = {
        "isbn": "978-2", "title": "Sonstwas", "loaned_to_id": 2,
    }
    ws = _connect_authorized_scanner(client, state)
    try:
        ws.send_json({"type": "scan", "code": "9999999"})
        assert _wait_until(lambda: state.printer_scanners[_TOKEN].last_scan_status == "unknown")
    finally:
        ws.__exit__(None, None, None)


# ---- print_mode-Aufteilung „student" (Scan-Station) --------------------


def _connect_authorized_station(client, state):
    ws = client.websocket_connect(f"/ws/scan-station?token={_STATION_TOKEN}").__enter__()
    _recv_until(ws, "registration")
    state.scan_stations[_STATION_TOKEN].authorized = True
    return ws


def _prep_print_mode_student(state, student):
    """Bereits verbundene (per `_connect_authorized_station` freigeschaltete)
    Station in den Druckmodus versetzen — Voraussetzung `worker_ready`, das
    der WS-Handler prüft. MUSS erst NACH dem Connect laufen: der WS-Handler
    behandelt eine bereits existierende Station beim Verbinden als Reconnect
    und gibt eine vorab gesetzte Schüler-Bindung sofort wieder frei (s.
    `ws_scan_station`/`release_station_student`)."""
    station = state.scan_stations[_STATION_TOKEN]
    station.student_id = student.student_id
    station.student_lastname = student.lastname
    station.student_firstname = student.firstname
    station.student_form = student.form
    station.worker_ready = True
    return station


def test_student_trigger_with_eligible_scanner_defers_job(client, scan_env, monkeypatch):
    state, student, code = scan_env
    state.active_context.slip_trigger = "student"
    state.settings.printers = [PrinterConfig(id="p1", name="HP")]
    display = PrinterDisplaySession(
        display_id="disp1", registration_code="AAAA", authorized=True, assigned_printer_ids=None,
    )
    display.ws = object()  # nur `is not None` wird geprüft (Verbindungsstatus)
    state.printer_displays["disp1"] = display
    scanner_token = "bbb111bbb222"
    from server.state import PrinterScannerSession

    scanner = PrinterScannerSession(
        scanner_id=scanner_token, registration_code="BBBB", authorized=True, label="Drucker 1",
    )
    scanner.ws = object()
    state.printer_scanners[scanner_token] = scanner

    ws = _connect_authorized_station(client, state)
    try:
        _prep_print_mode_student(state, student)
        ws.send_json({"type": "print_mode"})
        msg = _recv_until(ws, "print_mode_result")
        assert msg["trigger"] == "student"
        assert msg["scanner_names"] == ["Drucker 1"]
        # Kein Auftrag jetzt — der entsteht erst beim Scan am Drucker-Scanner.
        assert state.print_queue.print_job_states() == {}
    finally:
        ws.__exit__(None, None, None)


def test_student_trigger_without_eligible_scanner_falls_back_to_auto(client, scan_env):
    state, student, _code = scan_env
    state.active_context.slip_trigger = "student"
    state.settings.printers = [PrinterConfig(id="p1", name="HP")]
    display = PrinterDisplaySession(
        display_id="disp1", registration_code="AAAA", authorized=True, assigned_printer_ids=None,
    )
    display.ws = object()
    state.printer_displays["disp1"] = display
    # Kein Drucker-Scanner registriert → Fallback aufs Auto-Verhalten.

    ws = _connect_authorized_station(client, state)
    try:
        _prep_print_mode_student(state, student)
        ws.send_json({"type": "print_mode"})
        msg = _recv_until(ws, "print_mode_result")
        assert msg["trigger"] == "student"
        assert "scanner_names" not in msg
        assert msg["printer_available"] is True
        assert state.print_queue.print_job_states().get(student.student_id) == "waiting"
    finally:
        ws.__exit__(None, None, None)
