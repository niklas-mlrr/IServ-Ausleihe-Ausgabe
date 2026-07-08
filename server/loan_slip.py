"""Leihschein-Nachbearbeitung: die gedruckte Klasse lokal korrigieren.

Der IServ-Leihschein zeigt im Kopf zwei Zeilen::

    Jahrgang / Klasse          (kleiner Label-Text, Helvetica)
    Klasse 12Slw               (fetter Wert, Helvetica-Bold)

Der Wert nach „Klasse " stammt aus IServ und ist dort teils falsch hinterlegt.
`override_class_on_slip()` überschreibt **rein lokal** (auf den bereits
read-only geholten PDF-Bytes) den Code hinter „Klasse " mit der echten Klasse
aus dem Serverstate — kein Schreibzugriff auf IServ (CLAUDE.md / PLAN §6).

Sonderfall 5. Jahrgang: Dort steht in der Wertzeile statt „Klasse <code>" der
Platzhalter „- Schule verlassen -" (die Schüler waren im Bezugsjahr noch nicht
an der Schule). Auch dieser wird durch die echte, aktuelle Klasse ersetzt.

Technik: den fetten Wert-Span finden, den alten Code mit einem weißen Rechteck
verdecken (metrik-genau begrenzt, damit die Label-Zeile darüber unberührt
bleibt) und die echte Klasse in derselben Schrift/Größe neu setzen. Der alte
Text bleibt unter der Abdeckung liegen (unsichtbar) — es wird nichts an der
IServ-Quelle verändert.

Der Import von PyMuPDF (`fitz`) passiert bewusst lazy in der Funktion, damit der
Rest der App nicht hart davon abhängt.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Führendes „Klasse "/„Klasse:" im gelieferten Wert (manche System-Klassennamen
# kommen als „Klasse 13") — sonst entstünde auf dem Leihschein hinter dem dort
# schon vorhandenen „Klasse "-Präfix ein doppeltes „Klasse Klasse 13".
_LEADING_KLASSE = re.compile(r"^\s*Klasse\b[\s:]*", re.IGNORECASE)

# Fetter Wert beginnt mit diesem Präfix; danach folgt der zu ersetzende Code.
_PREFIX = "Klasse "

# Sonderfall 5. Jahrgang: Diese Schüler waren im Vorjahr (auf das sich der
# IServ-Leihschein bezieht) noch nicht an der Schule und damit in keiner Klasse.
# IServ druckt an der Stelle der Klassen-Wertzeile deshalb „- Schule verlassen -"
# statt „Klasse <code>". Auch das wird durch die echte, aktuelle Klasse ersetzt
# (dann als komplettes „Klasse <form>", da das „Klasse "-Präfix hier fehlt).
_LEFT_SCHOOL = re.compile(r"schule\s+verlassen", re.IGNORECASE)
# Kappenhöhe von Helvetica-Bold als Anteil der Schriftgröße. Bewusst NICHT die
# volle Font-Ascender-Metrik (die ist ~1.07 und ragt bis in die Label-Zeile
# darüber) — die Ober-/Unterkante der Abdeckung wird so eng um den Wert gelegt,
# dass die „Jahrgang / Klasse"-Zeile darüber garantiert unberührt bleibt.
_CAP = 0.72
_DESC = 0.28


def override_class_on_slip(pdf_bytes: bytes, form: str) -> bytes:
    """Klasse hinter „Klasse " auf dem Leihschein durch `form` ersetzen.

    Rein lokale PDF-Bearbeitung auf `pdf_bytes` (kein IServ-Write). Gibt die
    bearbeiteten PDF-Bytes zurück; bei Fehlern oder wenn kein passender Span
    gefunden wird, werden die **unveränderten** Original-Bytes zurückgegeben —
    ein Druck darf nie an dieser Korrektur scheitern.
    """
    form = (form or "").strip()
    # Ein bereits enthaltenes „Klasse "-Präfix entfernen, damit es nach dem
    # vorhandenen Präfix auf dem Leihschein nicht doppelt erscheint.
    form = _LEADING_KLASSE.sub("", form).strip() or form
    if not form:
        return pdf_bytes
    try:
        import fitz  # PyMuPDF — lazy, siehe Modul-Docstring

        font = fitz.Font("hebo")  # Helvetica-Bold, für Textbreite + Neusatz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        changed = 0
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = line["spans"]
                    # Wertzeile ist ein einzelner fetter Span „Klasse <code>";
                    # die Label-Zeile („Jahrgang / Klasse") ist nicht fett.
                    if len(spans) != 1:
                        continue
                    span = spans[0]
                    text = span["text"]
                    if "Bold" not in span["font"] or "Jahrgang" in text:
                        continue
                    # Zwei Wertzeilen-Formen: „Klasse <code>" (Normalfall) und
                    # „- Schule verlassen -" (5. Jahrgang, kein Klasse-Präfix).
                    if text.startswith(_PREFIX):
                        # Nur den Code hinter „Klasse " ersetzen — das Präfix
                        # bleibt stehen, der Neusatz beginnt dahinter.
                        prefix = _PREFIX
                        new_text = form
                    elif _LEFT_SCHOOL.search(text):
                        # Kein Präfix vorhanden → komplette „Klasse <form>" setzen.
                        prefix = ""
                        new_text = _PREFIX + form
                    else:
                        continue
                    x0, baseline = span["origin"]
                    size = span["size"]
                    code_x = x0 + font.text_length(prefix, size)
                    top = baseline - _CAP * size - 0.2
                    bottom = baseline + _DESC * size
                    right = max(
                        span["bbox"][2], code_x + font.text_length(new_text, size)
                    ) + 2
                    # Alten Wert verdecken (weiß) und echte Klasse neu setzen.
                    page.draw_rect(
                        fitz.Rect(code_x - 0.5, top, right, bottom),
                        color=None, fill=(1, 1, 1),
                    )
                    page.insert_text(
                        (code_x, baseline), new_text,
                        fontname="hebo", fontsize=size, color=0,
                    )
                    changed += 1
        if not changed:
            log.warning("Leihschein-Klasse: keine Klassen-Wertzeile gefunden - unveraendert")
            return pdf_bytes
        out = doc.tobytes()
        log.info("Leihschein-Klasse lokal auf %r gesetzt (%d Stelle(n))", form, changed)
        return out
    except Exception:  # noqa: BLE001 — Druck darf hieran nie scheitern
        log.exception("Leihschein-Klasse konnte nicht ersetzt werden — Original wird gedruckt")
        return pdf_bytes


def select_pages(pdf_bytes: bytes, pages: str | None) -> bytes:
    """PDF auf einen 1-basierten, inklusiven Seitenbereich beschränken.

    `pages` wie beim Druck: ``"1"`` (nur Seite 1), ``"1-2"`` (beide) oder ``None``
    (alle Seiten). Für den Host-Download, damit dort dieselben Seiten landen, die
    sonst gedruckt würden. Defensiv: bei ungültigem Bereich oder Fehler werden die
    unveränderten Original-Bytes zurückgegeben.
    """
    if not pages:
        return pdf_bytes
    try:
        import fitz  # PyMuPDF — lazy

        start, _, end = pages.partition("-")
        lo = int(start)
        hi = int(end) if end else lo
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        # 1-basiert/inklusiv → 0-basierte Indizes, auf gültigen Bereich geklemmt.
        idx = [n - 1 for n in range(lo, hi + 1) if 1 <= n <= doc.page_count]
        if not idx or len(idx) == doc.page_count:
            return pdf_bytes
        doc.select(idx)
        return doc.tobytes()
    except Exception:  # noqa: BLE001
        log.exception("Leihschein-Seitenauswahl (%r) fehlgeschlagen — ganzes PDF", pages)
        return pdf_bytes
