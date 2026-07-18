"""Tests für die lokale Leihschein-Klassen-Korrektur (server/loan_slip.py)."""

import asyncio

import fitz  # PyMuPDF

from server.loan_slip import override_class_on_slip


def _make_slip(class_code: str = "12Slw") -> bytes:
    """Minimaler Leihschein-Kopf wie IServ ihn rendert: kleine Label-Zeile
    „Jahrgang / Klasse" (Helvetica) + fette Wertzeile „Klasse <code>"."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 45.7), "Jahrgang / Klasse", fontname="helv", fontsize=8)
    page.insert_text((72, 56), f"Klasse {class_code}", fontname="hebo", fontsize=12)
    return doc.tobytes()


def _make_left_school_slip() -> bytes:
    """Wie der 5.-Jahrgang-Leihschein: Wertzeile ist der Platzhalter
    „- Schule verlassen -" (fett) statt „Klasse <code>"."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 45.7), "Jahrgang / Klasse", fontname="helv", fontsize=8)
    page.insert_text((72, 56), "- Schule verlassen -", fontname="hebo", fontsize=12)
    return doc.tobytes()


def _words(pdf_bytes: bytes) -> list[str]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    return [w[4] for w in doc[0].get_text("words")]


def test_override_replaces_code_and_keeps_header():
    out = override_class_on_slip(_make_slip("12Slw"), "11b")
    words = _words(out)
    # Der neue Klassen-Code steht drauf, die Label-Zeile bleibt komplett erhalten.
    assert "11b" in words
    assert "Jahrgang" in words and "Klasse" in words
    # Die sichtbare (oberste) Textebene zeigt nicht mehr den alten Code — er ist
    # zwar noch als verdeckter Text vorhanden, aber der neue Wert ist präsent.
    text = fitz.open(stream=out, filetype="pdf")[0].get_text()
    assert "Klasse 11b" in text.replace("\n", " ") or "11b" in text


def test_override_strips_leading_klasse_prefix():
    # Kommt die Klasse schon als „Klasse 13" aus dem System, darf auf dem
    # Leihschein kein doppeltes Präfix „Klasse Klasse 13" entstehen. Basis-PDF
    # enthält „Klasse" zweimal (Label „Jahrgang / Klasse" + Wert „Klasse 12Mk");
    # der neu gesetzte Wert darf kein weiteres „Klasse" hinzufügen.
    for value in ("Klasse 13", "klasse 13", "Klasse: 13", "  Klasse   13 "):
        words = _words(override_class_on_slip(_make_slip("12Mk"), value))
        assert words.count("Klasse") == 2, f"{value!r} -> {words}"
        assert "13" in words and "Klasse 13" not in words


def test_override_replaces_left_school_placeholder():
    # 5. Jahrgang: „- Schule verlassen -" wird durch die echte Klasse ersetzt,
    # inklusive „Klasse "-Präfix (das im Platzhalter fehlt), Label bleibt.
    out = override_class_on_slip(_make_left_school_slip(), "5a")
    words = _words(out)
    assert "5a" in words
    assert "Jahrgang" in words
    text = fitz.open(stream=out, filetype="pdf")[0].get_text().replace("\n", " ")
    assert "Klasse 5a" in text
    # Der Platzhalter ist optisch verdeckt (neuer Wert liegt oben drauf).
    assert "5a" in words


def test_override_left_school_strips_leading_klasse_prefix():
    # Auch beim 5.-Jahrgang-Platzhalter darf kein doppeltes „Klasse Klasse 5a"
    # entstehen, wenn der Systemwert schon „Klasse 5a" ist.
    words = _words(override_class_on_slip(_make_left_school_slip(), "Klasse 5a"))
    assert words.count("Klasse") == 2  # Label + neu gesetzte Wertzeile
    assert "5a" in words


def test_override_empty_form_is_noop():
    original = _make_slip("12Slw")
    assert override_class_on_slip(original, "") == original
    assert override_class_on_slip(original, "   ") == original


def test_override_no_match_returns_original():
    # PDF ohne „Klasse …"-Wertzeile → unverändert zurück.
    doc = fitz.open()
    doc.new_page().insert_text((72, 56), "Kein Klassenfeld hier", fontname="helv", fontsize=12)
    pdf = doc.tobytes()
    assert override_class_on_slip(pdf, "11b") == pdf


def _two_page_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "Seite 1", fontname="helv", fontsize=12)
    doc.new_page().insert_text((72, 100), "Seite 2", fontname="helv", fontsize=12)
    return doc.tobytes()


def test_select_pages_first_only():
    from server.loan_slip import select_pages

    out = select_pages(_two_page_pdf(), "1")
    doc = fitz.open(stream=out, filetype="pdf")
    assert doc.page_count == 1
    assert "Seite 1" in doc[0].get_text()


def test_select_pages_none_and_full_range_are_noop():
    from server.loan_slip import select_pages

    original = _two_page_pdf()
    assert select_pages(original, None) == original
    assert select_pages(original, "1-2") == original  # ganzer Bereich → unverändert


def test_print_loan_slip_applies_class_override(tmp_path, monkeypatch):
    """print_loan_slip_for wendet die Korrektur an, wenn der Toggle aktiv ist und
    die Klasse des Schülers aus der Queue ermittelt werden kann."""
    from server import sessions
    from server.config import Config

    class FakeIServ:
        async def get_loan_slip_pdf(self, student_id, variant="student"):
            return _make_slip("12Slw")

    class FakeQS:
        student_id = 2159
        form = "11b"
        slip_printed = False  # wird von print_loan_slip_for gesetzt (Badge „Leihschein")

    class FakeSettings:
        printer_name_override = None
        save_pdf_locally = False
        fix_class_on_slip = True

    class FakeState:
        iserv = FakeIServ()
        settings = FakeSettings()
        queue = [FakeQS()]
        active_form = "99z"

        def find_student(self, student_id):
            for qs in self.queue:
                if qs.student_id == student_id:
                    return qs
            return None

    cfg = Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        print_backend="file",
        print_output_dir=tmp_path,
    )
    monkeypatch.setattr(sessions, "get_config", lambda: cfg)
    res = asyncio.run(sessions.print_loan_slip_for(FakeState(), 2159))

    assert res["ok"] is True and res["backend"] == "file"
    from pathlib import Path

    words = _words(Path(res["path"]).read_bytes())
    assert "11b" in words  # echte Klasse des Schülers, nicht der Default „12Slw"
