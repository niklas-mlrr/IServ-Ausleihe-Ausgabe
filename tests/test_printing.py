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

    assert calls["fetch"] == (2159, "student")  # read-only GET mit Default-Variante
    assert res["ok"] is True and res["backend"] == "file"
    assert Path(res["path"]).read_bytes().startswith(b"%PDF")
