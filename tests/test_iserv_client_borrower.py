"""`get_book_by_code()` — Ausleiher-Auflösung (Name/Klasse).

`loaned_to`/`loaned_to_id` gab es schon; hier neu getestet:
`loaned_to_firstname`/`loaned_to_lastname` (getrennte Felder statt nur des
zusammengesetzten `loaned_to`-Strings) und `loaned_to_form` (Klasse), die
NUR bei ausgemusterten Büchern mit Schüler-Verknüpfung (Ersatzanspruch-Fall)
per zusätzlichem Request aufgelöst wird — bei allen anderen Büchern bleibt
sie `None`, ohne dass ein Extra-Request feuert (Effizienz-Garantie).
"""

from __future__ import annotations

import asyncio

from ausleihe.exceptions import NotFoundError

from server import iserv_client as iserv_client_module
from server.iserv_client import IsServClient


class _FakeSeries:
    def __init__(self, isbn, title, subject):
        self.isbn = isbn
        self.title = title
        self.subjects_flat = [subject]
        self.subjects = [subject]


class _FakeSeriesEndpoint:
    def get_all(self):
        return [_FakeSeries("111", "Mathe 9", "Mathematik")]


class _FakeEmbeddedStudent:
    def __init__(self, firstname, lastname):
        self.firstname = firstname
        self.lastname = lastname


class _FakeBook:
    def __init__(
        self,
        code="B1",
        isbn="111",
        available=True,
        distributed=False,
        deleted=False,
        student_id=None,
        student=None,
    ):
        self.code = code
        self.isbn = isbn
        self.available = available
        self.distributed = distributed
        self.deleted = deleted
        self.student_id = student_id
        self.student = student


class _FakeBooksEndpoint:
    def __init__(self, book):
        self._book = book

    def get_by_code(self, code):
        if self._book is None:
            raise NotFoundError("not found")
        return self._book


class _FakeStudent:
    def __init__(self, firstname, lastname):
        self.firstname = firstname
        self.lastname = lastname


class _FakeStudentsEndpoint:
    def __init__(self, by_id=None, forms_by_id=None, raise_on_detail=False):
        self._by_id = by_id or {}
        self._forms_by_id = forms_by_id or {}
        self._raise_on_detail = raise_on_detail

    def get_by_id(self, sid):
        return self._by_id[sid]

    def get_detail(self, sid, forms=False):
        if self._raise_on_detail:
            raise RuntimeError("boom")
        return {"forms": self._forms_by_id.get(sid, [])}


class _FakeAusleiheClient:
    def __init__(self, domain, username, password, allow_writes=False, book=None, students=None):
        self.series = _FakeSeriesEndpoint()
        self.books = _FakeBooksEndpoint(book)
        self.students = students or _FakeStudentsEndpoint()


def _make_client(monkeypatch, book, students=None):
    def factory(domain, username, password, allow_writes=False):
        return _FakeAusleiheClient(domain, username, password, allow_writes, book, students)

    monkeypatch.setattr(iserv_client_module, "AusleiheClient", factory)
    return IsServClient("d", "u", "p")


def test_borrower_name_split_from_embedded_student(monkeypatch):
    # Eingebettete Student-Struktur in /books/:code → kein Extra-Request nötig.
    book = _FakeBook(
        distributed=True, student_id=99, student=_FakeEmbeddedStudent("Max", "Mustermann")
    )
    client = _make_client(monkeypatch, book)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to"] == "Max Mustermann"
    assert result["loaned_to_firstname"] == "Max"
    assert result["loaned_to_lastname"] == "Mustermann"
    assert result["loaned_to_id"] == 99


def test_borrower_name_falls_back_to_get_by_id(monkeypatch):
    # Keine eingebettete Student-Struktur → Nachladen via get_by_id.
    book = _FakeBook(distributed=True, student_id=99, student=None)
    students = _FakeStudentsEndpoint(by_id={99: _FakeStudent("Erika", "Musterfrau")})
    client = _make_client(monkeypatch, book, students)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to"] == "Erika Musterfrau"
    assert result["loaned_to_firstname"] == "Erika"
    assert result["loaned_to_lastname"] == "Musterfrau"


def test_loaned_to_form_none_for_normal_lent_book(monkeypatch):
    # Nicht ausgemustert, nur verliehen (not_in_stock-Fall) → loaned_to_form
    # bleibt None, KEIN Extra-Request für die Klasse (Effizienz-Garantie).
    book = _FakeBook(
        distributed=True, deleted=False, student_id=99,
        student=_FakeEmbeddedStudent("Max", "Mustermann"),
    )
    # get_detail würde crashen, wenn es aufgerufen würde — beweist, dass es
    # für den Nicht-ausgemustert-Fall nicht aufgerufen wird.
    students = _FakeStudentsEndpoint(raise_on_detail=True)
    client = _make_client(monkeypatch, book, students)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to_form"] is None


def test_loaned_to_form_resolved_for_deleted_book_with_student(monkeypatch):
    # Ausgemustert MIT Schüler-Verknüpfung (Ersatzanspruch) → loaned_to_form
    # wird per Zusatz-Request aufgelöst, aktuellstes Schuljahr (höchste id) gewinnt.
    book = _FakeBook(
        deleted=True, student_id=99,
        student=_FakeEmbeddedStudent("Max", "Mustermann"),
    )
    students = _FakeStudentsEndpoint(forms_by_id={
        99: [
            {"schoolyear": 1, "name": "8a"},
            {"schoolyear": 2, "name": "9a"},
        ]
    })
    client = _make_client(monkeypatch, book, students)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to_form"] == "9a"


def test_loaned_to_form_none_when_form_lookup_fails(monkeypatch):
    # Klasse ist Kosmetik, nie fatal — Fehler beim Zusatz-Request darf die
    # restliche Buch-Auflösung nicht kippen.
    book = _FakeBook(deleted=True, student_id=99, student=_FakeEmbeddedStudent("Max", "Mustermann"))
    students = _FakeStudentsEndpoint(raise_on_detail=True)
    client = _make_client(monkeypatch, book, students)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to_form"] is None
    assert result["loaned_to"] == "Max Mustermann"  # Rest bleibt unberührt


def test_loaned_to_form_none_without_student_id(monkeypatch):
    # Ausgemustert, aber gar keine Schüler-Verknüpfung → kein Ersatzanspruch,
    # loaned_to bleibt None, kein Zusatz-Request.
    book = _FakeBook(deleted=True, student_id=None, student=None)
    students = _FakeStudentsEndpoint(raise_on_detail=True)
    client = _make_client(monkeypatch, book, students)
    result = asyncio.run(client.get_book_by_code("B1"))
    assert result["loaned_to"] is None
    assert result["loaned_to_form"] is None
