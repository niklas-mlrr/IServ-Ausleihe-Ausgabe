"""End-to-End-Tests des Scan-Stations-WebSockets (`/ws/scan-station`).

Fährt den Message-Loop von `ws_scan_station` über einen echten
`TestClient.websocket_connect` — Registrierung, Freischaltung, Annahme bzw.
Ablehnung von Zettel-Codes, Buch-Scan und Freigabe.

PRODUKTIONSSCHUTZ: kein echter Playwright/Browser/Netzwerk. `state.worker_pool`
ist None (Degraded-Modus → kein `open_student`), `state.iserv` ist ein reiner
In-Memory-Fake, der Lifespan wird NICHT gestartet (s. conftest.py). Wie in
`test_ws_scanner.py` muss `get_state` in `server.routes.ws`, `server.hub` UND
`server.sessions` dieselbe frische AppState liefern.
"""

from __future__ import annotations

import time

import pytest

import server.hub as hub_module
import server.routes.ws as ws_module
import server.sessions as sessions_module
from server.ratelimit import SlidingWindowLimiter
from server.state import AppState, QueueStudent

_TOKEN = "abc123def456"


class _FakeIServ:
    """Read-only In-Memory-Fake — liefert eine Buchliste für die Station."""

    def __init__(self) -> None:
        self.books = [
            {"isbn": "978-1", "title": "Green Line 6", "subject": "Englisch",
             "status": "vorgemerkt"},
        ]

    async def get_student_info(self, student_id, schoolyear):
        return {
            "student_id": student_id,
            "books": [dict(b) for b in self.books],
            "current_books": [],
        }

    async def get_class_book_catalog(self, form, schoolyear):
        return (None, [])

    async def get_book_by_code(self, code):
        # "9999999" ist ein ausgemustertes Exemplar (Blockier-Test),
        # "0015166" ein im Lager verfügbares Exemplar der vorgemerkten Reihe
        # (Green Line 6, s. `self.books`), jeder andere Barcode unbekannt.
        if code == "9999999":
            return {
                "code": code, "isbn": "978-1", "title": "Green Line 6",
                "subject": "Englisch", "available": True, "distributed": False,
                "deleted": True, "student_id": None,
                "loaned_to": None, "loaned_to_id": None,
            }
        if code == "0015166":
            return {
                "code": code, "isbn": "978-1", "title": "Green Line 6",
                "subject": "Englisch", "available": True, "distributed": False,
                "deleted": False, "student_id": None,
                "loaned_to": None, "loaned_to_id": None,
            }
        return None


class _FakeWorkerSession:
    """Minimaler Playwright-Worker-Ersatz — nur `submit_barcode` (Stagen ohne
    Enter), s. `automation/worker.py::StudentSession.submit_barcode`. Der
    Worker-Pool ist in diesen Tests `None` (Degraded-Modus, s. `ss_env`), also
    öffnet `load_station_student` nie selbst einen — für Tests, die einen
    ERFOLGREICHEN Scan brauchen, wird diese Fake-Session direkt eingetragen.
    """

    async def submit_barcode(self, barcode):
        return {"status": "staged", "isbn": "978-1", "title": "Green Line 6"}


@pytest.fixture
def ss_env(monkeypatch):
    """Frische AppState in ws.py, hub.py und sessions.py; ein wartender Schüler
    mit vergebenem Zettel-Code. Gibt (state, student, code)."""
    state = AppState()
    state.iserv = _FakeIServ()
    state.worker_pool = None
    monkeypatch.setattr(ws_module, "get_state", lambda: state)
    monkeypatch.setattr(hub_module, "get_state", lambda: state)
    monkeypatch.setattr(sessions_module, "get_state", lambda: state)
    # Frischer Rate-Limiter je Test: die Modul-Instanz ist global und würde
    # sonst das Budget über Testgrenzen hinweg verbrauchen (nachfolgende Tests
    # bekämen „bitte warten" statt der erwarteten Antwort).
    monkeypatch.setattr(
        ws_module, "_station_code_limiter", SlidingWindowLimiter(max_hits=10, window_s=60.0)
    )
    ctx = state.open_context("Klasse 10a")
    student = QueueStudent(
        student_id=1, lastname="Muster", firstname="Max", form="Klasse 10a"
    )
    ctx.queue.append(student)
    code = state.allocate_station_code(1)
    return state, student, code


def _wait_until(predicate, timeout_s: float = 2.0) -> bool:
    """Auf serverseitiges Aufräumen warten.

    Der `finally`-Block des WS-Handlers läuft im Portal-Thread des TestClients
    und ist mit dem Verlassen des `websocket_connect`-Kontexts nicht
    synchronisiert — ohne dieses Warten wäre die Prüfung ein Zeitrennen (in
    Isolation grün, im Gesamtlauf gelegentlich rot)."""
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


