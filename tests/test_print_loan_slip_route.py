"""Unit-Tests für `/api/print-loan-slip` (server/routes/slips.py) — insbesondere
die serverseitige Betreuerauslöser-Erkennung: druckt der Host aus „Aktuell in
Ausgabe" für einen live Schülerclient (Modus B, kein Helfer, Klasse mit
`slip_trigger == "helper"`) stellvertretend, muss der Auftrag IMMER wie ein
Schüler-Auftrag behandelt werden (role="student", kein `host_sid`, dafür
`student_token"`) — unabhängig von einem Client-Flag, das es nicht mehr gibt.
Für alle anderen Fälle (Helfer-Zuordnung vorhanden, andere Trigger-Modi) bleibt
es ein klassischer Host-Auftrag.

Läuft über einen echten HTTP-Client gegen `create_app()` (Fixtures `client`/
`ctx` aus `conftest.py`/`test_api_guards.py`-Konvention), ohne den Print-Queue-
Worker zu starten — der enqueuete Job bleibt daher unverändert in
`state.print_queue.waiting` und kann direkt inspiziert werden.
"""

from __future__ import annotations

import pytest

import server.routes.auth as auth_routes
import server.routes.booklists as booklists_routes
import server.routes.classes as classes_routes
import server.routes.helpers as helpers_routes
import server.routes.modus_b as modus_b_routes
import server.routes.queue as queue_routes
import server.routes.settings as settings_routes
import server.routes.slips as slips_routes
from server.config import Config
from server.routes import _deps as deps_routes
from server.state import AppState, PrinterConfig, QueueStudent, StudentSessionB

_ROUTE_MODULES = [
    deps_routes,
    auth_routes,
    classes_routes,
    booklists_routes,
    helpers_routes,
    queue_routes,
    slips_routes,
    modus_b_routes,
    settings_routes,
]


class _FakeHub:
    async def broadcast_host(self, snapshot) -> None:
        pass

    async def broadcast_settings(self, *a, **kw) -> None:
        pass

    async def send_scanner(self, token, msg) -> None:
        pass

    async def send_websocket(self, websocket, msg) -> bool:
        await websocket.send_json(msg)
        return True


class _FakeWS:
    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


def _make_config(**over) -> Config:
    base = dict(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        allow_booking=False,
    )
    base.update(over)
    return Config(**base)


@pytest.fixture
def ctx(monkeypatch):
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub = _FakeHub()
    for mod in _ROUTE_MODULES:
        if hasattr(mod, "get_state"):
            monkeypatch.setattr(mod, "get_state", lambda: state)
        if hasattr(mod, "get_config"):
            monkeypatch.setattr(mod, "get_config", lambda: cfg)
        if hasattr(mod, "get_hub"):
            monkeypatch.setattr(mod, "get_hub", lambda: hub)
    state.settings.printers = [PrinterConfig(id="p1", name="P1")]
    return state, cfg, hub


def _live_student(state, *, slip_trigger="helper", assigned_helper=None):
    """Klasse mit gegebenem `slip_trigger` + ein aktiver Modus-B-Schüler im
    Druckmodus, mit gepaarter, verbundener Session — wie er nach dem Scannen
    aller vorgemerkten Bücher am Board „Aktuell in Ausgabe" erscheint."""
    ctx = state.open_context("10a")
    ctx.slip_trigger = slip_trigger
    student = QueueStudent(
        student_id=42,
        lastname="Test",
        firstname="Schueler",
        form="10a",
        status="active",
    )
    student.print_mode = True
    student.assigned_helper = assigned_helper
    ctx.queue.append(student)
    ws = _FakeWS()
    state.student_sessions["student-token"] = StudentSessionB(
        session_token="student-token",
        pairing_code="4242",
        student_id=42,
        state="paired",
        ws=ws,
    )
    return student, ws


