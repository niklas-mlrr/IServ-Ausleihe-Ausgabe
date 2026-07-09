from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from ..book_order import normalize_book_order
from ..booklist_store import save as save_booklist_state
from ..config import get_config
from ..hub import get_hub
from ..ratelimit import join_limiter, login_limiter
from ..sessions import (
    assign_student_to_helper,
    broadcast_displays,
    create_student_session,
    end_student,
    gen_join_secret,
    handle_commit,
    invalidate_session,
    load_and_push_paired_student,
    make_qr_data_url,
    send_display_update,
)
from ..state import HelperSession, QueueStudent, get_state
from ..tls import primary_lan_ip

log = logging.getLogger(__name__)

# `router` trägt die öffentlichen Routen (login, logout, das per-QR erreichbare
# student/join) — bewusst OHNE Host-Auth. `host_router` trägt alle ~39 Host-
# authentifizierten Endpunkte über eine einzige `dependencies=[Depends(...)]`
# statt der vorher an jedem Endpoint wiederholten `_require_host(session_id)`.
# `require_host` wird am Ende in `router` eingehängt (siehe Dateiende).
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
# löst Router-`dependencies` immer vor dem Endpoint auf), ersetzt also 1:1 das
# frühere manuelle `_require_host(session_id)` als erste Zeile jeder Funktion.
# (Empirisch geprüft: FastAPI wertet Router-`dependencies` VOR der Body-
# Validierung aus — ein fehlgeschlagener `require_host` liefert 403, selbst
# wenn der Body zugleich ungültig/leer ist. Die Gate-Reihenfolge bei
# `/api/commit-book` bleibt damit erhalten, siehe dort.)
host_router = APIRouter(dependencies=[Depends(require_host)])


# ---------------------------------------------------------------------------
# Request-Models
# ---------------------------------------------------------------------------
#
# 400 vs. 422: Wo ein fehlendes/falsch getyptes Feld bisher eine von Hand
# geschriebene 400-Antwort auslöste, geben wir jetzt bewusst zwei Fälle
# unterschiedlich zurück:
#   - Feld FEHLT ganz (Client schickt den Key nicht) → Feld bleibt im Model
#     optional mit Default, die alte manuelle 400-Prüfung im Funktionsrumpf
#     bleibt erhalten (Fehlermeldungstext unverändert).
#   - Feld ist VORHANDEN, aber vom falschen Typ (z. B. "student_id": "x") →
#     das war vorher ein manueller int()-Versuch mit 400; jetzt lässt Pydantic
#     das Request schon bei der Validierung mit 422 abbrechen. Kein Client
#     (web/host.js, web/scan.js, web/student.html — geprüft per grep) wertet
#     den Statuscode 400 aus, daher ist das eine bewusst akzeptierte
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
    weiterhin die alte 400-Meldung ("student_id fehlt") aus dem
    Funktionsrumpf; nur ein falscher Werttyp lässt Pydantic vorab mit 422
    abbrechen (siehe Abschnittskommentar oben)."""
    student_id: int | None = None


class LoginRequest(BaseModel):
    password: str = ""


class SelectSchoolyearRequest(BaseModel):
    schoolyear: str | None = None
    force: bool = False


class OpenClassRequest(BaseModel):
    form: str = ""


class CloseClassRequest(BaseModel):
    context_id: str = ""


class ContextIdBody(BaseModel):
    """`context_id` optional, auch der ganze Body optional (kein Body im
    Request → Default-Instanz, `context_id=None` → aktiver Kontext, Kompat zu
    vorher `body: dict | None = None`)."""
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
    Endpoint (siehe `_BOOL_SETTINGS`-Kommentar weiter unten), daher ein
    eigenes (wenn auch identisch aussehendes) Model statt `SettingsToggleRequest`."""
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
    if get_state().force_tailscale_ip:
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


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@router.post("/api/login")
async def login(body: LoginRequest, response: Response, request: Request = None) -> dict:
    cfg = get_config()
    # Login-Rate-Limit (pro-IP) — Brute-Force-Anläufe auf das Host-Passwort
    # drosseln. `request` hat bewusst KEINE Union-Annotation (Request | None):
    # FastAPI erkennt `Request` nur als Special-Parameter (Injektion ohne
    # Pydantic-Feld), wenn die Annotation genau `Request` ist — bei einer Union
    # versucht FastAPI ein Pydantic-Feld draus zu bauen und stirbt. Der Default
    # None greift nur beim Direktaufruf im Unit-Test (Tests übergeben kein
    # request-Objekt); in Produktion injiziert FastAPI das echte Request.
    if request is not None:
        if request.client is None:
            raise HTTPException(400, "Client-Info nicht verfügbar")
        if not login_limiter.hit(request.client.host):
            raise HTTPException(429, "Zu viele Login-Versuche — bitte kurz warten")
    # Konstantzeit-Vergleich — kein Short-Circuit-Timing-Leak wie bei `!=`.
    # compare_digest verlangt gleichartige Typen; None/non-str über str() abgesichert.
    if not secrets.compare_digest(str(body.password or ""), str(cfg.host_password or "")):
        raise HTTPException(403, "Falsches Passwort")
    sid = str(uuid.uuid4())
    get_state().add_host_session(sid)
    # secure=True: Cookie nur über HTTPS (der Server läuft ausschließlich über TLS).
    response.set_cookie("session_id", sid, httponly=True, samesite="lax", secure=True)
    return {"ok": True}


@router.post("/api/logout")
async def logout(response: Response, session_id: str | None = Cookie(default=None)) -> dict:
    if session_id:
        get_state().remove_host_session(session_id)
    response.delete_cookie("session_id")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Schuljahr
# ---------------------------------------------------------------------------

