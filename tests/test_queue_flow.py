"""Unit-Tests für die Queue-/Session-Übergänge (server/sessions + state).

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker-Browser.
Deckt die Lebenszyklus-Funktionen ab, die test_sessions.py noch offen lässt:
Pairing-Code-Eindeutigkeit, end_student, advance_helper, harten Worker-Release.
"""

from __future__ import annotations

import asyncio

import pytest

import server.sessions as sessions
from server.state import AppState, HelperSession, QueueStudent, SpectatorWaiter

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
    # Der Test-Kontext wird beim ersten Aufruf angelegt und aktiviert; spätere
    # Aufrufe im selben Test hängen an dieselbe Queue an. Tests, die einen
    # ZWEITEN Kontext brauchen, rufen `open_context` explizit selbst.
    ctx = st.active_context or st.open_context("10a")
    ctx.queue.append(s)
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
    assert helper.student_id is None  # Helfer wieder frei
    assert hub.host_broadcasts == 1
    # Scanner muss aktiv über die Trennung informiert werden ("Alle
    # Verbindungen trennen" wirkte sonst nur am Host, der Helfer sah nichts).
    assert hub.scanner_msgs == [
        (
            "h1",
            {
                "type": "waiting",
                "msg": "Warte auf Schüler-Zuweisung",
                "queue_size": st.pending_count(),
                "queue": st.pending_queue_as_list(),
                "queue_all": st.queue_as_list(),
            },
        )
    ]


def test_end_student_resets_peeking():
    """Menü-Peek: end_student muss `helper.peeking` zurücksetzen — sonst bekäme
    ein freier Helfer weiter Live-Queues (und der Client-Ansichtsstatus driftet)."""
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_student(st, 7, status="active")
    helper = HelperSession(token="h1", name="Helfer", student_id=7, peeking=True)
    student.assigned_helper = "h1"
    st.helper_sessions["h1"] = helper

    asyncio.run(end_student_call(st, hub, 7, "done", "completed"))

    assert helper.peeking is False


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
    assert sess.session_token not in st.student_sessions  # Token hart entwertet
    assert 9 not in st.student_worker_sessions  # Worker freigegeben
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
# end_student — Beförderung eines wartenden Spectators (Warteliste, s.
# sessions.spectate_student). Endet der aktive Helfer den Schüler, übernimmt
# der am längsten Wartende automatisch (Worker öffnet erst jetzt für ihn).
# ---------------------------------------------------------------------------


def test_end_student_promotes_next_spectator_for_real_queue_student():
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_student(st, 7, status="active")
    student.assigned_helper = "h1"
    h1 = HelperSession(token="h1", name="Helfer 1", student_id=7)
    h2 = HelperSession(token="h2", name="Helfer 2", ws=object(), spectating_student_id=7)
    st.helper_sessions["h1"] = h1
    st.helper_sessions["h2"] = h2
    st.student_spectators[7] = [SpectatorWaiter("h2", "N7", "V", "10a")]

    asyncio.run(end_student_call(st, hub, 7, "done", "completed"))

    # Schüler bleibt "active" — nicht wirklich beendet, sondern an h2 übergeben.
    assert student.status == "active"
    assert student.assigned_helper == "h2"
    assert h1.student_id is None  # alter Helfer sauber abgeräumt
    assert h2.student_id == 7  # neuer Besitzer — jetzt MIT Worker-Ladepfad
    assert h2.spectating_student_id is None  # Warteliste verlassen
    assert 7 not in st.student_spectators  # Liste war danach leer → gelöscht
    types = [m["type"] for _, m in hub.scanner_msgs if m.get("type")]
    assert "loading" in types  # assign_student_to_helper hat h2 neu geladen


def test_end_student_promotion_chain_third_waiter_still_waits():
    """Zwei Wartende (h2, h3): endet der aktive Helfer, übernimmt NUR h2 —
    h3 bleibt in der Warteliste, bis auch h2 fertig ist."""
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_student(st, 7, status="active")
    student.assigned_helper = "h1"
    h1 = HelperSession(token="h1", name="Helfer 1", student_id=7)
    h2 = HelperSession(token="h2", name="Helfer 2", ws=object(), spectating_student_id=7)
    h3 = HelperSession(token="h3", name="Helfer 3", ws=object(), spectating_student_id=7)
    st.helper_sessions["h1"] = h1
    st.helper_sessions["h2"] = h2
    st.helper_sessions["h3"] = h3
    st.student_spectators[7] = [
        SpectatorWaiter("h2", "N7", "V", "10a"),
        SpectatorWaiter("h3", "N7", "V", "10a"),
    ]

    asyncio.run(end_student_call(st, hub, 7, "done", "completed"))

    assert h2.student_id == 7
    assert h3.student_id is None  # noch nicht befördert
    assert h3.spectating_student_id == 7  # wartet weiterhin
    assert [w.token for w in st.student_spectators[7]] == ["h3"]

    # h2 (jetzt aktiver Besitzer) beendet seinerseits — h3 rückt nach.
    hub2 = _FakeHub()
    asyncio.run(end_student_call(st, hub2, 7, "done", "completed"))
    assert h2.student_id is None
    assert h3.student_id == 7
    assert h3.spectating_student_id is None
    assert 7 not in st.student_spectators


