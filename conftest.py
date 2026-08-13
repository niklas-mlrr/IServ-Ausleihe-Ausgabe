# Sorgt dafür, dass das Projekt-Root im sys.path liegt, damit die Tests
# `server.*` und `automation.*` importieren können (Projekt ist package=false).

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from server.app import create_app


class _CookieCompatTestClient(TestClient):
    """Keep legacy test calls off Starlette's deprecated request-cookie API.

    Existing tests deliberately pass a different host SID per request.  Apply
    it to the client cookie jar only for that request, then restore the jar.
    This keeps the old test semantics while using the supported client-level
    cookie mechanism.
    """

    def request(self, *args, **kwargs):
        cookies = kwargs.pop("cookies", None)
        if cookies is None:
            return super().request(*args, **kwargs)
        items = dict(cookies).items()
        previous = {name: self.cookies.get(name) for name, _value in items}
        self.cookies.update(cookies)
        try:
            return super().request(*args, **kwargs)
        finally:
            for name, value in previous.items():
                self.cookies.delete(name)
                if value is not None:
                    self.cookies.set(name, value)


@pytest.fixture(autouse=True)
def _isolate_device_persistence(tmp_path, monkeypatch):
    """Helfer-/Drucker-Display-/Scan-Station-Persistenz (`server/helper_store.py`,
    `printer_display_store.py`, `scan_station_store.py`) schreibt bei jeder
    erste WS-Verbindung UND beim App-Shutdown auf die Platte (s.
    `sessions.persist_helpers` & Co., aufgerufen aus `routes/ws.py` und
    `app.py`-Lifespan) — automatisch (autouse) für JEDEN Test auf `tmp_path`
    umgeleitet, sonst würden schon einfache WS-Tests in die echten
    `data/*.json` schreiben (Spiegel der Isolation, die `booklist_store.py`/
    `printer_store.py`-Tests einzeln per Hand machen)."""
    import server.helper_store as helper_store
    import server.printer_display_store as printer_display_store
    import server.printer_scanner_store as printer_scanner_store
    import server.scan_station_store as scan_station_store

    monkeypatch.setattr(helper_store, "STORE_PATH", tmp_path / "helpers.json")
    monkeypatch.setattr(printer_display_store, "STORE_PATH", tmp_path / "printer_displays.json")
    monkeypatch.setattr(scan_station_store, "STORE_PATH", tmp_path / "scan_stations.json")
    monkeypatch.setattr(printer_scanner_store, "STORE_PATH", tmp_path / "printer_scanners.json")


@pytest.fixture
def client() -> TestClient:
    """Echter HTTP-Client (Starlette TestClient) auf einer frischen App-Instanz.

    Bewusst KEIN `with TestClient(app)` — das würde den Lifespan starten, und
    der loggt einen echten Playwright-WorkerPool gegen die IServ-PRODUKTION
    ein (server/app.py:lifespan). Ohne Context-Manager laufen Startup/Shutdown
    nie; der TestClient kann trotzdem ganz normal Requests schicken (Starlette
    routet direkt über den ASGI-Callable, Depends/Cookie-Injection inklusive).
    Jeder Test bekommt eine frische `create_app()`-Instanz, aber der globale
    State (`server.state.get_state()`) bleibt ein Singleton — Tests, die den
    State beeinflussen, patchen ihn über `monkeypatch.setattr`.
    """
    app = create_app()
    return _CookieCompatTestClient(app)
