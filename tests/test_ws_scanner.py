"""End-to-End-Tests des Scanner-WebSocket-Dispatchers (server/routes/ws.py).

Anders als `test_scanner_reconnect.py` (das `_deferred_end` isoliert prüft)
fahren diese Tests den **Message-Loop von `ws_scanner` selbst** über einen
echten `TestClient.websocket_connect` — der ASGI-WS-Pfad inkl. accept/receive/
send, den es sonst nirgends gibt. Die dort dispatchte Logik (`call`-Re-Check,
`search_call`-Transient, `peek_queue`/`peek_close`, `scan` ohne Schüler, der
`json.JSONDecodeError`-Zweig) ist der bislang ungetestete Kern.

PRODUKTIONSSCHUTZ: kein echter Playwright/Browser/Netzwerk. `state.worker_pool`
ist None (Degraded-Modus → kein open_student), `state.iserv` ist ein reiner
In-Memory-Fake. Der Lifespan wird NICHT gestartet (client-Fixture ohne
`with`-Block, s. conftest.py) — kein Produktions-Login.

WICHTIG zur State-Injektion: `ws_scanner` löst `get_state()` im Namespace von
`server.routes.ws` auf; der Hub fällt für `broadcast_host`/`send_scanner` (in
ws.py ohne explizites `state`-Argument gerufen) auf `server.hub.get_state()`
zurück. Beide MÜSSEN dieselbe frische AppState liefern — daher patchen wir
`get_state` in BEIDEN Modulen.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

import server.hub as hub_module
import server.routes.ws as ws_module
from server.state import AppState, HelperSession, QueueStudent


class _FakeIServ:
    """Read-only In-Memory-Fake — nur die von den getesteten Pfaden berührten
    Methoden. Kein Netzwerk, keine Produktion."""

    async def get_student_info(self, student_id, schoolyear):
        return {"student_id": student_id, "books": [], "current_books": []}

    async def get_class_book_catalog(self, form, schoolyear):
        # (grade, catalog) — leer genügt: get_book_order_for_form fällt auf []
        # zurück, hydrate_student_info bleibt gültig.
        return (None, [])


@pytest.fixture
def ws_env(monkeypatch):
    """Frische AppState in ws.py UND hub.py injizieren, Fake-IServ, kein
    worker_pool. Registriert einen Helfer-Token 'h1'. Gibt (state, token)."""
    state = AppState()
    state.iserv = _FakeIServ()
    state.worker_pool = None
    monkeypatch.setattr(ws_module, "get_state", lambda: state)
    monkeypatch.setattr(hub_module, "get_state", lambda: state)
    helper = HelperSession(token="h1", name="Helfer")
    state.helper_sessions["h1"] = helper
    return state, "h1"


def _recv_until(ws, mtype: str, cap: int = 20) -> dict:
    """Nachrichten lesen, bis eine vom Typ `mtype` kommt (oder `cap` erreicht)."""
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") == mtype:
            return msg
    raise AssertionError(f"Nachricht vom Typ {mtype!r} nicht innerhalb {cap} Frames erhalten")


def _recv_until_any(ws, mtypes: set[str], cap: int = 20) -> dict:
    """Lesen, bis eine Nachricht mit einem Typ aus `mtypes` kommt. Nützlich,
    wenn zwei sich ausschließende Zweige unterschiedliche Terminal-Nachrichten
    senden (z. B. `error` vs. `loading`) — so hängt der Test unter Mutation
    nicht, sondern liest die (falsche) Terminal-Nachricht und schlägt beim
    Assert fehl."""
    for _ in range(cap):
        msg = ws.receive_json()
        if msg.get("type") in mtypes:
            return msg
    raise AssertionError(f"keine Nachricht aus {mtypes} innerhalb {cap} Frames")


def _drain_handshake(ws) -> None:
    """Die Begrüßungs-Nachrichten (waiting/settings/contexts_update/queue_update)
    abholen, bis die erste `contexts_update` durch ist — danach ist der Loop
    bereit für gesendete Kommandos."""
    _recv_until(ws, "contexts_update")


# ---------------------------------------------------------------------------
# Ungültiger Token → Verbindung wird abgewiesen (4004).
# ---------------------------------------------------------------------------


def test_scanner_rejects_unknown_token(client, ws_env):
    # Unbekannter Token → ws.close(4004) direkt nach (bzw. statt) accept.
    with pytest.raises(Exception), client.websocket_connect("/ws/scanner/bogus") as ws:  # noqa: B017
        ws.receive_json()


# ---------------------------------------------------------------------------
# 1) scan ohne zugewiesenen Schüler → scan_result mit status "error".
# ---------------------------------------------------------------------------


def test_scan_without_student_yields_error(client, ws_env):
    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        ws.send_json({"type": "scan", "value": "B1"})
        msg = _recv_until(ws, "scan_result")
        assert msg["status"] == "error"
        assert msg["barcode"] == "B1"


# ---------------------------------------------------------------------------
# 2) Malformed JSON-Frame darf die Empfangsschleife NICHT töten.
# ---------------------------------------------------------------------------


def test_malformed_frame_does_not_kill_loop(client, ws_env):
    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        # Kein valides JSON → json.JSONDecodeError im Handler → continue.
        ws.send_text("das-ist-kein-json{")
        # Danach ein valides Frame: kommt eine Antwort → Loop lebt noch.
        ws.send_json({"type": "scan", "value": "B2"})
        msg = _recv_until(ws, "scan_result")
        assert msg["barcode"] == "B2"
        assert msg["status"] == "error"  # weiterhin kein Schüler zugewiesen


# ---------------------------------------------------------------------------
# 3) peek_queue setzt helper.peeking=True + pusht queue_update & contexts_update;
#    peek_close setzt es zurück.
# ---------------------------------------------------------------------------


def test_peek_queue_and_close_toggle_peeking(client, ws_env):
    state, token = ws_env
    # Ein paar wartende Schüler im aktiven Kontext, damit die Queue nicht leer ist.
    ctx = state.open_context("10a")
    ctx.queue.append(QueueStudent(student_id=1, lastname="A", firstname="a", form="10a"))
    ctx.queue.append(QueueStudent(student_id=2, lastname="B", firstname="b", form="10a"))
    helper = state.helper_sessions[token]
    helper.context_id = ctx.id

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)

        ws.send_json({"type": "peek_queue"})
        qmsg = _recv_until(ws, "queue_update")
        assert qmsg["queue_size"] == 2
        _recv_until(ws, "contexts_update")
        # peek_queue setzt peeking VOR dem Senden → nach Empfang bereits True.
        assert helper.peeking is True

        ws.send_json({"type": "peek_close"})
        # peek_close pusht nichts — wir prüfen den State über einen Folge-Roundtrip:
        # ein scan (ohne Schüler) liefert scan_result; danach ist peek_close
        # längst verarbeitet.
        ws.send_json({"type": "scan", "value": "X"})
        _recv_until(ws, "scan_result")
        assert helper.peeking is False


# ---------------------------------------------------------------------------
# 4) call auf einen nicht-pending Schüler → error + Queue-Nachpush (Re-Check
#    „zwei Helfer rufen denselben Schüler auf").
# ---------------------------------------------------------------------------


def test_call_non_pending_student_errors(client, ws_env):
    state, token = ws_env
    ctx = state.open_context("10a")
    # Schüler bereits 'active' (z. B. von einem anderen Helfer aufgerufen).
    ctx.queue.append(
        QueueStudent(student_id=5, lastname="C", firstname="c", form="10a", status="active")
    )
    state.helper_sessions[token].context_id = ctx.id

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        ws.send_json({"type": "call", "student_id": 5})
        # Terminal muss `error` sein — NICHT `loading` (das käme, wenn der
        # Re-Check fehlte und der aktive Schüler doch zugewiesen würde).
        msg = _recv_until_any(ws, {"error", "loading"})
        assert msg["type"] == "error", f"erwartete error, bekam {msg['type']}"
        assert "nicht (mehr)" in msg["msg"]
        # Helfer bleibt ohne Schüler (nicht fälschlich gebunden).
        assert state.helper_sessions[token].student_id is None


# ---------------------------------------------------------------------------
# 4b) call aus einem fremden Klassen-Tab → Helfer wird an die Klasse des
#     aufgerufenen Schülers gebunden (rebind_helper_to_context).
# ---------------------------------------------------------------------------


def test_call_rebinds_helper_to_students_context(client, ws_env):
    state, token = ws_env
    ctx_other = state.open_context("10b")
    ctx_other.queue.append(
        QueueStudent(student_id=9, lastname="D", firstname="d", form="10b", status="pending")
    )
    helper = state.helper_sessions[token]
    helper.context_id = None  # „(aktive)" — noch keiner Klasse gebunden

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        ws.send_json({"type": "call", "student_id": 9})
        # Der Ladepfad (degraded, kein worker_pool) endet mit worker_ready.
        _recv_until(ws, "worker_ready")
        assert helper.student_id == 9
        assert helper.context_id == ctx_other.id  # rebind erfolgt
        # Schüler wurde auf active gesetzt und dem Helfer zugewiesen.
        student = state.find_student(9)
        assert student.status == "active"
        assert student.assigned_helper == token


# ---------------------------------------------------------------------------
# 5) search_call baut einen transienten Schüler, der in KEINER Queue landet.
# ---------------------------------------------------------------------------


def test_search_call_loads_transient_student_not_in_queue(client, ws_env):
    state, token = ws_env
    helper = state.helper_sessions[token]

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        ws.send_json(
            {
                "type": "search_call",
                "student_id": 4242,
                "form": "10a",
                "lastname": "Test",
                "firstname": "S",
            }
        )
        _recv_until(ws, "worker_ready")
        assert helper.student_id == 4242
        assert helper.student_form == "10a"
        # Schnellsprung: der Schüler steht bewusst in KEINER Kontext-Queue.
        assert state.find_student(4242) is None


def test_call_second_helper_becomes_spectator_of_active_queue_student(client, ws_env):
    """`call` auf einen Schüler, der gerade bei einem ANDEREN Helfer aktiv
    ist (Queue-Schüler): statt eines Fehlers wird der zweite Helfer
    Zuschauer (Bücherliste read-only, kein `worker_ready`) und in die
    Warteliste des Schülers eingetragen (s. spectate_student)."""
    state, token = ws_env
    ctx = state.open_context("10a")
    student = QueueStudent(student_id=4242, lastname="Test", firstname="S", form="10a")
    ctx.queue.append(student)
    state.helper_sessions["h2"] = HelperSession(token="h2", name="Helfer 2")

    with client.websocket_connect("/ws/scanner/h1") as ws1:
        _drain_handshake(ws1)
        ws1.send_json({"type": "call", "student_id": 4242})
        _recv_until(ws1, "worker_ready")
        assert student.status == "active"
        assert student.assigned_helper == "h1"

        with client.websocket_connect("/ws/scanner/h2") as ws2:
            _drain_handshake(ws2)
            ws2.send_json({"type": "call", "student_id": 4242})
            msg = _recv_until_any(ws2, {"student_info", "error"})
            assert msg["type"] == "student_info", f"erwartete student_info, bekam {msg}"
            assert msg.get("spectator") is True
            h2 = state.helper_sessions["h2"]
            assert h2.student_id is None  # kein eigener Worker
            assert h2.spectating_student_id == 4242
            assert state.student_spectators.get(4242) and (
                state.student_spectators[4242][0].token == "h2"
            )
            # Der aktive Helfer bleibt unangetastet.
            assert student.status == "active"
            assert student.assigned_helper == "h1"


def test_search_call_second_helper_becomes_spectator_of_transient_target(client, ws_env):
    """Lupe auf einen Schüler, der bei einem ANDEREN Helfer bereits (ebenfalls
    via Lupe, also transient — steht in KEINER Queue) aktiv ist: der zweite
    Helfer wird Zuschauer statt eines Fehlers zu bekommen. `find_student`
    würde diesen Fall NICHT erkennen (transient) — der Guard nutzt darum
    `find_helper_for_student`."""
    state, token = ws_env
    state.helper_sessions["h2"] = HelperSession(token="h2", name="Helfer 2")
    search_call = {
        "type": "search_call",
        "student_id": 4242,
        "form": "10a",
        "lastname": "Test",
        "firstname": "S",
    }

    with client.websocket_connect("/ws/scanner/h1") as ws1:
        _drain_handshake(ws1)
        ws1.send_json(search_call)
        _recv_until(ws1, "worker_ready")
        assert state.find_student(4242) is None  # weiterhin transient

        with client.websocket_connect("/ws/scanner/h2") as ws2:
            _drain_handshake(ws2)
            ws2.send_json(search_call)
            msg = _recv_until_any(ws2, {"student_info", "error"})
            assert msg["type"] == "student_info", f"erwartete student_info, bekam {msg}"
            assert msg.get("spectator") is True
            h2 = state.helper_sessions["h2"]
            assert h2.student_id is None
            assert h2.spectating_student_id == 4242


def test_scan_fans_out_to_spectator(client, ws_env):
    """Ein Scan des aktiven Helfers wird an alle Spectators dieses Schülers
    gespiegelt (`spectator: True`) — die Bücherliste bleibt so live
    synchron, auch ohne eigenen Worker."""
    state, token = ws_env
    ctx = state.open_context("10a")
    student = QueueStudent(student_id=4242, lastname="Test", firstname="S", form="10a")
    ctx.queue.append(student)
    state.helper_sessions["h2"] = HelperSession(token="h2", name="Helfer 2")

    with client.websocket_connect("/ws/scanner/h1") as ws1:
        _drain_handshake(ws1)
        ws1.send_json({"type": "call", "student_id": 4242})
        _recv_until(ws1, "worker_ready")

        with client.websocket_connect("/ws/scanner/h2") as ws2:
            _drain_handshake(ws2)
            ws2.send_json({"type": "call", "student_id": 4242})
            _recv_until(ws2, "student_info")

            ws1.send_json({"type": "scan", "value": "123456"})
            scan1 = _recv_until(ws1, "scan_result")
            assert not scan1.get("spectator")

            scan2 = _recv_until(ws2, "scan_result")
            assert scan2.get("spectator") is True
            assert scan2["barcode"] == scan1["barcode"]
            assert scan2["status"] == scan1["status"]


def test_spectator_disconnect_removed_from_waitlist(client, ws_env):
    """Trennt sich ein Zuschauer, wird er sofort (ohne Gnadenfrist — er hält
    keine exklusive Ressource) aus der Warteliste entfernt."""
    state, token = ws_env
    ctx = state.open_context("10a")
    student = QueueStudent(student_id=4242, lastname="Test", firstname="S", form="10a")
    ctx.queue.append(student)
    state.helper_sessions["h2"] = HelperSession(token="h2", name="Helfer 2")

    with client.websocket_connect("/ws/scanner/h1") as ws1:
        _drain_handshake(ws1)
        ws1.send_json({"type": "call", "student_id": 4242})
        _recv_until(ws1, "worker_ready")

        with client.websocket_connect("/ws/scanner/h2") as ws2:
            _drain_handshake(ws2)
            ws2.send_json({"type": "call", "student_id": 4242})
            _recv_until(ws2, "student_info")
            assert 4242 in state.student_spectators

        # ws2 ist jetzt geschlossen (with-Block verlassen) — finally-Block
        # von ws_scanner muss die Warteliste aufgeräumt haben.
        assert 4242 not in state.student_spectators
        assert state.helper_sessions["h2"].spectating_student_id is None


def test_search_call_missing_form_errors(client, ws_env):
    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        ws.send_json({"type": "search_call", "student_id": 4242})  # form fehlt
        # Terminal `error` erwartet — NICHT `loading` (das käme, wenn der
        # `not form`-Teil des Guards fehlte und trotzdem geladen würde).
        msg = _recv_until_any(ws, {"error", "loading"})
        assert msg["type"] == "error", f"erwartete error, bekam {msg['type']}"
        assert "Klasse" in msg["msg"] or "fehlt" in msg["msg"]


# ---------------------------------------------------------------------------
# 6) `finally`-Block von ws_scanner: gibt `helper.ws` nur frei, wenn er noch
#    zur trennenden Verbindung gehört (Reconnect-Schutz, s. Docstring über
#    `_RECONNECT_GRACE_S` in server/routes/ws.py).
#
#    `test_scanner_reconnect.py` prüft `_deferred_end` isoliert; hier geht es
#    um den Guard selbst (`if helper.ws is websocket:`) im echten ASGI-Pfad
#    über `TestClient.websocket_connect`.
# ---------------------------------------------------------------------------


def _settle(ws, iters: int = 30) -> None:
    """Der `TestClient`-WS läuft auf einem eigenen Portal-Thread/Event-Loop.
    Ein gesendetes `disconnect`-Frame wird nicht synchron verarbeitet — wir
    geben dem Portal-Loop über ein paar `asyncio.sleep(0)`-Runden (im Portal-
    Thread selbst, nicht im Testthread) Gelegenheit, die Coroutine bis in den
    `finally`-Block von `ws_scanner` laufen zu lassen. Kein Wandzeit-Sleep,
    kein Timing-Flakiness — nur kooperative Scheduling-Runden."""
    for _ in range(iters):
        ws.portal.call(asyncio.sleep, 0)


async def _cancel_and_await(t) -> None:
    if t is not None and not t.done():
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


def test_finally_releases_owned_ws_and_schedules_teardown(client, ws_env):
    """A1 — normale Trennung (kein Reconnect hat übernommen): `helper.ws` wird
    None, und weil ein Schüler zugewiesen war, wird ein Grace-Teardown-Task
    (`_deferred_end`) angelegt."""
    state, token = ws_env
    ctx = state.open_context("10a")
    ctx.queue.append(
        QueueStudent(student_id=1, lastname="A", firstname="B", form="10a", status="active")
    )
    helper = state.helper_sessions[token]
    helper.student_id = 1
    helper.context_id = ctx.id

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        assert helper.ws is not None
        ws.close(code=1000)
        _settle(ws)
        assert helper.ws is None, "finally sollte die eigene WS-Referenz freigeben"
        assert helper.end_task is not None, "Grace-Teardown-Task sollte angelegt worden sein"
        assert isinstance(helper.end_task, asyncio.Task)
        # Aufräumen, solange das Portal noch lebt (der Task hängt auf
        # asyncio.sleep(_RECONNECT_GRACE_S) — cancel statt auf die Frist warten).
        ws.portal.call(_cancel_and_await, helper.end_task)


def test_finally_noop_when_ownership_stolen(client, ws_env):
    """A2 — Reconnect-Simulation: bevor die alte Verbindung schließt, hat
    (angeblich) eine neue `helper.ws` bereits übernommen. Wir setzen dazu
    einen Sentinel statt einen echten zweiten Socket zu öffnen (laut Auftrag
    zulässig) — einfacher, und prüft exakt den Identitätsvergleich im Guard,
    ohne die Komplexität eines echten Doppel-Connects über denselben Token."""
    state, token = ws_env
    ctx = state.open_context("10a")
    ctx.queue.append(
        QueueStudent(student_id=2, lastname="C", firstname="D", form="10a", status="active")
    )
    helper = state.helper_sessions[token]
    helper.student_id = 2
    helper.context_id = ctx.id

    with client.websocket_connect("/ws/scanner/h1") as ws:
        _drain_handshake(ws)
        sentinel = object()
        # Simuliert: eine neue Verbindung hat synchron helper.ws übernommen,
        # BEVOR das finally der alten Verbindung läuft (s. ws_scanner: die
        # neue Verbindung setzt helper.ws = websocket VOR jedem eigenen await).
        helper.ws = sentinel
        ws.close(code=1000)
        _settle(ws)
        assert helper.ws is sentinel, (
            "finally darf die WS-Referenz einer neuen Verbindung nicht anfassen"
        )
        assert helper.end_task is None, (
            "finally darf keinen Teardown auslösen, wenn es die WS nicht mehr besitzt"
        )
