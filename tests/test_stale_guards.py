"""Unit-Tests für die Stale-Guards in server/sessions.py.

Beide Lade-Pfade (Modus A: `load_and_push_helper_student`, Modus B:
`load_and_push_paired_student`) öffnen einen Worker-Context via
`state.worker_pool.open_student(...)` — ein `await`, während dessen sich der
Zustand ändern kann (Helfer weitergeschaltet / Session invalidiert). Ohne den
Stale-Guard direkt danach würde der frisch geöffnete Context unter einer
inzwischen toten Zuordnung registriert und nie wieder freigegeben (Pool-Leak).

Wir simulieren das Rennen mit einem Fake-`WorkerPool`, dessen `open_student`
erst zurückkehrt, nachdem der Test mitten im Await den Zustand mutiert hat
(über ein `asyncio.Event`, keine echten Sleeps).
"""

from __future__ import annotations

import asyncio

from server.sessions import load_and_push_helper_student, load_and_push_paired_student
from server.state import AppState, HelperSession, QueueStudent, StudentSessionB


class _FakeHub:
    def __init__(self) -> None:
        self.host_broadcasts = 0
        self.scanner_msgs: list[tuple[str, dict]] = []

    async def broadcast_host(self, snapshot) -> None:
        self.host_broadcasts += 1

    async def send_scanner(self, token: str, msg: dict) -> None:
        self.scanner_msgs.append((token, msg))


class _FakeIServ:
    async def get_student_info(self, student_id, schoolyear):
        return {"student_id": student_id, "books": []}


class _FakeWorkerSession:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeWorkerPool:
    """`open_student` blockiert auf einem Event, bis der Test es setzt —
    simuliert die Lücke zwischen Context-Öffnung und Registrierung, in der
    ein Skip/Invalidate den Zustand unter den Füßen wegziehen kann."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.opened: list[int] = []
        self.session = _FakeWorkerSession()

    async def open_student(self, student_id: int, name: str) -> _FakeWorkerSession:
        self.opened.append(student_id)
        await self.gate.wait()
        return self.session


def _state_with_iserv() -> AppState:
    st = AppState()
    st.iserv = _FakeIServ()
    return st


# ---------------------------------------------------------------------------
# Modus A — load_and_push_helper_student: Stale-Guard bei weitergeschaltetem
# Helfer während open_student.
# ---------------------------------------------------------------------------

def test_load_and_push_helper_student_stale_guard_closes_orphan_context(monkeypatch):
    st = _state_with_iserv()
    hub = _FakeHub()
    pool = _FakeWorkerPool()
    st.worker_pool = pool
    helper = HelperSession(token="h1", name="Helfer", student_id=42)
    st.helper_sessions["h1"] = helper
    student = QueueStudent(student_id=42, lastname="M", firstname="N", form="")

    async def run():
        task = asyncio.create_task(
            load_and_push_helper_student(st, hub, student, helper)
        )
        # Bis open_student() den Context anfordert laufen lassen (er hängt
        # dort auf dem Gate).
        for _ in range(20):
            await asyncio.sleep(0)
        assert pool.opened == [42], "open_student sollte bereits laufen"
        # Während des Awaits: der Helfer wird weitergeschaltet (z. B. /api/skip
        # oder Disconnect setzt helper.student_id zurück).
        helper.student_id = None
        pool.gate.set()
        await task

    asyncio.run(run())

    assert pool.session.closed is True, "Stale Context muss selbst geschlossen werden"
    assert 42 not in st.student_worker_sessions, "Stale Context darf NICHT registriert werden"


def test_load_and_push_helper_student_happy_path_registers_worker(monkeypatch):
    """Gegenprobe: bleibt helper.student_id unverändert, registriert der
    Pfad den Worker ganz normal (kein falsch-positiver Guard)."""
    st = _state_with_iserv()
    hub = _FakeHub()
    pool = _FakeWorkerPool()
    st.worker_pool = pool
    helper = HelperSession(token="h1", name="Helfer", student_id=42)
    st.helper_sessions["h1"] = helper
    student = QueueStudent(student_id=42, lastname="M", firstname="N", form="")

    async def run():
        task = asyncio.create_task(
            load_and_push_helper_student(st, hub, student, helper)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        pool.gate.set()
        await task

    asyncio.run(run())

    assert pool.session.closed is False
    assert st.student_worker_sessions.get(42) is pool.session
    assert any(m.get("type") == "worker_ready" for _, m in hub.scanner_msgs)


# ---------------------------------------------------------------------------
# Modus B — load_and_push_paired_student: Stale-Guard bei invalidierter
# Session während open_student.
# ---------------------------------------------------------------------------

class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def test_load_and_push_paired_student_stale_guard_closes_orphan_context():
    st = _state_with_iserv()
    hub = _FakeHub()
    pool = _FakeWorkerPool()
    st.worker_pool = pool
    session = StudentSessionB(session_token="tok", pairing_code="1234", student_id=42, state="paired")
    session.ws = _FakeWS()
    st.student_sessions["tok"] = session
    student = QueueStudent(student_id=42, lastname="M", firstname="N", form="")
    info = {"student_id": 42, "books": []}

    async def run():
        task = asyncio.create_task(
            load_and_push_paired_student(st, hub, session, student, info)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        assert pool.opened == [42]
        # Während des Awaits: die Session wird invalidiert (z. B. Timeout,
        # Ausgabe geschlossen, Host bricht ab).
        session.state = "revoked"
        session.student_id = None
        pool.gate.set()
        await task

    asyncio.run(run())

    assert pool.session.closed is True, "Stale Context muss selbst geschlossen werden"
    assert 42 not in st.student_worker_sessions, "Stale Context darf NICHT registriert werden"


def test_load_and_push_paired_student_happy_path_registers_worker():
    st = _state_with_iserv()
    hub = _FakeHub()
    pool = _FakeWorkerPool()
    st.worker_pool = pool
    session = StudentSessionB(session_token="tok", pairing_code="1234", student_id=42, state="paired")
    session.ws = _FakeWS()
    st.student_sessions["tok"] = session
    student = QueueStudent(student_id=42, lastname="M", firstname="N", form="")
    info = {"student_id": 42, "books": []}

    async def run():
        task = asyncio.create_task(
            load_and_push_paired_student(st, hub, session, student, info)
        )
        for _ in range(20):
            await asyncio.sleep(0)
        pool.gate.set()
        await task

    asyncio.run(run())

    assert pool.session.closed is False
    assert st.student_worker_sessions.get(42) is pool.session
    assert any(m.get("type") == "worker_ready" for m in session.ws.sent)