@host_router.get("/api/schoolyears")
async def get_schoolyears() -> dict:
    """Auswählbare Schuljahre + aktuell gewähltes (None = aktuelles Jahr)."""
    state = get_state()
    try:
        years = await state.iserv.get_schoolyears()
    except Exception as e:
        log.exception("Schuljahre konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"schoolyears": years, "selected": state.selected_schoolyear}


@host_router.post("/api/select-schoolyear")
async def select_schoolyear(body: SelectSchoolyearRequest) -> dict:
    """Schuljahr wählen. Setzt die Queue/Klasse zurück, da Klassen jahresspezifisch sind.

    `schoolyear=null` (oder leer) → aktuelles Schuljahr.
    """
    state = get_state()
    hub = get_hub()

    raw = body.schoolyear
    schoolyear = str(raw).strip() if raw else None

    # Guard: laufende Sessions würden durch den Wechsel verwaist. Über ALLE
    # Kontexte prüfen (nicht nur den aktiven Tab) — ein aktiver Schüler in
    # einem nicht-fokussierten Klassen-Tab würde sonst übersehen und der
    # Schuljahreswechsel risse ihn ohne Warnung ab.
    active_q = state.active_students()
    live_b = [s for s in state.student_sessions.values() if s.state in ("pending_pairing", "paired")]
    if (active_q or live_b) and not body.force:
        raise HTTPException(409, detail={
            "reason": "active_sessions",
            "msg": f"{len(active_q)} aktive Schüler / {len(live_b)} Live-Session(s) — "
                   "Schuljahreswechsel bricht sie ab.",
        })

    # Laufende Sessions sauber beenden (keine verwaisten Sessions).
    for sess in list(state.student_sessions.values()):
        if sess.state in ("pending_pairing", "paired"):
            await invalidate_session(state, sess, "revoked", reason="schuljahreswechsel")
    for helper in state.helper_sessions.values():
        helper.student_id = None
        helper.context_id = None  # Klassen-Bindung hinfällig (Kontexte fliegen weg)

    state.selected_schoolyear = schoolyear
    # Alle Klassen-Kontexte fallen — Klassen/Schüler sind jahresspezifisch.
    # (Kompat-Felder `active_form`/`queue`/`book_order` laufen leer, da kein
    # aktiver Kontext mehr gesetzt ist.)
    state.contexts = {}
    state.active_context_id = None
    # Reihenfolge/Ausblendung bleiben erhalten (serverseitig persistiert, global
    # über alle Schuljahre); `normalize_book_order` + `hidden & catalog` fangen
    # ISBN-Drift zum anderen Schuljahr ab. Nur der Katalog-Cache muss weg, da
    # die ISBNs jahresspezifisch sind.
    state.form_catalog_cache.clear()
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "selected": schoolyear}


# ---------------------------------------------------------------------------
# Klassen
# ---------------------------------------------------------------------------

