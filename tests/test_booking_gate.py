"""Unit-Tests für das Buchungs-Gate (server/sessions.handle_commit).

Kernzusicherung (CLAUDE.md / PLAN §6): bei ALLOW_BOOKING=false wird der Worker —
und damit das einzige Enter (commit_barcode) — NIE berührt. Kein IServ/Playwright.
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions


class _Cfg:
    def __init__(self, allow: bool):
        self.allow_booking = allow


class _SpyWorker:
    def __init__(self):
        self.called = False

    async def commit_barcode(self, barcode: str) -> dict:
        self.called = True
        return {"status": "booked", "barcode": barcode}


class _State:
    def __init__(self, worker=None):
        self.student_worker_sessions = {42: worker} if worker else {}


def test_blocked_without_flag(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    worker = _SpyWorker()
    res = asyncio.run(sessions.handle_commit(_State(worker), 42, "B1"))
    assert res["status"] == "blocked"
    assert worker.called is False  # Enter-Pfad nie erreicht


def test_allows_with_flag(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    worker = _SpyWorker()
    res = asyncio.run(sessions.handle_commit(_State(worker), 42, "B1"))
    assert res["status"] == "booked"
    assert worker.called is True


def test_error_when_no_worker(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    res = asyncio.run(sessions.handle_commit(_State(None), 42, "B1"))
    assert res["status"] == "error"