def test_end_student_promotes_next_spectator_for_transient_search_student():
    """Wie oben, aber der Schüler ist ein transienter Lupe-Treffer (steht in
    KEINER Queue) — die Beförderung muss ihn aus dem SpectatorWaiter (lastname/
    firstname/form) neu aufbauen, statt einen QueueStudent nachzuschlagen."""
    st = _state_with_iserv()
    hub = _FakeHub()
    h1 = HelperSession(token="h1", name="Helfer 1", student_id=77)
    h1.student_form = "10a"
    h2 = HelperSession(token="h2", name="Helfer 2", ws=object(), spectating_student_id=77)
    st.helper_sessions["h1"] = h1
    st.helper_sessions["h2"] = h2
    # Bewusst KEIN _add_student → 77 steht in keiner Queue (Schnellsprung).
    st.student_spectators[77] = [SpectatorWaiter("h2", "Test", "S", "10a")]

    asyncio.run(end_student_call(st, hub, 77, "done", "completed"))

    assert h1.student_id is None
    assert h2.student_id == 77
    assert h2.student_form == "10a"
    assert 77 not in st.student_spectators
    assert st.find_student(77) is None  # weiterhin transient, keine Queue


# ---------------------------------------------------------------------------
# end_student — In-flight Lade-Task abbrechen (Leak-Fix)
# ---------------------------------------------------------------------------


class _FakeTask:
    """Steht für einen laufenden load_and_push_helper_student-Task.

    Awaitbar ( wie ein echter asyncio.Task ), denn end_student/invalidate_session
    canceln nicht nur, sondern awaiten den Task jetzt auch (Stale-Guard +
    CancelledError-Observance). Ein cancel'ter Fake-Task raising CancelledError
    beim Await spiegelt das echte Verhalten — der production-Code fängt das via
    contextlib.suppress(asyncio.CancelledError) ab.
    """

    def __init__(self) -> None:
        self.cancelled = False
        self.awaited = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True

    def __await__(self):
        self.awaited = True
        if self.cancelled:
            raise asyncio.CancelledError()
        # Yield nothing — completes immediately without suspension.
        return
        yield  # pragma: no cover — unreachable, keeps it an async-gen-style awaitable


def test_end_student_cancels_inflight_load_task():
    """end_student muss den laufenden Lade-Task des Helfers canceln — sonst
    leakt dessen Worker-Context (open_student noch nicht registriert)."""
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_student(st, 7, status="active")
    helper = HelperSession(token="h1", name="Helfer", student_id=7)
    student.assigned_helper = "h1"
    st.helper_sessions["h1"] = helper

    task = _FakeTask()
    helper.load_task = task

    asyncio.run(end_student_call(st, hub, 7, "done", "completed"))

    assert task.cancelled is True


def test_invalidate_cancels_inflight_load_task():
    """invalidate_session (Modus B) muss den Lade-Task canceln — gleicher Leak."""
    st = AppState()
    sess = sessions.create_student_session(st)
    sess.student_id = 3
    sess.state = "paired"

    task = _FakeTask()
    sess.load_task = task

    asyncio.run(_invalidate_and_drain(st, sess))

    assert task.cancelled is True


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
    assert prev.status == "done"  # vorheriger abgeschlossen
    assert nxt.status == "active"  # nächster aktiv
    assert nxt.assigned_helper == "h1"
    # Modus A: Bücher sofort in `student_info`; `worker_ready` (ohne Pool sofort)
    # flippt den Helferclient von „Warten…" auf „Scanner bereit".
    msgs = [m for _, m in hub.scanner_msgs]
    si = next((m for m in msgs if m["type"] == "student_info"), None)
    assert si is not None and si["student"]["books"] == []  # Fake liefert books=[]
    assert any(m["type"] == "worker_ready" for m in msgs)
    assert helper.student_id == 2
    # Advance schickt `loading` (Queue verbergen), KEIN Idle-`waiting` — sonst
    # würde die Warteschlange während des Ladens des nächsten Schülers aufblitzen.
    assert any(m["type"] == "loading" for m in msgs)
    assert not any(m["type"] == "waiting" for m in msgs)


