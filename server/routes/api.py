"""Aggregator der API-Router-Schicht.

Diese Datei enthält KEINE Endpunkte — sie sind in themenweise Module
aufgeteilt (`auth`, `classes`, `booklists`, `helpers`, `queue`, `slips`,
`modus_b`, `settings`), die gemeinsamen Bausteine liegen in `_deps`. Dieses
Modul bleibt als stabiler Einstiegspunkt bestehen:

- `server/app.py` importiert weiterhin `from .routes.api import router`,
- die Tests importieren `server.routes.api` (bzw. Symbole daraus).

Aufgabe: die Endpoint-Module importieren (→ ihre Routen registrieren sich auf
`router`/`host_router` aus `_deps`) und danach — EINMALIG, nach allen
Registrierungen — den `host_router` in den öffentlichen `router` einhängen.
"""

from __future__ import annotations

from ..book_order import normalize_book_order  # re-export: tests importieren es von hier
from . import (  # noqa: F401 — Import NUR wegen Seiteneffekt (Routen-Registrierung)
    auth,
    booklists,
    classes,
    helpers,
    modus_b,
    queue,
    settings,
    slips,
)
from ._deps import (  # re-export für app.py / Tests
    _base_url,
    _detect_lan_ip,
    host_router,
    require_host,
    router,
)

# Re-Exports, auf die die Tests direkt zugreifen.
from .classes import TEST_CONFIG_FORM, TEST_STUDENTS, _load_test_students  # noqa: F401
from .slips import _last_scan_for  # noqa: F401

# Host-authentifizierte Routen an den öffentlichen Router hängen — ERST hier,
# nachdem alle Endpoint-Module oben importiert (= ihre Routen auf host_router
# registriert) sind. `router` bleibt der einzige Export für app.py.
router.include_router(host_router)

__all__ = [
    "router",
    "host_router",
    "require_host",
    "normalize_book_order",
    "_base_url",
    "_detect_lan_ip",
    "_last_scan_for",
    "TEST_STUDENTS",
    "TEST_CONFIG_FORM",
    "_load_test_students",
]