def test_host_print_for_helper_trigger_student_is_student_job(client, ctx):
    """Betreuerauslöser: Host druckt aus „Aktuell in Ausgabe" für einen live
    Schülerclient ohne Helfer — der Auftrag muss serverseitig automatisch als
    Schüler-Auftrag laufen (kein `host_sid`, `student_token` gesetzt), damit
    Druckerdisplay/Warteschlange „Schüler" statt „Host" zeigen und der
    Schülerclient `print_progress`/`print_result` bekommt."""
    state, _, _ = ctx
    _live_student(state, slip_trigger="helper")
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    jobs = state.print_queue.waiting
    assert len(jobs) == 1
    job = jobs[0]
    assert job.role == "student"
    assert job.host_sid is None
    assert job.student_token == "student-token"


def test_host_print_for_assigned_helper_student_stays_host_job(client, ctx):
    """Ein Modus-A-Schüler mit zugeordnetem Helfer (kein live Schülerclient)
    bleibt ein klassischer Host-Auftrag, auch bei `slip_trigger == 'helper'`
    — es gibt hier keinen Schülerclient, an den geroutet werden könnte."""
    state, _, _ = ctx
    _live_student(state, slip_trigger="helper", assigned_helper="helper-token")
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    job = state.print_queue.waiting[0]
    assert job.role == "host"
    assert job.host_sid == "sid"
    assert job.student_token is None


def test_host_print_for_auto_trigger_student_stays_host_job(client, ctx):
    """Außerhalb des Betreuerauslösers (z. B. `slip_trigger == 'auto'`) bleibt
    ein Host-Druck ein Host-Auftrag, selbst für einen live Schülerclient."""
    state, _, _ = ctx
    _live_student(state, slip_trigger="auto")
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    job = state.print_queue.waiting[0]
    assert job.role == "host"
    assert job.host_sid == "sid"
    assert job.student_token is None


def test_host_print_for_helper_trigger_student_reprint_is_host_job(client, ctx):
    """Nachdruck: ist der Leihschein bereits gedruckt (`slip_printed`), wird
    jeder weitere Druck über den Host-Button als Host-Auftrag angelegt — auch
    bei `slip_trigger == 'helper'` und ohne Helfer (sonst Erstdruck =
    Schüler-Auftrag). So bleibt der Wiederholungsdruck (verlorener/
    zerstörter Leihschein) eine vom Host geprüfte Aktion, unabhängig davon,
    wer den ersten Druck ausgelöst hat."""
    state, _, _ = ctx
    student, _ = _live_student(state, slip_trigger="helper")
    student.slip_printed = True
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    job = state.print_queue.waiting[0]
    assert job.role == "host"
    assert job.host_sid == "sid"
    assert job.student_token is None


def test_host_print_for_auto_trigger_student_reprint_is_host_job(client, ctx):
    """Nachdruck nach Schüler-Selbstauslöser (`slip_trigger == 'auto'`): auch
    hier wird der zweite Druck als Host-Auftrag angelegt — der erste Druck
    lief damals automatisch als Schüler-Auftrag, der Wiederholungsdruck
    läuft aber vom Host aus."""
    state, _, _ = ctx
    student, _ = _live_student(state, slip_trigger="auto")
    student.slip_printed = True
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    job = state.print_queue.waiting[0]
    assert job.role == "host"
    assert job.host_sid == "sid"
    assert job.student_token is None


def test_host_print_for_helper_trigger_student_rejects_while_in_flight(client, ctx):
    """Ein laufender Erstdruck blockiert einen zweiten Druck (Doppel-Schutz
    via `in_flight_student_ids`) — der Button bleibt zwar sichtbar, der
    Server weist den zweiten Auftrag ab, solange der erste noch läuft."""
    from server.print_queue import PrintJob

    state, _, _ = ctx
    _live_student(state, slip_trigger="helper")
    # Simuliert einen bereits laufenden Druckauftrag für diesen Schüler.
    state.print_queue.waiting.append(
        PrintJob.create(role="student", student_id=42, pages="1", name="Test, Schueler (10a)")
    )
    r = client.post(
        "/api/print-loan-slip",
        json={"student_id": 42, "second_page": False},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
