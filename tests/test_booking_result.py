"""Unit-Tests für ``StudentSession._read_booking_result`` (automation/worker.py).

Diese Methode entscheidet nach einem Enter-Submit (echte Buchung gegen
Produktion — nur im freigegebenen Buchungstest erreichbar), ob das Ergebnis
`booked`, `error` oder `unknown` war. Die Selektoren sind im Code als
UNVERIFIZIERT markiert; der dokumentierte Bug (Typeahead-Eingabefeld enthält
den Barcode nach ``fill()`` noch als Wert und würde ohne den ``has_not``-Filter
fälschlich als „Buchung erfolgreich" gelesen) ist der wichtigste Test hier.

Kein echter Browser — ein minimaler Fake bildet nur die Teilmenge der
Playwright-Locator-API nach, die ``_read_booking_result`` tatsächlich nutzt:
``page.locator(sel)``, ``.count()``, ``.first``, ``.is_visible()``,
``.inner_text()``, ``.filter(has_not=...)``, ``.nth(i)``.

Struktur an ``automation/out/06b_kartei_geladen.html`` orientiert (gitignored,
daher nur als Schnipsel hier übernommen): Bücherzeilen liegen in
``<table class="table"><tbody><tr ng-repeat="book in bl.books ...">`` mit dem
Buchcode in einem ``<samp>``-Kindelement; das Typeahead-Feld ist
``input.tt-input`` mit ``tt-hint``-Begleitelement.
"""

from __future__ import annotations

import asyncio

from automation.worker import StudentSession


class _FakeElement:
    """Ein DOM-Knoten: eine Menge von „Selektor-Etiketten" (welche Locator-
    Queries ihn treffen würden), Text, Sichtbarkeit und optionale Kinder
    (für die ``has_not``-Containment-Prüfung in ``filter``)."""

    def __init__(self, selectors, text: str = "", visible: bool = True, children=None) -> None:
        self.selectors = set(selectors)
        self.text = text
        self.visible = visible
        self.children = children or []

    def contains_match(self, selector_set: set[str]) -> bool:
        for child in self.children:
            if child.selectors & selector_set:
                return True
            if child.contains_match(selector_set):
                return True
        return False


class _FakeLocator:
    def __init__(self, elements: list[_FakeElement], *, raise_on_count: bool = False,
                 query_set: set[str] | None = None) -> None:
        self._elements = elements
        self._raise_on_count = raise_on_count
        self._query_set = query_set or set()

    async def count(self) -> int:
        if self._raise_on_count:
            raise RuntimeError("Locator-Transportfehler (simuliert)")
        return len(self._elements)

    @property
    def first(self) -> _FakeLocator:
        return _FakeLocator(self._elements[:1], raise_on_count=self._raise_on_count)

    def nth(self, i: int) -> _FakeLocator:
        return _FakeLocator([self._elements[i]], raise_on_count=self._raise_on_count)

    def filter(self, has_not: _FakeLocator | None = None) -> _FakeLocator:
        if has_not is None:
            kept = list(self._elements)
        else:
            excl = has_not._query_set
            kept = [e for e in self._elements if not e.contains_match(excl)]
        return _FakeLocator(kept, raise_on_count=self._raise_on_count, query_set=self._query_set)

    async def is_visible(self) -> bool:
        return bool(self._elements) and self._elements[0].visible

    async def inner_text(self) -> str:
        return self._elements[0].text


class _FakePage:
    """Flache Elementliste; ``locator(sel)`` matcht per Selektor-Etikett
    (kein echter CSS-Parser — die Elemente tragen einfach die exakten
    Selektor-Strings, die ``_read_booking_result`` verwendet, als Etikett)."""

    def __init__(self, elements: list[_FakeElement], *, raise_on_selectors: set[str] | None = None) -> None:
        self._elements = elements
        self._raise_on_selectors = raise_on_selectors or set()

    def locator(self, sel: str) -> _FakeLocator:
        criteria = {s.strip() for s in sel.split(",")}
        matched = [e for e in self._elements if e.selectors & criteria]
        raise_flag = bool(criteria & self._raise_on_selectors)
        return _FakeLocator(matched, raise_on_count=raise_flag, query_set=criteria)


