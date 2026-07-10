"""Regression-Test: `_get_series_map()` darf sich nicht selbst deadlocken.

`_init_lock` ist ein `threading.Lock` (nicht reentrant). `_get_series_map()`
nahm den Lock und rief darin `_get_client()` auf, das denselben Lock erneut
nimmt, sobald `_client` noch `None` ist — der aufrufende Thread blockiert
dann für immer auf sich selbst. Bisher passierte das nie, weil jeder
`_sync()`-Body zuerst `_get_client()` und erst danach `_get_series_map()`
aufruft; eine vertauschte Aufrufreihenfolge hätte den Server unwiderruflich
hängen lassen (in einem `asyncio.to_thread`-Worker, ohne Traceback).

Dieser Test reproduziert exakt den gefährlichen Pfad: eine frische Instanz,
bei der `_client is None` ist, ruft direkt `_get_series_map()` auf — ohne
vorherigen `_get_client()`-Aufruf.
"""

from __future__ import annotations

import threading

from server import iserv_client as iserv_client_module
from server.iserv_client import IsServClient


class _FakeSeries:
    def __init__(self, isbn, title, subject):
        self.isbn = isbn
        self.title = title
        self.subjects_flat = [subject]
        self.subjects = [subject]


class _FakeSeriesEndpoint:
    def get_all(self):
        return [
            _FakeSeries("111", "Mathe 9", "Mathematik"),
            _FakeSeries("222", "Deutsch 9", "Deutsch"),
        ]


class _FakeAusleiheClient:
    """Stellt sicher, dass kein echter Netzwerk-Login stattfindet."""

    def __init__(self, domain, username, password, allow_writes=False):
        self.domain = domain
        self.username = username
        self.password = password
        self.allow_writes = allow_writes
        self.series = _FakeSeriesEndpoint()


def test_get_series_map_does_not_self_deadlock(monkeypatch):
    monkeypatch.setattr(iserv_client_module, "AusleiheClient", _FakeAusleiheClient)

    client = IsServClient("d", "u", "p")
    assert client._client is None  # exakt der gefährliche Ausgangszustand

    result: dict = {}

    def _call():
        result["map"] = client._get_series_map()

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), (
        "_get_series_map() hat sich selbst deadlockt (Thread nach 5s noch aktiv) — "
        "_init_lock ist nicht reentrant und wurde erneut genommen, waehrend er schon "
        "gehalten wurde."
    )
    assert set(result["map"].keys()) == {"111", "222"}
    assert result["map"]["111"].title == "Mathe 9"