# ---------------------------------------------------------------------------
# assign_student_to_helper — gezielter Aufruf eines wartenden Schülers
# (Helfer wählt per Button aus der Warteschlange, statt „nächster").
# ---------------------------------------------------------------------------


async def _assign_and_drain(st, hub, helper, student):
    res = await sessions.assign_student_to_helper(st, hub, helper, student)
    await asyncio.sleep(0)  # load_and_push_helper_student-Task abarbeiten
    return res


def test_assign_student_to_helper_assigns_specific_student():
    st = _state_with_iserv()
    hub = _FakeHub()
    first = _add_student(st, 1, status="pending")  # würde von „nächster" gewählt
    target = _add_student(st, 2, status="pending")  # gezielt aufrufen
    helper = HelperSession(token="h1", name="Helfer")
    st.helper_sessions["h1"] = helper

    res = asyncio.run(_assign_and_drain(st, hub, helper, target))

    assert res == {"ok": True, "student_id": 2}
    assert target.status == "active"
    assert target.assigned_helper == "h1"
    assert helper.student_id == 2
    # Der erste (ältere) Wartende bleibt unangetastet — gezielte Zuweisung
    # nimmt NICHT automatisch den nächsten.
    assert first.status == "pending"
    assert first.assigned_helper is None
    msgs = [m for _, m in hub.scanner_msgs]
    assert any(m["type"] == "student_info" for m in msgs)
    assert any(m["type"] == "worker_ready" for m in msgs)
    # `loading`-Push: Client verbirgt die Queue, während der Schüler geladen wird
    # (deckt den Fall, dass der Helfer keinen alten Schüler hatte → kein
    # end_student-`loading`, dieser Send ist das einzige Signal).
    assert any(m["type"] == "loading" for m in msgs)


def test_rebind_helper_to_context_switches_assigned_class():
    """Aufrufen aus einer fremden Klasse (anderer Klassen-Tab im Helfer-Menü):
    der Helfer wird an die Klasse des aufgerufenen Schülers gebunden. Danach
    zieht „Nächster" (``next_pending(helper.context_id)``) aus der neuen Klasse.
    Auch ein bisher ungebundener `(aktive)`-Helfer lässt sich so binden."""
    st = AppState()
    ctx_a = st.open_context("10a")
    ctx_b = st.open_context("10b")
    ctx_a.queue.append(QueueStudent(student_id=1, lastname="A", firstname="a", form="10a"))
    ctx_b.queue.append(QueueStudent(student_id=9, lastname="B", firstname="b", form="10b"))
    helper = HelperSession(token="h1", name="Helfer", context_id=ctx_a.id)

    sessions.rebind_helper_to_context(helper, ctx_b.id)

    assert helper.context_id == ctx_b.id
    nxt = st.next_pending(helper.context_id)
    assert nxt is not None and nxt.student_id == 9  # aus 10b (neue Klasse)

    # Ungebundener Helfer (context_id None) wird ebenfalls bindbar.
    unbound = HelperSession(token="h2", name="X")
    sessions.rebind_helper_to_context(unbound, ctx_a.id)
    assert unbound.context_id == ctx_a.id


def test_assign_student_to_helper_resets_peeking():
    """Menü-Peek: eine neue Schülerzuweisung beendet die Queue-Ansicht —
    `helper.peeking` muss False sein, sonst driften Live-Queues in die
    Schüler-Ansicht."""
    st = _state_with_iserv()
    hub = _FakeHub()
    target = _add_student(st, 2, status="pending")
    helper = HelperSession(token="h1", name="Helfer", peeking=True)
    st.helper_sessions["h1"] = helper

    asyncio.run(_assign_and_drain(st, hub, helper, target))

    assert helper.peeking is False


def test_pending_queue_as_list_returns_only_pending():
    st = AppState()
    _add_student(st, 1, status="pending")
    _add_student(st, 2, status="active")
    _add_student(st, 3, status="done")
    _add_student(st, 4, status="pending")

    pending = st.pending_queue_as_list()
    assert [s["student_id"] for s in pending] == [1, 4]
    assert all(s["status"] == "pending" for s in pending)


# ---------------------------------------------------------------------------
# Lupe-Suche: transiente (nicht in der Queue stehende) Schüler
# ---------------------------------------------------------------------------


