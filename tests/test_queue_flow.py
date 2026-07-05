"""Unit-Tests für die Queue-/Session-Übergänge (server/sessions + state).

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker-Browser.
Deckt die Lebenszyklus-Funktionen ab, die test_sessions.py noch offen lässt:
Pairing-Code-Eindeutigkeit, end_student, advance_helper, harten Worker-Release.
"""

from __future__ import annotations

import asyncio

import pytest

import server.sessions as sessions
from server.state import AppState, HelperSession, QueueStudent


# ---------------------------------------------------------------------------
# Test-Doubles (keine echten WS/Worker/Hub)
# ---------------------------------------------------------------------------

class _FakeHub:
    """Zählt Broadcasts/Scanner-Pushes, ohne echte WebSockets."""

    def __init__(self):
        self.host_broadcasts = 0
        self.scanner_msgs: list[tuple[str, dict]] = []

    async def broadcast_host(self, snapshot) -> None:
        self.host_broadcasts += 1

    async def send_scanner(self, token: str, msg: dict) -> None:
        self.scanner_msgs.append((token, msg))


class _FakeWorker:
    def __init__(self):
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeIServ:
    """Liefert deterministische Schülerinfo für load_and_push_helper_student."""

    async def get_student_info(self, student_id, schoolyear):
        return {"student_id": student_id, "books": []}


def _state_with_iserv() -> AppState:
    st = AppState()
    st.iserv = _FakeIServ()
    return st


def _add_student(st: AppState, sid: int, status: str = "pending") -> QueueStudent:
    s = QueueStudent(student_id=sid, lastname=f"N{sid}", firstname="V", form="10a", status=status)
    st.queue.append(s)
    return s


# ---------------------------------------------------------------------------
# gen_pairing_code — Eindeutigkeit & Erschöpfung
# ---------------------------------------------------------------------------

def test_pairing_code_skips_codes_in_use(monkeypatch):
    st = AppState()
    # 0123 ist belegt → die Schleife muss ihn überspringen und 0456 nehmen.
    st.student_sessions["tok"] = sessions.StudentSessionB(
        session_token="tok", pairing_code="0123", state="pending_pairing"
    )
    draws = iter([123, 456])  # erster Versuch kollidiert, zweiter ist frei
    monkeypatch.setattr(sessions.secrets, "randbelow", lambda _n: next(draws))
    assert sessions.gen_pairing_code(st) == "0456"


def test_pairing_code_exhausted_raises():
    st = AppState()
    for n in range(10000):
        code = f"{n:04d}"
        st.student_sessions[f"tok{n}"] = sessions.StudentSessionB(
            session_token=f"tok{n}", pairing_code=code, state="pending_pairing"
        )
    with pytest.raises(RuntimeError):
        sessions.gen_pairing_code(st)


# ---------------------------------------------------------------------------
# end_student — Status, Helfer-Lösung, Session-Invalidierung
# ---------------------------------------------------------------------------

def test_end_student_sets_status_and_releases_helper():
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_student(st, 7, status="active")
    helper = HelperSession(token="h1", name="Helfer", student_id=7)
    student.assigned_helper = "h1"
    st.helper_sessions["h1"] = helper

    asyncio.run(end_student_call(st, hub, 7, "done", "completed"))

    assert student.status == "done"
    assert student.assigned_helper is None
    assert helper.student_id is None       # Helfer wieder frei
    assert hub.host_broadcasts == 1
    # Scanner muss aktiv über die Trennung informiert werden ("Alle
    # Verbindungen trennen" wirkte sonst nur am Host, der Helfer sah nichts).
    assert hub.scanner_msgs == [("h1", {
        "type": "waiting",
        "msg": "Warte auf Schüler-Zuweisung",
        "queue_size": st.pending_count(),
    })]


def test_end_student_invalidates_modus_b_session_and_releases_worker():
    st = _state_with_iserv()
    hub = _FakeHub()
    _add_student(st, 9, status="active")
    sess = sessions.create_student_session(st)
    sess.student_id = 9
    sess.state = "paired"
    worker = _FakeWorker()
    st.student_worker_sessions[9] = worker

    asyncio.run(end_student_call(st, hub, 9, "done", "completed"))

    assert sess.state == "completed"
    assert sess.session_token not in st.student_sessions   # Token hart entwertet
    assert 9 not in st.student_worker_sessions             # Worker freigegeben
    assert worker.closed is True


def test_end_student_releases_worker_without_session():
    """Modus A: kein Modus-B-Session-Objekt, aber Worker muss trotzdem zu."""
    st = _state_with_iserv()
    hub = _FakeHub()
    _add_student(st, 5, status="active")
    worker = _FakeWorker()
    st.student_worker_sessions[5] = worker

    asyncio.run(end_student_call(st, hub, 5, "skipped", "revoked"))

    assert 5 not in st.student_worker_sessions
    assert worker.closed is True


# ---------------------------------------------------------------------------
# advance_helper — Helfer auf nächsten Wartenden setzen
# ---------------------------------------------------------------------------

def test_advance_helper_empty_queue():
    st = _state_with_iserv()
    hub = _FakeHub()
    helper = HelperSession(token="h1", name="Helfer")
    st.helper_sessions["h1"] = helper

    res = asyncio.run(sessions.advance_helper(st, hub, helper))

    assert res == {"ok": False, "reason": "empty"}
    assert any(m["type"] == "waiting" for _, m in hub.scanner_msgs)


def test_advance_helper_picks_next_and_completes_previous():
    st = _state_with_iserv()
    hub = _FakeHub()
    prev = _add_student(st, 1, status="active")
    nxt = _add_student(st, 2, status="pending")
    helper = HelperSession(token="h1", name="Helfer", student_id=1)
    prev.assigned_helper = "h1"
    st.helper_sessions["h1"] = helper

    res = asyncio.run(_advance_and_drain(st, hub, helper))

    assert res == {"ok": True, "student_id": 2}
    assert prev.status == "done"            # vorheriger abgeschlossen
    assert nxt.status == "active"           # nächster aktiv
    assert nxt.assigned_helper == "h1"
    assert helper.student_id == 2


# ---------------------------------------------------------------------------
# invalidate_session — idempotent & hart
# ---------------------------------------------------------------------------

def test_invalidate_releases_worker_and_clears_token():
    st = AppState()
    sess = sessions.create_student_session(st)
    sess.student_id = 3
    sess.state = "paired"
    worker = _FakeWorker()
    st.student_worker_sessions[3] = worker

    asyncio.run(_invalidate_and_drain(st, sess))

    assert sess.state == "revoked"
    assert sess.session_token not in st.student_sessions
    assert 3 not in st.student_worker_sessions
    assert worker.closed is True


# ---------------------------------------------------------------------------
# Helfer für asyncio.create_task-lastige Pfade: laufende Loop + Drain
# ---------------------------------------------------------------------------

async def end_student_call(st, hub, sid, queue_status, session_state):
    await sessions.end_student(
        st, hub, sid, queue_status=queue_status, session_state=session_state
    )
    # release_worker plant worker.close() als Task ein → einmal ticken lassen.
    await asyncio.sleep(0)


async def _advance_and_drain(st, hub, helper):
    res = await sessions.advance_helper(st, hub, helper)
    await asyncio.sleep(0)  # load_and_push_helper_student-Task abarbeiten
    return res


async def _invalidate_and_drain(st, sess):
    await sessions.invalidate_session(st, sess, "revoked", reason="test")
    await asyncio.sleep(0)
