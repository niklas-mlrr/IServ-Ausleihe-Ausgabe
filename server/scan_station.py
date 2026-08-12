"""Scan-Station-Zettel: Code-39-Barcode + A4-Blatt für Schüler ohne Handy.

Der Host druckt einem Schüler ohne eigenes Gerät einen A4-Zettel. Darauf steht
oben links Klasse + Name, oben rechts ein Code-39-Barcode (6,5 × 1,2 cm) mit
der vierstelligen Stationsnummer darunter, und im Blattkörper die Bücherliste:
bereits ausgeliehene Reihen zur Information, noch vorgemerkte mit einem
Kästchen zum Abhaken mit dem Stift. Mit diesem Zettel meldet sich der Schüler
an einer Scan-Station an (s. `server/routes/scan_station.py`).

Reines Bau-Modul: kein AppState, kein IServ-Kontakt, keine Nebenwirkungen —
Eingaben rein, PDF-Bytes raus. Der Import von PyMuPDF (`fitz`) passiert wie in
`loan_slip.py` lazy in der Funktion, damit der Rest der App nicht hart davon
abhängt.

Barcode-Typ: Code 39 (dieselbe Symbologie wie die Buch-Barcodes im Bestand,
s. `Code.PNG` im Repo-Root). Kodiert wird ausschließlich der vierstellige
Zifferncode zwischen den Start-/Stopp-Zeichen `*`; für Ziffern ist Code 39
identisch mit „Code 39 Full ASCII", eine Erweiterungskodierung ist also nicht
nötig. Andere Zeichen lehnt `encode_code39` bewusst ab, statt still etwas
Unlesbares zu drucken.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Code-39-Muster je Zeichen: 9 Elemente, abwechselnd Balken/Lücke, beginnend
# mit einem Balken. `False` = schmal, `True` = breit (genau 3 breite je Zeichen).
# Nur Ziffern + Start-/Stoppzeichen — mehr braucht der Stationscode nicht.
_PATTERNS: dict[str, str] = {
    "0": "nnnwwnwnn",
    "1": "wnnwnnnnw",
    "2": "nnwwnnnnw",
    "3": "wnwwnnnnn",
    "4": "nnnwwnnnw",
    "5": "wnnwwnnnn",
    "6": "nnwwwnnnn",
    "7": "nnnwnnwnw",
    "8": "wnnwnnwnn",
    "9": "nnwwnnwnn",
    "*": "nwnnwnwnn",
}

# Verhältnis breit:schmal. Code 39 erlaubt 2,0–3,0; 2,0 gibt bei fester
# Gesamtbreite die dicksten Balken und damit die beste Lesbarkeit.
_WIDE_RATIO = 2.0

# Zeichenzwischenraum: genau ein schmales weißes Modul.
_GAP_MODULES = 1.0

_START_STOP = "*"


def encode_code39(text: str) -> list[tuple[bool, float]]:
    """`text` als Code-39-Elementfolge kodieren (inkl. Start-/Stoppzeichen).

    Liefert eine Liste von ``(is_bar, module_width)`` in Zeichenreihenfolge:
    ``is_bar=True`` ist ein schwarzer Balken, ``False`` eine weiße Lücke;
    ``module_width`` ist die Breite in schmalen Modulen (1,0 bzw. `_WIDE_RATIO`).
    Die Summe aller Breiten ist die Gesamtbreite in Modulen — der Zeichner
    skaliert damit auf die gewünschte physische Breite.

    Wirft `ValueError` bei nicht kodierbaren Zeichen (alles außer Ziffern).
    """
    payload = (text or "").strip().strip(_START_STOP)
    if not payload:
        raise ValueError("Code 39: leerer Inhalt")
    unknown = sorted({c for c in payload if c not in _PATTERNS or c == _START_STOP})
    if unknown:
        raise ValueError(f"Code 39: nicht kodierbare Zeichen {unknown}")

    elements: list[tuple[bool, float]] = []
    chars = f"{_START_STOP}{payload}{_START_STOP}"
    for i, char in enumerate(chars):
        if i:
            elements.append((False, _GAP_MODULES))  # Zeichenzwischenraum
        for pos, kind in enumerate(_PATTERNS[char]):
            # Gerade Positionen sind Balken, ungerade Lücken (Muster beginnt
            # und endet mit einem Balken).
            elements.append((pos % 2 == 0, _WIDE_RATIO if kind == "w" else 1.0))
    return elements


def _draw_code39(page, text: str, *, x: float, y: float, width: float, height: float) -> None:
    """Barcode als Rechtecke in `page` zeichnen, exakt in `width` × `height`."""
    import fitz  # lazy, s. Modul-Docstring

    elements = encode_code39(text)
    total_modules = sum(w for _, w in elements)
    module = width / total_modules
    cursor = x
    for is_bar, module_width in elements:
        bar_width = module_width * module
        if is_bar:
            page.draw_rect(
                fitz.Rect(cursor, y, cursor + bar_width, y + height),
                color=None,
                fill=(0, 0, 0),
            )
        cursor += bar_width


# --- Blatt-Geometrie (Punkte; 1 cm = 28,3465 pt) ---------------------------

_CM = 72.0 / 2.54
_PAGE_W = 595.276  # A4 hoch
_PAGE_H = 841.89
_MARGIN = 42.0
_BARCODE_W = 6.5 * _CM  # 184,25 pt — vom Nutzer vorgegeben
_BARCODE_H = 1.2 * _CM  # 34,02 pt
_BARCODE_TOP = _MARGIN


def _book_line(book: dict) -> str:
    """„Fach · Titel" wie in den Clients (vgl. `web/student.js`).

    Trenner bewusst der Mittelpunkt (U+00B7) und nicht der Gedankenstrich der
    Clients: die PDF-Standardschriften (`helv`/`hebo`) kodieren Latin-1, ein
    En-Dash (U+2013) käme dort als Ersatzzeichen heraus.
    """
    subject = (book.get("subject") or "").strip()
    title = (book.get("title") or "").strip()
    return f"{subject} · {title}" if subject and title else (title or subject or "—")


def build_sheet_pdf(
    *,
    form: str | None,
    lastname: str | None,
    firstname: str | None,
    code: str,
    lent_books: list[dict],
    pending_books: list[dict],
) -> bytes:
    """A4-Zettel für die Scan-Station bauen und als PDF-Bytes zurückgeben.

    `lent_books` = bereits ausgeliehene Reihen (nur Auflistung), `pending_books`
    = noch vorgemerkte (jede Zeile mit Abhak-Kästchen). Beide Listen sind
    `info["books"]`-Einträge mit `subject`/`title` (s. `sessions.hydrate_student_info`).
    """
    import fitz  # lazy, s. Modul-Docstring

    from .loan_slip import _wrap_text  # gemeinsamer wortweiser Umbruch

    doc = fitz.open()
    page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
    helv = fitz.Font("helv")
    hebo = fitz.Font("hebo")

    # --- Kopf links: Klasse, dann „Nachname, Vorname" ---
    form_clean = (form or "").removeprefix("Klasse ").strip()
    name = ", ".join(p for p in ((lastname or "").strip(), (firstname or "").strip()) if p)
    if form_clean:
        page.insert_text((_MARGIN, _MARGIN + 12), f"Klasse {form_clean}",
                         fontname="helv", fontsize=13, color=0)
    page.insert_text((_MARGIN, _MARGIN + 34), name or "—",
                     fontname="hebo", fontsize=17, color=0)

    # --- Kopf rechts: Barcode + Nummer ---
    barcode_x = _PAGE_W - _MARGIN - _BARCODE_W
    _draw_code39(page, code, x=barcode_x, y=_BARCODE_TOP, width=_BARCODE_W, height=_BARCODE_H)
    code_size = 15
    code_w = hebo.text_length(code, code_size)
    page.insert_text(
        (barcode_x + (_BARCODE_W - code_w) / 2, _BARCODE_TOP + _BARCODE_H + code_size),
        code,
        fontname="hebo",
        fontsize=code_size,
        color=0,
    )

    # --- Trennlinie unter dem Kopf ---
    head_bottom = _BARCODE_TOP + _BARCODE_H + code_size + 16
    page.draw_line(
        fitz.Point(_MARGIN, head_bottom),
        fitz.Point(_PAGE_W - _MARGIN, head_bottom),
        color=(0, 0, 0),
        width=0.8,
    )

    # --- Bücherlisten ---
    y = head_bottom + 34
    body_size = 12
    line_height = body_size * 1.65
    box = 11.0  # Kantenlänge des Abhak-Kästchens
    text_x = _MARGIN + box + 10
    max_width = _PAGE_W - _MARGIN - text_x

    def section(title: str, books: list[dict], *, checkbox: bool) -> None:
        nonlocal y
        page.insert_text((_MARGIN, y), title, fontname="hebo", fontsize=13, color=0)
        y += 22
        if not books:
            page.insert_text((text_x, y), "—", fontname="helv", fontsize=body_size, color=0)
            y += line_height + 12
            return
        for book in books:
            lines = _wrap_text(helv, _book_line(book), body_size, max_width) or ["—"]
            if checkbox:
                # Kästchen auf Höhe der ersten Zeile (Grundlinie ist `y`).
                page.draw_rect(
                    fitz.Rect(_MARGIN, y - box + 2, _MARGIN + box, y + 2),
                    color=(0, 0, 0),
                    width=0.9,
                )
            for i, line in enumerate(lines):
                page.insert_text(
                    (text_x, y + i * line_height),
                    line,
                    fontname="helv",
                    fontsize=body_size,
                    color=0,
                )
            y += len(lines) * line_height
        y += 12

    section("Noch vorgemerkt", pending_books, checkbox=True)
    section("Bereits ausgeliehen", lent_books, checkbox=False)

    out = doc.tobytes()
    doc.close()
    log.info(
        "Scan-Station-Zettel gebaut: code=%s vorgemerkt=%d ausgeliehen=%d",
        code, len(pending_books), len(lent_books),
    )
    return out