def _session(page: _FakePage) -> StudentSession:
    return StudentSession(context=None, page=page, domain="example.test",
                           student_id=42, student_name="Test, Tina")


# ---------------------------------------------------------------------------
# 1) Sichtbarer Fehlerhinweis → status "error", Text auf 200 Zeichen gekürzt.
# ---------------------------------------------------------------------------

def test_visible_error_alert_yields_error_status_truncated():
    long_msg = "Buch bereits verliehen an jemand anderes. " * 10  # > 200 Zeichen
    page = _FakePage([_FakeElement({".alert-danger"}, text=long_msg, visible=True)])
    session = _session(page)

    result = asyncio.run(session._read_booking_result("0017798"))

    assert result["status"] == "error"
    assert result["msg"] == long_msg.strip()[:200]
    assert len(result["msg"]) == 200


def test_invisible_error_alert_is_ignored():
    """Ein `.alert-danger`-Element, das (noch) nicht sichtbar ist (z. B. ein
    Angular-Template-Rest), darf nicht als Fehler gewertet werden."""
    page = _FakePage([_FakeElement({".alert-danger"}, text="Fehler", visible=False)])
    session = _session(page)

    result = asyncio.run(session._read_booking_result("0017798"))

    assert result["status"] != "error"


# ---------------------------------------------------------------------------
# 2) Barcode taucht in einer echten Bücherzeile auf → status "booked".
# ---------------------------------------------------------------------------

def test_barcode_in_book_row_yields_booked():
    barcode = "0017798"
    row = _FakeElement(
        {"table tbody tr", '[ng-repeat*="book"]'},
        text=f"Vamos! Adelante! 4 {barcode} 15.09.25 14:09",
        visible=True,
    )
    page = _FakePage([row])
    session = _session(page)

    result = asyncio.run(session._read_booking_result(barcode))

    assert result["status"] == "booked"


# ---------------------------------------------------------------------------
# 3) DER dokumentierte False-Positive (wichtigster Test): Barcode steht NUR im
# Typeahead-Eingabefeld (input.tt-input), das ihn nach fill() noch als Wert
# trägt — in keiner Bücherzeile. Der has_not-Filter muss verhindern, dass eine
# Zeile, die (fälschlich) das Eingabefeld als Kind enthält, mitgezählt wird.
# Erwartung: "unknown", NICHT "booked".
# ---------------------------------------------------------------------------

def test_barcode_only_in_typeahead_input_does_not_count_as_booked():
    barcode = "0017798"
    tt_input = _FakeElement({"input.tt-input"}, text=barcode, visible=True)
    # Ein Container, der `table tbody tr` UND `[ng-repeat*="book"]` matcht,
    # aber in Wahrheit nur das Eingabefeld umschließt (z. B. ein zu weiter
    # Selektor, der versehentlich einen Wrapper statt einer echten Zeile
    # trifft) — genau der Fall, den has_not=input_scope abfangen muss.
    fragile_row = _FakeElement(
        {"table tbody tr", '[ng-repeat*="book"]'},
        text=barcode, visible=True, children=[tt_input],
    )
    page = _FakePage([tt_input, fragile_row])
    session = _session(page)

    result = asyncio.run(session._read_booking_result(barcode))

    assert result["status"] == "unknown", (
        "Barcode nur im Typeahead-Feld darf NICHT als Buchungserfolg gelten "
        "(dokumentierter False-Positive in worker.py)"
    )


# ---------------------------------------------------------------------------
# 4) Weder Fehlermeldung noch Barcode auffindbar → status "unknown".
# ---------------------------------------------------------------------------

def test_nothing_found_yields_unknown():
    page = _FakePage([])
    session = _session(page)

    result = asyncio.run(session._read_booking_result("0017798"))

    assert result["status"] == "unknown"


# ---------------------------------------------------------------------------
# 5) Eine Locator-Exception (count() wirft) darf nicht durchschlagen.
# ---------------------------------------------------------------------------

def test_locator_exception_does_not_propagate_and_yields_unknown():
    page = _FakePage([], raise_on_selectors={"table tbody tr"})
    session = _session(page)

    # Wirft NICHT — die Exception wird intern gefangen.
    result = asyncio.run(session._read_booking_result("0017798"))

    assert result["status"] == "unknown"
