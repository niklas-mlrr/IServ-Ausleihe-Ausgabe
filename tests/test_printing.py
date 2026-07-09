"""Unit-Tests für die Druck-Backend-Auswahl (server/printing.py).

Backend-Resolution + `file`-Backend ohne echten Drucker / IServ.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import server.printing as printing


def test_resolve_explicit_backends():
    assert printing.resolve_backend("lp") == "lp"
    assert printing.resolve_backend("file") == "file"
    assert printing.resolve_backend("sumatra") == "sumatra"


def test_resolve_auto_per_platform(monkeypatch):
    monkeypatch.setattr(printing.platform, "system", lambda: "Windows")
    assert printing.resolve_backend("auto") == "sumatra"
    monkeypatch.setattr(printing.platform, "system", lambda: "Darwin")
    assert printing.resolve_backend("auto") == "lp"
    monkeypatch.setattr(printing.platform, "system", lambda: "Linux")
    assert printing.resolve_backend("auto") == "file"


def test_file_backend_writes_pdf(tmp_path):
    res = asyncio.run(
        printing.print_pdf(b"%PDF-1.4\ntest\n", backend="file", output_dir=tmp_path, label="probe")
    )
    assert res["ok"] is True
    assert res["backend"] == "file"
    path = Path(res["path"])
    assert path.is_file() and path.read_bytes().startswith(b"%PDF")


def test_print_loan_slip_for_reads_and_prints(tmp_path, monkeypatch):
    """Die gemeinsame Orchestrierung holt das PDF read-only und reicht es ans
    `file`-Backend durch (kein echter Drucker, kein IServ-Submit)."""
    from server import sessions
    from server.config import Config

    calls = {}

    class FakeIServ:
        async def get_loan_slip_pdf(self, student_id, variant="student"):
            calls["fetch"] = (student_id, variant)
            return b"%PDF-1.4\nslip\n"

    class FakeState:
        iserv = FakeIServ()
        printer_name_override = None
        save_pdf_locally = False

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

    # Read-only GET holt stets den 2-seitigen Beleg (Default-Variante).
    assert calls["fetch"] == (2159, "student-always_school-auto")
    assert res["ok"] is True and res["backend"] == "file"
    assert Path(res["path"]).read_bytes().startswith(b"%PDF")


def _cfg(tmp_path, **kw):
    from server.config import Config
    return Config(
        iserv_domain="example.org", iserv_username="u", iserv_password="p",
        host_password="secret", print_output_dir=tmp_path, **kw,
    )


class _FakeHostWS:
    """Minimaler Host-WebSocket-Stub, der gesendete JSON-Nachrichten sammelt."""
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)


def test_print_loan_slip_save_pdf_locally_pushes_download(tmp_path, monkeypatch):
    """`save_pdf_locally` druckt nicht, sondern pusht das PDF an alle verbundenen
    Host-Browser zum Download (auch wenn PRINT_BACKEND auf `lp` zeigt)."""
    import base64

    from server import sessions

    class FakeIServ:
        async def get_loan_slip_pdf(self, student_id, variant="student"):
            return b"%PDF-1.4\nslip\n"

    ws = _FakeHostWS()

    class FakeState:
        iserv = FakeIServ()
        printer_name_override = None
        save_pdf_locally = True
        fix_class_on_slip = False
        host_ws_connections = [ws]

    monkeypatch.setattr(sessions, "get_config", lambda: _cfg(tmp_path, print_backend="lp"))
    res = asyncio.run(sessions.print_loan_slip_for(FakeState(), 2159))

    assert res["ok"] is True and res["backend"] == "download"
    # Der Host-WS hat genau eine Download-Nachricht mit dem PDF (base64) bekommen.
    assert len(ws.sent) == 1
    dl = ws.sent[0]
    assert dl["type"] == "loan_slip_download"
    assert dl["filename"].startswith("leihschein_2159_") and dl["filename"].endswith(".pdf")
    assert base64.b64decode(dl["data_b64"]).startswith(b"%PDF")


def test_print_loan_slip_save_pdf_locally_falls_back_to_file(tmp_path, monkeypatch):
    """Ohne verbundenen Host-Browser kann nichts heruntergeladen werden — als
    Sicherheitsnetz landet das PDF im Ausgabeverzeichnis (`file`-Backend)."""
    from server import sessions

    class FakeIServ:
        async def get_loan_slip_pdf(self, student_id, variant="student"):
            return b"%PDF-1.4\nslip\n"

    class FakeState:
        iserv = FakeIServ()
        printer_name_override = None
        save_pdf_locally = True
        fix_class_on_slip = False
        host_ws_connections = []  # kein Host-Browser verbunden

    monkeypatch.setattr(sessions, "get_config", lambda: _cfg(tmp_path, print_backend="lp"))
    res = asyncio.run(sessions.print_loan_slip_for(FakeState(), 2159))

    assert res["ok"] is True and res["backend"] == "file"
    assert Path(res["path"]).read_bytes().startswith(b"%PDF")


def test_print_pdf_lp_passes_page_range(monkeypatch):
    """`pages` wird als CUPS-Seitenbereich an `lp` durchgereicht (Seite 1 only)."""
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd
        return 0, "ok"

    monkeypatch.setattr(printing, "_run", fake_run)
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lp")

    res = asyncio.run(printing.print_pdf(b"%PDF-1.4\n", backend="lp", pages="1"))
    assert res["ok"] is True and res["backend"] == "lp"
    assert "page-ranges=1" in captured["cmd"]


def test_print_pdf_lp_without_pages_prints_all(monkeypatch):
    """Ohne `pages` wird kein Seitenbereich gesetzt (alle Seiten)."""
    captured = {}

    async def fake_run(cmd):
        captured["cmd"] = cmd
        return 0, "ok"

    monkeypatch.setattr(printing, "_run", fake_run)
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lp")

    asyncio.run(printing.print_pdf(b"%PDF-1.4\n", backend="lp"))
    assert not any("page-ranges" in str(a) for a in captured["cmd"])