def test_end_student_transient_search_student_cleans_helper():
    """Lupe-Suche lädt beliebige IServ-Schüler, die NICHT in einer Queue stehen.
    end_student muss auch solche transienten Schüler beim Helfer aufräumen —
    sonst bliebe `helper.student_id` stale und ein laufender Worker leakte."""
    st = _state_with_iserv()
    hub = _FakeHub()
    helper = HelperSession(token="h1", name="Helfer", student_id=77, peeking=True)
    helper.student_form = "10a"  # war via Lupe zugewiesen → Form am Helfer
    st.helper_sessions["h1"] = helper
    # Bewusst KEIN _add_student → 77 steht in keiner Queue (Schnellsprung).
    worker = _FakeWorker()
    st.student_worker_sessions[77] = worker

    asyncio.run(end_student_call(st, hub, 77, "done", "completed"))

    assert helper.student_id is None  # Helfer freigegeben (sonst stale)
    assert helper.student_form is None  # Form aufräumen (sonst stale beim Reconnect)
    assert helper.peeking is False
    assert helper.expected_isbns == set()  # ISBN-Sets auch am transienten Pfad leer
    assert 77 not in st.student_worker_sessions  # Worker freigegeben
    assert worker.closed is True
    # Helfer wurde benachrichtigt (Default waiting), nicht im Dunkeln gelassen.
    assert hub.scanner_msgs and hub.scanner_msgs[0][0] == "h1"


def test_assign_transient_search_student_loads_without_queue():
    """search_call legt einen transienten QueueStudent an (nicht in einer Queue)
    und lädt ihn via assign_student_to_helper wie einen normalen Schüler:
    helper.student_id gesetzt, loading/student_info/worker_ready geschickt."""
    st = _state_with_iserv()
    hub = _FakeHub()
    helper = HelperSession(token="h1", name="Helfer", peeking=True)
    st.helper_sessions["h1"] = helper
    # Transienter Schüler — bewusst NICHT in eine Queue eingetragen.
    student = QueueStudent(
        student_id=88,
        lastname="Test",
        firstname="S",
        form="10a",
        status="pending",
        assigned_helper=None,
    )

    asyncio.run(_assign_and_drain(st, hub, helper, student))

    assert helper.student_id == 88
    assert helper.student_form == "10a"  # Form am Helfer — Quelle für Reconnect
    assert helper.peeking is False  # neuer Schüler beendet den Peek
    assert student.status == "active"
    assert student.assigned_helper == "h1"
    # Lade-Pipeline hat den Helfer versorgt (kein worker_pool → worker_ready direkt).
    types = [m["type"] for _, m in hub.scanner_msgs]
    assert "loading" in types and "student_info" in types and "worker_ready" in types
    # Der Student taucht in KEINER Kontext-Queue (Schnellsprung, nicht eingetragen).
    assert st.find_student(88) is None


def test_assign_student_to_helper_sets_student_form_for_reconnect():
    """`helper.student_form` wird beim Zuweisen aus `student.form` gesetzt — für
    Queue-Schüler (call/next) ebenso wie für transiente Lupe-Schüler. Der
    Reconnect-Pfad (ws_scanner) braucht die Form, um book_order + info["form"]
    zu liefern, falls `find_student` den Schüler nicht findet (Lupe: nicht in
    einer Queue). Voraussetzung für die Lupe-Wiederherstellung beim Seiten-Reload."""
    st = _state_with_iserv()
    hub = _FakeHub()
    # Queue-Schüler (form "10b") — der Normalfall.
    queue_student = _add_student(st, 5, status="pending")
    queue_student.form = "10b"
    helper = HelperSession(token="h1", name="Helfer")
    st.helper_sessions["h1"] = helper

    asyncio.run(_assign_and_drain(st, hub, helper, queue_student))

    assert helper.student_id == 5
    assert helper.student_form == "10b"

    # Auch nach advance (end+assign des nächsten) steht die Form des NEUEN
    # Schülers — keine Drift der vorherigen.
    nxt = _add_student(st, 6, status="pending")
    nxt.form = "10c"
    asyncio.run(_advance_and_drain(st, hub, helper))
    assert helper.student_id == 6
    assert helper.student_form == "10c"


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
    await sessions.end_student(st, hub, sid, queue_status=queue_status, session_state=session_state)
    # release_worker plant worker.close() als Task ein → einmal ticken lassen.
    await asyncio.sleep(0)


async def _advance_and_drain(st, hub, helper):
    res = await sessions.advance_helper(st, hub, helper)
    await asyncio.sleep(0)  # load_and_push_helper_student-Task abarbeiten
    return res


async def _invalidate_and_drain(st, sess):
    await sessions.invalidate_session(st, sess, "revoked", reason="test")
    await asyncio.sleep(0)
