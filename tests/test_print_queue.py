"""Unit-Tests für die interne Druckerwarteschlange (server/print_queue.py).

Rollen-gerechte Einfügung (HOST > HELFER > SCHÜLER), 2-in-flight-Pipeline und
Positions-Notifications — alles gegen frische `AppState`-Instanzen mit gemocktem
`print_loan_slip_for` / `await_print_completion`; kein IServ, kein echter Drucker.
"""

from __future__ import annotations

import asyncio

import server.hub as hub
import server.print_queue as print_queue
import server.printing as printing
import server.sessions as sessions
import server.state as state_mod
from server.print_queue import PrintJob
from server.state import AppState, HelperSession

# ---- Helfer ------------------------------------------------------------


class _FakeWS:
    """Sammelt gesendete JSON-Nachrichten (Helfer- oder Host-WS)."""

    def __init__(self) -> None:
        self.sent = []

    async def send_json(self, msg) -> None:
        self.sent.append(msg)


def _job(role, student_id, *, helper_token=None, host_sid=None, name="x"):
    return PrintJob.create(
        role=role,
        student_id=student_id,
        pages="1",
        name=name,
        helper_token=helper_token,
        host_sid=host_sid,
    )


def _roles(jobs):
    return [j.role for j in jobs]


# ---- Einfüge-Logik (ohne Worker) ---------------------------------------


def test_enqueue_role_ordering_basic():
    """HOST vor HELFER vor SCHÜLER; gleiche Rollen in Ankunfts-Reihenfolge."""
    pq = print_queue.PrintQueue()
    a = asyncio.run(pq.enqueue(_job("helper", 1)))   # [A]
    b = asyncio.run(pq.enqueue(_job("helper", 2)))   # [A,B]
    h = asyncio.run(pq.enqueue(_job("host", 3)))     # HOST vor HELFER → [H,A,B]
    s = asyncio.run(pq.enqueue(_job("student", 4)))  # ans Ende → [H,A,B,S]
    c = asyncio.run(pq.enqueue(_job("helper", 5)))   # hinter letzte HELFER → [H,A,B,C,S]
    assert _roles(pq.jobs) == ["host", "helper", "helper", "helper", "student"]
    assert (a, b, h, s, c) == (0, 1, 0, 3, 3)


def test_enqueue_behind_last_same_rank():
    """Neuer Auftrag landet hinter dem letzten gleichrangigen, nicht ganz vorne."""
    pq = print_queue.PrintQueue()
    asyncio.run(pq.enqueue(_job("host", 1)))
    asyncio.run(pq.enqueue(_job("helper", 2)))
    asyncio.run(pq.enqueue(_job("student", 3)))
    # weiterer HOST hinter den bestehenden HOST (Index 0) → Index 1
    asyncio.run(pq.enqueue(_job("host", 4)))
    assert _roles(pq.jobs) == ["host", "host", "helper", "student"]
    # weiterer HELFER hinter dem bestehenden HELFER
    asyncio.run(pq.enqueue(_job("helper", 5)))
    assert _roles(pq.jobs) == ["host", "host", "helper", "helper", "student"]


def test_enqueue_host_front_when_no_host():
    """Ohne bestehenden HOST rückt ein neuer HOST an die Spitze (vor HELFER)."""
    pq = print_queue.PrintQueue()
    asyncio.run(pq.enqueue(_job("helper", 1)))
    asyncio.run(pq.enqueue(_job("student", 2)))
    pos = asyncio.run(pq.enqueue(_job("host", 3)))
    assert _roles(pq.jobs) == ["host", "helper", "student"]
    assert pos == 0


def test_enqueue_dispatched_jobs_pinned():
    """Bereits gespoolte/druckende Aufträge bleiben am Kopf — ein späterer
    HOST rückt nicht vor sie (am OS verbindlich, dokumentierter Schlupf)."""
    pq = print_queue.PrintQueue()
    a = _job("helper", 1)
    b = _job("helper", 2)
    pq.jobs.extend([a, b])
    a.status = "printing"
    b.status = "spooled"
    pos = asyncio.run(pq.enqueue(_job("host", 3)))
    assert pq.jobs[0] is a and pq.jobs[1] is b
    assert pq.jobs[2].role == "host"
    assert pos == 2


# ---- Pipeline + Notifications (mit Worker, gemockt) --------------------


def _patch(monkeypatch, st: AppState):
    """Worker auf frische AppState lenken + Druck/Completion mocken.

    `hub.py` importiert `get_state` modul-lokal (`from .state import get_state`),
    daher muss es zusätzlich auf hub-Ebene gepatcht werden, sonst liefert
    `hub.send_scanner` die echte Singleton-State ohne unsere Helfer-Sessions.
    """
    monkeypatch.setattr(state_mod, "get_state", lambda: st)
    monkeypatch.setattr(hub, "get_state", lambda: st)
    monkeypatch.setattr(sessions, "print_loan_slip_for", _fake_print)
    monkeypatch.setattr(printing, "await_print_completion", _fake_completion)


