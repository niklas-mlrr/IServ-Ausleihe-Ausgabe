"""Buchungs-Vorabprüfung (Freigabe 2026-07-02).

Gebucht (Enter) wird nur, wenn ALLE Bedingungen erfüllt sind:
  1. Buch im Lager (available and not distributed and not deleted),
  2. Schüler hat bestellt UND von der Reihe ist noch keins ausgeliehen
     (= ISBN ∈ vorgemerkt).
Sonst wird der Barcode gar nicht erst ins Feld getippt. Bei Unsicherheit
(kein Client / Buchliste leer / Lookup-Fehler) wird NICHT gebucht.
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions


class _Cfg:
    def __init__(self, allow: bool):
        self.allow_booking = allow


class _FakeIserv:
    def __init__(self, book=None, raises=False):
        self._book = book
        self._raises = raises

    async def get_book_by_code(self, code: str):
        if self._raises:
            raise RuntimeError("boom")
        return self._book


class _State:
    def __init__(self, iserv=None, worker=None):
        self.iserv = iserv
        self.student_worker_sessions = {42: worker} if worker else {}

    def find_student(self, student_id):
        return None


def _book(isbn="978-1", available=True, distributed=False, deleted=False):
    return {
        "code": "B1", "isbn": isbn, "title": "Mathe 5", "subject": "Mathematik",
        "available": available, "distributed": distributed, "deleted": deleted,
        "student_id": None,
    }


def _eval(state, vormerk, lent, barcode="B1"):
    return asyncio.run(sessions.evaluate_scan_for_booking(state, vormerk, lent, barcode))


# --- evaluate_scan_for_booking -------------------------------------------------

def test_bookable_when_ordered_and_in_stock():
    res = _eval(_State(_FakeIserv(_book())), {"978-1"}, set())
    assert res["ok"] is True
    assert res["isbn"] == "978-1"


def test_reject_not_in_stock_distributed():
    res = _eval(_State(_FakeIserv(_book(distributed=True))), {"978-1"}, set())
    assert res["ok"] is False
    assert res["status"] == "not_in_stock"


def test_reject_not_in_stock_unavailable():
    res = _eval(_State(_FakeIserv(_book(available=False))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "not_in_stock"


def test_reject_series_already_lent():
    res = _eval(_State(_FakeIserv(_book())), set(), {"978-1"})
    assert res["ok"] is False
    assert res["status"] == "series_already_lent"


def test_reject_deleted_before_not_enrolled():
    # Ausgemustert (deleted) UND nicht bestellt -> "book_deleted" muss VOR der
    # Anmeldeprüfung greifen (ToDo: sofort erkennbar, egal ob bestellt).
    res = _eval(_State(_FakeIserv(_book(isbn="978-9", deleted=True))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "book_deleted"


def test_reject_deleted_before_not_in_stock():
    res = _eval(_State(_FakeIserv(_book(deleted=True, distributed=True))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "book_deleted"


def test_reject_not_enrolled():
    # ISBN weder vorgemerkt noch ausgeliehen, aber Liste ist geladen (andere ISBN).
    res = _eval(_State(_FakeIserv(_book(isbn="978-9"))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "not_enrolled"


def test_reject_unknown_book():
    res = _eval(_State(_FakeIserv(None)), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "unknown_book"


def test_reject_when_lists_not_loaded():
    res = _eval(_State(_FakeIserv(_book())), set(), set())
    assert res["ok"] is False and res["status"] == "not_ready"


def test_reject_when_no_client():
    res = _eval(_State(None), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "error"


def test_reject_on_lookup_error():
    res = _eval(_State(_FakeIserv(raises=True)), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "error"


# --- booking_isbn_sets_from_info ----------------------------------------------

def test_sets_split_by_status():
    info = {"books": [
        {"isbn": "A", "status": "vorgemerkt"},
        {"isbn": "B", "status": "ausgeliehen"},
        {"isbn": "", "status": "vorgemerkt"},   # leere ISBN wird ignoriert
    ]}
    vormerk, lent = sessions.booking_isbn_sets_from_info(info)
    assert vormerk == {"A"} and lent == {"B"}


# --- process_scan (Gate-Verhalten) --------------------------------------------

class _SpyWorker:
    def __init__(self):
        self.committed = None
        self.staged = None

    async def commit_barcode(self, barcode: str) -> dict:
        self.committed = barcode
        return {"status": "booked", "barcode": barcode}

    async def submit_barcode(self, barcode: str) -> dict:
        self.staged = barcode
        return {"status": "staged"}


def test_process_scan_books_when_gate_on(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    worker = _SpyWorker()
    state = _State(_FakeIserv(_book()), worker)
    res = asyncio.run(sessions.process_scan(state, 42, {"978-1"}, set(), "B1"))
    assert res["status"] == "booked"
    assert worker.committed == "B1" and worker.staged is None


def test_process_scan_stages_when_gate_off(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    worker = _SpyWorker()
    state = _State(_FakeIserv(_book()), worker)
    res = asyncio.run(sessions.process_scan(state, 42, {"978-1"}, set(), "B1"))
    assert res["status"] == "staged"
    assert worker.staged == "B1" and worker.committed is None


def test_process_scan_no_field_touch_when_conditions_fail(monkeypatch):
    # Gate an, aber Buch nicht im Lager → weder Buchung noch Staging (kein Feldkontakt).
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    worker = _SpyWorker()
    state = _State(_FakeIserv(_book(distributed=True)), worker)
    res = asyncio.run(sessions.process_scan(state, 42, {"978-1"}, set(), "B1"))
    assert res["status"] == "not_in_stock"
    assert worker.committed is None and worker.staged is None
