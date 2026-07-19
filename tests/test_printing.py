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

    class FakeSettings:
        printer_name_override = None
        save_pdf_locally = False

    class FakeState:
        iserv = FakeIServ()
        settings = FakeSettings()

        # Kein Queue-Eintrag → der Leihschein-Marker (Badge „Leihschein") wird
        # still übersprungen; hier geht es nur um den Druckpfad.
        def find_student(self, student_id):
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

    # Read-only GET holt stets den 2-seitigen Beleg (Default-Variante).
    assert calls["fetch"] == (2159, "student-always_school-auto")
    assert res["ok"] is True and res["backend"] == "file"
    assert Path(res["path"]).read_bytes().startswith(b"%PDF")


def _cfg(tmp_path, **kw):
    from server.config import Config

    return Config(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        print_output_dir=tmp_path,
        **kw,
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

    class FakeSettings:
        printer_name_override = None
        save_pdf_locally = True
        fix_class_on_slip = False

    class FakeState:
        iserv = FakeIServ()
        settings = FakeSettings()

        # Kein Queue-Eintrag → der Leihschein-Marker (Badge „Leihschein") wird
        # still übersprungen; hier geht es nur um den Druckpfad.
        def find_student(self, student_id):
            return None
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

    class FakeSettings:
        printer_name_override = None
        save_pdf_locally = True
        fix_class_on_slip = False

    class FakeState:
        iserv = FakeIServ()
        settings = FakeSettings()

        # Kein Queue-Eintrag → der Leihschein-Marker (Badge „Leihschein") wird
        # still übersprungen; hier geht es nur um den Druckpfad.
        def find_student(self, student_id):
            return None
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


def test_print_pdf_lp_returns_cups_job_handle(monkeypatch):
    """`lp` meldet „request id is <dest>-<n>" — das wird als `job_handle`
    durchgereicht, damit der Poller das physische Druckende erkennen kann."""
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lp")

    async def fake_run(cmd):
        return 0, "request id is HP-123"

    monkeypatch.setattr(printing, "_run", fake_run)
    res = asyncio.run(printing.print_pdf(b"%PDF-1.4\n", backend="lp"))
    assert res["ok"] is True
    assert res["job_handle"] == {"kind": "cups", "job_id": "HP-123"}


def test_print_pdf_file_has_no_job_handle(tmp_path):
    """`file`-Backend: kein physischer Drucker → `job_handle` None (sofort
    „gedruckt", kein OS-Polling)."""
    res = asyncio.run(
        printing.print_pdf(b"%PDF-1.4\n", backend="file", output_dir=tmp_path)
    )
    assert res["ok"] is True
    assert res["job_handle"] is None


def test_await_print_completion_none_is_immediate():
    """Ohne Handle (file/win-default) kehrt `await_print_completion` sofort
    zurück (True) — kein Polling."""
    assert asyncio.run(printing.await_print_completion(None)) is True


def test_read_job_state_none_is_absent():
    """Ohne Handle (file/win-default) liefert `read_job_state` sofort „absent"
    → der Tracker finalisiert zügig (kein OS-Polling möglich). Unbekanntes
    Handle ebenso."""
    assert asyncio.run(printing.read_job_state(None)) == "absent"
    assert asyncio.run(printing.read_job_state({"kind": "unbekannt"})) == "absent"


def test_read_job_state_cups(monkeypatch):
    """CUPS: Job-ID nicht mehr im `lpstat -o`-Output → absent; Zeile mit
    „active" → printing; sonst → spooled. `lpstat` fehlt → absent."""
    monkeypatch.setattr(printing.shutil, "which", lambda name: "/usr/bin/lpstat")

    seq = iter([
        (0, "HP-123  root  1024  active"),   # druckt gerade
        (0, "HP-123  root  1024  1st"),       # nur gespoolt
        (0, ""),                              # weg → fertig
    ])

    async def fake_run(cmd):
        return next(seq)

    monkeypatch.setattr(printing, "_run", fake_run)
    assert asyncio.run(printing.read_job_state({"kind": "cups", "job_id": "HP-123"})) == "printing"
    assert asyncio.run(printing.read_job_state({"kind": "cups", "job_id": "HP-123"})) == "spooled"
    assert asyncio.run(printing.read_job_state({"kind": "cups", "job_id": "HP-123"})) == "absent"


def test_read_job_state_win(monkeypatch):
    """Windows: `Get-PrintJob`-Output mit „Printing" → printing; anderer
    Status → spooled; kein Treffer (leer) → absent; Druckerfehler → spooled
    (nie absent, damit der Tracker aus einem Lesefehler nicht vorzeitig
    finalisiert)."""
    # Standarddrucker nicht nötig (handle hat bereits printer).
    seq = iter([
        (0, "Printing"),    # aktiv
        (0, "Spooling"),     # gespoolt
        (0, ""),             # weg
        (1, "Fehler"),       # rc!=0 → spooled (Sicherheit)
    ])

    async def fake_run(cmd):
        return next(seq)

    monkeypatch.setattr(printing, "_run", fake_run)
    h = {"kind": "win", "printer": "P1", "doc": "leihschein_1_x.pdf"}
    assert asyncio.run(printing.read_job_state(h)) == "printing"
    assert asyncio.run(printing.read_job_state(h)) == "spooled"
    assert asyncio.run(printing.read_job_state(h)) == "absent"
    assert asyncio.run(printing.read_job_state(h)) == "spooled"
