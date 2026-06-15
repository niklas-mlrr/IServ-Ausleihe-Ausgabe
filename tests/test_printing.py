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
