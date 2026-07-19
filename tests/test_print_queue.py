"""Unit-Tests für die interne Druckerwarteschlange (server/print_queue.py).

Drucker-Pool-Verteilung: rollen-gerechte Einfügung in die zentrale
Warteschlange (HOST > HELFER > SCHÜLER), 2-Slots-pro-Drucker-Pipeline
(printing + spooled) und Positions-Notifications — alles gegen frische
`AppState`-Instanzen (Default-Pool = ein Standarddrucker) mit gemocktem
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


def _job(role, student_id, *, helper_token=None, host_sid=None, name="x", allowed=None):
    return PrintJob.create(
        role=role,
        student_id=student_id,
        pages="1",
        name=name,
        helper_token=helper_token,
        host_sid=host_sid,
        allowed_printers=allowed,
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
    assert _roles(pq.waiting) == ["host", "helper", "helper", "helper", "student"]
    assert (a, b, h, s, c) == (0, 1, 0, 3, 3)


def test_enqueue_behind_last_same_rank():
    """Neuer Auftrag landet hinter dem letzten gleichrangigen, nicht ganz vorne."""
    pq = print_queue.PrintQueue()
    asyncio.run(pq.enqueue(_job("host", 1)))
    asyncio.run(pq.enqueue(_job("helper", 2)))
    asyncio.run(pq.enqueue(_job("student", 3)))
    # weiterer HOST hinter den bestehenden HOST (Index 0) → Index 1
    asyncio.run(pq.enqueue(_job("host", 4)))
    assert _roles(pq.waiting) == ["host", "host", "helper", "student"]
    # weiterer HELFER hinter dem bestehenden HELFER
    asyncio.run(pq.enqueue(_job("helper", 5)))
    assert _roles(pq.waiting) == ["host", "host", "helper", "helper", "student"]


def test_enqueue_host_front_when_no_host():
    """Ohne bestehenden HOST rückt ein neuer HOST an die Spitze (vor HELFER)."""
    pq = print_queue.PrintQueue()
    asyncio.run(pq.enqueue(_job("helper", 1)))
    asyncio.run(pq.enqueue(_job("student", 2)))
    pos = asyncio.run(pq.enqueue(_job("host", 3)))
    assert _roles(pq.waiting) == ["host", "helper", "student"]
    assert pos == 0


def test_enqueue_dispatched_jobs_pinned():
    """Bereits zugewiesene (druckende/gespoolte) Aufträge sitzen in den
    Drucker-Slots, nicht in der zentralen Warteschlange — ein späterer HOST
    rückt in `waiting` an die Spitze, berührt die Slots aber nicht (am OS
    verbindlich, dokumentierter Schlupf)."""
    pq = print_queue.PrintQueue()
    printing_job = _job("helper", 1)
    printing_job.status = "printing"
    spooled_job = _job("helper", 2)
    spooled_job.status = "spooled"
    pq.slots["p1"] = print_queue._Slots(printing=printing_job, spooled=spooled_job)
    # Ein HELFER wartet, dann kommt ein späterer HOST → vorne in `waiting`,
    # Slots unangetastet.
    asyncio.run(pq.enqueue(_job("helper", 3)))
    pos = asyncio.run(pq.enqueue(_job("host", 4)))
    assert pq.slots["p1"].printing is printing_job
    assert pq.slots["p1"].spooled is spooled_job
    assert _roles(pq.waiting) == ["host", "helper"]
    assert pos == 0


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


async def _fake_print(state, student_id, *, pages=None, printer_name=None):
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
    Auftrag B wird als `spooled` (Position 1) gemeldet, C bleibt `queued`
    (Position 0 in der zentralen Warteschlange), nach Fertigstellung von A
    rückt B auf `printing` (Pos 0)."""
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
        assert any(m["status"] == "queued" and m["position"] == 0 for m in c_progress), c_progress
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

    async def failing_print(state, student_id, *, pages=None, printer_name=None):
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


# ---- Pool-Verteilung (Round-Robin, Kapazität 2, leerer Pool) ------------


async def _claim(pq, printers):
    async with pq._lock:
        return pq._claim_fills(printers)


def test_pool_round_robin_fill():
    """4 Aufträge auf 2 Drucker: linkester-freie-Last-Verteilung füllt erst
    beide Drucker auf Last 1 (J1→p1, J2→p2), dann auf Last 2 (J3→p1, J4→p2)
    — klassische Round-Robin-Füllung wie vom Nutzer spezifiziert."""
    from server.state import PrinterConfig

    st = AppState()
    st.settings.printers = [
        PrinterConfig(id="p1", name="P1"),
        PrinterConfig(id="p2", name="P2"),
    ]
    pq = st.print_queue
    jobs = [_job("helper", i) for i in (1, 2, 3, 4)]
    for j in jobs:
        asyncio.run(pq.enqueue(j))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    # (printer_id, printer_name, job, slot_type)
    assert [(c[0], c[2].student_id, c[3]) for c in claims] == [
        ("p1", 1, "printing"),
        ("p2", 2, "printing"),
        ("p1", 3, "spooled"),
        ("p2", 4, "spooled"),
    ], claims
    assert pq.waiting == []  # alle verteilt
    # Slots entsprechen der Verteilung.
    assert (pq.slots["p1"].printing.student_id, pq.slots["p1"].spooled.student_id) == (1, 3)
    assert (pq.slots["p2"].printing.student_id, pq.slots["p2"].spooled.student_id) == (2, 4)