@host_router.get("/api/classes")
async def get_classes() -> dict:
    state = get_state()
    try:
        classes = await state.iserv.get_class_names(state.selected_schoolyear)
    except Exception as e:
        log.exception("IServ-Klassen konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"classes": classes}


# ---------------------------------------------------------------------------
# Queue-Aufbau
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Klassen-Kontexte (Multi-Tab) — öffnen / schließen / aktivieren
# ---------------------------------------------------------------------------

@host_router.post("/api/open-class")
async def open_class(body: OpenClassRequest) -> dict:
    """Neuen Klassen-Kontext öffnen (Klassen-Tab am Host). Lädt die Schüler der
    Klasse in eine frische, separate Queue und aktiviert den Kontext. Mehrere
    Klassen können parallel offen sein (je ein Tab). Doppel-Öffnen derselben
    Klasse aktiviert den bestehenden Kontext wieder (keine zweite Queue)."""
    form = body.form.strip()
    if not form:
        raise HTTPException(400, "form fehlt")
    state = get_state()
    hub = get_hub()

    existing = next(
        (c for c in state.contexts.values() if c.form == form), None
    )
    if existing is not None:
        state.set_active_context(existing.id)
        await hub.broadcast_host(state.state_snapshot())
        return {"ok": True, "context_id": existing.id, "count": len(existing.queue), "reused": True}

    try:
        students = await state.iserv.get_students_for_form(form, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    ctx = state.open_context(form)
    ctx.queue = [QueueStudent.from_iserv(s, form=form) for s in students]
    # Katalog + Bücher-Reihenfolge sofort aufbauen (übernimmt eine im
    # Einstellungen-Dialog vorkonfigurierte Reihenfolge automatisch für den
    # Scanner) — Fehler hier sind nicht fatal, die Klasse bleibt trotzdem geladen.
    try:
        await _ensure_class_catalog(state, context_id=ctx.id)
    except Exception:
        log.exception("Klassen-Bücherkatalog konnte beim Öffnen nicht vorgebaut werden")
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": ctx.id, "count": len(ctx.queue)}


@host_router.post("/api/close-class")
async def close_class(body: CloseClassRequest) -> dict:
    """Klassen-Kontext schließen (Tab × am Host). Beendet laufende Sessions der
    Schüler dieses Kontexts, löst Helfer-Bindungen an diesen Kontext und entfernt
    den Kontext. Read-only bzgl. IServ — keine Buchung, nur In-Memory-Teardown."""
    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")

    # Alle Schüler des Kontexts sauber beenden (Worker zu, Helfer notify,
    # Modus-B-Session revoked). end_student nimmt Student über alle Kontexte
    # wahr (student_id eindeutig); broadcast=False → am Ende einmal bündeln.
    for s in list(ctx.queue):
        await end_student(
            state, hub, s.student_id,
            queue_status="skipped", session_state="revoked", broadcast=False,
        )
    # Helfer-Bindungen an diesen Kontext lösen (ihre Schüler oben bereits
    # abgeschlossen; context_id weg → „Nächster" zieht künftig aus dem aktiven
    # Kontext oder einem neu gewählten Tab).
    for helper in state.helper_sessions.values():
        if helper.context_id == context_id:
            helper.context_id = None

    state.close_context(context_id)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": context_id}


@host_router.post("/api/set-active-context")
async def set_active_context(body: ContextIdBody) -> dict:
    """Aktiven Klassen-Kontext setzen (welcher Tab am Host fokussiert ist).
    `context_id=null` → kein aktiver Kontext (Host-Tab ohne Klasse)."""
    state = get_state()
    context_id = body.context_id
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    state.set_active_context(context_id)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "active_context_id": state.active_context_id}


@host_router.get("/api/students-for-class")
async def students_for_class(form: str) -> dict:
    """Schülerliste einer Klasse für die Einzel-Auswahl (ohne die Queue anzufassen)."""
    form = form.strip()
    if not form:
        raise HTTPException(400, "form fehlt")
    state = get_state()
    try:
        students = await state.iserv.get_students_for_form(form, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"students": students}


# ---------------------------------------------------------------------------
# Klassenweite Bücher-Reihenfolge (Scanner-Anzeige) — konfiguriert wird sie nur
# noch jahrgangsweit im Einstellungen-Dialog (`/api/booklist-order`); hier nur
# noch der Katalog-Aufbau für die aktive Klasse (`select_class` ruft ihn auf).
# ---------------------------------------------------------------------------

def _persist_booklist_settings(state) -> None:
    """Aktuellen jahrgangsweiten Reihenfolge-/Ausblendungs-Stand auf die
    Server-Persistenz (`data/booklist_settings.json`) wegschreiben. Non-fatal —
    Schreibfehler werden geloggt, der In-Memory-State bleibt Leading und der
    Endpoint crasht nicht."""
    try:
        save_booklist_state(state.book_orders_by_grade, state.hidden_isbns_by_grade)
    except Exception:
        log.exception("Speichern der booklist-Einstellungen fehlgeschlagen (non-fatal)")


async def _ensure_class_catalog(state, context_id: str | None = None) -> None:
    """Katalog (ausleihbare Jahrgangs-Bücher) für einen Klassen-Kontext bauen und
    cachen, falls noch nicht für dessen Klasse geschehen. `book_order` wird beim
    ersten Bauen aus der jahrgangsweit gesetzten Reihenfolge übernommen (falls im
    Einstellungen-Dialog vorkonfiguriert), sonst mit der Default-Reihenfolge
    (subject/title) initialisiert. `context_id=None` → aktiver Kontext (Kompat,
    z. B. über /api/select-class)."""
    ctx = state.ctx_or_active(context_id)
    if ctx is None or not ctx.form:
        return
    if ctx.class_catalog_form == ctx.form and ctx.class_catalog:
        return
    grade, catalog = await state.iserv.get_class_book_catalog(
        ctx.form, state.selected_schoolyear
    )
    ctx.class_catalog = catalog
    ctx.class_catalog_form = ctx.form
    ctx.class_catalog_grade = grade
    catalog_isbns = [b["isbn"] for b in catalog]
    if grade is not None:
        state.form_catalog_cache[ctx.form] = (grade, catalog_isbns)
    stored = state.book_orders_by_grade.get(grade) if grade is not None else None
    if stored:
        ctx.book_order = normalize_book_order(catalog_isbns, stored)
    elif not ctx.book_order:
        ctx.book_order = catalog_isbns


@host_router.get("/api/booklists")
async def list_booklists() -> dict:
    """Alle Bücherlisten (Jahrgänge) des gewählten Schuljahrs — für die Reiter im
    Einstellungen-Dialog. Read-only (ein GET gegen IServ), kein DB-Write."""
    state = get_state()
    try:
        booklists = await state.iserv.get_booklists_overview(state.selected_schoolyear)
    except Exception as e:
        log.exception("Bücherlisten konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    return {"schoolyear": state.selected_schoolyear, "booklists": booklists}


@host_router.get("/api/booklist-order")
async def get_booklist_order(
    grade: int
) -> dict:
    """Ausleihbare Bücher eines Jahrgangs + aktuelle (ggf. vorkonfigurierte)
    Reihenfolge. Read-only, kein DB-Write."""
    state = get_state()
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(
            grade, state.selected_schoolyear
        )
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = [b["isbn"] for b in catalog]
    stored = state.book_orders_by_grade.get(grade)
    order = normalize_book_order(catalog_isbns, stored) if stored else catalog_isbns
    hidden = sorted(state.hidden_isbns_by_grade.get(grade, set()) & set(catalog_isbns))
    return {"grade": grade, "catalog": catalog, "order": order, "hidden": hidden}


@host_router.post("/api/booklist-order")
async def set_booklist_order(body: BooklistOrderRequest) -> dict:
    """Jahrgangsweite Bücher-Reihenfolge (aus dem Einstellungen-Dialog) speichern.

    Reiner In-Memory-State (kein DB-/IServ-Write). `broadcast_settings()` schickt
    jedem verbundenen Helfer die für **seinen eigenen** zugewiesenen Schüler
    passende Reihenfolge (per Jahrgang ermittelt über `get_book_order_for_form`)
    — funktioniert daher auch bei klassenübergreifenden Warteschlangen mit
    Schülern aus verschiedenen Jahrgängen (z. B. „Test Config"), nicht nur bei
    einer komplett geladenen Klasse. Gehört ein offener Klassen-Kontext zu diesem
    Jahrgang, wird dessen `book_order` + der Host selbst (`broadcast_host`) live
    nachgezogen, damit ein Reload des Hosts konsistent bleibt.
    """
    state = get_state()
    grade = body.grade
    requested = body.order
    if grade is None or requested is None:
        raise HTTPException(400, "grade (int) und order (Liste) erforderlich")
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(
            grade, state.selected_schoolyear
        )
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = [b["isbn"] for b in catalog]
    order = normalize_book_order(catalog_isbns, requested)
    state.book_orders_by_grade[grade] = order
    _persist_booklist_settings(state)
    hub = get_hub()
    # Jeder Helfer bekommt (unabhängig von der aktiven Klasse) seine eigene,
    # zum Jahrgang seines zugewiesenen Schülers passende Reihenfolge.
    await hub.broadcast_settings()
    # Alle gerade offenen Klassen desselben Jahrgangs live nachziehen (je
    # Klassen-Tab seinen eigenen book_order-Stand).
    touched = False
    for c in state.contexts.values():
        if c.class_catalog_grade == grade:
            c.book_order = list(order)
            touched = True
    if touched:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "grade": grade, "order": order}


@host_router.post("/api/booklist-hidden")
async def set_booklist_hidden(body: BooklistHiddenRequest) -> dict:
    """Ausgeblendete Buchreihen eines Jahrgangs (Einstellungen-Dialog, „Ausblenden"-
    Button je Buch) setzen.

    Reiner In-Memory-State (kein DB-/IServ-Write, kein PUT/POST gegen IServ —
    nur der lesende Katalog-Check zur ISBN-Validierung). Ausgeblendete Reihen
    gelten für neu geladene/neu verbundene Schüler dieses Jahrgangs nicht mehr
    als „vorgemerkt" (`apply_hidden_books` in `sessions.py`/`routes/ws.py`) und
    sind damit auch nicht mehr buchbar (`evaluate_scan_for_booking` sieht die
    ISBN nicht mehr in `vormerk_isbns`)."""
    state = get_state()
    grade = body.grade
    requested = body.hidden
    if grade is None or requested is None:
        raise HTTPException(400, "grade (int) und hidden (Liste) erforderlich")
    try:
        catalog = await state.iserv.get_booklist_catalog_by_grade(
            grade, state.selected_schoolyear
        )
    except Exception as e:
        log.exception("Jahrgangs-Bücherliste konnte nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e
    catalog_isbns = {b["isbn"] for b in catalog}
    hidden = {isbn for isbn in requested if isinstance(isbn, str) and isbn in catalog_isbns}
    state.hidden_isbns_by_grade[grade] = hidden
    _persist_booklist_settings(state)
    hub = get_hub()
    await hub.broadcast_settings()
    return {"ok": True, "grade": grade, "hidden": sorted(hidden)}


@host_router.post("/api/add-student")
async def add_student_to_queue(body: AddStudentRequest) -> dict:
    """Einen einzelnen Schüler an die Queue eines Klassen-Kontexts anhängen
    (klassenübergreifend). `context_id` optional — fehlt er, wird der aktive
    Kontext genutzt (bei Einzel-Schüler-Reiter im Klassen-Tab gesetzt); ohne
    aktiven Kontext (kein Klassen-Tab offen) schlägt der Request mit 400 fehl,
    statt still einen Geister-Kontext anzulegen.

    Im Gegensatz zu `/api/open-class` wird die Queue NICHT ersetzt und es
    werden keine laufenden Sessions angefasst.
    """
    state = get_state()
    hub = get_hub()

    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt/ungültig")
    student_id = body.student_id
    lastname = body.lastname.strip()
    firstname = body.firstname.strip()
    form = body.form.strip()
    if not lastname and not firstname:
        raise HTTPException(400, "Name fehlt")

    context_id = str(body.context_id or "").strip() or None
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")

    if state.find_student(student_id):
        raise HTTPException(409, "Schüler bereits in der Queue")

    target_ctx = state.ctx_or_active(context_id)
    if target_ctx is None:
        raise HTTPException(400, "Kein Klassen-Tab geöffnet")
    target_ctx.queue.append(
        QueueStudent(student_id=student_id, lastname=lastname, firstname=firstname, form=form)
    )
    if not target_ctx.form:
        target_ctx.form = form or ""

    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(target_ctx.queue)}


# Testschüler für den "Test Config"-Reiter (IDs einmalig per read-only
# Namenssuche ermittelt, siehe Git-Historie). Klassen-Angabe nur informativ —
# die Queue arbeitet rein über student_id.
#
# Die vier Testschüler stehen bewusst im Source (Niklas = freigegebener
# Testschüler für Buchungstests; Lukas/Lucas/Finn = Mitentwickler/Mitschüler
# für Queue-/UI-Tests, keine Buchung). Eine optionale pro-Entwickler:in-
# Override-Datei `tests/test_students.local.json` (gitignored) kann die Liste
# ersetzen — fehlt sie, gilt dieser Default. Buchungen gegen Produktion werden
# ohnehin nur mit Niklas + expliziter Freigabe gefahren (CLAUDE.md).
_TEST_STUDENTS_FILE = Path(__file__).resolve().parent.parent.parent / "tests" / "test_students.local.json"
_TEST_STUDENTS_DEFAULT = [
    {"student_id": 2159, "firstname": "Niklas", "lastname": "Müller", "form": "Klasse 12Slw"},
    {"student_id": 2164, "firstname": "Lukas", "lastname": "Podleschny", "form": "Klasse 12Mk"},
    {"student_id": 2167, "firstname": "Lucas", "lastname": "Stolpe", "form": "Klasse 12Slw"},
    {"student_id": 2415, "firstname": "Finn", "lastname": "Podleschny", "form": "Klasse 10c"},
]


def _load_test_students() -> list[dict]:
    try:
        with _TEST_STUDENTS_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        log.warning("Testschüler-Datei nicht gefunden (%s) — nutze Default.", _TEST_STUDENTS_FILE)
        return list(_TEST_STUDENTS_DEFAULT)
    except (OSError, ValueError) as exc:
        log.warning("Testschüler-Datei nicht lesbar (%s: %s) — nutze Default.", _TEST_STUDENTS_FILE, exc)
        return list(_TEST_STUDENTS_DEFAULT)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        log.warning("Testschüler-Datei hat falsches Format — nutze Default.")
        return list(_TEST_STUDENTS_DEFAULT)
    return data


TEST_STUDENTS = _load_test_students()

# Pseudo-Klassen-Name für den dedizierten "Test Config"-Tab (kein echter IServ-
# Klassencode, daher kollisionsfrei mit `/api/open-class`-Dedup über `c.form`).
TEST_CONFIG_FORM = "Test Config"


@host_router.post("/api/open-test-config")
async def open_test_config() -> dict:
    """Dedizierten "Test Config"-Tab öffnen (kein IServ-Roundtrip, kein echter
    Klassen-Katalog) und sofort mit den festen Testschülern befüllen. Erneutes
    Öffnen aktiviert den bestehenden Tab wieder (keine zweite Queue), analog zu
    `/api/open-class`."""
    state = get_state()
    hub = get_hub()

    existing = next(
        (c for c in state.contexts.values() if c.form == TEST_CONFIG_FORM),
        None,
    )
    if existing is not None:
        state.set_active_context(existing.id)
        await hub.broadcast_host(state.state_snapshot())
        return {"ok": True, "context_id": existing.id, "count": len(existing.queue), "reused": True}

    ctx = state.open_context(TEST_CONFIG_FORM)
    for s in TEST_STUDENTS:
        if state.find_student(s["student_id"]):
            continue
        ctx.queue.append(QueueStudent.from_iserv(s, form=s["form"]))
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": ctx.id, "count": len(ctx.queue)}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@host_router.get("/api/state")
async def get_state_endpoint() -> dict:
    return get_state().state_snapshot()


@host_router.post("/api/force-tailscale-ip")
async def set_force_tailscale_ip(
    body: BoolToggleRequest, request: Request
) -> dict:
    """Header-Toggle „Tailscale-IP": Auto-Auswahl (LAN-first) ↔ erzwungene Tailscale-IP.

    Beeinflusst alle QR-/Join-URLs (Helfer-, Schüler-Join-, iPad-Display-QR).
    Die On-Demand-QRs übernehmen den Modus beim nächsten Abruf automatisch; den
    bei `/modus-b/open` eingefrorenen Schüler-Join-QR bauen wir hier neu, wenn
    die Ausgabe gerade offen ist.
    """
    state = get_state()
    state.force_tailscale_ip = body.enabled

    if state.modus_b_open and state.modus_b_join_secret:
        state.modus_b_join_url = (
            f"{_base_url(request)}/student?j={state.modus_b_join_secret}"
        )
        state.modus_b_join_qr = make_qr_data_url(state.modus_b_join_url)
        await broadcast_displays(state)

    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "force_tailscale_ip": state.force_tailscale_ip}


