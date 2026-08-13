"""Unit-Tests für den Modus-B-Session-Lebenszyklus (server/sessions + state).

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker.
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions
from server.state import AppState, DisplaySession, PrinterConfig, PrinterDisplaySession


class _FakeDisplayWS:
    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


class _FakeDisplayHub:
    async def send_websocket(self, websocket, msg) -> bool:
        await websocket.send_json(msg)
        return True


def test_send_display_update_sends_paused_frame(monkeypatch):
    st = AppState()
    st.modus_b_open = True
    st.modus_b_paused = True
    st.modus_b_join_qr = "data:image/png;base64,test"
    ws = _FakeDisplayWS()
    display = DisplaySession(
        display_id="display-1", registration_code="ABCD", authorized=True, ws=ws
    )
    monkeypatch.setattr(sessions, "get_hub", lambda: _FakeDisplayHub())

    asyncio.run(sessions.send_display_update(st, display))

    assert ws.sent == [{"type": "paused"}]


def test_create_and_lookup():
    st = AppState()
    s = sessions.create_student_session(st)
    assert s.state == "pending_pairing"
    assert len(s.pairing_code) == 4 and s.pairing_code.isdigit()
    assert st.find_session_by_code(s.pairing_code) is s
    assert st.code_in_use(s.pairing_code)
    assert st.student_sessions[s.session_token] is s


def test_session_tokens_and_codes_unique():
    st = AppState()
    tokens, codes = set(), set()
    for _ in range(50):
        s = sessions.create_student_session(st)
        assert s.session_token not in tokens
        assert s.pairing_code not in codes  # eindeutig unter aktiven pending-Sessions
        tokens.add(s.session_token)
        codes.add(s.pairing_code)


def test_invalidate_is_hard_and_idempotent():
    st = AppState()
    s = sessions.create_student_session(st)
    token = s.session_token
    asyncio.run(sessions.invalidate_session(st, s, "revoked", reason="test"))
    assert s.state == "revoked"
    assert token not in st.student_sessions  # Token entwertet (kein Datenzugang mehr)
    assert st.find_session_by_code(s.pairing_code) is None
    # Erneuter Aufruf ändert den terminalen Zustand nicht.
    asyncio.run(sessions.invalidate_session(st, s, "completed"))
    assert s.state == "revoked"


def test_find_session_by_student_only_active():
    st = AppState()
    s = sessions.create_student_session(st)
    s.student_id = 7
    s.state = "paired"
    assert st.find_session_by_student(7) is s
    s.state = "completed"
    assert st.find_session_by_student(7) is None


# ---------------------------------------------------------------------------
# release_worker — starke Referenz auf den in-flight Release-Task
# (Invariante bislang nur im Docstring von release_worker beschrieben, s.
# server/sessions.py:518ff und das Modul-globale `_release_tasks`-Set).
#
# asyncio hält Tasks nur schwach; ein reines `asyncio.create_task(coro)` ohne
# gehaltene Referenz KANN mitten in der Coroutine GC't werden (in der Praxis
# abhängig vom GC-Zeitpunkt). `release_worker` steckt den Task deshalb in
# `_release_tasks` und entfernt ihn erst im `done`-Callback wieder. Wir prüfen
# hier ausschließlich diese Buchhaltung (Membership während des Awaits, danach
# nicht mehr) — NICHT das GC-Verhalten selbst, das ist in einem Prozess mit
# CPython-Refcounting ohnehin kaum deterministisch reproduzierbar.
# ---------------------------------------------------------------------------


class _FakeReleasingWorker:
    """Fake-Worker mit `close()` — Fallback-Pfad, falls kein Pool vorhanden."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePoolWithGatedRelease:
    """Fake-Pool, dessen `release()` auf einem Event hängt, bis der Test es
    setzt — simuliert die In-Flight-Phase, während der `_release_tasks` den
    Task halten muss."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.released: list[object] = []

    async def release(self, worker) -> None:
        await self.gate.wait()
        self.released.append(worker)


def test_release_worker_holds_task_in_flight_and_discards_after(monkeypatch):
    """B1: solange release() hängt, ist der Task in `_release_tasks` — nach
    Abschluss ist er wieder draußen (done_callback via `.discard`).

    GRENZE DIESES TESTS: geprüft wird die Buchführung (add/discard), NICHT die
    GC-Sicherheit selbst. Dass ein Task ohne starke Referenz tatsächlich
    mid-coroutine eingesammelt wird, ist nicht deterministisch reproduzierbar.
    Der Test schlägt fehl, sobald `_release_tasks.add(t)` entfällt — er belegt
    also, dass die Referenz gehalten wird, nicht, was ohne sie passieren würde.
    """
    st = AppState()
    pool = _FakePoolWithGatedRelease()
    st.worker_pool = pool
    worker = _FakeReleasingWorker()

    async def run():
        before = set(sessions._release_tasks)
        sessions.release_worker(st, worker)
        new_tasks = sessions._release_tasks - before
        assert len(new_tasks) == 1, "release_worker sollte genau einen neuen Task anlegen"
        t = next(iter(new_tasks))
        assert t in sessions._release_tasks, "Task muss während des Awaits gehalten werden"
        pool.gate.set()
        await t
        assert t not in sessions._release_tasks, "Task muss nach Abschluss entfernt sein"
        assert pool.released == [worker]

    asyncio.run(run())


def test_release_worker_falls_back_to_close_without_pool(monkeypatch):
    """B2: ohne (releasefähigen) Pool wird `worker.close()` genutzt — auch
    dieser Pfad wird über `_release_tasks` verfolgt und wieder ausgetragen."""
    st = AppState()
    st.worker_pool = None
    worker = _FakeReleasingWorker()

    async def run():
        before = set(sessions._release_tasks)
        sessions.release_worker(st, worker)
        new_tasks = sessions._release_tasks - before
        assert len(new_tasks) == 1
        t = next(iter(new_tasks))
        await t
        assert t not in sessions._release_tasks
        assert worker.closed is True

    asyncio.run(run())


def test_sweep_helper_scan_secrets_removes_expired_keeps_fresh():
    """Ungenutzte Helfer-Scan-Secrets verfallen nach der TTL; frische bleiben."""
    from datetime import datetime, timedelta

    st = AppState()
    st.helper_scan_secrets["old"] = (1, datetime.now() - timedelta(seconds=601))
    st.helper_scan_secrets["fresh"] = (2, datetime.now() - timedelta(seconds=10))

    sessions.sweep_helper_scan_secrets(st, ttl_s=600)

    assert "old" not in st.helper_scan_secrets
    assert "fresh" in st.helper_scan_secrets


def test_helper_scanned_serialized_and_reset():
    """helper_scanned (Abwesend + Helfer-Scan) ist im Draht-Format sichtbar und
    wird beim Zurücksetzen auf „Wartend" wie die übrigen Leihschein-Marker gelöscht."""
    from server.state import QueueStudent

    s = QueueStudent(student_id=1, lastname="A", firstname="a", form="10a")
    s.helper_scanned = True

    d = s.as_dict()
    assert d["helper_scanned"] is True

    s.reset_progress()
    assert s.helper_scanned is False


