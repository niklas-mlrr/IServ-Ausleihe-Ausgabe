"""Unit-Tests für die interne Druckerwarteschlange (server/print_queue.py).

Drucker-Pool-Verteilung: rollen-gerechte Einfügung in die zentrale
Warteschlange (HOST > HELFER > SCHÜLER), Kapazität 2 je Drucker (max 2 gesendete
Aufträge) und OS-getriebene Status-Übergänge spooled→printing→done über
einzelne Tracker-Tasks — alles gegen frische `AppState`-Instanzen (Default-Pool
= ein Standarddrucker) mit gemocktem `print_loan_slip_for` /
`printing.read_job_state`; kein IServ, kein echter Drucker.
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
    pq.slots["p1"] = print_queue._Slots(jobs=[printing_job, spooled_job])
    # Ein HELFER wartet, dann kommt ein späterer HOST → vorne in `waiting`,
    # Slots unangetastet.
    asyncio.run(pq.enqueue(_job("helper", 3)))
    pos = asyncio.run(pq.enqueue(_job("host", 4)))
    assert pq.slots["p1"].jobs == [printing_job, spooled_job]
    assert _roles(pq.waiting) == ["host", "helper"]
    assert pos == 0


# ---- Pipeline + Notifications (mit Worker, gemockt) --------------------


def _patch(monkeypatch, st: AppState):
    """Worker auf frische AppState lenken + Druck/OS-Status mocken.

    `hub.py` importiert `get_state` modul-lokal (`from .state import get_state`),
    daher muss es zusätzlich auf hub-Ebene gepatcht werden, sonst liefert
    `hub.send_scanner` die echte Singleton-State ohne unsere Helfer-Sessions.
    Der Tracker pollt `printing.read_job_state` — Default: sofort „absent"
    (file-Backend-Verhalten, Aufträge schließen zügig ab). Tests, die
    Zwischenstände prüfen, überschreiben `read_job_state` selbst.
    """
    monkeypatch.setattr(state_mod, "get_state", lambda: st)
    monkeypatch.setattr(hub, "get_state", lambda: st)
    monkeypatch.setattr(sessions, "print_loan_slip_for", _fake_print)
    monkeypatch.setattr(printing, "read_job_state", _fake_read_state)
    # Schnelles Poll-Intervall im Tracker, damit die Tests nicht Sekunden warten.
    monkeypatch.setattr(print_queue, "_TRACK_POLL_S", 0.01)


async def _fake_print(state, student_id, *, pages=None, printer_name=None):
    return {"ok": True, "backend": "file", "detail": "gedruckt", "job_handle": None}


async def _fake_read_state(handle):
    # file-Backend (handle None) → sofort fertig (kein OS-Polling möglich).
    return "absent"


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
    """OS-getrieben (read_job_state-Mock): 2-in-flight sichtbar —
    A druckt (printing, Pos 0), B ist gesendet (spooled, Pos 1), C ist der erste
    zentrale Wartende (queued, Pos 2). Nach A-Fertigstellung rückt B auf
    printing (Pos 0), C wird gesendet (spooled, Pos 1)."""
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

    # OS-Job-Handle mit student_id, damit der Mock den Zustand pro Auftrag steuert.
    async def fake_print(state, student_id, *, pages=None, printer_name=None):
        return {
            "ok": True, "backend": "sumatra", "detail": "an Drucker gesendet",
            "job_handle": {"kind": "test", "sid": student_id},
        }

    # OS-Zustand pro student_id, vom Test gesteuert (Default „spooled").
    os_states: dict[int, str] = {}

    async def fake_read_state(handle):
        sid = handle.get("sid") if handle else None
        return os_states.get(sid, "spooled")

    monkeypatch.setattr(sessions, "print_loan_slip_for", fake_print)
    monkeypatch.setattr(printing, "read_job_state", fake_read_state)

    async def run():
        pq.start()
        ja = _job("helper", 1, helper_token="tok-a", name="A")
        jb = _job("helper", 2, helper_token="tok-b", name="B")
        jc = _job("helper", 3, helper_token="tok-c", name="C")
        await pq.enqueue(ja)
        await pq.enqueue(jb)
        await pq.enqueue(jc)
        # A druckt aktiv (OS „printing"); B bleibt gesendet („spooled"); C
        # wartet zentral (Kapazität 1 Drucker = 2 gesendete). Kurz warten, dann
        # Stand einfrieren.
        os_states[1] = "printing"
        await asyncio.sleep(0.1)
        a_progress = [m for m in helpers["a"].ws.sent if m["type"] == "print_progress"]
        b_progress = [m for m in helpers["b"].ws.sent if m["type"] == "print_progress"]
        c_progress = [m for m in helpers["c"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "printing" and m["position"] == 0 for m in a_progress), a_progress
        assert any(m["status"] == "spooled" and m["position"] == 1 for m in b_progress), b_progress
        assert any(m["status"] == "queued" and m["position"] == 2 for m in c_progress), c_progress
        # A wird fertig (OS „absent") → A done, B rückt auf printing (Pos 0),
        # C wird gesendet (spooled, Pos 1).
        os_states[1] = "absent"
        os_states[2] = "printing"
        await asyncio.wait_for(ja.done.wait(), timeout=5)
        await asyncio.sleep(0.1)
        b_after = [m for m in helpers["b"].ws.sent if m["type"] == "print_progress"]
        c_after = [m for m in helpers["c"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "printing" and m["position"] == 0 for m in b_after), b_after
        assert any(m["status"] == "spooled" and m["position"] == 1 for m in c_after), c_after
        # B und C fertigstellen.
        os_states[2] = "absent"
        os_states[3] = "printing"
        await asyncio.wait_for(jb.done.wait(), timeout=5)
        await asyncio.sleep(0.1)
        c_print = [m for m in helpers["c"].ws.sent if m["type"] == "print_progress"]
        assert any(m["status"] == "printing" and m["position"] == 0 for m in c_print), c_print
        os_states[3] = "absent"
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


def test_parallel_dispatch_two_printers(monkeypatch):
    """Zwei Drucker, zwei Aufträge: beide werden gleichzeitig dispatcht und
    gepollt (parallele Tracker-Tasks) — nicht nacheinander. Früher blockierte
    der serielle Worker im Completion-Poll, sodass der zweite Drucker erst nach
    dem ersten bedient wurde („nur ein Drucker"-Bug)."""
    from server.state import PrinterConfig

    st = AppState()
    st.settings.printers = [
        PrinterConfig(id="p1", name="P1"),
        PrinterConfig(id="p2", name="P2"),
    ]
    _patch(monkeypatch, st)
    pq = st.print_queue

    # OS-Job bleibt „spooled" (wartet), bis der Test ihn freigibt — so halten wir
    # beide Aufträge gleichzeitig in-flight und können die parallele Belegung
    # beider Drucker beobachten.
    os_states: dict[int, str] = {}

    async def fake_print(state, student_id, *, pages=None, printer_name=None):
        return {
            "ok": True, "backend": "sumatra", "detail": "an Drucker gesendet",
            "job_handle": {"kind": "test", "sid": student_id},
        }

    async def fake_read_state(handle):
        return os_states.get(handle.get("sid") if handle else None, "spooled")

    monkeypatch.setattr(sessions, "print_loan_slip_for", fake_print)
    monkeypatch.setattr(printing, "read_job_state", fake_read_state)

    async def run():
        pq.start()
        j1 = _job("helper", 1)
        j2 = _job("helper", 2)
        await pq.enqueue(j1)
        await pq.enqueue(j2)
        # Beide Drucker sind jetzt je Last 1 (parallel), keiner wartet auf den
        # anderen — beide Tracker dispatchen und pollen gleichzeitig.
        await asyncio.sleep(0.1)
        assert pq.slots["p1"].load == 1 and pq.slots["p2"].load == 1
        assert [j.status for j in pq.slots["p1"].jobs + pq.slots["p2"].jobs] == [
            "spooled", "spooled",
        ]
        # Freigabe → beide fertig.
        os_states[1] = "absent"
        os_states[2] = "absent"
        await asyncio.wait_for(j1.done.wait(), timeout=5)
        await asyncio.wait_for(j2.done.wait(), timeout=5)
        assert j1.status == "done" and j2.status == "done"
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
    # (printer_id, printer_name, job) — level-weise Füllung: erst beide Drucker
    # auf Last 1 (J1→p1, J2→p2), dann auf Last 2 (J3→p1, J4→p2).
    assert [(c[0], c[2].student_id) for c in claims] == [
        ("p1", 1),
        ("p2", 2),
        ("p1", 3),
        ("p2", 4),
    ], claims
    assert pq.waiting == []  # alle verteilt
    # Slots entsprechen der Verteilung (FIFO: J1+J3 auf p1, J2+J4 auf p2).
    assert [j.student_id for j in pq.slots["p1"].jobs] == [1, 3]
    assert [j.student_id for j in pq.slots["p2"].jobs] == [2, 4]


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


def test_waiting_list_originator_and_form(monkeypatch):
    """`waiting_list` liefert pro wartendem Auftrag Position, Schüler, Klasse,
    Auftraggeber (Host / Helfer namentlich / Schüler) und die erlaubten Drucker
    (Allowlist zum Enqueue-Zeitpunkt). Schüler-Lookup live aus dem State; ohne
    aktive Kontext-Queue Fallback auf den job.name."""
    from server.state import QueueStudent

    st = AppState()
    monkeypatch.setattr(state_mod, "get_state", lambda: st)
    monkeypatch.setattr(hub, "get_state", lambda: st)
    # Helfer als Auftraggeber namentlich auflösbar; zweiter Token bleibt unbekannt.
    st.helper_sessions["tok-h1"] = HelperSession(token="tok-h1", name="Lukas")
    # Schüler 1 steht in einer aktiven Kontext-Queue → Lookup mit echter Form.
    ctx = st.open_context("Klasse 5a")
    ctx.queue.append(
        QueueStudent(student_id=1, lastname="Müller", firstname="Max", form="Klasse 5a")
    )
    # Default-Pool = ein Standarddrucker (name=None); dessen ID für die Allowlist.
    default_pid = st.settings.printers[0].id
    pq = st.print_queue
    # Worker nicht starten → Aufträge bleiben in `waiting` (kein Dispatch).
    asyncio.run(pq.enqueue(_job("host", 1, host_sid="s1", name="Müller, Max (Klasse 5a)")))
    asyncio.run(pq.enqueue(_job("helper", 2, helper_token="tok-h1", name="Schmidt, Anna (6b)")))
    asyncio.run(pq.enqueue(_job("helper", 3, helper_token="tok-x", name="Karl (7c)")))
    # Allowlist eingeschränkt auf den Default-Drucker bzw. auf einen verwaisten ID
    # (Drucker nach Enqueue entfernt → kein erlaubter Drucker mehr im Pool).
    asyncio.run(pq.enqueue(_job(
        "helper", 5, helper_token="tok-h1", name="Roth, Leo (9c)", allowed={default_pid},
    )))
    asyncio.run(pq.enqueue(_job("student", 4, name="Wolf, Tom (8d)")))
    asyncio.run(pq.enqueue(_job("student", 6, name="Bach, Sam (10d)", allowed={"orphan"})))

    wl = pq.waiting_list(st)
    # Rollen-gerechte Reihenfolge: HOST vor HELFER (3×) vor SCHÜLER (2×).
    assert [w["position"] for w in wl] == [0, 1, 2, 3, 4, 5]
    # Schüler 1 aus Kontext-Queue: Name ohne Form, Form bereinigt („Klasse " gestrippt).
    assert wl[0]["student"] == "Müller, Max"
    assert wl[0]["form"] == "5a"
    assert wl[0]["originator"] == "Host"
    # Helfer namentlich; unbekannter Token → Fallback „Helfer".
    assert wl[1]["originator"] == "Lukas"
    assert wl[2]["originator"] == "Helfer"
    assert wl[3]["originator"] == "Lukas"  # zweiter Auftrag desselben Helfers
    assert wl[4]["originator"] == "Schüler"
    assert wl[5]["originator"] == "Schüler"
    # Schüler 2–6 nicht in aktiver Kontext-Queue → Fallback auf job.name, form None.
    assert wl[1]["student"] == "Schmidt, Anna (6b)" and wl[1]["form"] is None
    # Allowlist: None = alle Pool-Drucker („Standarddrucker"); eingeschränkt = nur
    # die benannten; verwaiste ID ohne Treffer im Pool → leere Liste.
    assert wl[0]["all_allowed"] is True and wl[0]["allowed_printers"] == ["Standarddrucker"]
    assert wl[3]["all_allowed"] is False and wl[3]["allowed_printers"] == ["Standarddrucker"]
    assert wl[5]["all_allowed"] is False and wl[5]["allowed_printers"] == []


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
    assert [(c[0], c[2].student_id) for c in claims] == [("p2", 1)]
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
    assert pq.slots["p1"].jobs[0].student_id == 1
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
    pq.slots["p1"] = print_queue._Slots(jobs=[running])
    asyncio.run(pq.enqueue(_job("helper", 1, allowed={"p1", "p2"})))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert [(c[0], c[2].student_id) for c in claims] == [("p2", 1)]
    # p1 bleibt bei Last 1 (kein 2. Auftrag), p2 bekommt den neuen.
    assert pq.slots["p1"].load == 1 and len(pq.slots["p1"].jobs) == 1
    assert pq.slots["p2"].jobs[0].student_id == 1


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


# ---- Positionen (min über erlaubte Drucker, Slot-Index für gesendete) ----


def test_positions_two_spooled_first_not_yet_printing():
    """Regression: zwei gesendete Jobs an einem Drucker, beide noch ``spooled``
    (OS hat den ersten noch nicht aktiv zu drucken begonnen). Der zweite
    gesendete Job muss Position 1 zeigen — nicht 2 —, denn genau ein Job liegt
    vor ihm. Früher zählte `_compute_positions` nur die nicht-druckenden Jobs
    hoch und schob den zweiten fälschlich auf Position 2."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    a = _job("helper", 1, name="A")
    a.status = "spooled"
    b = _job("helper", 2, name="B")
    b.status = "spooled"
    pq.slots["p1"] = print_queue._Slots(jobs=[a, b])  # beide an p1 gesendet
    # Zentraler Wartender, der NUR p1 erlaubt (p2 idle, aber nicht erlaubt) →
    # load(p1)=2, kein erlaubter idle-Drucker → Position 2.
    c = _job("helper", 3, name="C", allowed={"p1"})
    pq.waiting.append(c)  # direkt einreihen (ohne Notify-Seiteneffekte)
    positions = pq._compute_positions(list(st.settings.printers))
    assert positions[a.id] == 0  # ältester gesendeter → 0 (druckt / druckt nächstens)
    assert positions[b.id] == 1  # zweiter gesendeter → 1 (genau einer vor ihm)
    assert positions[c.id] == 2  # erster zentraler bei vollem Drucker → 2


def test_positions_one_printing_one_spooled():
    """Soll-Zustand: erster Job druckt aktiv (``printing``) → 0, zweiter
    gesendet (``spooled``) → 1, erster zentraler Wartender (nur p1 erlaubt,
    p1 voll) → 2."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    a = _job("helper", 1, name="A")
    a.status = "printing"
    b = _job("helper", 2, name="B")
    b.status = "spooled"
    pq.slots["p1"] = print_queue._Slots(jobs=[a, b])
    c = _job("helper", 3, name="C", allowed={"p1"})
    pq.waiting.append(c)
    positions = pq._compute_positions(list(st.settings.printers))
    assert positions[a.id] == 0
    assert positions[b.id] == 1
    assert positions[c.id] == 2


# ---- Inaktivitäts-Stall + Peer-Error + fehlerhafte Drucker ----------------


def test_stall_marks_printer_faulty_and_notifies_originator(monkeypatch):
    """Bleibt der OS-Status länger als `_INACTIVITY_TIMEOUT_S` auf „spooled",
    wird der Auftrag `stalled` (ok=False, stalled=True), der Drucker als
    fehlerhaft markiert und der Urheber erhält die lange Hinweismeldung."""
    st = AppState()
    _patch(monkeypatch, st)
    pq = st.print_queue
    monkeypatch.setattr(print_queue, "_INACTIVITY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(print_queue, "_TRACK_TIMEOUT_S", 1.0)

    async def fake_print(state, student_id, *, pages=None, printer_name=None):
        return {"ok": True, "backend": "sumatra", "detail": "gesendet",
                "job_handle": {"kind": "test", "sid": student_id}}

    async def fake_read_state(handle):
        return "spooled"  # nie Fortschritt → Inaktivität

    monkeypatch.setattr(sessions, "print_loan_slip_for", fake_print)
    monkeypatch.setattr(printing, "read_job_state", fake_read_state)

    h = HelperSession(token="tok-a", name="T")
    h.ws = _FakeWS()
    st.helper_sessions["tok-a"] = h

    async def run():
        pq.start()
        j = _job("helper", 1, helper_token="tok-a", name="A")
        await pq.enqueue(j)
        await asyncio.wait_for(j.done.wait(), timeout=5)
        # done ist vor der result-Notification gesetzt → kurz warten, bis der
        # Tracker die print_result-Nachricht gesendet hat.
        await asyncio.sleep(0.1)
        assert j.status == "stalled"
        assert (j.result or {}).get("ok") is False
        assert (j.result or {}).get("stalled") is True
        msg = (j.result or {}).get("msg", "")
        assert "ungewöhnlich lange" in msg
        # Drucker ist fehlerhaft markiert (Default-Drucker des Pools).
        assert st.settings.printers[0].id in pq.faulty_printers
        # Urheber hat print_result mit stalled=True bekommen.
        results = [m for m in h.ws.sent if m["type"] == "print_result"]
        assert results and results[-1]["ok"] is False and results[-1].get("stalled") is True
        await pq.stop()

    asyncio.run(run())


def test_stall_peer_at_same_printer_gets_peer_error(monkeypatch):
    """Ein zweiter Auftrag am selben Drucker (Slot 1) wird beim Stall als
    `peer_error` finalisiert (ok=False) mit „Fehler bei vorigem Auftrag",
    sein Tracker wird cancelt."""
    st = AppState()
    _patch(monkeypatch, st)
    pq = st.print_queue
    monkeypatch.setattr(print_queue, "_INACTIVITY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(print_queue, "_TRACK_TIMEOUT_S", 1.0)
    # Kapazität 1 Drucker, beide Aufträge landen darauf (Slot 0 + 1).

    async def fake_print(state, student_id, *, pages=None, printer_name=None):
        return {"ok": True, "backend": "sumatra", "detail": "gesendet",
                "job_handle": {"kind": "test", "sid": student_id}}

    async def fake_read_state(handle):
        return "spooled"

    monkeypatch.setattr(sessions, "print_loan_slip_for", fake_print)
    monkeypatch.setattr(printing, "read_job_state", fake_read_state)

    helpers = {}
    for key in ("a", "b"):
        hh = HelperSession(token=f"tok-{key}", name="T")
        hh.ws = _FakeWS()
        st.helper_sessions[f"tok-{key}"] = hh
        helpers[key] = hh

    async def run():
        pq.start()
        ja = _job("helper", 1, helper_token="tok-a", name="A")
        jb = _job("helper", 2, helper_token="tok-b", name="B")
        await pq.enqueue(ja)
        await pq.enqueue(jb)
        # Beide finalisieren (Stall bzw. Peer-Error).
        await asyncio.wait_for(ja.done.wait(), timeout=5)
        await asyncio.wait_for(jb.done.wait(), timeout=5)
        # done ist vor den result-Notifications gesetzt (Stall-Pfad: erst
        # canceln/joinen, dann benachrichtigen) → kurz warten, bis die
        # Tracker ihre print_result-Nachrichten gesendet haben.
        await asyncio.sleep(0.1)
        assert ja.status == "stalled"
        assert jb.status == "peer_error"
        assert (jb.result or {}).get("ok") is False
        assert (jb.result or {}).get("peer_error") is True
        assert "Fehler bei vorigem Auftrag" in (jb.result or {}).get("msg", "")
        # Peer-Tracker wurde cancelt → kein aktiver Task mehr für jb.
        assert jb.id not in pq._job_tasks
        # Beide Ergebnisse wurden an den jeweiligen Helfer geliefert.
        b_results = [m for m in helpers["b"].ws.sent if m["type"] == "print_result"]
        assert b_results and b_results[-1].get("peer_error") is True
        await pq.stop()

    asyncio.run(run())


def test_compute_positions_excludes_faulty_for_replacement_jobs():
    """Ein zentraler Wartender mit Ersatzdrucker bekommt seine Position ohne
    den fehlerhaften Drucker gezählt; ein No-Alternative-Job bekommt seine
    Position relativ zum fehlerhaften Drucker."""
    st = AppState()
    st.settings.printers = _two_printers()
    pq = st.print_queue
    p1, p2 = st.settings.printers
    # p1 fehlerhaft; ein gesendeter Job blockiert p1 (Load 1).
    pq.faulty_printers.add("p1")
    blocking = _job("helper", 9, name="X")
    blocking.status = "spooled"
    pq.slots["p1"] = print_queue._Slots(jobs=[blocking])
    # Job mit Ersatzdrucker p2 (p2 idle) → Position 0 (p1 zählt nicht).
    with_alt = _job("helper", 1, name="A", allowed={"p2"})
    pq.waiting.append(with_alt)
    # Job, der NUR p1 erlaubt (kein Ersatzdrucker) → Position = load(p1)=1.
    only_faulty = _job("helper", 2, name="B", allowed={"p1"})
    pq.waiting.append(only_faulty)
    positions = pq._compute_positions(list(st.settings.printers), faulty_ids={"p1"})
    assert positions[with_alt.id] == 0  # p2 idle, p1 zählt nicht
    assert positions[only_faulty.id] == 1  # hinter blockierendem Job auf p1


def test_notify_all_peer_error_for_no_alternative_central_job(monkeypatch):
    """Zentraler Wartender ohne Ersatzdrucker (Allowlist nur auf dem
    fehlerhaften Drucker) bekommt im `_notify_all`-Snapshot `peer_error=True`."""
    st = AppState()
    st.settings.printers = _two_printers()
    _patch(monkeypatch, st)
    pq = st.print_queue
    p1, p2 = st.settings.printers
    pq.faulty_printers.add("p1")
    # No-Alternative-Job (nur p1) und Ersatzdrucker-Job (nur p2).
    no_alt = _job("helper", 1, helper_token="tok-a", name="A", allowed={"p1"})
    with_alt = _job("helper", 2, helper_token="tok-b", name="B", allowed={"p2"})
    ha = HelperSession(token="tok-a", name="T")
    ha.ws = _FakeWS()
    hb = HelperSession(token="tok-b", name="T")
    hb.ws = _FakeWS()
    st.helper_sessions["tok-a"] = ha
    st.helper_sessions["tok-b"] = hb
    pq.waiting.extend([no_alt, with_alt])

    async def run():
        await pq._notify_all()
        no_alt_prog = [m for m in ha.ws.sent if m["type"] == "print_progress"]
        with_alt_prog = [m for m in hb.ws.sent if m["type"] == "print_progress"]
        assert no_alt_prog and no_alt_prog[-1].get("peer_error") is True
        assert with_alt_prog and with_alt_prog[-1].get("peer_error") is False

    asyncio.run(run())


def test_reactivate_clears_faulty_and_redispatches(monkeypatch):
    """Nach `reactivate(pid)` ist die fehlerhaft-Marke weg und `_claim_fills`
    dispatcht wieder dorthin."""
    from server.state import PrinterConfig

    st = AppState()
    st.settings.printers = [PrinterConfig(id="p1", name="P1")]
    _patch(monkeypatch, st)
    pq = st.print_queue
    pq.faulty_printers.add("p1")
    # Wartender Auftrag → wird nicht dispatcht, solange p1 fehlerhaft.
    j = _job("helper", 1, name="A")
    asyncio.run(pq.enqueue(j))
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert claims == []
    assert "p1" in pq.faulty_printers
    # Reactivate → Marke weg, Auftrag wird dispatcht.
    assert pq.reactivate("p1") is True
    assert "p1" not in pq.faulty_printers
    claims = asyncio.run(_claim(pq, st.settings.printers))
    assert [(c[0], c[2].student_id) for c in claims] == [("p1", 1)]