def _connect_authorized(client, state):
    """Station verbinden und (serverseitig) freischalten, dann `ready` abholen."""
    ws = client.websocket_connect(f"/ws/scan-station?token={_TOKEN}").__enter__()
    _recv_until(ws, "registration")
    state.scan_stations[_TOKEN].authorized = True
    state.scan_stations[_TOKEN].label = "Eingang"
    return ws


def _recv_all_until(ws, mtype: str, cap: int = 20) -> list[dict]:
    """Wie `_recv_until`, sammelt aber ALLE Frames bis dahin (inklusive des
    Treffers) — für Tests, die belegen müssen, dass ein bestimmtes Frame
    NICHT dazwischen aufgetaucht ist (z. B. ein ignorierter Scan)."""
    frames = []
    for _ in range(cap):
        msg = ws.receive_json()
        frames.append(msg)
        if msg.get("type") == mtype:
            return frames
    raise AssertionError(f"Nachricht vom Typ {mtype!r} nicht innerhalb {cap} Frames erhalten")


def _add_second_student(state, *, student_id=2) -> tuple:
    """Zweiten wartenden Schüler mit eigenem Zettel-Code registrieren — für
    die Wechsel-Tests (Scan des Codes eines ANDEREN Schülers während einer
    laufenden Anmeldung)."""
    ctx = state.active_context or state.open_context("Klasse 10a")
    student = QueueStudent(
        student_id=student_id, lastname="Zweit", firstname="Erika", form="Klasse 10a"
    )
    ctx.queue.append(student)
    code = state.allocate_station_code(student_id)
    return student, code


# ---- Verbindung / Registrierung -------------------------------------------


def test_missing_token_is_rejected(client, ss_env):
    with pytest.raises(Exception), client.websocket_connect("/ws/scan-station") as ws:  # noqa: B017
        ws.receive_json()


def test_fresh_station_gets_registration_code_only(client, ss_env):
    state, _student, _code = ss_env
    with client.websocket_connect(f"/ws/scan-station?token={_TOKEN}") as ws:
        msg = _recv_until(ws, "registration")
        assert msg["code"] == state.scan_stations[_TOKEN].registration_code
        # Vor der Freischaltung fließen keine Schülerdaten.
        assert "student" not in msg


def test_banned_token_is_refused(client, ss_env):
    state, _student, _code = ss_env
    state.banned_scan_station_tokens.add(_TOKEN)
    with client.websocket_connect(f"/ws/scan-station?token={_TOKEN}") as ws:
        assert ws.receive_json()["type"] == "forbidden"
    assert _TOKEN not in state.scan_stations


def test_unauthorized_station_rejects_codes(client, ss_env):
    _state, _student, code = ss_env
    with client.websocket_connect(f"/ws/scan-station?token={_TOKEN}") as ws:
        _recv_until(ws, "registration")
        ws.send_json({"type": "student_code", "value": code})
        msg = _recv_until(ws, "code_error")
        assert "freigeschaltet" in msg["msg"].lower()


# ---- Zettel-Code -----------------------------------------------------------


def test_valid_code_loads_student(client, ss_env):
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        info = _recv_until(ws, "student_info")
        assert info["student"]["lastname"] == "Muster"
        # Bewusst NUR Name/Klasse — die Station ist ein geteiltes Gerät.
        assert set(info["student"]) == {"lastname", "firstname", "form"}
        ready = _recv_until(ws, "worker_ready")
        assert ready["books"][0]["title"] == "Green Line 6"
        # Klassenweite Reihenfolge kommt mit — die Station sortiert die
        # Bücher-Tabelle genauso wie der Schülerclient.
        assert "book_order" in ready
        assert state.scan_stations[_TOKEN].student_id == 1
        assert state.scan_stations[_TOKEN].worker_ready is True
    finally:
        ws.__exit__(None, None, None)


def test_code_with_star_delimiters_is_accepted(client, ss_env):
    """Manche Scanner liefern die Code-39-Start/Stopp-Zeichen mit."""
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": f"*{code}*"})
        _recv_until(ws, "student_info")
        assert state.scan_stations[_TOKEN].student_id == 1
    finally:
        ws.__exit__(None, None, None)


