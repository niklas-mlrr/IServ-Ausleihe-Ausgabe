"""Unit-Tests für den WebSocket-Verteiler (server/hub.py).

Rein logisch — keine echten WebSockets, kein Server. Fokus auf die
Verteil- und Cleanup-Pfade, die in der Vergangenheit WS-Leaks verursacht
haben (Hardening 2026-06-18): tote Host-Sockets werden aus der Liste
entfernt, tote Scanner-Sockets auf `ws=None` gesetzt, und jede
Host-Broadcast zieht ein `queue_update` an unzugewiesene Scanner nach.
"""

from __future__ import annotations

import asyncio

from server.hub import Hub
from server.state import AppState, HelperSession, QueueStudent


class _FakeWS:
    """WebSocket-Double: sammelt gesendete Nachrichten, optional kaputt."""

    def __init__(self, *, broken: bool = False):
        self.broken = broken
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        if self.broken:
            raise RuntimeError("socket tot")
        self.sent.append(msg)


def _state_with_queue(pending: int = 0) -> AppState:
    s = AppState()
    for i in range(pending):
        s.queue.append(
            QueueStudent(student_id=i, lastname=f"L{i}", firstname=f"F{i}", form="10a")
        )
    return s


def test_broadcast_host_delivers_and_prunes_dead() -> None:
    s = _state_with_queue()
    live = _FakeWS()
    dead = _FakeWS(broken=True)
    s.host_ws_connections.extend([live, dead])

    asyncio.run(Hub().broadcast_host({"type": "state"}, s))

    assert live.sent == [{"type": "state"}]
    # Tote Verbindung wird aus der Liste entfernt, lebende bleibt.
    assert dead not in s.host_ws_connections
    assert live in s.host_ws_connections


def test_broadcast_host_pushes_queue_size_to_unassigned_scanners() -> None:
    s = _state_with_queue(pending=3)
    unassigned = _FakeWS()
    assigned = _FakeWS()
    s.helper_sessions["t1"] = HelperSession(token="t1", name="A", ws=unassigned)
    s.helper_sessions["t2"] = HelperSession(
        token="t2", name="B", student_id=42, ws=assigned
    )

    asyncio.run(Hub().broadcast_host({"type": "state"}, s))

    # Nur der unzugewiesene Scanner bekommt die Queue-Größe.
    assert {"type": "queue_update", "queue_size": 3, "queue": s.pending_queue_as_list()} in unassigned.sent
    assert assigned.sent == []


def test_broadcast_queue_size_reaches_assigned_helper_while_peeking() -> None:
    """Menü-Peek: ein zugewiesener Helfer mit `peeking=True` bekommt die Live-
    Queue (wie ein unzugewiesener), ohne peekende zugewiesene Helfer nicht."""
    s = _state_with_queue(pending=2)
    peeking = _FakeWS()
    assigned = _FakeWS()
    s.helper_sessions["t1"] = HelperSession(
        token="t1", name="A", student_id=7, ws=peeking, peeking=True
    )
    s.helper_sessions["t2"] = HelperSession(
        token="t2", name="B", student_id=8, ws=assigned
    )

    asyncio.run(Hub().broadcast_queue_size(s))

    assert {"type": "queue_update", "queue_size": 2, "queue": s.pending_queue_as_list()} in peeking.sent
    assert assigned.sent == []


def test_broadcast_queue_size_clears_dead_scanner_ws() -> None:
    s = _state_with_queue(pending=1)
    helper = HelperSession(token="t", name="A", ws=_FakeWS(broken=True))
    s.helper_sessions["t"] = helper

    asyncio.run(Hub().broadcast_queue_size(s))

    # Toter Scanner-Socket wird gelöst (kein Leak), Session bleibt bestehen.
    assert helper.ws is None
    assert "t" in s.helper_sessions


def test_send_scanner_delivers_to_known_token() -> None:
    s = _state_with_queue()
    ws = _FakeWS()
    s.helper_sessions["tok"] = HelperSession(token="tok", name="A", ws=ws)

    asyncio.run(Hub().send_scanner("tok", {"type": "ping"}, s))

    assert ws.sent == [{"type": "ping"}]


def test_send_scanner_noop_for_unknown_or_unconnected() -> None:
    s = _state_with_queue()
    s.helper_sessions["tok"] = HelperSession(token="tok", name="A", ws=None)

    # Unbekannter Token und Session ohne ws dürfen nicht werfen.
    asyncio.run(Hub().send_scanner("missing", {"type": "ping"}, s))
    asyncio.run(Hub().send_scanner("tok", {"type": "ping"}, s))


def test_send_scanner_clears_dead_ws() -> None:
    s = _state_with_queue()
    helper = HelperSession(token="tok", name="A", ws=_FakeWS(broken=True))
    s.helper_sessions["tok"] = helper

    asyncio.run(Hub().send_scanner("tok", {"type": "ping"}, s))

    assert helper.ws is None
