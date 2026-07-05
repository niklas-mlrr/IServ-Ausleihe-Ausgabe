from __future__ import annotations

import logging

from .state import AppState

log = logging.getLogger(__name__)


def normalize_book_order(catalog_isbns: list[str], requested: list) -> list[str]:
    """Gewünschte Reihenfolge auf die Katalog-ISBNs beschränken (unbekannte/Dubletten
    raus) und fehlende Katalog-ISBNs in Katalogreihenfolge hinten anhängen, damit
    kein bestelltes Buch verloren geht."""
    catalog_set = set(catalog_isbns)
    seen: set[str] = set()
    order: list[str] = []
    for isbn in requested:
        if isinstance(isbn, str) and isbn in catalog_set and isbn not in seen:
            seen.add(isbn)
            order.append(isbn)
    for isbn in catalog_isbns:
        if isbn not in seen:
            order.append(isbn)
    return order


async def get_book_order_for_form(state: AppState, form: str) -> list[str]:
    """Bücher-Reihenfolge für einen einzelnen Schüler anhand seines eigenen
    Jahrgangs (`form`) — unabhängig von der aktiven Klasse (`state.book_order`).

    Nötig für klassenübergreifende Warteschlangen (einzeln hinzugefügte Schüler,
    „Test Config"), deren Schüler aus verschiedenen Jahrgängen stammen können:
    jeder Schüler bekommt die für seinen eigenen Jahrgang vorkonfigurierte
    Reihenfolge (`book_orders_by_grade`), nicht die der zufällig aktiven Klasse.

    `form_catalog_cache` erspart einen IServ-Roundtrip pro Zuweisung; Fallback
    auf `state.book_order` sowohl bei nicht ermittelbarem Jahrgang als auch bei
    IServ-Fehlern — ein Fehler hier darf `student_info` nie verhindern (der
    Aufrufer schickt sie danach direkt an den Helfer)."""
    if not form or state.iserv is None:
        return state.book_order
    cached = state.form_catalog_cache.get(form)
    if cached is None:
        try:
            grade, catalog = await state.iserv.get_class_book_catalog(form, state.selected_schoolyear)
        except Exception:
            log.exception("Jahrgangs-Katalog für Klasse %r konnte nicht geladen werden", form)
            return state.book_order
        cached = (grade, [b["isbn"] for b in catalog])
        state.form_catalog_cache[form] = cached
    grade, catalog_isbns = cached
    if grade is None:
        return state.book_order
    stored = state.book_orders_by_grade.get(grade)
    return normalize_book_order(catalog_isbns, stored) if stored else catalog_isbns


async def get_hidden_isbns_for_form(state: AppState, form: str) -> set[str]:
    """Ausgeblendete ISBNs (Einstellungen-Dialog) für den Jahrgang eines Schülers.

    Analog zu `get_book_order_for_form`: nutzt denselben `form_catalog_cache`
    für die Jahrgangs-Ermittlung, daher i. d. R. kein zusätzlicher IServ-
    Roundtrip, wenn `get_book_order_for_form` für dieselbe `form` bereits
    gerufen wurde. Leeres Set bei nicht ermittelbarem Jahrgang oder IServ-
    Fehlern — Ausblenden ist eine reine Anzeige-/Buchungsfilterung, darf
    `student_info` nie verhindern."""
    if not form or state.iserv is None:
        return set()
    cached = state.form_catalog_cache.get(form)
    if cached is None:
        try:
            grade, catalog = await state.iserv.get_class_book_catalog(form, state.selected_schoolyear)
        except Exception:
            log.exception("Jahrgangs-Katalog für Klasse %r konnte nicht geladen werden", form)
            return set()
        cached = (grade, [b["isbn"] for b in catalog])
        state.form_catalog_cache[form] = cached
    grade, _ = cached
    if grade is None:
        return set()
    return state.hidden_isbns_by_grade.get(grade, set())
