"""Buchungs-Vorabprüfung.

Gebucht (Enter) wird nur, wenn ALLE Bedingungen erfüllt sind:
  1. Buch im Lager (available and not distributed and not deleted),
  2. Schüler hat bestellt UND von der Reihe ist noch keins ausgeliehen
     (= ISBN ∈ vorgemerkt).
Sonst wird der Barcode gar nicht erst ins Feld getippt. Bei Unsicherheit
(kein Client / Buchliste leer / Lookup-Fehler) wird NICHT gebucht.

Ist die ISBN bereits ausgeliehen (Schüler hat sie schon), wird zusätzlich
per Barcode-Abgleich (`lent_codes`) unterschieden: `book_already_lent`
(genau DIESES Exemplar läuft schon auf den Schüler) vs. `series_already_lent`
(ein ANDERES Exemplar derselben Reihe).
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


def _book(isbn="978-1", available=True, distributed=False, deleted=False, code="B1"):
    return {
        "code": code,
        "isbn": isbn,
        "title": "Mathe 5",
        "subject": "Mathematik",
        "available": available,
        "distributed": distributed,
        "deleted": deleted,
        "student_id": None,
        "loaned_to": None,
        "loaned_to_id": None,
    }


def _eval(state, vormerk, lent, barcode="B1", lent_codes=None):
    return asyncio.run(
        sessions.evaluate_scan_for_booking(state, vormerk, lent, lent_codes or set(), barcode)
    )


def _process(state, student_id, vormerk, lent, barcode="B1", lent_codes=None, source="student"):
    codes = lent_codes if lent_codes is not None else set()
    return asyncio.run(
        sessions.process_scan(state, student_id, vormerk, lent, codes, barcode, source=source)
    )


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


def test_not_in_stock_carries_loaned_to():
    # Verliehenes Buch → aktueller Ausleiher wird (read-only aus /books/:code)
    # als eigenes Feld durchgereicht; die `msg` bleibt bewusst name-frei (der
    # Name wandert nur via `loaned_to`, damit Schüler-Client ihn weglassen kann).
    book = _book(distributed=True)
    book["loaned_to"] = "Max Mustermann"
    book["loaned_to_id"] = 4321
    res = _eval(_State(_FakeIserv(book)), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "not_in_stock"
    assert res["loaned_to"] == "Max Mustermann"
    assert res["loaned_to_id"] == 4321
    assert "Max Mustermann" not in res["msg"]
    assert "verliehen" in res["msg"]


def test_not_in_stock_without_borrower_stays_silent():
    # Lager-Status „verliehen", aber kein Ausleiher auflösbar → keine
    # Namensnennung (Felder None), Meldung bleibt ohne „verliehen an …".
    res = _eval(_State(_FakeIserv(_book(distributed=True))), {"978-1"}, set())
    assert res["status"] == "not_in_stock"
    assert res["loaned_to"] is None and res["loaned_to_id"] is None
    assert "verliehen an" not in res["msg"]


def test_reject_series_already_lent():
    # ISBN schon ausgeliehen, aber der gescannte Barcode ist NICHT das
    # Exemplar, das auf dem Schüler läuft (lent_codes leer) → series_already_lent.
    res = _eval(_State(_FakeIserv(_book())), set(), {"978-1"})
    assert res["ok"] is False
    assert res["status"] == "series_already_lent"


def test_reject_book_already_lent_same_code():
    # ISBN schon ausgeliehen UND der gescannte Barcode ist GENAU das Exemplar,
    # das schon auf dem Schüler läuft (Barcode ∈ lent_codes) → book_already_lent.
    res = _eval(
        _State(_FakeIserv(_book(code="B1"))), set(), {"978-1"}, barcode="B1", lent_codes={"B1"}
    )
    assert res["ok"] is False
    assert res["status"] == "book_already_lent"


def test_series_already_lent_when_different_code_of_same_isbn():
    # ISBN schon ausgeliehen (anderes Exemplar, Code "B2" läuft auf dem
    # Schüler), gescannt wird ein ANDERES Exemplar derselben ISBN ("B1")
    # → series_already_lent (nicht book_already_lent).
    res = _eval(
        _State(_FakeIserv(_book(code="B1"))), set(), {"978-1"}, barcode="B1", lent_codes={"B2"}
    )
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


def test_reject_deleted_carries_loaned_to_when_student_id():
    # Ausgemustert UND noch mit einem Schüler verknüpft (student_id != null,
    # z. B. [not_timely]/[unusable]) → loaned_to/loaned_to_id durchreichen,
    # damit Host + Helfer den Ersatzanspruch-Hinweis zeigen können. Die msg
    # bleibt name-frei (Name wandert nur via loaned_to).
    book = _book(isbn="978-9", deleted=True)
    book["loaned_to"] = "Max Mustermann"
    book["loaned_to_id"] = 4321
    res = _eval(_State(_FakeIserv(book)), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "book_deleted"
    assert res["loaned_to"] == "Max Mustermann"
    assert res["loaned_to_id"] == 4321
    assert "Max Mustermann" not in res["msg"]
    assert "ausgemustert" in res["msg"]


def test_reject_deleted_without_student_id_silent():
    # Ausgemustert ohne Schüler-Verknüpfung → keine Ersatzanspruch-Daten
    # (loaned_to/loaned_to_id None), msg ohne Hinweis.
    res = _eval(_State(_FakeIserv(_book(isbn="978-9", deleted=True))), {"978-1"}, set())
    assert res["status"] == "book_deleted"
    assert res["loaned_to"] is None and res["loaned_to_id"] is None
    assert "Ersatzanspruch" not in res["msg"]


def test_reject_not_enrolled():
    # ISBN weder vorgemerkt noch ausgeliehen, aber Liste ist geladen (andere ISBN).
    res = _eval(_State(_FakeIserv(_book(isbn="978-9"))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "not_enrolled"


def test_reject_not_in_stock_before_not_enrolled():
    # Verliehen (distributed) UND nicht bestellt → „nicht im Lager" geht VOR
    # „nicht bestellt". Lager-Prüfung zuerst, damit ein verliehenes Buch immer
    # als solches angezeigt wird.
    res = _eval(_State(_FakeIserv(_book(isbn="978-9", distributed=True))), {"978-1"}, set())
    assert res["ok"] is False and res["status"] == "not_in_stock"
    assert "verliehen" in res["msg"]


def test_reject_series_already_lent_before_not_in_stock():
    # Reihe bereits an dich ausgeliehen (isbn in lent) UND Exemplar verliehen
    # (distributed, z. B. dein eigenes Exemplar) → series_already_lent VOR
    # not_in_stock, sonst wuerde „verliehen an dich selbst" gemeldet.
    res = _eval(_State(_FakeIserv(_book(isbn="978-9", distributed=True))), set(), {"978-9"})
    assert res["ok"] is False and res["status"] == "series_already_lent"


def test_reject_series_already_lent_even_when_in_stock():
    # Reihe bereits an dich ausgeliehen, aber das gescannte Exemplar liegt
    # im Lager (available, nicht distributed) → trotzdem series_already_lent
    # (du hast die Reihe schon, kein zweites Exemplar noetig).
    res = _eval(_State(_FakeIserv(_book(isbn="978-9"))), set(), {"978-9"})
    assert res["ok"] is False and res["status"] == "series_already_lent"


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
    info = {
        "books": [
            {"isbn": "A", "status": "vorgemerkt"},
            {"isbn": "B", "status": "ausgeliehen"},
            {"isbn": "", "status": "vorgemerkt"},  # leere ISBN wird ignoriert
        ]
    }
    vormerk, lent, lent_codes = sessions.booking_isbn_sets_from_info(info)
    assert vormerk == {"A"} and lent == {"B"}
    assert lent_codes == set()  # kein current_books im Test-Payload


def test_lent_from_current_books_ignores_hidden_filter():
    """Eine ausgeblendete Reihe, die der Schüler bereits hat, muss trotzdem in
    `lent` stehen — `apply_hidden_books` entfernt sie nur aus `info["books"]`,
    nicht aus `info["current_books"]`. Sonst würde ein Scan des eigenen Exemplars
    als „verliehen an jemand anderes" (`not_in_stock`) statt „an dich selbst
    verliehen" deklariert. `lent_codes` wird ebenfalls aus `current_books` gebaut."""
    info = {
        # `info["books"]` OHNE ISBN X — simuliert apply_hidden_books (X ausgeblendet)
        "books": [{"isbn": "A", "status": "vorgemerkt"}],
        # `current_books` ist die ungefilterte Quelle: X ist darin (Schüler hat X)
        "current_books": [{"isbn": "X", "code": "CX"}, {"isbn": "B", "code": "CB"}],
    }
    vormerk, lent, lent_codes = sessions.booking_isbn_sets_from_info(info)
    assert vormerk == {"A"}
    assert lent == {"X", "B"}  # X bleibt trotz Ausblendung in lent
    assert lent_codes == {"CX", "CB"}


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
    res = _process(state, 42, {"978-1"}, set())
    assert res["status"] == "booked"
    assert worker.committed == "B1" and worker.staged is None


