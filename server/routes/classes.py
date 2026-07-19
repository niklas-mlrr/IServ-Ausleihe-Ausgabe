"""Schuljahr / Klassen / Klassen-Kontexte (Multi-Tab) + Einzel-Schüler + Test-Config."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import HTTPException

from ..book_order import get_hidden_isbns_for_form
from ..hub import get_hub
from ..sessions import (
    apply_hidden_books,
    booking_isbn_sets_from_info,
    end_student,
    invalidate_session,
)
from ..state import AppState, ClassContext, QueueStudent, get_state
from ._deps import (
    AddStudentRequest,
    CloseClassRequest,
    ContextIdBody,
    ContextPrintersRequest,
    OpenClassRequest,
    SelectSchoolyearRequest,
    host_router,
)
from .booklists import _ensure_class_catalog

log = logging.getLogger(__name__)

# Beim Klassen-Laden wählbare Sofort-fertig-Filter, s. `_load_student_flags`.
_AUTO_DONE_FILTERS = {
    "not_enrolled", "unpaid", "remission_pending", "exemption_pending", "all_lent",
}


def _resolve_allowed_printers(printers: list[str] | None) -> set[str] | None:
    """Client-Druckerauswahl → Allowlist. `None` oder leere Liste = kein Filter
    (alle Pool-Drucker, Default, kompatibel mit Test-Config / Öffnen ohne
    Auswahl). Sonst Menge der übergebenen Drucker-IDs (Dubletten/Leerstring
    herausgefiltert)."""
    if not printers:
        return None
    return {pid.strip() for pid in printers if pid and pid.strip()}


async def _load_student_flags(state: AppState, ctx: ClassContext, auto_done: list[str]) -> None:
    """Anmelde-/Zahlstatus der ganzen Klasse laden — parallele read-only
    IServ-GETs pro Schüler (`get_student_info`, wie in der Scanner-Anzeige).

    Zwei Zwecke aus EINEM Abruf:

    1. **Info-Anzeige (immer):** `QueueStudent.set_info_flags` füllt
       `enrolled`/`paid`/`remission_pending`/`exemption_pending` für die
       Info-Spalte der Host-Queue. Rein informativ — der `status` bleibt
       unberührt.
    2. **Auto-Fertig (nur mit gewählten Filtern):** Schüler, auf die eine der
       gewählten Bedingungen zutrifft, direkt auf 'done' setzen (nicht
       angemeldet / nicht bezahlt / Ermäßigungs- bzw. Befreiungsantrag ohne
       Nachweis / alle vorgemerkten Bücher bereits ausgeliehen).

    Fehler pro Schüler sind nicht fatal (Flags bleiben `None`, Schüler bleibt
    'pending'), damit ein einzelner IServ-Fehler nicht das ganze Klassen-Laden
    blockiert.

    'nicht angemeldet' schließt die übrigen Filter aus — ohne Anmeldung liefert
    IServ keinen Zahl-/Nachweis-/Bücher-Status, also wären `unpaid` u. a. sonst
    bedeutungslose Platzhalterwerte, die einen unangemeldeten Schüler
    fälschlich träfen, selbst wenn nur z. B. `unpaid` gewählt wurde."""
    filters = set(auto_done) & _AUTO_DONE_FILTERS

    async def _check(student: QueueStudent) -> None:
        try:
            info = await state.iserv.get_student_info(student.student_id, state.selected_schoolyear)
        except Exception:
            log.exception(
                "Anmelde-/Zahlstatus für Schüler %s konnte nicht geladen werden", student.student_id
            )
            return
        student.set_info_flags(info)
        if not info.get("enrolled"):
            if "not_enrolled" in filters:
                student.status = "done"
            return
        if (
            ("unpaid" in filters and not info.get("paid"))
            or ("remission_pending" in filters and info.get("remission_pending"))
            or ("exemption_pending" in filters and info.get("exemption_pending"))
        ):
            student.status = "done"
            return
        if "all_lent" in filters:
            hidden = await get_hidden_isbns_for_form(state, student.form)
            apply_hidden_books(info, hidden)
            vormerk, _lent, _lent_codes = booking_isbn_sets_from_info(info)
            if not vormerk:
                student.status = "done"

    await asyncio.gather(*(_check(s) for s in ctx.queue))


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
    live_b = [
        s for s in state.student_sessions.values() if s.state in ("pending_pairing", "paired")
    ]
    if (active_q or live_b) and not body.force:
        raise HTTPException(
            409,
            detail={
                "reason": "active_sessions",
                "msg": f"{len(active_q)} aktive Schüler / {len(live_b)} Live-Session(s) — "
                "Schuljahreswechsel bricht sie ab.",
            },
        )

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
    state.caches.form_catalog_cache.clear()
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

    existing = next((c for c in state.contexts.values() if c.form == form), None)
    if existing is not None:
        state.set_active_context(existing.id)
        # Erneutes Öffnen aktualisiert die Druck-Allowlist („Öffnen" ist der
        # Bedienpunkt dafür); leer/None = alle Pool-Drucker.
        existing.allowed_printer_ids = _resolve_allowed_printers(body.printers)
        await hub.broadcast_host(state.state_snapshot())
        return {"ok": True, "context_id": existing.id, "count": len(existing.queue), "reused": True}

    try:
        students = await state.iserv.get_students_for_form(form, state.selected_schoolyear)
    except Exception as e:
        log.exception("Schüler konnten nicht geladen werden")
        raise HTTPException(502, f"IServ-Fehler: {e}") from e

    ctx = state.open_context(form)
    ctx.allowed_printer_ids = _resolve_allowed_printers(body.printers)
    ctx.queue = [QueueStudent.from_iserv(s, form=form) for s in students]
    # Immer (nicht nur bei gewählten Auto-Fertig-Filtern): der Abruf füllt auch
    # die Info-Flags für die Queue-Anzeige. Fehler sind pro Schüler gekapselt.
    try:
        await _load_student_flags(state, ctx, body.auto_done or [])
    except Exception:
        log.exception("Anmelde-/Zahlstatus der Klasse %s konnte nicht geladen werden", form)
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
            state,
            hub,
            s.student_id,
            queue_status="skipped",
            session_state="revoked",
            broadcast=False,
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


@host_router.post("/api/context-printers")
async def set_context_printers(body: ContextPrintersRequest) -> dict:
    """Druck-Allowlist einer bereits geöffneten Klasse nachträglich setzen
    (Checkboxen im Klassen-Tab). `printers` = Drucker-IDs; `None`/leer = kein
    Filter (alle Pool-Drucker). Wirkt ab dem nächsten Druckauftrag (bereits
    wartende behalten ihre Allowlist — s. print_queue `PrintJob.allowed_printers`).

    Reiner In-Memory-State, kein DB-/IServ-Zugriff. Weckt den Scheduler, damit
    künftige Aufträge sofort verteilt werden können."""
    state = get_state()
    hub = get_hub()
    context_id = body.context_id.strip()
    ctx = state.contexts.get(context_id)
    if ctx is None:
        raise HTTPException(404, "Kontext unbekannt")
    ctx.allowed_printer_ids = _resolve_allowed_printers(body.printers)
    state.print_queue.wake()
    await hub.broadcast_host(state.state_snapshot())
    return {"ok": True, "context_id": context_id, "allowed_printers": (
        None if ctx.allowed_printer_ids is None else sorted(ctx.allowed_printer_ids)
    )}


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
_TEST_STUDENTS_FILE = (
    Path(__file__).resolve().parent.parent.parent / "tests" / "test_students.local.json"
)
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
        log.warning(
            "Testschüler-Datei nicht lesbar (%s: %s) — nutze Default.", _TEST_STUDENTS_FILE, exc
        )
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
