"""Unit-Tests für WorkerPool.stats() (automation/worker.py).

Reine Buchhaltung — kein Browser-Start, kein IServ.
"""

from __future__ import annotations

import asyncio

import pytest

from automation.worker import StudentSession, WorkerPool

# ---------------------------------------------------------------------------
# WorkerPool.release — Idempotenz (kein Doppel-Append desselben Contexts)
#
# Bislang nur im Docstring von WorkerPool.release beschrieben (s.
# automation/worker.py:552ff): ein zweiter release()-Aufruf auf dieselbe
# StudentSession (möglich durch eine Race im Server-Code, s. release_worker /
# set_worker_session) darf den Context NICHT ein zweites Mal in den Pool
# anhängen — sonst würden zwei open_student-Aufrufe denselben Context poppen,
# beide Pages auf demselben Context erzeugen, und stats()["available"] über
# stats()["total"] hinaus wachsen.
# ---------------------------------------------------------------------------


class _FakeReleasePage:
    async def close(self) -> None:
        pass


def test_release_is_idempotent_no_double_append():
    p = WorkerPool(n=1, domain="d", username="u", password="p")
    ctx = _FakeContext("only")
    p._contexts = [ctx]
    p._total = 1
    # Context ausgecheckt (wie open_student es täte).
    p._contexts.pop()
    session = StudentSession(ctx, _FakeReleasePage(), "d", 1, "Test, Nina")

    async def run():
        await p.release(session)
        await p.release(session)  # zweiter Aufruf — muss no-op sein

    asyncio.run(run())

    assert p._contexts.count(ctx) == 1, "Context darf nach Doppel-Release nur einmal im Pool sein"
    assert session._context is None
    stats = p.stats()
    assert stats["available"] <= stats["total"]


# Leere Warteschlange — `stats()["waiting"]` zählt je Rolle (s. WorkerPool.waiting_counts).
_NO_WAITERS = {"helper": 0, "station": 0, "student": 0}


def test_stats_empty_pool():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    assert p.stats() == {"total": 0, "available": 0, "in_use": 0, "waiting": _NO_WAITERS}


def test_stats_tracks_checkout():
    p = WorkerPool(n=3, domain="d", username="u", password="p")
    p._contexts = ["a", "b", "c"]
    p._total = 3
    assert p.stats() == {"total": 3, "available": 3, "in_use": 0, "waiting": _NO_WAITERS}
    p._contexts.pop()  # ein Context ausgecheckt (open_student)
    assert p.stats() == {"total": 3, "available": 2, "in_use": 1, "waiting": _NO_WAITERS}


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


# ---------------------------------------------------------------------------
# Rangfolge vor dem Pool: helper → station → student (analog der Rollen-
# Rangfolge der Druckerwarteschlange). Innerhalb einer Rolle FIFO.
#
# Getestet auf der Vergabe-Ebene (`_add_waiter`/`_put_context`), damit kein
# Browser/Playwright nötig ist — genau diese beiden Methoden entscheiden, wer
# einen freiwerdenden Context bekommt.
# ---------------------------------------------------------------------------


def _pool_for_waiters() -> WorkerPool:
    p = WorkerPool(n=1, domain="d", username="u", password="p")
    p._contexts = []
    p._total = 1
    return p


def test_waiters_are_served_by_rank_then_fifo():
    p = _pool_for_waiters()
    served: list[str] = []

    async def run() -> None:
        async def take(priority: str, name: str) -> None:
            async with p._cond:
                waiter = p._add_waiter(priority)
            await waiter.future
            served.append(name)

        # Bewusst in der „falschen" Reihenfolge angemeldet.
        tasks = [
            asyncio.create_task(take("student", "student")),
            asyncio.create_task(take("station", "station")),
            asyncio.create_task(take("helper", "helper-1")),
            asyncio.create_task(take("helper", "helper-2")),
        ]
        for _ in range(20):
            await asyncio.sleep(0)
        assert p.waiting_counts() == {"helper": 2, "station": 1, "student": 1}

        ctx = _FakeContext("c1")
        for _ in range(4):
            async with p._cond:
                p._put_context(ctx)
            for _ in range(5):
                await asyncio.sleep(0)
        await asyncio.gather(*tasks)

    asyncio.run(run())
    assert served == ["helper-1", "helper-2", "station", "student"]


def test_cancelled_waiter_passes_context_on():
    """Ein abgebrochener Wartender darf den Context nicht mitnehmen — er geht
    an den nächsten in der Rangfolge."""
    p = _pool_for_waiters()

    async def run() -> None:
        async with p._cond:
            first = p._add_waiter("helper")
            second = p._add_waiter("student")
        # Erster bricht ab (Timeout/Cancel) → aus der Liste entfernen.
        first.future.cancel()
        async with p._cond:
            p._drop_waiter(first)
        ctx = _FakeContext("c1")
        async with p._cond:
            p._put_context(ctx)
        assert second.future.done()
        assert second.future.result() is ctx
        assert p._contexts == []

    asyncio.run(run())


def test_already_assigned_context_is_not_lost_on_timeout():
    """Zuteilung und Timeout im selben Tick: der bereits zugeteilte Context muss
    zurück in den Pool (bzw. zum nächsten Wartenden), nicht verschwinden."""
    p = _pool_for_waiters()

    async def run() -> None:
        async with p._cond:
            waiter = p._add_waiter("student")
        ctx = _FakeContext("c1")
        async with p._cond:
            p._put_context(ctx)  # Waiter hat den Context bereits bekommen
        # ...und läuft im selben Moment in seinen Timeout.
        async with p._cond:
            p._drop_waiter(waiter)
        assert p._contexts == [ctx], "zugeteilter Context darf nicht verloren gehen"

    asyncio.run(run())


def test_new_caller_does_not_overtake_existing_waiters():
    """Ein frisch eintreffender Aufruf darf sich nicht an der Warteschlange
    vorbeidrängeln, auch wenn gerade ein Context im Pool liegt."""
    p = _pool_for_waiters()

    async def run() -> None:
        async with p._cond:
            waiting = p._add_waiter("student")
        # Context kommt zurück → geht direkt an den Wartenden, landet also gar
        # nicht erst in `_contexts`, wo ein neuer Aufrufer ihn greifen könnte.
        ctx = _FakeContext("c1")
        async with p._cond:
            p._put_context(ctx)
        assert p._contexts == []
        assert waiting.future.result() is ctx

    asyncio.run(run())
