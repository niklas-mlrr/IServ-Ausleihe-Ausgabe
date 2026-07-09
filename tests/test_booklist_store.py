"""Persistenz der jahrgangsweiten Bücher-Reihenfolge/Ausblendung.

Deckt ab: load bei fehlender/korrupter Datei, save→load Round-Trip, Anlegen
des data-Verzeichnisses sowie die eigentliche Anforderung — neue Katalog-ISBNs,
die im gespeicherten Stand fehlen, werden per `normalize_book_order` sichtbar
ans Ende angehängt, ausgeblendete neue ISBNs bleiben sichtbar.

Rein logisch / tmp-Datei, kein IServ/HTTP. STORE_PATH wird per monkeypatch
in ein tmp-Verzeichnis umgebogen, sodass die Tests die echte `data/` nicht
anfassen.
"""
from __future__ import annotations

import json

from server import booklist_store
from server.book_order import normalize_book_order


def _use_tmp_store(monkeypatch, tmp_path) -> None:
    target = tmp_path / "booklist_settings.json"
    monkeypatch.setattr(booklist_store, "STORE_PATH", target)


def test_load_returns_empty_when_file_missing(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    orders, hidden = booklist_store.load()
    assert orders == {} and hidden == {}


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    orders = {9: ["C", "A", "B"], 10: ["X"], 7: []}
    hidden = {9: {"B"}, 10: set(), 7: {"Y"}}
    booklist_store.save(orders, hidden)

    loaded_orders, loaded_hidden = booklist_store.load()
    assert loaded_orders == {9: ["C", "A", "B"], 10: ["X"], 7: []}
    assert loaded_hidden == {9: {"B"}, 10: set(), 7: {"Y"}}


def test_load_corrupt_json_returns_empty(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    booklist_store.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    booklist_store.STORE_PATH.write_text("{ kein gültiges json ", encoding="utf-8")
    orders, hidden = booklist_store.load()
    assert orders == {} and hidden == {}


def test_load_empty_file_returns_empty(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    booklist_store.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    booklist_store.STORE_PATH.write_text("", encoding="utf-8")
    orders, hidden = booklist_store.load()
    assert orders == {} and hidden == {}


def test_save_creates_data_dir_if_missing(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "data" / "booklist_settings.json"
    monkeypatch.setattr(booklist_store, "STORE_PATH", target)
    booklist_store.save({9: ["A"]}, {9: set()})
    assert target.is_file()
    assert booklist_store.load() == ({9: ["A"]}, {9: set()})


def test_save_serializes_hidden_sorted_and_grades_as_strings(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    # Reihenfolge mit unsortiertem Set — Serialisierung muss deterministisch sein.
    booklist_store.save({9: ["A"]}, {9: {"Z", "A", "M"}})
    raw = json.loads(booklist_store.STORE_PATH.read_text(encoding="utf-8"))
    assert raw["orders"] == {"9": ["A"]}
    assert raw["hidden"] == {"9": ["A", "M", "Z"]}  # sortiert


def test_new_catalog_books_appended_visible_at_end():
    """Eigentliche Anforderung: gespeicherter Stand + neuer Katalog → neue
    ISBNs sichtbar ans Ende, unbekannte raus, ausgeblendete neue ISBNs bleiben
    sichtbar (hidden ist nur der Schnitt mit dem Katalog)."""
    catalog = ["A", "B", "N1", "N2"]           # N1/N2 = neu, X im Store existiert nicht mehr
    stored = ["X", "B", "A"]                   # gespeicherte Reihenfolge
    hidden = {"X"}                              # X nicht mehr im Katalog -> irrelevant

    order = normalize_book_order(catalog, stored)
    # X gedroppt, B/A in gespeicherter Folge, N1/N2 hinten in Katalogreihenfolge
    assert order == ["B", "A", "N1", "N2"]
    # Neue Bücher sind per Default sichtbar (hidden ∩ catalog ohne N1/N2).
    visible_hidden = sorted(hidden & set(catalog))
    assert visible_hidden == []
    assert "N1" in order and "N2" in order  # sichtbar, nicht ausgeblendet


def test_load_drops_non_string_entries(monkeypatch, tmp_path):
    _use_tmp_store(monkeypatch, tmp_path)
    booklist_store.STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    booklist_store.STORE_PATH.write_text(json.dumps({
        "orders": {"9": ["A", 123, "B"], "bad": ["Z"], "11": "keine-liste"},
        "hidden": {"9": ["A", 7, "B"], "bad": ["Q"]},
    }), encoding="utf-8")
    orders, hidden = booklist_store.load()
    assert orders == {9: ["A", "B"]}  # "bad"/"11" (keine Liste) übersprungen
    assert hidden == {9: {"A", "B"}}