def test_unknown_code_is_rejected_without_binding(client, ss_env):
    """Ein Code, der weder ein vergebener Zettel-Code noch (laut
    `_FakeIServ.get_book_by_code`) ein Buch ist, gilt als "ungültig" (rot) —
    nicht mehr die alte "Code unbekannt"-Meldung."""
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        wrong = "0001" if code != "0001" else "0002"
        ws.send_json({"type": "student_code", "value": wrong})
        msg = _recv_until(ws, "code_error")
        assert "ungültig" in msg["msg"].lower()
        assert msg.get("kind") == "invalid"
        assert state.scan_stations[_TOKEN].student_id is None
    finally:
        ws.__exit__(None, None, None)


def test_book_code_before_login_asks_for_student_code(client, ss_env):
    """Wird vor der Anmeldung ein (irgendein) Buch-Barcode gescannt, soll die
    Meldung gezielt zum Schülercode zurückschicken (`kind == "book"`), nicht
    pauschal "ungültig"."""
    state, _student, _code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": "0015166"})
        msg = _recv_until(ws, "code_error")
        assert msg["kind"] == "book"
        assert "schülercode" in msg["msg"].lower()
        assert state.scan_stations[_TOKEN].student_id is None
    finally:
        ws.__exit__(None, None, None)


def test_code_of_finished_student_is_rejected(client, ss_env):
    state, student, code = ss_env
    student.status = "done"
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        msg = _recv_until(ws, "code_error")
        assert "abgeschlossen" in msg["msg"].lower()
        assert state.scan_stations[_TOKEN].student_id is None
    finally:
        ws.__exit__(None, None, None)


def test_code_attempts_are_rate_limited(client, ss_env):
    """Vier Stellen sind durchprobierbar — nach 10 Versuchen/Minute drosseln."""
    state, _student, _code = ss_env
    ws = _connect_authorized(client, state)
    try:
        seen_throttle = False
        for i in range(14):
            ws.send_json({"type": "student_code", "value": f"{9000 + i:04d}"})
            msg = _recv_until(ws, "code_error")
            if "warten" in msg["msg"].lower():
                seen_throttle = True
                break
        assert seen_throttle, "Rate-Limit hat nicht gegriffen"
    finally:
        ws.__exit__(None, None, None)


def test_paper_code_never_reaches_the_log(client, ss_env, caplog):
    """Der Zettel-Code ist ein Credential (PLAN §3.7) — auch ein abgelehnter
    Versuch darf nicht im Log landen."""
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        with caplog.at_level("DEBUG"):
            ws.send_json({"type": "student_code", "value": code})
            _recv_until(ws, "worker_ready")
        assert code not in caplog.text
    finally:
        ws.__exit__(None, None, None)


# ---- Scannen + Freigeben ---------------------------------------------------


def test_scan_before_student_is_refused(client, ss_env):
    state, _student, _code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "scan", "value": "0015166"})
        msg = _recv_until(ws, "scan_result")
        assert msg["status"] == "error"
    finally:
        ws.__exit__(None, None, None)


def test_release_returns_station_to_ready(client, ss_env):
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        ws.send_json({"type": "release"})
        _recv_until(ws, "released")
        _recv_until(ws, "ready")
        assert state.scan_stations[_TOKEN].student_id is None
        # Die Station bleibt freigeschaltet — nur die Bindung fällt weg.
        assert state.scan_stations[_TOKEN].authorized is True
    finally:
        ws.__exit__(None, None, None)


def test_second_student_can_use_station_after_release(client, ss_env):
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        ws.send_json({"type": "release"})
        _recv_until(ws, "ready")
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        assert state.scan_stations[_TOKEN].student_id == 1
    finally:
        ws.__exit__(None, None, None)


def test_disconnect_releases_bound_student(client, ss_env):
    """Ein weggehendes Gerät darf keinen Schüler (und keinen Worker) halten."""
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    ws.send_json({"type": "student_code", "value": code})
    _recv_until(ws, "worker_ready")
    ws.__exit__(None, None, None)
    station = state.scan_stations[_TOKEN]
    assert _wait_until(lambda: station.student_id is None and station.ws is None)


def test_unauthorized_station_disappears_on_disconnect(client, ss_env):
    """Nicht freigeschaltete Stationen hinterlassen keinen Reiter am Host."""
    state, _student, _code = ss_env
    with client.websocket_connect(f"/ws/scan-station?token={_TOKEN}") as ws:
        _recv_until(ws, "registration")
    assert _wait_until(lambda: _TOKEN not in state.scan_stations)


# ---- Blockierender Buch-Hinweis (Host-Freigabe wie am Handy) --------------