# Whitelist für POST /api/settings/{key} — bündelt die drei strukturell
# gleichen Bool-Toggles (setzen genau ein Attribut im Serverstate, broadcasten
# an alle Hosts). `force-tailscale-ip` (baut zusätzlich den Modus-B-QR neu,
# braucht `Request`) und `printer` (String-Wert, andere Semantik: leer =
# .env-Default) bleiben bewusst eigenständige Endpunkte — reinquetschen würde
# den gemeinsamen Rumpf nur mit Sonderfällen vollstopfen, ohne echte
# Duplikation zu sparen (siehe Welle-3-Bericht).
# key -> (state-Attribut, Body-Feldname auf `SettingsToggleRequest`)
_BOOL_SETTINGS: dict[str, tuple[str, str]] = {
    "save-pdf-locally": ("save_pdf_locally", "enabled"),
    "fix-class-on-slip": ("fix_class_on_slip", "enabled"),
    "slip-default": ("slip_second_page_default", "second_page"),
}


@host_router.post("/api/settings/{key}")
async def set_bool_setting(key: str, body: SettingsToggleRequest) -> dict:
    """Einfache Bool-Entwickler-/Host-Toggles gegen eine Whitelist
    (`_BOOL_SETTINGS`) gebündelt — ersetzt die vormals separaten Endpunkte
    `/api/save-pdf-locally` (Entwickler-Toggle „PDF lokal speichern"),
    `/api/fix-class-on-slip` (experimenteller Entwickler-Toggle „Klasse auf
    Leihschein korrigieren") und `/api/slip-default` (Host-Toggle „Schüler-
    Leihschein" als Druck-Dialog-Default für die Helfer). Alle drei: rein
    In-Memory, kein IServ-/DB-Zugriff.
    """
    entry = _BOOL_SETTINGS.get(key)
    if entry is None:
        raise HTTPException(404, f"Unbekannte Einstellung: {key}")
    attr, field = entry
    value = bool(getattr(body, field))
    state = get_state()
    setattr(state, attr, value)
    hub = get_hub()
    if key == "slip-default":
        # `slip_second_page_default` ist zusätzlich für die Helfer relevant
        # (Druck-Dialog-Vorauswahl) — anders als die beiden reinen
        # Entwickler-Toggles auch an sie broadcasten.
        await hub.broadcast_settings(state)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, attr: value}


