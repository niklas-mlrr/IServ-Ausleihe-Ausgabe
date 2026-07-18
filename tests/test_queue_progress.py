"""Unit-Tests für den Queue-Fortschritt „X/Y Bücher" und den Leihschein-Marker.

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker. Y = die
angemeldeten Bücher OHNE ausgeblendete Reihen, X = davon erledigte (bereits
ausgeliehen oder in dieser Session gescannt/gebucht).
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions
from server.state import AppState, QueueStudent


class _Target:
    """Minimaler Stand-in für HelperSession/StudentSessionB in hydrate_student_info."""

    def __init__(self, student_id: int) -> None:
        self.student_id = student_id
        self.expected_isbns: set[str] = set()
        self.vormerk_isbns: set[str] = set()
        self.lent_isbns: set[str] = set()
        self.lent_codes: set[str] = set()


def _state_with_student(sid: int = 7) -> tuple[AppState, QueueStudent]:
    st = AppState()
    ctx = st.open_context("10a")
    s = QueueStudent(student_id=sid, lastname="N", firstname="V", form="10a", status="active")
    ctx.queue.append(s)
    return st, s


def _info(*books: tuple[str, str]) -> dict:
    return {"books": [{"isbn": isbn, "status": status} for isbn, status in books]}


def test_fresh_student_has_no_counter():
    """Vor dem ersten Laden ist nichts bekannt — der Host zeigt dann kein X/Y."""
    _st, s = _state_with_student()
    assert s.as_dict()["books_total"] is None
    assert s.as_dict()["books_done"] == 0
    assert s.as_dict()["slip_printed"] is False


def test_init_counts_lent_books_as_done():
    st, s = _state_with_student()
    sessions.init_book_progress(
        st, 7, _info(("A", "vorgemerkt"), ("B", "ausgeliehen"), ("C", "vorgemerkt"))
    )
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (1, 3)


def test_hidden_books_count_in_neither_x_nor_y():
    """`apply_hidden_books` läuft vor der Zählung → ausgeblendete Reihen zählen
    weder als angemeldet (Y) noch als ausgegeben (X)."""
    st, s = _state_with_student()
    info = _info(("A", "vorgemerkt"), ("HIDE", "ausgeliehen"))
    sessions.apply_hidden_books(info, {"HIDE"})
    sessions.init_book_progress(st, 7, info)
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (0, 1)


def test_scanned_book_counts_and_is_idempotent():
    st, s = _state_with_student()
    sessions.init_book_progress(st, 7, _info(("A", "vorgemerkt"), ("B", "vorgemerkt")))
    sessions.mark_book_done(st, 7, "A")
    sessions.mark_book_done(st, 7, "A")  # zweiter Scan derselben Reihe zählt nicht doppelt
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (1, 2)
    sessions.mark_book_done(st, 7, None)  # Scan ohne ISBN ändert nichts
    assert s.as_dict()["books_done"] == 1


def test_transient_student_without_queue_entry_is_ignored():
    """Lupe-Schüler stehen in keiner Queue — Zähler-Updates laufen still ins Leere."""
    st, _s = _state_with_student()
    sessions.init_book_progress(st, 999, _info(("A", "vorgemerkt")))
    sessions.mark_book_done(st, 999, "A")


def test_hydrate_fills_progress(monkeypatch):
    st, s = _state_with_student()
    monkeypatch.setattr(sessions, "get_book_order_for_form", _async_none)
    monkeypatch.setattr(sessions, "get_hidden_isbns_for_form", _async_empty_set)
    target = _Target(7)
    asyncio.run(
        sessions.hydrate_student_info(
            st, _info(("A", "ausgeliehen"), ("B", "vorgemerkt")), "10a", target
        )
    )
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (1, 2)


def test_reset_to_pending_clears_progress_and_slip():
    """Zurück in die Warteschlange = neuer Durchlauf → Zähler und Leihschein-
    Marker fallen auf Null zurück (done/skipped behalten ihren Stand)."""
    _st, s = _state_with_student()
    s.books_total, s.done_isbns, s.slip_printed = 2, {"A"}, True
    s.reset_progress()
    assert s.as_dict()["books_total"] is None
    assert s.as_dict()["books_done"] == 0
    assert s.as_dict()["slip_printed"] is False


def test_info_flags_from_student_info():
    _st, s = _state_with_student()
    s.set_info_flags(
        {
            "enrolled": True,
            "paid": False,
            "amount_open": "40.54",
            "remission_pending": True,
            "exemption_pending": False,
        }
    )
    d = s.as_dict()
    assert d["amount_open"] == 40.54  # auch als String geliefert → float
    assert (d["enrolled"], d["paid"], d["remission_pending"], d["exemption_pending"]) == (
        True,
        False,
        True,
        False,
    )
    # Der informative Zahl-/Antragsstand rührt den ablaufsteuernden Status nicht an.
    assert d["status"] == "active"


def test_info_flags_without_enrollment_stay_unknown():
    """Ohne Anmeldung liefert IServ zu Zahlung/Anträgen nichts Belastbares —
    die Felder bleiben None (kein Badge), statt „nicht bezahlt" vorzutäuschen."""
    _st, s = _state_with_student()
    s.set_info_flags({"enrolled": False, "paid": False, "remission_pending": True})
    d = s.as_dict()
    assert d["enrolled"] is False
    assert d["paid"] is None and d["remission_pending"] is None and d["exemption_pending"] is None


def test_load_student_flags_fills_flags_without_auto_done_filters():
    """Ohne gewählte Auto-Fertig-Filter wird trotzdem geladen (Info-Spalte) —
    und dann darf sich am Status nichts ändern."""
    from server.routes import classes

    st, s = _state_with_student()
    s.status = "pending"

    class _FakeIServ:
        async def get_student_info(self, student_id, schoolyear):
            return {"enrolled": True, "paid": False, "books": []}

    st.iserv = _FakeIServ()
    asyncio.run(classes._load_student_flags(st, st.active_context, []))
    assert s.paid is False and s.enrolled is True
    assert s.status == "pending"


def test_load_student_flags_survives_iserv_error():
    from server.routes import classes

    st, s = _state_with_student()

    class _BoomIServ:
        async def get_student_info(self, student_id, schoolyear):
            raise RuntimeError("IServ down")

    st.iserv = _BoomIServ()
    asyncio.run(classes._load_student_flags(st, st.active_context, ["unpaid"]))
    assert s.enrolled is None and s.status == "active"  # unverändert, kein Abbruch


async def _async_none(*_a, **_kw):
    return None


async def _async_empty_set(*_a, **_kw):
    return set()