def test_blocking_scan_locks_further_scans_until_host_clears(client, ss_env):
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")

        # Ausgemustertes Buch scannen -> blockierendes Modal, kein eigener
        # Schließen-Weg.
        ws.send_json({"type": "scan", "value": "9999999"})
        alert = _recv_until(ws, "scan_result")
        assert alert["status"] == "book_deleted"
        assert state.scan_stations[_TOKEN].book_alert_open is True

        # Ein weiterer Scan während der Sperre wird STILL ignoriert — kein
        # zweites scan_result für diesen Barcode. `release` danach dient nur
        # als Synchronisationspunkt (garantierte Antwort), damit der Test
        # nicht auf ein nie kommendes Frame wartet.
        ws.send_json({"type": "scan", "value": "1234567"})
        ws.send_json({"type": "release"})
        frames = _recv_all_until(ws, "released")
        assert not any(
            f.get("type") == "scan_result" and f.get("barcode") == "1234567"
            for f in frames
        )
    finally:
        ws.__exit__(None, None, None)


def test_host_clear_unblocks_the_station(client, ss_env, monkeypatch):
    state, _student, code = ss_env
    # /api/clear-book-alert hängt an server.routes.queue + die Host-Auth-
    # Dependency in server.routes._deps — dieselbe frische State dorthin
    # durchreichen (Spiegel von ss_env für ws.py/hub.py) und eine gültige
    # Host-Session anlegen.
    import server.routes._deps as deps_module
    import server.routes.queue as queue_module

    monkeypatch.setattr(queue_module, "get_state", lambda: state)
    monkeypatch.setattr(deps_module, "get_state", lambda: state)
    state.add_host_session("sid")

    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        ws.send_json({"type": "scan", "value": "9999999"})
        _recv_until(ws, "scan_result")
        assert state.scan_stations[_TOKEN].book_alert_open is True

        r = client.post(
            "/api/clear-book-alert",
            json={"student_id": 1},
            cookies={"session_id": "sid"},
        )
        assert r.status_code == 200
        _recv_until(ws, "book_alert_clear")
        assert state.scan_stations[_TOKEN].book_alert_open is False

        # Scannen funktioniert jetzt wieder normal (Fake-Worker, da der
        # Worker-Pool in diesen Tests None ist — s. _FakeWorkerSession).
        state.student_worker_sessions[1] = _FakeWorkerSession()
        ws.send_json({"type": "scan", "value": "0015166"})
        result = _recv_until(ws, "scan_result")
        assert result["status"] in ("staged", "booked")
    finally:
        ws.__exit__(None, None, None)


# ---- Stationswechsel: Zettel-Code eines ANDEREN Schülers während der
#      Anmeldung ------------------------------------------------------------


def test_scanning_another_students_code_switches_station(client, ss_env):
    state, _student, code = ss_env
    _other, other_code = _add_second_student(state, student_id=2)
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        assert state.scan_stations[_TOKEN].student_id == 1

        # Der zweite Schüler scannt (versehentlich oder ungeduldig) seinen
        # eigenen Zettel-Code, während Schüler 1 noch an der Station ist.
        ws.send_json({"type": "scan", "value": other_code})
        info = _recv_until(ws, "student_info")
        assert info["student"]["lastname"] == "Zweit"
        _recv_until(ws, "worker_ready")
        assert state.scan_stations[_TOKEN].student_id == 2
    finally:
        ws.__exit__(None, None, None)


def test_switch_to_finished_student_is_rejected_without_dropping_current(client, ss_env):
    """Der Zielschüler ist nicht (mehr) wechselbar (z. B. bereits fertig) —
    der aktuell angemeldete Schüler bleibt unangetastet."""
    state, _student, code = ss_env
    other, other_code = _add_second_student(state, student_id=2)
    other.status = "done"
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")

        ws.send_json({"type": "scan", "value": other_code})
        err = _recv_until(ws, "code_error")
        assert "abgeschlossen" in err["msg"].lower()
        assert state.scan_stations[_TOKEN].student_id == 1
    finally:
        ws.__exit__(None, None, None)


def test_own_four_digit_isbn_like_code_is_not_treated_as_switch(client, ss_env):
    """Ein zufällig 4-stelliger, aber unbekannter Barcode (kein fremder
    Zettel-Code) läuft normal in die Buch-Prüfung statt einen Stationswechsel
    auszulösen."""
    state, _student, code = ss_env
    ws = _connect_authorized(client, state)
    try:
        ws.send_json({"type": "student_code", "value": code})
        _recv_until(ws, "worker_ready")
        ws.send_json({"type": "scan", "value": "0001"})
        result = _recv_until(ws, "scan_result")
        assert result["status"] == "unknown_book"
        assert state.scan_stations[_TOKEN].student_id == 1
    finally:
        ws.__exit__(None, None, None)