def test_process_scan_booked_isbn_moves_to_lent(monkeypatch):
    """Nach einer Buchung in derselben Session muss die ISBN von vormerk nach
    lent umgehängt werden — sonst würde ein erneuter Scan (das Exemplar ist
    jetzt `distributed` an den Schüler selbst) als „verliehen an jemand anderes"
    (`not_in_stock`, loaned_to = Schüler selbst) statt als „an dich selbst
    verliehen" deklariert, weil `lent_isbns` noch aus der Lade-Zeit stammt.
    Der Code wandert ebenfalls in `lent_codes`, damit der erneute Scan
    desselben Exemplars als `book_already_lent` (nicht `series_already_lent`)
    erkannt wird. Die übergebenen Sets sind die Session-Mutables."""
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    vormerk = {"978-1"}
    lent: set[str] = set()
    lent_codes: set[str] = set()
    # 1. Scan: buchbar (vorgemerkt, im Lager) → wird gebucht.
    state = _State(_FakeIserv(_book(isbn="978-1", available=True, distributed=False)), _SpyWorker())
    res = _process(state, 42, vormerk, lent, lent_codes=lent_codes, source="helper")
    assert res["status"] == "booked"
    assert "978-1" in lent and "978-1" not in vormerk  # ISBN umgehängt
    assert "B1" in lent_codes  # Code ebenfalls gemerkt
    # 2. Scan: dasselbe Exemplar erneut gescannt (jetzt an den Schüler selbst
    # verliehen, distributed) → book_already_lent (exakt dieses Exemplar).
    state2 = _State(
        _FakeIserv(_book(isbn="978-1", available=False, distributed=True)), _SpyWorker()
    )
    res2 = _process(state2, 42, vormerk, lent, lent_codes=lent_codes, source="helper")
    assert res2["status"] == "book_already_lent"


