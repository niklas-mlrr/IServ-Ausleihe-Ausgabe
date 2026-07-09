"""Unit-Tests für WorkerPool.stats() (automation/worker.py).

Reine Buchhaltung — kein Browser-Start, kein IServ.
"""

from __future__ import annotations

import asyncio

import pytest

from automation.worker import StudentSession, WorkerPool


def test_stats_empty_pool():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    assert p.stats() == {"total": 0, "available": 0, "in_use": 0}


def test_stats_tracks_checkout():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    p._contexts = ["a", "b", "c"]
    p._total = 3
    assert p.stats() == {"total": 3, "available": 3, "in_use": 0}
    p._contexts.pop()  # ein Context ausgecheckt (open_student)
    assert p.stats() == {"total": 3, "available": 2, "in_use": 1}


# ---------------------------------------------------------------------------
# open_student bei Cancel — Worker-Context muss zurück in den Pool
# (Regression zum Leak bei schnellem „Weiter"-Klicken)
# ---------------------------------------------------------------------------

class _FakePage:
    async def close(self) -> None:
        pass


class _FakeContext:
    """Stellvertretender Browser-Context: new_page() liefert eine Fake-Page."""

    def __init__(self, label: str) -> None:
        self.label = label

    async def new_page(self) -> _FakePage:
        return _FakePage()


async def _hang_load_card(self: StudentSession) -> None:
    """load_card, die nie zurückkehrt — simuliert die laufende Navigation,
    während der Nutzer «Weiter» klickt."""
    await asyncio.Event().wait()  # wird nie gesetzt


def test_open_student_cancel_returns_context(monkeypatch):
    """Wird open_student während load_card gecancel't, muss der Context
    zurück in den Pool — sonst leakt er und der Pool läuft leer."""
    monkeypatch.setattr(StudentSession, "load_card", _hang_load_card)

    p = WorkerPool(n=1, domain="d", username="u", password="p")
    ctx = _FakeContext("c1")
    p._contexts = [ctx]
    p._total = 1

    async def run() -> None:
        task = asyncio.create_task(p.open_student(42, "Test, Nina"))
        # Task bis in load_card laufen lassen (Context ist dann ausgecheckt).
        for _ in range(20):
            await asyncio.sleep(0)
        assert ctx not in p._contexts, "Context sollte ausgecheckt sein"
        assert p.stats()["available"] == 0

        task.cancel()
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await task

    asyncio.run(run())

    # Kern-Assertion: der Context ist nach Cancel wieder im Pool.
    assert p._contexts == [ctx], "Worker-Context geleakt (nicht zurückgegeben)"
    assert p.stats()["available"] == 1


def test_open_student_wait_then_released(monkeypatch):
    """Leerer Pool + wartender open_student: kommt ein Context via release()
    zurück, muss notify_all den Wartenden aufwecken (statt 12 s Timeout)."""
    monkeypatch.setattr(StudentSession, "load_card", _hang_load_card)

    p = WorkerPool(n=1, domain="d", username="u", password="p")
    ctx = _FakeContext("c1")
    p._contexts = [ctx]
    p._total = 1

    async def run() -> None:
        # Erster open_student nimmt den Context und bleibt in load_card hängen.
        first = asyncio.create_task(p.open_student(1, "Eins, Erste"))
        for _ in range(20):
            await asyncio.sleep(0)
        assert p.stats()["available"] == 0

        # Zweiter open_student auf leerem Pool → muss warten (nicht sofort werfen).
        second = asyncio.create_task(p.open_student(2, "Zwei, Zweite", wait_timeout=1.0))
        await asyncio.sleep(0)
        assert not second.done(), "zweiter open_student sollte warten, nicht fehlschlagen"

        # first canceln → BaseException-Handler gibt Context zurück + notify_all.
        first.cancel()
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await first

        # second sollte jetzt den zurückgegebenen Context nehmen und selbst in
        # load_card hängen (wir canceln ihn, um den Test sauber zu beenden).
        for _ in range(20):
            await asyncio.sleep(0)
        assert not second.done(), "zweiter sollte in load_card hängen"
        second.cancel()
        with pytest.raises((asyncio.CancelledError, RuntimeError)):
            await second

    asyncio.run(run())
    # Pool am Ende wieder vollständig (beide Cancel haben Context zurückgegeben).
    assert p._contexts == [ctx]
    assert p.stats()["available"] == 1