def test_empty_pool_keeps_jobs_waiting():
    """Leerer Drucker-Pool: Aufträge bleiben in der zentralen Warteschlange —
    der Scheduler hat nichts, worauf er verteilen könnte (Druck verweigern
    passiert vorab in den Endpoints, s. routes/slips.py + ws.py)."""
    st = AppState()
    st.settings.printers = []
    pq = st.print_queue
    asyncio.run(pq.enqueue(_job("helper", 1)))
    asyncio.run(pq.enqueue(_job("host", 2)))
    claims = asyncio.run(_claim(pq, []))
    assert claims == []
    assert _roles(pq.waiting) == ["host", "helper"]  # rang-gerecht, unverteilt


def test_pool_snapshot_shape():
    """`pool_printers`/`pool_summary` liefern die Form, die der Host-Snapshot
    erwartet (is_default, load, printing_name/spooled_name, waiting)."""
    st = AppState()
    # Default-Pool = ein Standarddrucker (name=None).
    printers = st.settings.printers
    rendered = st.print_queue.pool_printers(printers)
    assert len(rendered) == 1
    p = rendered[0]
    assert p["name"] is None and p["is_default"] is True
    assert p["load"] == 0 and p["printing_name"] is None and p["spooled_name"] is None
    assert p["duplex"] == "one_sided"
    assert st.print_queue.pool_summary() == {"waiting": 0}


# ---- Pool-Verteilung mit pro-Klasse Drucker-Allowlist -------------------


def _two_printers():
    from server.state import PrinterConfig

    return [
        PrinterConfig(id="p1", name="P1"),
        PrinterConfig(id="p2", name="P2"),
    ]


def test_pool_respects_allowed_printers():
    """Ein Auftrag, der nur Drucker p2 erlaubt, geht an p2 — p1 bleibt frei,
    obwohl er idle und linkester ist."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    j = _job("helper", 1, allowed={"p2"})
    asyncio.run(pq.enqueue(j))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert [(c[0], c[2].student_id, c[3]) for c in claims] == [("p2", 1, "printing")]
    assert (pq.slots.get("p1") is None or pq.slots["p1"].load == 0)  # p1 nicht belegt
    assert pq.waiting == []


def test_pool_head_job_leftmost_allowed():
    """Kopf-Auftrag erlaubt p1+p2, beide idle → linkester (p1) druckt."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    asyncio.run(pq.enqueue(_job("helper", 1, allowed={"p1", "p2"})))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert [(c[0], c[2].student_id) for c in claims] == [("p1", 1)]
    assert pq.slots["p1"].printing.student_id == 1
    assert pq.slots.get("p2") is None or pq.slots["p2"].load == 0


def test_pool_parallelism_idle_preferred():
    """p1 druckt bereits (Last 1), p2 idle. Ein beide-erlaubender Auftrag geht
    an den idle p2 — nicht an p1s 2. Slot (Parallelismus statt nacheinander)."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    # p1 bereits belegt (druckt einen anderen Auftrag).
    running = _job("helper", 9)
    running.status = "printing"
    pq.slots["p1"] = print_queue._Slots(printing=running)
    asyncio.run(pq.enqueue(_job("helper", 1, allowed={"p1", "p2"})))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert [(c[0], c[2].student_id, c[3]) for c in claims] == [("p2", 1, "printing")]
    # p1 bleibt bei Last 1 (kein 2. Auftrag), p2 bekommt den neuen als printing.
    assert pq.slots["p1"].load == 1 and pq.slots["p1"].spooled is None
    assert pq.slots["p2"].printing.student_id == 1


def test_pool_skips_head_when_not_allowed():
    """Kopf-Auftrag erlaubt nur p2. Der linkeste idle Drucker p1 überspringt ihn
    und zieht den nächsten, der ihn erlaubt; der Kopf geht an p2."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    asyncio.run(pq.enqueue(_job("helper", 1, allowed={"p2"})))   # Kopf, nur p2
    asyncio.run(pq.enqueue(_job("helper", 2, allowed={"p1", "p2"})))  # nächster, beide
    claims = asyncio.run(_claim(pq, st.settings.printers))
    by_pid = {c[0]: c[2].student_id for c in claims}
    assert by_pid == {"p1": 2, "p2": 1}  # p1 zieht J2, p2 zieht den Kopf J1
    assert pq.waiting == []


def test_allowed_empty_set_no_dispatch():
    """Explizit leere Allowlist → kein Drucker erlaubt; Auftrag bleibt in der
    zentralen Warteschlange (Scheduler-Grace zusätzlich zur Enqueue-Verweigerung)."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    asyncio.run(pq.enqueue(_job("helper", 1, allowed=set())))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert claims == []
    assert _roles(pq.waiting) == ["helper"]
