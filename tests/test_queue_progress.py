"""Unit-Tests für den Queue-Fortschritt „X/Y Bücher" und den Leihschein-Marker.

Rein logisch (RAM-State) — kein IServ, kein WebSocket, kein Worker. Y = die
angemeldeten Bücher OHNE ausgeblendete Reihen, X = davon erledigte (bereits
ausgeliehen oder in dieser Session gescannt/gebucht).
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions
from server.print_queue import PrintJob
from server.state import AppState, HelperSession, QueueStudent


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
    # Bei Laden bereits ausgeliehene Bücher — Grundlage für den session-
    # basierten Fortschritt in der Host-Status-Spalte.
    assert s.as_dict()["loaned_at_load"] == 1


def test_session_progress_excludes_pre_loaned_books():
    """Session-basierter Fortschritt (wie Druck-/Nächster-Schüler-Hinweis):
    X = seit Aufrufen ausgeliehene = books_done - loaned_at_load,
    Y = beim Aufrufen noch offene vorgemerkte = books_total - loaned_at_load.
    Vorbestand (bei Laden schon ausgeliehen) fließt in beide NICHT ein."""
    st, s = _state_with_student()
    sessions.init_book_progress(
        st, 7, _info(("A", "vorgemerkt"), ("B", "ausgeliehen"), ("C", "vorgemerkt"))
    )
    sessions.mark_book_done(st, 7, "A")  # ein offenes vorgemerktes ausgegeben
    d = s.as_dict()
    loaned = d["loaned_at_load"]
    assert (d["books_done"] - loaned, d["books_total"] - loaned) == (1, 2)


def test_hidden_books_count_in_neither_x_nor_y():
    """`apply_hidden_books` läuft vor der Zählung → ausgeblendete Reihen zählen
    weder als angemeldet (Y) noch als ausgegeben (X)."""
    st, s = _state_with_student()
    info = _info(("A", "vorgemerkt"), ("HIDE", "ausgeliehen"))
    sessions.apply_hidden_books(info, {"HIDE"})
    sessions.init_book_progress(st, 7, info)
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (0, 1)


def test_empty_stock_book_counts_neither_in_active_y_nor_hidden_from_true_total():
    """Anders als `apply_hidden_books` bleibt eine „Bestand leer"-Zeile in
    `info["books"]"`/`books_total` erhalten — der Client zieht sie nur für die
    aktive Y-Anzeige ab (`books_empty_outstanding`), die wahre Gesamtzahl
    (`books_total`) bleibt unverändert für die Klammer-Anzeige `X/Y (Z)`."""
    st, s = _state_with_student()
    st.caches.empty_isbns = {"EMPTY"}
    info = _info(("A", "vorgemerkt"), ("EMPTY", "vorgemerkt"))
    sessions.init_book_progress(st, 7, info)
    d = s.as_dict()
    assert (d["books_done"], d["books_total"]) == (0, 2)  # Zeile bleibt in books_total
    assert d["books_empty_outstanding"] == 1


def test_scanning_empty_stock_book_clears_outstanding_counter():
    """`mark_book_done` dekrementiert `books_empty_outstanding` beim
    tatsächlichen Scan — unabhängig von der Ja/Nein-Rückfrage im Helfer-
    Client (die hängt nicht an diesem Zähler)."""
    st, s = _state_with_student()
    st.caches.empty_isbns = {"EMPTY"}
    sessions.init_book_progress(st, 7, _info(("A", "vorgemerkt"), ("EMPTY", "vorgemerkt")))
    assert s.as_dict()["books_empty_outstanding"] == 1
    sessions.mark_book_done(st, 7, "EMPTY")
    assert s.as_dict()["books_empty_outstanding"] == 0
    assert s.as_dict()["books_done"] == 1


def test_scanned_book_counts_and_is_idempotent():
    st, s = _state_with_student()
    sessions.init_book_progress(st, 7, _info(("A", "vorgemerkt"), ("B", "vorgemerkt")))
    sessions.mark_book_done(st, 7, "A")
    sessions.mark_book_done(st, 7, "A")  # zweiter Scan derselben Reihe zählt nicht doppelt
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (1, 2)
    sessions.mark_book_done(st, 7, None)  # Scan ohne ISBN ändert nichts
    assert s.as_dict()["books_done"] == 1


def test_reconnect_preserves_baseline_but_refreshes_done():
    """reset_baseline=False (Reload derselben Verbindung): `loaned_at_load`
    bleibt auf dem Stand vom echten Aufrufen stehen, auch wenn zwischenzeitlich
    ein vorgemerktes Buch ausgeliehen wurde — Y sinkt dadurch NICHT."""
    st, s = _state_with_student()
    sessions.init_book_progress(
        st, 7, _info(("A", "vorgemerkt"), ("B", "vorgemerkt"), ("C", "vorgemerkt"))
    )
    assert s.as_dict()["loaned_at_load"] == 0
    # Zwischenzeitlich wurde "A" ausgeliehen — ein Reload holt das über IServ
    # als aktuellen Stand ("ausgeliehen") zurück.
    sessions.init_book_progress(
        st,
        7,
        _info(("A", "ausgeliehen"), ("B", "vorgemerkt"), ("C", "vorgemerkt")),
        reset_baseline=False,
    )
    d = s.as_dict()
    assert d["loaned_at_load"] == 0  # Baseline unverändert
    assert d["books_done"] == 1  # done_isbns trotzdem aufgefrischt
    loaned = d["loaned_at_load"]
    assert (d["books_done"] - loaned, d["books_total"] - loaned) == (1, 3)


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
            st, _info(("A", "ausgeliehen"), ("B", "vorgemerkt")), "10a", target, is_helper=True
        )
    )
    assert (s.as_dict()["books_done"], s.as_dict()["books_total"]) == (1, 2)


def test_hydrate_hides_unloaned_empty_stock_book_for_non_helper(monkeypatch):
    """Modus B/Scan-Station (`is_helper=False`): eine noch nicht ausgeliehene
    Bestand-leer-Reihe fällt aus `info["books"]"` (Anzeige) — bleibt aber in
    `books_total`/`vormerk_isbns` mitgezählt bzw. buchbar."""
    st, s = _state_with_student()
    st.caches.empty_isbns = {"EMPTY"}
    monkeypatch.setattr(sessions, "get_book_order_for_form", _async_none)
    monkeypatch.setattr(sessions, "get_hidden_isbns_for_form", _async_empty_set)
    target = _Target(7)
    info = asyncio.run(
        sessions.hydrate_student_info(
            st,
            _info(("A", "vorgemerkt"), ("EMPTY", "vorgemerkt")),
            "10a",
            target,
            is_helper=False,
        )
    )
    assert [b["isbn"] for b in info["books"]] == ["A"]
    assert target.vormerk_isbns == {"A", "EMPTY"}  # bleibt regulär buchbar
    d = s.as_dict()
    assert d["books_total"] == 2  # Host-Zähler rechnet weiterhin mit der vollen Liste
    assert d["books_empty_outstanding"] == 1


def test_hydrate_keeps_loaned_empty_stock_book_visible_for_non_helper(monkeypatch):
    st, _s = _state_with_student()
    st.caches.empty_isbns = {"EMPTY"}
    monkeypatch.setattr(sessions, "get_book_order_for_form", _async_none)
    monkeypatch.setattr(sessions, "get_hidden_isbns_for_form", _async_empty_set)
    target = _Target(7)
    info = asyncio.run(
        sessions.hydrate_student_info(
            st, _info(("EMPTY", "ausgeliehen")), "10a", target, is_helper=False
        )
    )
    assert [b["isbn"] for b in info["books"]] == ["EMPTY"]


def test_hydrate_keeps_empty_stock_book_visible_for_helper(monkeypatch):
    """Modus A (`is_helper=True`): keine Filterung — die Reihe bleibt in der
    Liste, nur mit `bestand_leer`-Flag markiert (unveränderte Alt-Logik)."""
    st, _s = _state_with_student()
    st.caches.empty_isbns = {"EMPTY"}
    monkeypatch.setattr(sessions, "get_book_order_for_form", _async_none)
    monkeypatch.setattr(sessions, "get_hidden_isbns_for_form", _async_empty_set)
    target = _Target(7)
    info = asyncio.run(
        sessions.hydrate_student_info(
            st, _info(("EMPTY", "vorgemerkt")), "10a", target, is_helper=True
        )
    )
    assert [b["isbn"] for b in info["books"]] == ["EMPTY"]
    assert info["books"][0].get("bestand_leer") is True


def test_reset_to_pending_clears_progress_and_slip():
    """Zurück in die Warteschlange = neuer Durchlauf → Zähler und Leihschein-
    Marker fallen auf Null zurück (done/skipped behalten ihren Stand)."""
    _st, s = _state_with_student()
    s.books_total, s.done_isbns, s.slip_printed = 2, {"A"}, True
    s.loaned_at_load = 1
    s.slip_collected = True
    s.reset_progress()
    assert s.as_dict()["books_total"] is None
    assert s.as_dict()["books_done"] == 0
    assert s.as_dict()["slip_printed"] is False
    assert s.as_dict()["loaned_at_load"] == 0
    assert s.as_dict()["slip_collected"] is False


def test_new_scan_invalidates_current_slip_and_print_queue_state():
    """Ein Scan während/nach dem Druck setzt den Leihscheinstatus zurück.

    Der alte Auftrag darf physisch weiterlaufen, aber ein neuer Auftrag ist
    nötig, bevor der Schüler wieder als gedruckt gilt.
    """
    st, s = _state_with_student()
    s.slip_printed = True
    old = PrintJob.create(role="helper", student_id=7, pages="1", name="N, V")
    asyncio.run(st.print_queue.enqueue(old))
    assert st.print_queue.print_job_states() == {7: "waiting"}

    sessions.invalidate_slip_after_scan(st, 7)

    assert old.invalidated is True
    assert st.print_queue.print_job_states() == {}
    assert s.slip_printed is False
    assert s.slip_generation == 1


def test_stale_completed_job_cannot_mark_new_slip():
    """Auch das enge Fenster zwischen Job-Ende und Slip-Marker bleibt sicher."""
    st, s = _state_with_student()
    old = PrintJob.create(
        role="helper", student_id=7, pages="1", name="N, V", generation=s.slip_generation
    )
    old.status = "done"
    old.result = {"ok": True}
    sessions.invalidate_slip_after_scan(st, 7)
    asyncio.run(st.print_queue._mark_slip_printed_after_completion(old))
    assert s.slip_printed is False


def test_queue_snapshot_resolves_active_helper_name():
    st, s = _state_with_student()
    helper = HelperSession(token="helper-token", name="Anna", student_id=s.student_id)
    st.helper_sessions[helper.token] = helper
    s.assigned_helper = helper.token

    entry = st.state_snapshot()["contexts"][st.active_context.id]["queue"][0]
    assert entry["assigned_helper_name"] == "Anna"


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
    assert s.auto_skipped is False


def test_load_student_flags_marks_auto_done_as_auto_skipped():
    from server.routes import classes

    st, s = _state_with_student()
    s.status = "pending"

    class _FakeIServ:
        async def get_student_info(self, student_id, schoolyear):
            return {"enrolled": False, "books": []}

    st.iserv = _FakeIServ()
    asyncio.run(classes._load_student_flags(st, st.active_context, ["not_enrolled"]))

    assert s.status == "done"
    assert s.auto_skipped is True


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