async def _fake_print(state, student_id, *, pages=None):
    return {"ok": True, "backend": "file", "detail": "gedruckt", "job_handle": None}


async def _fake_completion(handle, *, timeout_s=90.0):
    return True


def test_pipeline_completes_all_in_order(monkeypatch):
    """Drei HELFER-Aufträge werden FIFO abgearbeitet; alle `done`, ok=True."""
    st = AppState()
    _patch(monkeypatch, st)
    pq = st.print_queue

    async def run():
        pq.start()
        jobs = [_job("helper", i, helper_token="h1") for i in (1, 2, 3)]
        for j in jobs:
            await pq.enqueue(j)
        # alle fertig warten (mit Zeitlimit)
        for j in jobs:
            await asyncio.wait_for(j.done.wait(), timeout=5)
        assert [j.status for j in jobs] == ["done", "done", "done"]
        assert all((j.result or {}).get("ok") for j in jobs)
        await pq.stop()

    asyncio.run(run())


def test_pipeline_2_in_flight_positions(monkeypatch):
    """Mit blockierendem Druck-Completion: 2-in-flight sichtbar —
    Auftrag B wird als `spooled` (Position 1) gemeldet, C als `queued` (Pos 2),
    nach Fertigstellung von A rückt B auf `printing` (Pos 0)."""
    st = AppState()
    _patch(monkeypatch, st)
    pq = st.print_queue

    # Pro Auftrag eigener Helfer + eigener Fake-WS → getrennte Nachrichtenströme.
    helpers = {}
    for key in ("a", "b", "c"):
        h = HelperSession(token=f"tok-{key}", name="T")
        h.ws = _FakeWS()
        st.helper_sessions[f"tok-{key}"] = h
        helpers[key] = h

    gates = {"a": asyncio.Event()}
    completion_calls = []

    async def gated_completion(handle, *, timeout_s=90.0):
        completion_calls.append(handle)
        await gates["a"].wait()  # Auftrag A „druckt", bis der Test die Freigabe erteilt
        return True

    monkeypatch.setattr(printing, "await_print_completion", gated_completion)

    async def run():
        pq.start()
        ja = _job("helper", 1, helper_token="tok-a", name="A")
        jb = _job("helper", 2, helper_token="tok-b", name="B")
        jc = _job("helper", 3, helper_token="tok-c", name="C")
        await pq.enqueue(ja)
        await pq.enqueue(jb)
        await pq.enqueue(jc)
        # B und C sollten dispatchen, während A „druckt": B → spooled (Pos 1),
        # C bleibt queued (Pos 2). Kurz warten, dann Stand einfrieren.
        await asyncio.sleep(0.15)
        b_progress = [m for m in helpers["b"].ws.sent if m["type"] == "print_progress"]
        c_progress = [m for m in helpers["c"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "spooled" and m["position"] == 1 for m in b_progress), b_progress
        assert any(m["status"] == "queued" and m["position"] == 2 for m in c_progress), c_progress
        # A druckt (Pos 0, printing)
        a_progress = [m for m in helpers["a"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "printing" and m["position"] == 0 for m in a_progress), a_progress
        # Freigabe: A wird fertig → B rückt auf printing (Pos 0), C auf spooled (Pos 1)
        gates["a"].set()
        await asyncio.wait_for(ja.done.wait(), timeout=5)
        await asyncio.sleep(0.15)
        b_after = [m for m in helpers["b"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "printing" and m["position"] == 0 for m in b_after), b_after
        # schließlich alle fertig
        await asyncio.wait_for(jc.done.wait(), timeout=5)
        for key in ("a", "b", "c"):
            assert any(m["type"] == "print_result" and m["ok"] for m in helpers[key].ws.sent)
        await pq.stop()

    asyncio.run(run())


def test_failed_dispatch_keeps_result_false(monkeypatch):
    """Schlägt `print_loan_slip_for` fehl, wird der Auftrag `failed` mit ok=False."""
    st = AppState()
    _patch(monkeypatch, st)
    pq = st.print_queue

    async def failing_print(state, student_id, *, pages=None):
        return {"ok": False, "msg": "kein IServ-Client"}

    monkeypatch.setattr(sessions, "print_loan_slip_for", failing_print)

    async def run():
        pq.start()
        j = _job("helper", 1, helper_token="h1")
        await pq.enqueue(j)
        await asyncio.wait_for(j.done.wait(), timeout=5)
        assert j.status == "failed"
        assert (j.result or {}).get("ok") is False
        await pq.stop()

    asyncio.run(run())