def test_process_scan_stages_when_gate_off(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    worker = _SpyWorker()
    state = _State(_FakeIserv(_book()), worker)
    res = _process(state, 42, {"978-1"}, set())
    assert res["status"] == "staged"
    assert worker.staged == "B1" and worker.committed is None


def test_process_scan_no_field_touch_when_conditions_fail(monkeypatch):
    # Gate an, aber Buch nicht im Lager → weder Buchung noch Staging (kein Feldkontakt).
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(True))
    worker = _SpyWorker()
    state = _State(_FakeIserv(_book(distributed=True)), worker)
    res = _process(state, 42, {"978-1"}, set())
    assert res["status"] == "not_in_stock"
    assert worker.committed is None and worker.staged is None


class _FakeHub:
    def __init__(self):
        self.broadcasts = []

    async def broadcast_host(self, payload):
        self.broadcasts.append(payload)


def _patch_hub(monkeypatch):
    hub = _FakeHub()
    monkeypatch.setattr(sessions, "get_hub", lambda: hub)
    return hub


def test_process_scan_broadcasts_alert_for_not_in_stock(monkeypatch):
    # „An jemand anderen verliehen" → Host-Alert mit source (wie ausgemustert).
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book(distributed=True)))
    res = _process(state, 42, {"978-1"}, set(), source="helper")
    assert res["status"] == "not_in_stock"
    assert len(hub.broadcasts) == 1
    alert = hub.broadcasts[0]
    assert alert["type"] == "book_alert"
    assert alert["kind"] == "not_in_stock"
    assert alert["source"] == "helper"
    assert alert["student_id"] == 42


def test_process_scan_no_alert_for_series_already_lent(monkeypatch):
    # „An sich selbst verliehen" (anderes Exemplar der Reihe) → nur Hinweis am
    # Client, kein Host-Alert.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book()))
    res = _process(state, 42, set(), {"978-1"}, source="helper")
    assert res["status"] == "series_already_lent"
    assert hub.broadcasts == []


