"""Unit-Tests für die Helfer-WS-Handler `mark_empty_stock`/`clear_empty_stock`
(server/routes/ws.py). Direkter Aufruf der Handler-Coroutinen mit Fake-
State/Hub — kein echter WebSocket (leichtgewichtiger als die volle
`ws_env`-Infrastruktur in test_ws_scanner.py, ausreichend für reine
Handler-Logik: ISBN-Validierung gegen `helper.expected_isbns`, globales
Set-Update, Persistenz-Aufruf, Repush-Fanout).
"""

from __future__ import annotations

import asyncio

import server.routes.ws as ws_module
from server.state import AppState, HelperSession, QueueStudent


class _FakeHub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []
        self.settings_broadcasts = 0
        self.host_broadcasts = 0

    async def broadcast_settings(self, *a, **kw) -> None:
        self.settings_broadcasts += 1

    async def broadcast_host(self, snapshot) -> None:
        self.host_broadcasts += 1

    async def send_scanner(self, token, msg) -> None:
        self.sent.append((token, msg))

    async def send_websocket(self, ws, msg) -> bool:
        return True


class _FakeIServ:
    def __init__(self, info: dict) -> None:
        self._info = info

    async def get_student_info(self, student_id, schoolyear):
        return dict(self._info)


def _setup(monkeypatch, persist_calls: list):
    state = AppState()
    state.selected_schoolyear = "2025"
    ctx = state.open_context("9a")
    ctx.queue.append(
        QueueStudent(student_id=5, lastname="N", firstname="V", form="9a", status="active")
    )
    helper = HelperSession(token="tok", name="H")
    helper.student_id = 5
    helper.ws = object()
    helper.expected_isbns = {"A", "B"}
    state.helper_sessions["tok"] = helper
    state.iserv = _FakeIServ(
        {"enrolled": True, "books": [{"isbn": "A", "status": "vorgemerkt"}]}
    )
    monkeypatch.setattr(
        ws_module, "_persist_booklist_settings", lambda st: persist_calls.append(st)
    )
    return state, helper


def test_mark_empty_stock_accepts_only_expected_isbns(monkeypatch):
    persisted = []
    state, helper = _setup(monkeypatch, persisted)
    hub = _FakeHub()
    asyncio.run(
        ws_module._handle_mark_empty_stock(
            state, hub, helper, None, {"isbns": ["A", "FOREIGN-ISBN"]}
        )
    )
    assert state.caches.empty_isbns == {"A"}  # FOREIGN-ISBN nicht in expected_isbns -> verworfen
    assert persisted == [state]
    assert hub.settings_broadcasts == 1


def test_mark_empty_stock_noop_when_nothing_valid(monkeypatch):
    persisted = []
    state, helper = _setup(monkeypatch, persisted)
    hub = _FakeHub()
    asyncio.run(
        ws_module._handle_mark_empty_stock(state, hub, helper, None, {"isbns": ["FOREIGN"]})
    )
    assert state.caches.empty_isbns == set()
    assert persisted == []
    assert hub.settings_broadcasts == 0


def test_clear_empty_stock_removes_isbn(monkeypatch):
    persisted = []
    state, helper = _setup(monkeypatch, persisted)
    state.caches.empty_isbns = {"A"}
    hub = _FakeHub()
    asyncio.run(ws_module._handle_clear_empty_stock(state, hub, helper, None, {"isbn": "A"}))
    assert state.caches.empty_isbns == set()
    assert persisted == [state]


def test_clear_empty_stock_noop_when_not_marked(monkeypatch):
    persisted = []
    state, helper = _setup(monkeypatch, persisted)
    hub = _FakeHub()
    asyncio.run(ws_module._handle_clear_empty_stock(state, hub, helper, None, {"isbn": "A"}))
    assert persisted == []
    assert hub.settings_broadcasts == 0


def test_mark_empty_stock_repushes_and_updates_host(monkeypatch):
    persisted = []
    state, helper = _setup(monkeypatch, persisted)
    hub = _FakeHub()
    asyncio.run(
        ws_module._handle_mark_empty_stock(state, hub, helper, None, {"isbns": ["A"]})
    )
    assert any(msg.get("type") == "booklist_update" for _tok, msg in hub.sent)
    assert hub.host_broadcasts == 1


def test_scanner_handlers_registers_both_types():
    assert ws_module._SCANNER_HANDLERS["mark_empty_stock"] is ws_module._handle_mark_empty_stock
    assert ws_module._SCANNER_HANDLERS["clear_empty_stock"] is ws_module._handle_clear_empty_stock


def test_student_ws_does_not_know_empty_stock_types():
    """Bewusst Helfer-exklusiv — Modus B (`ws_student`) kennt weder
    `mark_empty_stock` noch `clear_empty_stock` als `mtype`."""
    import inspect

    src = inspect.getsource(ws_module.ws_student)
    assert "mark_empty_stock" not in src
    assert "clear_empty_stock" not in src
