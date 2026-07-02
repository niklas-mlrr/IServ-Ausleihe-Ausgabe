"""Klassenweite Bücher-Reihenfolge für den Scanner.

Deckt ab: Katalog-Aufbau aus der Jahrgangs-Bücherliste (borrowable-Filter, Dedupe,
Mehrjahresband-Filter, Default-Sortierung), die Reihenfolge-Normalisierung des
POST-Endpoints und den State-Reset. Rein logisch — kein echter IServ/HTTP.
"""

from __future__ import annotations

import asyncio

from server.iserv_client import IsServClient
from server.routes.api import normalize_book_order
from server.state import AppState


# ---------------------------------------------------------------------------
# get_class_book_catalog — mit gefälschter Jahrgangs-Bücherliste
# ---------------------------------------------------------------------------

def _item(isbn, title, subject, borrowable=True, multi=False, grades=None):
    """Ein Booklist-Item mit `series_data` (wie der /booklists/:id-Endpunkt liefert)."""
    return {
        "borrowable": borrowable,
        "series": isbn,
        "series_data": {
            "isbn": isbn, "title": title, "subjectsFlat": [subject],
            "isMultiYear": multi, "gradesFlat": grades or [],
        },
    }


def _booklist(items):
    return {"sections": [{"options": [{"items": items}]}]}


class _FakeSchoolyears:
    def __init__(self, booklists, full):
        self._booklists = booklists
        self._full = full   # booklist_id -> full booklist

    def get_booklists(self, sy):
        return self._booklists

    def get_booklist(self, sy, bid):
        return self._full[bid]


class _FakeClient:
    def __init__(self, forms, booklists, full):
        self._forms = forms
        self.schoolyears = _FakeSchoolyears(booklists, full)

    def get(self, path, **kw):
        return self._forms  # nur der /forms-Endpunkt wird direkt genutzt


def _catalog(forms, booklists, full, form="9a", sy="2025/2026"):
    c = IsServClient("d", "u", "p")
    c._client = _FakeClient(forms, booklists, full)  # _get_client()
    c._series_map = {}                               # series_data reicht → kein Fetch
    return asyncio.run(c.get_class_book_catalog(form, sy))


def test_catalog_from_booklist_filters_borrowable_dedupes_sorts():
    forms = [{"name": "9a", "grade": 9}]
    items = [
        _item("A", "Mathe", "Mathematik"),
        _item("B", "Deutsch Arbeitsheft", "Deutsch", borrowable=False),  # raus
        _item("C", "Bio", "Biologie"),
        _item("A", "Mathe", "Mathematik"),                               # Dublette
    ]
    cat = _catalog(forms, [{"grade": 9, "id": 100}], {100: _booklist(items)})
    # borrowable + dedupe, sortiert nach (subject, title): Biologie(C), Mathematik(A)
    assert [b["isbn"] for b in cat] == ["C", "A"]
    assert cat[0] == {"isbn": "C", "title": "Bio", "subject": "Biologie"}


def test_catalog_includes_multiyear_in_every_grade():
    # Mehrjahresband M (Jg. 7-8): die komplette ausleihbare Jahrgangsliste wird
    # gezeigt — der Band erscheint in BEIDEN Jahrgängen (kein min-grade-Filter).
    m = _item("M", "Bioskop 7/8", "Biologie", multi=True, grades=[8, 7])

    cat7 = _catalog([{"name": "7a", "grade": 7}], [{"grade": 7, "id": 7}],
                    {7: _booklist([_item("N", "Normal", "Deutsch"), m])}, form="7a")
    assert {b["isbn"] for b in cat7} == {"N", "M"}

    cat8 = _catalog([{"name": "8a", "grade": 8}], [{"grade": 8, "id": 8}],
                    {8: _booklist([_item("P", "Physik", "Physik"), m])}, form="8a")
    assert {b["isbn"] for b in cat8} == {"P", "M"}   # oberer Jg. → Band bleibt drin


def test_catalog_empty_when_no_booklist_for_grade():
    forms = [{"name": "9a", "grade": 9}]
    cat = _catalog(forms, [{"grade": 5, "id": 5}], {5: _booklist([_item("A", "X", "Y")])})
    assert cat == []


def test_catalog_empty_when_form_unknown():
    cat = _catalog([{"name": "8a", "grade": 8}], [{"grade": 8, "id": 8}],
                   {8: _booklist([_item("A", "X", "Y")])}, form="9z")
    assert cat == []


# ---------------------------------------------------------------------------
# normalize_book_order — Beschränkung auf Katalog + Anhängen fehlender
# ---------------------------------------------------------------------------

def test_normalize_keeps_requested_order_within_catalog():
    catalog = ["A", "B", "C"]
    assert normalize_book_order(catalog, ["C", "A", "B"]) == ["C", "A", "B"]


def test_normalize_drops_unknown_and_dupes_and_appends_missing():
    catalog = ["A", "B", "C"]
    # X unbekannt (raus), A doppelt (einmal), B fehlt in requested → hinten angehängt
    assert normalize_book_order(catalog, ["C", "X", "A", "A"]) == ["C", "A", "B"]


def test_normalize_empty_request_yields_catalog_order():
    assert normalize_book_order(["A", "B"], []) == ["A", "B"]


# ---------------------------------------------------------------------------
# State-Reset
# ---------------------------------------------------------------------------

def test_reset_clears_order_and_catalog():
    st = AppState()
    st.book_order = ["A", "B"]
    st.class_catalog = [{"isbn": "A"}]
    st.class_catalog_form = "9a"
    st.reset_class_book_order()
    assert st.book_order == [] and st.class_catalog == [] and st.class_catalog_form is None


def test_snapshot_includes_book_order():
    st = AppState()
    st.book_order = ["A", "B"]
    assert st.state_snapshot()["book_order"] == ["A", "B"]
