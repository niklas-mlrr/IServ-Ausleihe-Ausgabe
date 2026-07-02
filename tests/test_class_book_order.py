"""Klassenweite Bücher-Reihenfolge für den Scanner.

Deckt ab: Katalog-Aggregation (Union über die Klasse, Dedupe, Default-Sortierung),
die Reihenfolge-Normalisierung des POST-Endpoints und den State-Reset.
Rein logisch — kein echter IServ/HTTP.
"""

from __future__ import annotations

import asyncio

from server.iserv_client import IsServClient
from server.routes.api import normalize_book_order
from server.state import AppState


# ---------------------------------------------------------------------------
# get_class_book_catalog — mit gefälschten Client-Internals
# ---------------------------------------------------------------------------

class _Series:
    def __init__(self, title, subjects):
        self.title = title
        self.subjects_flat = subjects
        self.subjects = subjects


class _FakeStudents:
    def __init__(self, details):
        self._details = details

    def get_detail(self, sid, **kw):
        return self._details[sid]


class _FakeClient:
    def __init__(self, forms, details):
        self._forms = forms
        self.students = _FakeStudents(details)

    def get(self, path, **kw):
        return self._forms  # nur der /forms-Endpunkt wird genutzt


def _catalog(client, form="9a", sy="2025/2026"):
    c = IsServClient("d", "u", "p")
    c._client = client                 # _get_client() gibt diesen zurück
    c._series_map = {                  # _get_series_map() überspringt den Fetch
        "A": _Series("Mathe", ["Mathematik"]),
        "B": _Series("Deutsch", ["Deutsch"]),
        "C": _Series("Bio", ["Biologie"]),
    }
    return asyncio.run(c.get_class_book_catalog(form, sy))


def test_catalog_unions_dedupes_and_sorts():
    forms = [{"name": "9a", "members": [{"id": 1}, {"id": 2}]}]
    details = {
        1: {"enrollments": [{"schoolyear": "2025/2026", "booklistItems": [
            {"series": "A"}, {"series": "B"}]}]},
        2: {"enrollments": [{"schoolyear": "2025/2026", "booklistItems": [
            {"series": "B"}, {"series": "C"}]}]},   # B ist Dublette
    }
    cat = _catalog(_FakeClient(forms, details))
    # Sortiert nach (subject, title): Biologie(C), Deutsch(B), Mathematik(A)
    assert [b["isbn"] for b in cat] == ["C", "B", "A"]
    assert cat[0] == {"isbn": "C", "title": "Bio", "subject": "Biologie"}


def test_catalog_skips_students_without_enrollment_this_year():
    forms = [{"name": "9a", "members": [{"id": 1}, {"id": 2}]}]
    details = {
        1: {"enrollments": [{"schoolyear": "2024/2025", "booklistItems": [{"series": "A"}]}]},
        2: {"enrollments": [{"schoolyear": "2025/2026", "booklistItems": [{"series": "B"}]}]},
    }
    cat = _catalog(_FakeClient(forms, details))
    assert [b["isbn"] for b in cat] == ["B"]  # Schüler 1 (falsches Jahr) ignoriert


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