@host_router.get("/api/printers")
async def get_printers() -> dict:
    """Dem Host-Gerät bekannte Drucker für die Auswahl im Einstellungen-Dialog.

    Rein lesend (lpstat/Get-Printer, lokales System — kein IServ-/DB-Zugriff).
    """
    from ..printing import list_printers

    cfg = get_config()
    state = get_state()
    info = await list_printers(cfg.print_backend)
    info["current"] = state.printer_name_override or cfg.printer_name
    info["env_default"] = cfg.printer_name
    return info


@host_router.post("/api/printer")
async def set_printer(body: PrinterRequest) -> dict:
    """Einstellungen-Dialog: Leihschein-Drucker wählen.

    Setzt nur den In-Memory-Override im Serverstate (leer = zurück auf
    .env/Systemstandard) — kein IServ-/DB-Zugriff, nichts wird persistiert.
    """
    state = get_state()
    name = body.printer.strip()
    state.printer_name_override = name or None
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "printer": state.printer_name_override}


# ---------------------------------------------------------------------------
# Helfer verwalten
# ---------------------------------------------------------------------------

@host_router.post("/api/add-helper")
async def add_helper(body: AddHelperRequest, request: Request) -> dict:
    name = body.name.strip() or "Helfer"
    token = str(uuid.uuid4()).replace("-", "")[:16]
    state = get_state()
    # Optionale Bindung an einen Klassen-Kontext (Helfer bedient genau diese
    # Klasse; „Nächster" zieht aus ihrer Queue). Ohne context_id später per
    # /api/helper/{token}/class setzbar.
    context_id = str(body.context_id or "").strip() or None
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    state.helper_sessions[token] = HelperSession(token=token, name=name, context_id=context_id)
    url = f"{_base_url(request)}/scan?token={token}"
    qr_data_url = make_qr_data_url(url)

    await get_hub().broadcast_host(get_state().state_snapshot())
    return {"ok": True, "token": token, "url": url, "qr": qr_data_url}


@host_router.post("/api/helper/{token}/class")
async def set_helper_class(token: str, body: SetHelperClassRequest) -> dict:
    """Helfer an einen Klassen-Kontext binden (`context_id`) oder lösen
    (`context_id=null`). Rein transient — kein IServ-/DB-Zugriff."""
    state = get_state()
    helper = state.helper_sessions.get(token)
    if not helper:
        raise HTTPException(404, "Unbekannter Token")
    context_id = body.context_id
    if context_id is not None and context_id not in state.contexts:
        raise HTTPException(404, "Kontext unbekannt")
    helper.context_id = context_id
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": helper.context_id}


@host_router.delete("/api/helper/{token}")
async def remove_helper(token: str) -> dict:
    state = get_state()
    hub = get_hub()
    helper = state.helper_sessions.get(token)
    if not helper:
        raise HTTPException(404, "Unbekannter Token")
    # Vollständige Cleanup-Reihenfolge analog invalidate_session / disconnect:
    # 1. laufenden Lade-Task canceln (sonst leakt der Worker-Context, falls er
    #    noch in open_student steckt),
    # 2. aktiven Schüler des Helfers beenden → Worker zu + Queue zurück auf
    #    pending (Modus A) bzw. Session revoked (Modus B via end_student),
    # 3. WS schließen,
    # 4. Helper aus der Map nehmen.
    # Reihenfolge 1 vor 2 stellt sicher, dass end_student's eigener cancel+
    # await denselben Task nicht doppelt canceln muss (idempotent, aber klarer).
    if helper.load_task is not None and not helper.load_task.done():
        helper.load_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await helper.load_task
        helper.load_task = None
    # Ein noch laufender Grace-Teardown-Task (Verbindung kürzlich getrennt)
    # wird hiermit ebenfalls cancelt — sonst hinge er bis zu 3 s als No-op
    # im Raum (die Re-Checks in _deferred_end machen ihn ohnehin unschädlich,
    # aber sauber ist, ihn deterministisch abzuräumen).
    if helper.end_task is not None and not helper.end_task.done():
        helper.end_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await helper.end_task
        helper.end_task = None
    if helper.student_id is not None:
        await end_student(
            state, hub, helper.student_id,
            queue_status="pending", session_state="revoked",
        )
    if helper.ws:
        try:
            await helper.ws.close()
        except Exception:
            pass
    state.helper_sessions.pop(token, None)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True}