def test_process_scan_result_carries_title_for_already_lent(monkeypatch):
    # Client-Statuszeile/-Modal brauchen den Buchtitel (nicht nur `msg`, das
    # ist die längere technische Server-Meldung) — process_scan muss ihn im
    # scan_result-Payload durchreichen, für beide already-lent-Fälle.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book(code="B1")))
    res_book = _process(state, 42, set(), {"978-1"}, lent_codes={"B1"}, source="helper")
    assert res_book["status"] == "book_already_lent"
    assert res_book["title"] == "Mathe 5"
    res_series = _process(state, 42, set(), {"978-1"}, source="helper")
    assert res_series["status"] == "series_already_lent"
    assert res_series["title"] == "Mathe 5"


def test_process_scan_no_alert_for_book_already_lent(monkeypatch):
    # „Dieses Buch bereits an dich verliehen" (exakt dasselbe Exemplar) →
    # ebenfalls nur Hinweis am Client, kein Host-Alert.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book(code="B1")))
    res = _process(state, 42, set(), {"978-1"}, lent_codes={"B1"}, source="helper")
    assert res["status"] == "book_already_lent"
    assert hub.broadcasts == []


def _book_with_borrower():
    b = _book(distributed=True)
    b["loaned_to"] = "Max Mustermann"
    b["loaned_to_id"] = 4321
    return b


def test_process_scan_loaned_to_for_helper(monkeypatch):
    # Helfer-Scanner (Modus A) sieht den Ausleiher-Namen im scan_result; der
    # Host bekommt ihn immer via book_alert-Broadcast.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book_with_borrower()))
    res = _process(state, 42, {"978-1"}, set(), source="helper")
    assert res["status"] == "not_in_stock"
    assert res["loaned_to"] == "Max Mustermann"
    assert res["loaned_to_id"] == 4321
    assert hub.broadcasts and hub.broadcasts[0]["loaned_to"] == "Max Mustermann"
    assert hub.broadcasts[0]["loaned_to_id"] == 4321


def test_process_scan_hides_loan_from_student(monkeypatch):
    # Schüler-Client (Modus B) bekommt den Ausleiher-Namen NICHT (Privatheit);
    # der Host sieht ihn trotzdem (book_alert-Broadcast unabhängig vom Source).
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_book_with_borrower()))
    res = _process(state, 42, {"978-1"}, set())
    assert res["status"] == "not_in_stock"
    assert res["loaned_to"] is None
    assert res["loaned_to_id"] is None
    assert "Max Mustermann" not in (res.get("msg") or "")
    assert hub.broadcasts and hub.broadcasts[0]["loaned_to"] == "Max Mustermann"


def _deleted_book_with_borrower():
    b = _book(isbn="978-9", deleted=True)
    b["loaned_to"] = "Max Mustermann"
    b["loaned_to_id"] = 4321
    return b


def test_process_scan_deleted_alert_with_loaned_to_helper(monkeypatch):
    # Ausgemustert mit Schüler-Verknüpfung → book_alert-Broadcast mit
    # kind=="book_deleted" UND loaned_to (für Ersatzanspruch-Hinweis am Host).
    # Helfer-Scanner (Modus A) sieht den Namen im scan_result.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_deleted_book_with_borrower()))
    res = _process(state, 42, {"978-1"}, set(), source="helper")
    assert res["status"] == "book_deleted"
    assert res["loaned_to"] == "Max Mustermann"
    assert res["loaned_to_id"] == 4321
    assert len(hub.broadcasts) == 1
    alert = hub.broadcasts[0]
    assert alert["type"] == "book_alert"
    assert alert["kind"] == "book_deleted"
    assert alert["loaned_to"] == "Max Mustermann"
    assert alert["loaned_to_id"] == 4321


def test_process_scan_deleted_hides_loan_from_student(monkeypatch):
    # Schüler-Client (Modus B) sieht bei ausgemustertem Buch nur „ausgemustert"
    # — kein Ersatzanspruch-Hinweis, kein Name. Der Host bekommt den Namen
    # trotzdem über den book_alert-Broadcast.
    monkeypatch.setattr(sessions, "get_config", lambda: _Cfg(False))
    hub = _patch_hub(monkeypatch)
    state = _State(_FakeIserv(_deleted_book_with_borrower()))
    res = _process(state, 42, {"978-1"}, set())
    assert res["status"] == "book_deleted"
    assert res["loaned_to"] is None
    assert res["loaned_to_id"] is None
    assert "Max Mustermann" not in (res.get("msg") or "")
    assert hub.broadcasts and hub.broadcasts[0]["loaned_to"] == "Max Mustermann"
    assert hub.broadcasts[0]["kind"] == "book_deleted"
