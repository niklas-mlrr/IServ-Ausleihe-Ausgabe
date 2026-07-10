"""Gemeinsame Bausteine der API-Router-Schicht.

Enthält den öffentlichen `router` (ohne Host-Auth) und den `host_router`
(mit `require_host`-Dependency), die Auth-Dependency selbst, alle
Request-Models sowie die QR-/Basis-URL-Helfer (`_base_url` & Cache). Die
Endpoint-Module (`auth`, `classes`, `booklists`, `helpers`, `queue`, `slips`,
`modus_b`, `settings`) importieren von hier — dieses Modul importiert
UMGEKEHRT nichts aus den Endpoint-Modulen (kein Import-Zyklus).

Die Router werden hier nur DEFINIERT; das Einhängen des `host_router` in den
öffentlichen `router` (`router.include_router(host_router)`) passiert bewusst
ERST in `routes/api.py`, nachdem alle Endpoint-Module importiert (= ihre Routen
registriert) sind.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import get_config
from ..state import get_state
from ..tls import primary_lan_ip

# `router` trägt die öffentlichen Routen (login, logout, das per-QR erreichbare
# student/join) — bewusst OHNE Host-Auth. `host_router` trägt alle ~39 Host-
# authentifizierten Endpunkte über eine einzige `dependencies=[Depends(...)]`
# statt einer je Endpoint wiederholten Auth-Prüfung.
# `require_host` wird in routes/api.py in `router` eingehängt.
router = APIRouter()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def require_host(session_id: str | None = Cookie(default=None)) -> str:
    state = get_state()
    if not state.is_host_session_valid(session_id, get_config().host_session_ttl_s):
        raise HTTPException(403, "Nicht eingeloggt")
    return session_id


# Alle Host-authentifizierten Endpunkte hängen an diesem Router — die
# Dependency läuft für JEDEN seiner Endpunkte VOR dem Funktionskörper (FastAPI
# löst Router-`dependencies` immer vor dem Endpoint auf).
# (Empirisch geprüft: FastAPI wertet Router-`dependencies` VOR der Body-
# Validierung aus — ein fehlgeschlagener `require_host` liefert 403, selbst
# wenn der Body zugleich ungültig/leer ist. Die Gate-Reihenfolge bei
# `/api/commit-book` bleibt damit erhalten, siehe dort.)
host_router = APIRouter(dependencies=[Depends(require_host)])


# ---------------------------------------------------------------------------
# Request-Models
# ---------------------------------------------------------------------------
#
# 400 vs. 422: ein fehlendes/falsch getyptes Feld liefert bewusst zwei
# unterschiedliche Statuscodes:
#   - Feld FEHLT ganz (Client schickt den Key nicht) → Feld bleibt im Model
#     optional mit Default, eine manuelle 400-Prüfung im Funktionsrumpf greift.
#   - Feld ist VORHANDEN, aber vom falschen Typ (z. B. "student_id": "x") →
#     Pydantic bricht das Request schon bei der Validierung mit 422 ab. Kein
#     Client (web/host.js, web/scan.js, web/student.html — geprüft per grep)
#     wertet den Statuscode 400 aus, daher ist das eine bewusst akzeptierte
#     Verschärfung (ehrlicherer Statuscode), keine Verhaltensänderung, auf die
#     sich ein Client verlassen hätte.
# Ausnahme: die drei Buchungs-Gates in commit_book (Host-Auth/allow_booking/
# confirm) — dort MUSS die Reihenfolge/der Statuscode exakt erhalten bleiben
# (CLAUDE.md, PLAN §6). `confirm` bleibt deshalb bewusst `bool = False` (kein
# Pflichtfeld), die 400-Prüfung bleibt im Funktionsrumpf NACH den anderen
# beiden Gates.


class StudentRef(BaseModel):
    """Gemeinsames Body-Model für alle Endpunkte, die nur eine `student_id`
    brauchen (skip/disconnect/finish/clear-book-alert/…). Bewusst
    `int | None = None` statt Pflichtfeld: ein komplett fehlendes Feld liefert
    die 400-Meldung ("student_id fehlt") aus dem Funktionsrumpf; nur ein
    falscher Werttyp lässt Pydantic vorab mit 422 abbrechen (siehe
    Abschnittskommentar oben)."""

    student_id: int | None = None


class LoginRequest(BaseModel):
    password: str = ""


class SelectSchoolyearRequest(BaseModel):
    schoolyear: str | None = None
    force: bool = False


class OpenClassRequest(BaseModel):
    form: str = ""
    # Filter, die beim Laden der Klasse sofort auf "fertig" gesetzt werden
    # sollen — Werte aus {"not_enrolled", "unpaid", "remission_pending",
    # "exemption_pending"}, s. classes.py `_AUTO_DONE_FILTERS`.
    auto_done: list[str] | None = None


class CloseClassRequest(BaseModel):
    context_id: str = ""


class ContextIdBody(BaseModel):
    """`context_id` optional, auch der ganze Body optional (kein Body im
    Request → Default-Instanz, `context_id=None` → aktiver Kontext)."""

    context_id: str | None = None


# Modul-Level-Singleton als Body-Default (statt `= ContextIdBody()` direkt im
# Funktionskopf — ruff/B008 verbietet Funktionsaufrufe in Argument-Defaults;
# die Instanz ist unveränderlich/wird nie mutiert, ein Singleton ist unbedenklich).
_EMPTY_CONTEXT_BODY = ContextIdBody()


class BooklistOrderRequest(BaseModel):
    grade: int | None = None
    order: list[str] | None = None


class BooklistHiddenRequest(BaseModel):
    grade: int | None = None
    hidden: list[str] | None = None


class AddStudentRequest(BaseModel):
    student_id: int | None = None
    lastname: str = ""
    firstname: str = ""
    form: str = ""
    context_id: str | None = None


class BoolToggleRequest(BaseModel):
    """Body für `/api/force-tailscale-ip` — bleibt bewusst ein eigener
    Endpoint (siehe `_BOOL_SETTINGS`-Kommentar in routes/settings.py), daher
    ein eigenes (wenn auch identisch aussehendes) Model statt
    `SettingsToggleRequest`."""

    enabled: bool = False


class SettingsToggleRequest(BaseModel):
    """Body für `POST /api/settings/{key}` (Whitelist `_BOOL_SETTINGS`). Beide
    Feldnamen optional, da die drei zusammengefassten Toggles historisch
    unterschiedliche Feldnamen im JSON-Body haben (`enabled` vs. `second_page`)
    — `web/host.js` bleibt bewusst unverändert, nur die URL wandert auf
    `/api/settings/<key>`. Welches Feld tatsächlich gelesen wird, bestimmt
    `_BOOL_SETTINGS[key]`."""

    enabled: bool | None = None
    second_page: bool | None = None


class PrinterRequest(BaseModel):
    printer: str = ""


class AddHelperRequest(BaseModel):
    name: str = "Helfer"
    context_id: str | None = None


class SetHelperClassRequest(BaseModel):
    context_id: str | None = None


class NextStudentRequest(BaseModel):
    helper_token: str = ""


class PrintLoanSlipRequest(BaseModel):
    student_id: int | None = None
    second_page: bool = False


class CommitBookRequest(BaseModel):
    student_id: int | None = None
    confirm: bool = False
    barcode: str = ""


class DisplayAuthorizeRequest(BaseModel):
    registration_code: str = ""


class StudentJoinRequest(BaseModel):
    join_secret: str = ""


class StudentPairRequest(BaseModel):
    pairing_code: str = ""
    student_id: int | None = None
    override_payment: bool = False


# Erfolgreich erkannte LAN-IP cachen — ändert sich im Betrieb praktisch nicht
# und spart pro QR-Request einen UDP-Socket. WICHTIG: Nur Treffer cachen, kein
# None — sonst friert ein einmaliger Netzwerk-Hänger beim ersten Request (WLAN
# noch nicht oben) die Erkennung dauerhaft ein und der QR zeigt 127.0.0.1.
# Pro Modus (Auto / Tailscale) getrennt cachen — die Erkennung kostet je einen
# UDP-Socket und ändert sich im Betrieb praktisch nicht. Nur Treffer cachen.
_auto_lan_ip: dict[bool, str | None] = {}


def _detect_lan_ip(force_tailscale: bool = False) -> str | None:
    if not _auto_lan_ip.get(force_tailscale):
        _auto_lan_ip[force_tailscale] = primary_lan_ip(force_tailscale=force_tailscale)
    return _auto_lan_ip[force_tailscale]


def _base_url(request: Request) -> str:
    # Hostname wird bewusst NICHT aus dem Host-Header übernommen — ein beliebiger
    # Host-Header (z. B. `evil.com`) würde sonst in die QR-URL wandern und dort
    # das join_secret transportieren (Host-Header-Injection). Der Host-Header
    # liefert nur noch den Port (der Host-Rechner hat sich ja selbst verbunden,
    # sein Port ist korrekt). Der Hostname kommt aus cfg.host_ip / Auto-Erkennung.
    host_header = request.headers.get("host", "")
    _, _, port = host_header.partition(":")
    cfg = get_config()
    # Toggle „Tailscale-IP": erzwingt die Tailscale-IP in JEDER QR-URL, auch wenn
    # der Host die Seite bereits über eine echte IP (statt localhost) geöffnet hat
    # — der Host-Header würde sonst gewinnen und der Toggle bliebe wirkungslos.
    if get_state().settings.force_tailscale_ip:
        ts = _detect_lan_ip(force_tailscale=True)
        if ts:
            port = port or str(cfg.port)
            return f"https://{ts}:{port}" if port else f"https://{ts}"
    # Hostname aus Config-Override oder Auto-Erkennung (LAN-Default-Route).
    # Expliziter HOST_IP vor der Heuristik — bei mehreren Interfaces wählt die
    # Auto-Erkennung sonst evtl. das falsche Netz.
    hostname = cfg.host_ip or _detect_lan_ip()
    if hostname:
        port = port or str(cfg.port)
        return f"https://{hostname}:{port}" if port else f"https://{hostname}"
    # Fallback: Auto-Erkennung lieferte nichts (z. B. Netzwerk noch nicht oben).
    # Dann den Host-Header als Ganzen nehmen — besser eine evtl. falsche URL als
    # keine. Betrifft nur Übergangszustände; _detect_lan_ip cacht nur Treffer,
    # so dass ein einmaliger Hänger die Erkennung nicht dauerhaft einfriert.
    host = host_header or "localhost"
    return f"https://{host}"