# ---------------------------------------------------------------------------
# Schüler-Queue-Steuerung
# ---------------------------------------------------------------------------

@host_router.post("/api/next-student")
async def next_student(body: NextStudentRequest) -> dict:
    helper_token = body.helper_token.strip()
    state = get_state()
    hub = get_hub()

    helper = state.helper_sessions.get(helper_token)
    if not helper:
        raise HTTPException(404, "Unbekannter Helper-Token")
    if helper.student_id is not None:
        raise HTTPException(409, "Helfer hat bereits einen aktiven Schüler")

    # „Nächster" zieht aus der Klasse, an die der Helfer gebunden ist; ohne
    # Bindung aus dem aktiven Kontext (Kompat).
    student = state.next_pending(helper.context_id)
    if not student:
        raise HTTPException(404, "Keine Schüler in der Queue")

    # Zuweisung + `loading`-Push an den Scanner (verbirgt die Queue, während
    # der Schüler geladen wird) zentral in `assign_student_to_helper`.
    await assign_student_to_helper(state, hub, helper, student)

    return {"ok": True, "student_id": student.student_id,
            "name": f"{student.lastname}, {student.firstname}"}


@host_router.post("/api/skip")
async def skip_student(body: StudentRef) -> dict:
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status in ("done", "skipped"):
        raise HTTPException(409, f"Schüler bereits als {student.status} markiert")

    # Setzt Queue-Status, löst Helfer und entwertet eine Modus-B-Session hart.
    await end_student(state, hub, student_id, queue_status="skipped", session_state="revoked")
    return {"ok": True}


@host_router.post("/api/disconnect")
async def disconnect_student(body: StudentRef) -> dict:
    """Schüler von Helfer/Schüler-Session trennen und auf 'Wartend' zurücksetzen.

    Anders als /api/skip wird der Schüler NICHT übersprungen, sondern bleibt als
    `pending` in der Queue (kann erneut zugeordnet werden). Für `pending`-Schüler
    ohne Verbindung ist es ein harmloser No-op.
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()
    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status in ("done", "skipped"):
        raise HTTPException(409, f"Schüler ist {student.status}")
    await end_student(state, hub, student_id, queue_status="pending", session_state="revoked")
    return {"ok": True}


@host_router.post("/api/disconnect-all")
async def disconnect_all(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Alle aktiven Verbindungen (Modus A + B) eines Klassen-Kontexts trennen,
    Schüler zurück auf 'Wartend'. `context_id` optional im Body — fehlt er,
    aktiver Kontext (Kompat)."""
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    active_ids = [s.student_id for s in ctx.queue if s.status == "active"]
    for sid in active_ids:
        await end_student(state, hub, sid, queue_status="pending",
                          session_state="revoked", broadcast=False)
    # Einmal am Ende broadcasten statt pro Schüler (sonst N Snapshots).
    if active_ids:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(active_ids)}