# ---- displayed_printer_ids (Scan-Station-Druckermodus-Gate) --------------


def _display(display_id, *, authorized=True, connected=True, assigned=None):
    d = PrinterDisplaySession(
        display_id=display_id, registration_code="0000",
        authorized=authorized, assigned_printer_ids=assigned,
    )
    if connected:
        d.ws = object()
    return d


def test_displayed_printer_ids_none_without_connected_display():
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1")]
    assert sessions.displayed_printer_ids(st) == set()


def test_displayed_printer_ids_unauthorized_or_disconnected_ignored():
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1")]
    st.printer_displays["d1"] = _display("d1", authorized=False)
    st.printer_displays["d2"] = _display("d2", connected=False)
    assert sessions.displayed_printer_ids(st) == set()


def test_displayed_printer_ids_none_assignment_means_all_pool():
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1"), PrinterConfig(id="p2", name="P2")]
    st.printer_displays["d1"] = _display("d1", assigned=None)
    assert sessions.displayed_printer_ids(st) == {"p1", "p2"}


def test_displayed_printer_ids_union_of_explicit_assignments():
    st = AppState()
    st.settings.printers = [
        PrinterConfig(id="p1", name="P1"), PrinterConfig(id="p2", name="P2"),
        PrinterConfig(id="p3", name="P3"),
    ]
    st.printer_displays["d1"] = _display("d1", assigned=["p1"])
    st.printer_displays["d2"] = _display("d2", assigned=["p2"])
    assert sessions.displayed_printer_ids(st) == {"p1", "p2"}


# ---- relevant_display_count (Singular/Plural am Scan-Station-Druckertext) --


def test_relevant_display_count_counts_displays_not_printers():
    """Zwei Displays zeigen zusammen denselben einen erlaubten Drucker →
    Anzahl 2 (Displays), nicht 1 (Drucker)."""
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1")]
    st.printer_displays["d1"] = _display("d1", assigned=["p1"])
    st.printer_displays["d2"] = _display("d2", assigned=["p1"])
    assert sessions.relevant_display_count(st, {"p1"}) == 2


def test_relevant_display_count_ignores_irrelevant_and_disconnected():
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1"), PrinterConfig(id="p2", name="P2")]
    st.printer_displays["shows_other"] = _display("shows_other", assigned=["p2"])
    st.printer_displays["disconnected"] = _display("disconnected", assigned=["p1"], connected=False)
    st.printer_displays["matches"] = _display("matches", assigned=["p1"])
    assert sessions.relevant_display_count(st, {"p1"}) == 1


def test_relevant_display_count_none_allowed_counts_any_shown():
    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1")]
    st.printer_displays["d1"] = _display("d1", assigned=["p1"])
    assert sessions.relevant_display_count(st, None) == 1