@host_router.post("/api/reset-queue")
async def reset_queue(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Queue-Status eines Klassen-Kontexts zurücksetzen: ALLE Schüler auf 'pending'.

    Trennt aktive Verbindungen (wie disconnect) und setzt zusätzlich
    `done`/`skipped`-Schüler zurück auf `pending`. Die Schüler bleiben in der
    Queue (kein Neuladen der Klasse). `context_id` optional — fehlt er, aktiver
    Kontext (Kompat).
    """
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    changed = [s.student_id for s in ctx.queue if s.status != "pending"]
    for sid in changed:
        await end_student(state, hub, sid, queue_status="pending",
                          session_state="revoked", broadcast=False)
    # Einmal am Ende broadcasten statt pro Schüler (sonst N Snapshots).
    if changed:
        await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": len(changed)}


@host_router.post("/api/clear-queue")
async def clear_queue(body: ContextIdBody = _EMPTY_CONTEXT_BODY) -> dict:
    """Queue eines Klassen-Kontexts komplett LEEREN: alle Schüler entfernen.

    Anders als `/api/reset-queue` (setzt nur den Status zurück) wird die Queue
    hier ganz geleert. Laufende Live-Sessions der Schüler dieses Kontexts werden
    sauber beendet und Helfer-Zuordnungen gelöst. Der Kontext (Tab) bleibt
    bestehen — nur seine Queue wird leer. `context_id` optional — fehlt er,
    aktiver Kontext (Kompat).
    """
    state = get_state()
    hub = get_hub()
    context_id = (body.context_id or "").strip() or None
    ctx = state.ctx_or_active(context_id)
    if ctx is None:
        return {"ok": True, "count": 0}
    count = len(ctx.queue)
    student_ids = {s.student_id for s in ctx.queue}
    for sess in list(state.student_sessions.values()):
        if sess.state in ("pending_pairing", "paired") and sess.student_id in student_ids:
            await invalidate_session(state, sess, "revoked", reason="queue-leeren")
    for helper in state.helper_sessions.values():
        if helper.student_id in student_ids:
            helper.student_id = None
            helper.expected_isbns = set()
            helper.vormerk_isbns = set()
            helper.lent_isbns = set()
            helper.peeking = False
    ctx.queue = []
    # book_order/Katalog bleiben (Klasse/Tab bleibt offen, nur Queue leer).
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "count": count}


@host_router.post("/api/finish")
async def finish_student(body: StudentRef) -> dict:
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    state = get_state()
    hub = get_hub()

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")

    await end_student(state, hub, student_id, queue_status="done", session_state="completed")
    return {"ok": True}


@host_router.post("/api/clear-book-alert")
async def clear_book_alert(body: StudentRef) -> dict:
    """Blockierendes Ausgemustert-Hinweis-Modal am Schüler-Client (Modus B)
    freigeben — der Client selbst hat dafür bewusst keinen Schließen-Button
    (Freigabe nur durch den Host). Wird das Buch am Helfer-Scanner (Modus A)
    gemeldet, gibt es keine Client-Session dazu — dann räumt dieser Call nur
    das Host-Kästchen auf."""
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id

    state = get_state()
    session = state.find_session_by_student(student_id)
    if session is not None and session.book_alert_open:
        session.book_alert_open = False
        session.book_alert_payload = None
        if session.ws is not None:
            try:
                await session.ws.send_json({"type": "book_alert_clear"})
            except Exception:
                pass

    await get_hub().broadcast_host({"type": "book_alert", "student_id": student_id, "cleared": True})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Leihschein-Druck (read-only PDF-Abruf + lokaler Druck)
# ---------------------------------------------------------------------------

@host_router.post("/api/print-loan-slip")
async def print_loan_slip(body: PrintLoanSlipRequest) -> dict:
    """Leihschein eines Schülers holen (read-only) und lokal drucken.

    Kein Schreibzugriff auf IServ — `get_loan_slip_pdf` ist ein reiner GET, das
    Drucken passiert am Laptop/Macbook (siehe server/printing.py).
    """
    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id
    # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur, wenn der
    # Host-Toggle gesetzt ist.
    pages = None if body.second_page else "1"

    from ..sessions import print_loan_slip_for

    state = get_state()
    try:
        return await print_loan_slip_for(state, student_id, pages=pages)
    except Exception as e:
        log.exception("Leihschein-Druck für %s fehlgeschlagen", student_id)
        raise HTTPException(502, f"Leihschein-Druck fehlgeschlagen: {e}") from e


# ---------------------------------------------------------------------------
# Buchung (GATED — nur freigegebener Buchungstest, PLAN §6)
# ---------------------------------------------------------------------------

def _last_scan_for(state, student_id: int) -> str:
    """Zuletzt gestageter Barcode des Schülers (Modus B Session oder Modus A Helfer)."""
    sess = state.find_session_by_student(student_id)
    if sess and sess.last_scan:
        return sess.last_scan
    helper = state.find_helper_for_student(student_id)
    if helper and helper.last_scan:
        return helper.last_scan
    return ""


@host_router.post("/api/commit-book")
async def commit_book(body: CommitBookRequest) -> dict:
    """Einen Barcode tatsächlich BUCHEN (Enter auf der IServ-Counter-Seite).

    Dreifach gesperrt: Host-Auth + `confirm:true` + Server-Flag
    `allow_booking`. Default `ALLOW_BOOKING=false` → gesperrt; `handle_commit`
    berührt den Worker dann gar nicht erst. Nur für den freigegebenen
    Buchungstest (Niklas + Lukas, CLAUDE.md / PLAN §6).

    `confirm` ist im Model bewusst `bool = False` (KEIN Pflichtfeld) — ein
    Pflichtfeld würde bei fehlendem/falschem `confirm` schon während der
    Pydantic-Validierung mit 422 abbrechen, BEVOR Gate 1 (`allow_booking`)
    geprüft wird. Das würde die geforderte Reihenfolge "403 vor 400"
    (CLAUDE.md / PLAN §6) verletzen. Mit Default bleibt die Validierung immer
    erfolgreich; die eigentliche confirm-Prüfung (Gate 3) bleibt unten im
    Funktionsrumpf, NACH Gate 1.
    """
    # Gate 2 (Host-Auth) läuft bereits vorab als Dependency (require_host auf
    # host_router) — FastAPI löst Dependencies immer vor dem Funktionskörper
    # auf, die Reihenfolge Gate2 -> Gate1 -> Gate3 bleibt damit erhalten.
    cfg = get_config()
    if not cfg.allow_booking:                   # Gate 1: Server-Flag
        raise HTTPException(403, "Buchung gesperrt (ALLOW_BOOKING=false)")
    if not body.confirm:                        # Gate 3: bewusster Extra-Schritt
        raise HTTPException(400, "confirm:true erforderlich")

    if body.student_id is None:
        raise HTTPException(400, "student_id fehlt")
    student_id = body.student_id

    state = get_state()
    hub = get_hub()
    barcode = body.barcode.strip() or _last_scan_for(state, student_id)
    if not barcode:
        raise HTTPException(400, "Kein Barcode (weder übergeben noch gestaged)")

    result = await handle_commit(state, student_id, barcode)
    await hub.broadcast_host(state.state_snapshot())
    # Nur "booked" gilt als Erfolg. "unknown" (Selektoren unverifiziert) darf
    # KEINE Buchung vortäuschen — der Host muss dann manuell prüfen.
    return {"ok": result.get("status") == "booked", "barcode": barcode, **result}


# ---------------------------------------------------------------------------
# Modus B — Live-Ausgabe
# ---------------------------------------------------------------------------

@host_router.post("/api/modus-b/open")
async def modus_b_open(request: Request) -> dict:
    """Live-Ausgabe öffnen: allgemeines Join-Secret + QR erzeugen und an iPads pushen."""
    state = get_state()
    state.modus_b_open = True
    # Frisches Join-Secret bei jedem Öffnen → alte Screenshots/QRs aus einer
    # früheren Ausgabe werden ungültig. Innerhalb einer Ausgabe bleibt es konstant
    # (rotiert NICHT mehr pro Zuordnung, 2026-06-18).
    state.modus_b_join_secret = gen_join_secret()
    state.modus_b_join_url = f"{_base_url(request)}/student?j={state.modus_b_join_secret}"
    state.modus_b_join_qr = make_qr_data_url(state.modus_b_join_url)

    await broadcast_displays(state)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "join_url": state.modus_b_join_url, "qr": state.modus_b_join_qr}


@host_router.post("/api/modus-b/close")
async def modus_b_close() -> dict:
    """Live-Ausgabe schließen: Join-Secret entwerten, offene pending-Sessions revoken.

    Bereits gepairte (aktive) Sessions laufen weiter, bis sie regulär abgeschlossen
    werden.
    """
    state = get_state()
    hub = get_hub()
    state.modus_b_open = False
    state.modus_b_join_secret = None
    state.modus_b_join_url = None
    state.modus_b_join_qr = None

    for sess in list(state.student_sessions.values()):
        if sess.state == "pending_pairing":
            await invalidate_session(state, sess, "revoked", reason="ausgabe-geschlossen")

    await broadcast_displays(state)
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True}


@host_router.get("/api/modus-b/qr")
async def modus_b_qr() -> dict:
    """QR/URL für den Host nachladen (z. B. nach Reconnect)."""
    state = get_state()
    return {
        "open": state.modus_b_open,
        "join_url": state.modus_b_join_url,
        "qr": state.modus_b_join_qr,
    }


@host_router.get("/api/display/qr")
async def display_qr(request: Request) -> dict:
    """QR, mit dem ein iPad die QR-Display-Seite (`/qr-display`) öffnet.

    Anders als der Schüler-Join-QR (`modus_b_join_qr`) zeigt dieser QR nur auf
    die statische Display-Seite — keine Schülerdaten, kein Join-Secret. Die
    LAN-IP-Korrektur aus `_base_url` macht den QR für das iPad erreichbar.
    """
    url = f"{_base_url(request)}/qr-display"
    return {"url": url, "qr": make_qr_data_url(url)}


@host_router.post("/api/display/authorize")
async def display_authorize(body: DisplayAuthorizeRequest) -> dict:
    """iPad-Display per Registrierungscode autorisieren (Registrierung am Host)."""
    code = body.registration_code.strip().upper()
    if not code:
        raise HTTPException(400, "registration_code fehlt")
    state = get_state()
    display = next(
        (d for d in state.displays.values() if d.registration_code == code and not d.authorized),
        None,
    )
    if not display:
        raise HTTPException(404, "Kein Display mit diesem Code (oder bereits autorisiert)")
    display.authorized = True
    await send_display_update(state, display)
    await get_hub().broadcast_host(state.state_snapshot())
    return {"ok": True, "display_id": display.display_id}


@router.post("/api/student/join")
async def student_join(body: StudentJoinRequest, request: Request) -> dict:
    """Öffentlich (per allgemeinem QR erreichbar): neue Schüler-Session anlegen.

    Verlangt das aktuelle Join-Secret aus dem QR. Liefert den langen
    session_token (Zugang) + den 4-stelligen Pairing-Code (Zuordnung am Host).
    """
    # DoS-Schutz: pro-IP gedrosselt, noch vor jeder Prüfung (auch Falsch-Secret-Floods).
    # request.client None (z. B. bei Test-Clients ohne Peer-Info) würde sonst alle
    # Anfragen in einen "?"-Bucket werfen und einen gemeinsamen Limit-Kontingent
    # teilen — lieber hart abweisen, bevor der Limiter gerufen wird.
    if request.client is None:
        raise HTTPException(400, "Client-Info nicht verfügbar")
    if not join_limiter.hit(request.client.host):
        raise HTTPException(429, "Zu viele Anfragen — bitte kurz warten")

    state = get_state()
    secret = body.join_secret.strip()
    if not state.modus_b_open or not state.modus_b_join_secret:
        raise HTTPException(403, "Live-Ausgabe ist geschlossen")
    # Konstantzeit-Vergleich — kein Short-Circuit-Timing-Leak wie bei `!=`.
    if not secrets.compare_digest(secret, str(state.modus_b_join_secret or "")):
        raise HTTPException(403, "Ungültiger oder abgelaufener QR")

    try:
        session = create_student_session(state)
    except RuntimeError:
        # Pairing-Code-Raum (4-stellig) erschöpft — sehr viele gleichzeitig Wartende.
        raise HTTPException(503, "Zu viele gleichzeitige Wartende — bitte gleich erneut scannen") from None
    await get_hub().broadcast_host(state.state_snapshot())
    return {"session_token": session.session_token, "pairing_code": session.pairing_code}


@host_router.post("/api/student/pair")
async def student_pair(body: StudentPairRequest) -> dict:
    """Host ordnet einen 4-stelligen Code einem Schüler zu (Doppel-Bestätigung)."""
    state = get_state()
    hub = get_hub()

    code = body.pairing_code.strip()
    student_id = body.student_id
    override = body.override_payment
    if not code or student_id is None:
        raise HTTPException(400, "pairing_code und student_id erforderlich")

    session = state.find_session_by_code(code)
    if not session:
        raise HTTPException(404, "Code unbekannt oder abgelaufen")

    student = state.find_student(student_id)
    if not student:
        raise HTTPException(404, "Schüler nicht in der Queue")
    if student.status not in ("pending",):
        raise HTTPException(409, f"Schüler nicht verfügbar (Status: {student.status})")
    if state.find_session_by_student(student_id):
        raise HTTPException(409, "Schüler hat bereits eine Live-Session")

    try:
        info = await state.iserv.get_student_info(student_id, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schülerinfo (Pairing) für %d fehlgeschlagen", student_id)
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    # Re-Check nach dem await (TOCTOU): während des IServ-Calls könnte eine
    # parallele Anfrage denselben Code/Schüler gebunden oder die Session
    # entwertet haben. Erneut prüfen, bevor wir verbindlich binden.
    if session.state != "pending_pairing" or state.find_session_by_code(code) is not session:
        raise HTTPException(409, "Code zwischenzeitlich vergeben oder abgelaufen")
    if student.status not in ("pending",):
        raise HTTPException(409, f"Schüler nicht verfügbar (Status: {student.status})")
    if state.find_session_by_student(student_id):
        raise HTTPException(409, "Schüler hat bereits eine Live-Session")

    # O6: nicht bezahlt → Host muss explizit freigeben. Genauso bei
    # ausstehendem Ermäßigungs-/Befreiungsnachweis (Antrag gestellt, aber
    # unentschieden) — der Host muss den Schüler bewusst freigeben. Beide
    # Blocker werden gesammelt und in einem einzigen Bestätigungs-Dialog
    # angezeigt; `override_payment` hebt alle Blocker auf einmal auf.
    # Nicht angemeldete Schüler haben keinen Bezahl-/Nachweis-Status → keine
    # Nachfrage, sie werden direkt gepaart.
    blockers = []
    if info.get("enrolled"):
        if not info.get("paid"):
            blockers.append({"kind": "unpaid", "amount_open": info.get("amount_open")})
        if info.get("remission_pending") or info.get("exemption_pending"):
            blockers.append({
                "kind": "nachweis",
                "remission": bool(info.get("remission_pending")),
                "exemption": bool(info.get("exemption_pending")),
            })
    if blockers and not override:
        raise HTTPException(
            409,
            detail={
                "reason": "blocked",
                "blockers": blockers,
                "msg": "Schüler-Status erfordert Freigabe",
            },
        )

    # Binden — ab jetzt gilt der session_token als freigegeben.
    session.student_id = student_id
    session.state = "paired"
    session.paired_at = datetime.now()
    session.last_activity = datetime.now()
    session.payment_overridden = bool(not info.get("paid") and override)
    student.status = "active"

    # Join-Secret ist konstant (PLAN §3, 2026-06-18) → kein Rotieren mehr.
    # Der QR bleibt unverändert; bereits angezeigte Displays brauchen kein Update.

    await hub.broadcast_host(state.state_snapshot())
    session.load_task = asyncio.create_task(
        load_and_push_paired_student(state, hub, session, student, info)
    )
    return {"ok": True, "student_id": student_id}


# Modus-A-Schülerladen liegt jetzt zentral in sessions.load_and_push_helper_student.

# Host-authentifizierte Routen an den öffentlichen Router hängen (siehe
# host_router-Definition oben) — nach außen unverändert, `router` bleibt der
# einzige Export für app.py.
router.include_router(host_router)
