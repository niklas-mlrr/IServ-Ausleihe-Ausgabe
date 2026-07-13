from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Cookie, WebSocket, WebSocketDisconnect

from ..hub import get_hub
from ..sessions import (
    advance_helper,
    assign_student_to_helper,
    broadcast_student_info_to_spectators,
    end_student,
    gen_registration_code,
    hydrate_student_info,
    print_loan_slip_for,
    process_scan,
    rebind_helper_to_context,
    send_display_update,
    spectate_student,
)
from ..state import DisplaySession, QueueStudent, get_state

log = logging.getLogger(__name__)
router = APIRouter()

# Grace-Frist zwischen Scanner-Disconnect und Schüler-Teardown (Reconnect-Fenster).
# Abgesichert: tests/test_scanner_reconnect.py
_RECONNECT_GRACE_S = 3.0


async def safe_broadcast(hub, state) -> None:
    """Host-Broadcast, dessen Fehler bewusst verschluckt werden.

    Wird an mehreren Stellen (Deferred-Teardown, `finally`-Blöcke der WS-
    Handler) verwendet, an denen ein fehlgeschlagener Broadcast den
    umgebenden Ablauf (Teardown bzw. Verbindungsabbau) nicht stören darf.
    """
    try:
        await hub.broadcast_host(state.state_snapshot())
    except Exception:  # noqa: BLE001 — Broadcast-Fehler nicht propagieren
        pass


async def _take_over_ws(holder, websocket) -> None:
    """Übernimmt eine Reconnect-Verbindung auf `holder.ws` (Helfer oder Schüler-Session).

    Synchron übernehmen — VOR jedem await. So erkennt das `finally` des alten
    WS (das asynchron zum Reconnect läuft) an `holder.ws is websocket`, dass
    ein Reconnect übernommen hat, und löst KEINEN Teardown aus.
    """
    old_ws = holder.ws
    holder.ws = websocket
    # Reconnect: die alte Verbindung sauber schließen, statt sie verwaist offen
    # zu lassen.
    if old_ws is not None and old_ws is not websocket:
        try:
            await old_ws.close(code=4009, reason="Neue Verbindung")
        except Exception:
            pass


async def _deferred_end(state, hub, helper, student_id: int) -> None:
    """Verzögerter Teardown des Helfer-Schülers nach WS-Trennung (s. ws_scanner).

    Abgesichert: tests/test_scanner_reconnect.py::test_deferred_end_noop_on_reconnect,
    ::test_deferred_end_noop_on_student_changed"""
    try:
        await asyncio.sleep(_RECONNECT_GRACE_S)
    except asyncio.CancelledError:
        return
    # Re-Check 1: Helfer hat wieder eine Verbindung (Reconnect) → kein Teardown.
    if helper.ws is not None:
        return
    # Re-Check 2: Helfer wurde inzwischen weitergeschaltet/zurückgesetzt.
    if helper.student_id != student_id:
        return
    try:
        if helper.load_task is not None and not helper.load_task.done():
            helper.load_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await helper.load_task
            helper.load_task = None
        await end_student(
            state,
            hub,
            student_id,
            queue_status="pending",
            session_state="revoked",
        )
    except Exception:  # noqa: BLE001 — Sweeper-Loop-artige Robustheit: ein fehlgeschlagener Teardown darf den Task nicht crashen
        log.exception("deferred end_student für %d fehlgeschlagen", student_id)
    await safe_broadcast(hub, state)


@router.websocket("/ws/host")
async def ws_host(websocket: WebSocket, session_id: str | None = Cookie(default=None)) -> None:
    state = get_state()
    hub = get_hub()
    from ..config import get_config

    if not state.is_host_session_valid(session_id, get_config().host_session_ttl_s):
        await websocket.close(code=4003, reason="Nicht authentifiziert")
        return

    await websocket.accept()
    state.host_ws_connections.append(websocket)
    try:
        await hub.send_websocket(websocket, state.state_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            state.host_ws_connections.remove(websocket)
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Modus A — Helfer-Scanner: Dispatch-Tabelle für die Empfangsschleife
# ---------------------------------------------------------------------------


async def _handle_next(state, hub, helper, websocket, raw) -> None:
    # Aktuellen Schüler abschließen (kein Browser-Submit) und
    # nächsten Wartenden auf diesen Helfer setzen. `context_id`
    # (optional): Client hat vorgeschlagen, auf eine andere
    # (nicht-leere) Klasse umzuspringen, weil die eigene
    # Warteschlange leer ist — s. advance_helper.
    await advance_helper(state, hub, helper, context_id=raw.get("context_id"))


async def _handle_call(state, hub, helper, websocket, raw) -> None:
    # Helfer ruft einen konkreten Schüler aus der Warteschlangen-
    # Anzeige auf (Button bei wartenden, aktiven UND bereits fertigen
    # Schülern — Aktive werden Zuschauer und warten, bis der Schüler
    # frei ist; Fertige lassen sich so erneut aufrufen, z. B. um
    # nachträglich ein vergessenes Buch zu erfassen). Rein lokale
    # Zuweisung — kein IServ-/DB-Schreibzugriff. Bei 'pending'/'done'
    # erfolgt die Zuweisung direkt; zwischen Prüfung und Zuweisung
    # liegt kein Await, also atomar im Eventloop (kein Doppel-
    # Aufruf zweier Helfer auf denselben Schüler).
    sid = raw.get("student_id")
    target_pair = state.find_student_with_ctx(sid) if sid is not None else None
    target = target_pair[1] if target_pair else None
    # Selbst-Aufruf (Helfer ruft SEINEN EIGENEN aktiven Schüler erneut auf):
    # zählt bewusst wie ein neuer Zugriff, nicht wie ein reiner Refresh. Gibt
    # es eine Warteliste für diesen Schüler, gibt der Aufrufer seine
    # Aktivität ab (der Erste in der Liste wird per end_student-Beförderung
    # übernehmen) und stellt sich selbst hinten an — statt sich direkt
    # zurückzuholen (das wäre wieder ein Doppel-Aktiv-Fall). Ohne Warteliste
    # fällt dieser Fall unten in den normalen Reload-Pfad (end_student +
    # Neuzuweisen an denselben Helfer), der `loading` sendet und so auch das
    # Menü/Such-Panel schließt.
    self_recall_reload = False
    if target is not None and target.status == "active":
        owner = state.find_helper_for_student(sid)
        if owner is not None and owner.token == helper.token:
            if state.student_spectators.get(sid):
                await end_student(
                    state,
                    hub,
                    helper.student_id,
                    queue_status="pending",
                    session_state="revoked",
                    helper_notify={"type": "loading"},
                )
                await spectate_student(
                    state,
                    hub,
                    helper,
                    student_id=sid,
                    lastname=target.lastname,
                    firstname=target.firstname,
                    form=target.form,
                )
                return
            self_recall_reload = True
        else:
            # Schüler ist gerade aktiv, aber NICHT beim aufrufenden Helfer:
            # entweder bei einem ANDEREN Helfer (Queue-`call`/Lupe) ODER bei
            # einem Schülerclient (Modus B — dann ist `owner` None, weil
            # Modus-B-Pairing `status='active'` ohne `assigned_helper` setzt).
            # Statt eines Fehlers wird der Aufrufer Zuschauer (read-only
            # Bücherliste, live mitaktualisiert) und automatisch befördert,
            # sobald der Aktive den Schüler freigibt (end_student/
            # pop_next_spectator — Owner-unabhängig, greift auch beim
            # Selbst-Abschluss eines Schülerclients).
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=target.lastname,
                firstname=target.firstname,
                form=target.form,
            )
            return
    if not self_recall_reload and (target is None or target.status not in ("pending", "done")):
        await hub.send_websocket(
            websocket,
            {
                "type": "error",
                "msg": "Schüler nicht (mehr) in der Warteschlange",
            },
        )
        # Queue sofort nachpushen, damit der Client die aktuelle
        # Liste sieht (z. B. zwischenzeitlich von anderem Helfer
        # aufgerufen) — statt auf den nächsten Broadcast zu warten.
        await hub.broadcast_queue_size(state)
        return
    # Aufrufen aus einer fremden Klasse (anderer Klassen-Tab im
    # Helfer-Menü) ist erlaubt: der Helfer wird dabei an die Klasse
    # des aufgerufenen Schülers gebunden (helper.context_id), sodass
    # „Nächster" danach aus dieser Klasse zieht (Workflow „ich
    # bediene jetzt diese Klasse"). `(aktive)`-Helfer (context_id
    # None) werden beim ersten Aufruf ebenfalls gebunden.
    if target_pair[0].id != helper.context_id:
        rebind_helper_to_context(helper, target_pair[0].id)
    if helper.student_id is not None:
        # Aufrufen aus der Peek-Ansicht (Menü): der alte Schüler wird
        # NICHT abgeschlossen, sondern als 'pending' zurück in die
        # Warteschlange gelegt (noch nicht bearbeitet). Der Worker-
        # Context schließt (revoked), der Schüler bleibt aber in der
        # Queue verfügbar — wie beim Disconnect-Teardown
        # (`_deferred_end`). Der „Weiter"-Button (`next`) dagegen
        # schließt den Schüler als 'done' ab (s. advance_helper).
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="pending",
            session_state="revoked",
            helper_notify={"type": "loading"},  # Queue verbergen — neuer wird geladen
        )
    await assign_student_to_helper(state, hub, helper, target)


async def _handle_search_classes(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: Liste aller Klassen des gewählten Schuljahrs
    # (IServ, read-only). Schuljahrbezogen gecached, damit wieder-
    # holtes Öffnen der Suche keine IServ-Roundtrips auslöst.
    if state.iserv is None:
        await hub.send_websocket(websocket, {"type": "search_classes", "classes": []})
        return
    sy = state.selected_schoolyear
    cached = state.caches.class_names_cache.get(sy)
    if cached is None:
        try:
            cached = await state.iserv.get_class_names(sy)
        except Exception as e:  # noqa: BLE001
            log.warning("search_classes fehlgeschlagen: %s", e)
            await hub.send_websocket(
                websocket,
                {"type": "error", "msg": f"Klassen konnten nicht geladen werden: {e}"},
            )
            return
        state.caches.class_names_cache[sy] = cached
    await hub.send_websocket(websocket, {"type": "search_classes", "classes": cached})


async def _handle_search_students(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: alle Schüler einer Klasse (IServ, read-only),
    # schuljahrbezogen gecached (Key "schoolyear|form").
    form = str(raw.get("form") or "").strip()
    if state.iserv is None:
        await hub.send_websocket(
            websocket,
            {"type": "search_students", "form": form, "students": []},
        )
        return
    sy = state.selected_schoolyear
    key = f"{sy}|{form}"
    cached = state.caches.form_students_cache.get(key)
    if cached is None:
        try:
            cached = await state.iserv.get_students_for_form(form, sy)
        except Exception as e:  # noqa: BLE001
            log.warning("search_students fehlgeschlagen: %s", e)
            await hub.send_websocket(
                websocket,
                {"type": "error", "msg": f"Schüler konnten nicht geladen werden: {e}"},
            )
            return
        state.caches.form_students_cache[key] = cached
    await hub.send_websocket(
        websocket,
        {"type": "search_students", "form": form, "students": cached},
    )


async def _handle_search_call(state, hub, helper, websocket, raw) -> None:
    # Helfer-Lupe: gezielt einen beliebigen IServ-Schüler laden
    # (Schnellsprung — der Schüler muss NICHT in der Warteschlange
    # stehen). Aktuellen Schüler wie beim Peek-`call` auf 'pending'
    # zurückgeben, dann einen transienten QueueStudent (bewusst NICHT
    # in eine Queue eingetragen) via assign_student_to_helper laden.
    # Read-only: IServ/DB werden nur gelesen (get_student_info),
    # kein Write.
    sid = raw.get("student_id")
    form = str(raw.get("form") or "").strip()
    lastname = str(raw.get("lastname") or "").strip()
    firstname = str(raw.get("firstname") or "").strip()
    if sid is None or not form:
        await hub.send_websocket(websocket, {"type": "error", "msg": "Schüler/Klasse fehlt"})
        return
    try:
        sid = int(sid)
    except (TypeError, ValueError):
        await hub.send_websocket(websocket, {"type": "error", "msg": "Ungültige Schüler-ID"})
        return
    # Guard gegen Doppel-Öffnen: anders als `call` (nur pending/done aus der
    # eigenen Queue) kann die Lupe JEDEN Schüler treffen — auch einen, der
    # gerade bei einem ANDEREN Helfer/Client aktiv ist (Queue-`call` oder
    # ebenfalls Lupe). `find_helper_for_student` erkennt das unabhängig davon,
    # ob der Ziel-Schüler ein echter Queue-Eintrag oder selbst ein transienter
    # Lupe-Schüler ist (der steht in KEINER Queue, `find_student` würde ihn
    # also nicht als „belegt" erkennen). Statt eines Fehlers wird der
    # Aufrufer Zuschauer (s. spectate_student) und automatisch befördert,
    # sobald der aktive Helfer den Schüler beendet.
    owner = state.find_helper_for_student(sid)
    if owner is not None and owner.token == helper.token:
        # Selbst-Aufruf (Helfer sucht/ruft SEINEN EIGENEN aktiven Schüler per
        # Lupe erneut auf): zählt wie ein neuer Zugriff, s. Kommentar in
        # _handle_call. Gibt es eine Warteliste, gibt der Aufrufer seine
        # Aktivität ab (Erster in der Liste übernimmt) und stellt sich selbst
        # hinten an; sonst fällt der Fall unten in den normalen Reload-Pfad.
        if state.student_spectators.get(sid):
            await end_student(
                state,
                hub,
                helper.student_id,
                queue_status="pending",
                session_state="revoked",
                helper_notify={"type": "loading"},
            )
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=lastname,
                firstname=firstname,
                form=form,
            )
            return
    elif owner is not None:
        await spectate_student(
            state, hub, helper, student_id=sid, lastname=lastname, firstname=firstname, form=form
        )
        return
    else:
        # Kein Helfer-Owner — aber der Schüler kann trotzdem aktiv sein, wenn
        # er gerade per Schülerclient (Modus B) geladen wurde: Modus-B-Pairing
        # setzt `status='active'` OHNE `assigned_helper`, sodass
        # `find_helper_for_student` None liefert. Ohne diesen Guard würde
        # unten ein transienter Schüler erzeugt und per
        # `assign_student_to_helper` übernommen → Doppel-Aktiv-Konflikt mit
        # dem Schülerclient. Stattdessen Zuschauer werden und warten, bis der
        # Schüler frei ist (Owner-unabhängige Beförderung via end_student).
        queued = state.find_student(sid)
        if queued is not None and queued.status == "active":
            await spectate_student(
                state,
                hub,
                helper,
                student_id=sid,
                lastname=lastname,
                firstname=firstname,
                form=form,
            )
            return
    if helper.student_id is not None:
        await end_student(
            state,
            hub,
            helper.student_id,
            queue_status="pending",
            session_state="revoked",
            helper_notify={"type": "loading"},  # Queue verbergen — neuer wird geladen
        )
    student = QueueStudent(
        student_id=sid,
        lastname=lastname,
        firstname=firstname,
        form=form,
        status="active",
        assigned_helper=helper.token,
    )
    await assign_student_to_helper(state, hub, helper, student)


async def _handle_peek_queue(state, hub, helper, websocket, raw) -> None:
    # Menü-Toggle: Helfer schaltet auf die Warteschlangen-Ansicht,
    # während sein Schüler im Hintergrund verbunden bleibt (kein
    # Trennen, kein IServ-/DB-Zugriff). Peek-Flag setzen, damit
    # nachfolgende `broadcast_queue_size`-Updates diesen Helfer
    # erreichen, und sofort die aktuelle Queue pushen (für ein
    # unmittelbares Rendern, ohne auf den nächsten Broadcast zu
    # warten).
    helper.peeking = True
    await hub.send_scanner(
        helper.token,
        {
            "type": "queue_update",
            "queue_size": state.pending_count(helper.context_id),
            "queue": state.pending_queue_as_list(helper.context_id),
            "queue_all": state.queue_as_list(helper.context_id),
        },
    )
    # Frische Kontext-Übersicht (alle offenen Klassen + eigene) für
    # die Klassen-Reiter — ein Helfer mit aktivem Schüler bekommt
    # sonst keine Live-contexts_update (broadcast_queue_size erreicht
    # ihn nur, weil hier gerade peeking=True gesetzt wurde; das eigene
    # peek_queue sendet sie aber bewusst sofort, ohne auf den nächsten
    # Broadcast zu warten).
    await hub.send_scanner(
        helper.token,
        {
            "type": "contexts_update",
            "contexts": state.real_contexts_summary(),
            "own_context_id": helper.context_id,
        },
    )


async def _handle_peek_close(state, hub, helper, websocket, raw) -> None:
    # Menü-Toggle zurück zur Schüler-Ansicht. Kein Push nötig — der
    # Client stellt die Bücherliste lokal wieder her.
    helper.peeking = False


async def _handle_clear_book_alert(state, hub, helper, websocket, raw) -> None:
    # Helfer schließt sein Ausgemustert-Hinweis-Modal selbst (Button)
    # → Host-Meldung für diesen Schüler ebenfalls aufräumen, damit das
    # Now-Serving-Kästchen wieder normal angezeigt wird. Read-only,
    # kein IServ-/DB-Zugriff; nur ein Host-Broadcast.
    sid = helper.student_id
    if sid is not None:
        await hub.broadcast_host(
            {
                "type": "book_alert",
                "student_id": sid,
                "cleared": True,
            }
        )


async def _handle_print(state, hub, helper, websocket, raw) -> None:
    # Leihschein des aktuell zugewiesenen Schülers drucken.
    # Read-only PDF-Abruf + lokaler Druck (kein IServ-Submit).
    if helper.student_id is None:
        await hub.send_websocket(
            websocket,
            {"type": "print_result", "ok": False, "msg": "Kein Schüler zugewiesen"},
        )
        return
    # Seite 1 wird immer gedruckt; Seite 2 (Schüler-Leihschein) nur,
    # wenn der Helfer sie im Druck-Dialog aktiviert hat.
    second_page = bool(raw.get("second_page"))
    pages = None if second_page else "1"
    try:
        result = await print_loan_slip_for(state, helper.student_id, pages=pages)
        await hub.send_websocket(websocket, {"type": "print_result", **result})
    except Exception as e:  # noqa: BLE001 — Fehler dem Client melden
        log.exception("Leihschein-Druck (Scanner) fehlgeschlagen")
        await hub.send_websocket(websocket, {"type": "print_result", "ok": False, "msg": str(e)})


async def _handle_scan(state, hub, helper, websocket, raw) -> None:
    barcode = str(raw.get("value", "")).strip()
    if not barcode:
        return

    helper.last_scan = barcode
    log.info("Scan von Helper %s: %s", helper.token, barcode)

    student_id = helper.student_id
    if student_id is None:
        await hub.send_websocket(
            websocket,
            {
                "type": "scan_result",
                "barcode": barcode,
                "status": "error",
                "msg": "Kein Schüler zugewiesen",
            },
        )
        return

    # Scan verarbeiten: Buchungs-Vorabprüfung (im Lager? bestellt? Reihe
    # noch nicht ausgeliehen?) → buchen (Enter) oder — Gate aus — stagen.
    # Nicht erfüllt → Feld wird NICHT berührt.
    result = await process_scan(
        state,
        student_id,
        helper.vormerk_isbns,
        helper.lent_isbns,
        helper.lent_codes,
        barcode,
        source="helper",
    )
    # ISBN mitgeben, damit der Helferclient das gescannte Buch in seiner
    # Liste markieren kann.
    await hub.send_websocket(websocket, {"type": "scan_result", "barcode": barcode, **result})
    await hub.broadcast_host(state.state_snapshot())
    # Zuschauer (Spectator, s. spectate_student) bekommen denselben Scan
    # gespiegelt, damit ihre Bücherliste live mit dem aktiven Helfer
    # mitläuft — `spectator: True` unterdrückt clientseitig Statuszeile/
    # Alert-Modal (die bleiben „Warten bis Schüler frei…").
    spectators = state.student_spectators.get(student_id)
    if spectators:
        spectator_payload = {"type": "scan_result", "barcode": barcode, **result, "spectator": True}
        for waiter in list(spectators):
            await hub.send_scanner(waiter.token, spectator_payload)


_SCANNER_HANDLERS = {
    "next": _handle_next,
    "call": _handle_call,
    "search_classes": _handle_search_classes,
    "search_students": _handle_search_students,
    "search_call": _handle_search_call,
    "peek_queue": _handle_peek_queue,
    "peek_close": _handle_peek_close,
    "clear_book_alert": _handle_clear_book_alert,
    "print": _handle_print,
    "scan": _handle_scan,
}


@router.websocket("/ws/scanner/{token}")
async def ws_scanner(websocket: WebSocket, token: str) -> None:
    state = get_state()
    hub = get_hub()

    if token not in state.helper_sessions:
        await websocket.close(code=4004, reason="Ungültiger Token")
        return

    await websocket.accept()
    helper = state.helper_sessions[token]
    # Frische Verbindung → Peek-Zustand ist clientseitig nicht mehr gesetzt.
    helper.peeking = False
    # Reconnect (Seite erneut geöffnet): einen noch laufenden Grace-Teardown-
    # Task des gerade getrennten alten WS abräumen (sonst würde er nach der
    # Frist den soeben neugeladenen Schüler doch noch abbrechen).
    t = helper.end_task
    helper.end_task = None
    if t is not None and not t.done():
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await _take_over_ws(helper, websocket)

    # Schüler bereits zugewiesen? Info sofort schicken. Die Reihenfolge wird
    # anhand des Jahrgangs *dieses* Schülers ermittelt (nicht einer globalen
    # Kontext-Reihenfolge) — sonst würde die direkt danach folgende `settings`-
    # Nachricht sie bei klassenübergreifenden Warteschlangen wieder überschreiben.
    # Fallback (ohne Schüler): Reihenfolge des Helfer-Kontexts — `[]`, wenn der
    # Helfer keiner Klasse zugewiesen ist (kein stiller Rückfall auf eine
    # zufällig aktive fremde Klasse, s. `AppState.book_order_of`).
    book_order = state.book_order_of(helper.context_id)
    if helper.student_id is not None and state.iserv is not None:
        student = state.find_student(helper.student_id)
        # Form: aus dem QueueStudent, falls vorhanden (call/next); sonst aus
        # helper.student_form — der Lupe-Schüler (search_call) steht bewusst
        # NICHT in einer Queue, seine Form wurde darum beim Zuweisen am Helfer
        # hinterlegt. So wird auch der Lupe-Schüler beim Reconnect wiederher-
        # gestellt (inkl. Worker-Reload) statt als `waiting` zu verfallen.
        form = student.form if student is not None else (helper.student_form or "")
        try:
            info = await state.iserv.get_student_info(helper.student_id, state.selected_schoolyear)
            info = await hydrate_student_info(state, info, form, helper)
            book_order = info["book_order"]
            helper.last_scan = None  # Worker-Page wird ggf. neu geladen → Feld leer
            # Modus A: Bücherliste sofort. Sends über das Hub-Lock
            # (send_websocket), damit sie nicht mit den Sends des In-Flight-
            # Lade-Tasks (send_scanner auf denselben neuen WS) interleaven.
            await hub.send_websocket(websocket, {"type": "student_info", "student": info})
            # Lädt der aktive Helfer seine Seite neu, soll sich die
            # Bücherliste auch bei allen Spectators dieses Schülers
            # aktualisieren (deren Ansicht läuft sonst mit einem veralteten
            # Stand weiter, bis der nächste Scan kommt).
            await broadcast_student_info_to_spectators(state, hub, helper.student_id, info)
            load_inflight = helper.load_task is not None and not helper.load_task.done()
            worker_session = state.student_worker_sessions.get(helper.student_id)
            worker_present = worker_session is not None
            if worker_present:
                # Worker war bereits bereit → Seite im Worker neu laden
                # (read-only GET-Reload auf dem bestehenden Context, kein
                # neuer Context). Identität danach re-checken: wurde der
                # Worker während des Reloads freigegeben (z. B. /api/skip),
                # KEIN `worker_ready` senden, sondern Fehler.
                ws_ref = worker_session
                reload_ok = False
                try:
                    await ws_ref.reload()
                    reload_ok = state.student_worker_sessions.get(helper.student_id) is ws_ref
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "Worker-Reload (Reconnect) für %d fehlgeschlagen: %s", helper.student_id, e
                    )
                    reload_ok = False
                if reload_ok:
                    await hub.send_websocket(websocket, {"type": "worker_ready"})
                else:
                    await hub.send_websocket(
                        websocket, {"type": "error", "msg": "Worker-Reload fehlgeschlagen"}
                    )
            elif load_inflight:
                # Worker wird gerade erst geöffnet (open_student läuft).
                # KEIN `worker_ready` senden — der In-Flight-Lade-Task
                # (`load_and_push_helper_student`) liefert ihn über
                # send_scanner(token) an den neuen WS. student_info steht
                # schon (oben gesendet), ggf. doppelt (vom In-Flight-Task) —
                # harmlos.
                pass
            else:
                # Degraded-Modus (kein worker_pool) oder Worker nie
                # bereit: sofort `worker_ready` senden.
                await hub.send_websocket(websocket, {"type": "worker_ready"})
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})
    elif helper.spectating_student_id is not None and state.iserv is not None:
        # Zuschauer (Spectator) lädt seine Seite neu: Warteposition bleibt
        # erhalten (die Disconnect-Behandlung entfernt ihn NICHT aus
        # `state.student_spectators` — er wartet also mit seiner bisherigen
        # Wartezeit weiter, statt sich hinten anzustellen). Nur die Ansicht
        # wiederherstellen, kein neuer Eintrag in der Warteliste.
        try:
            info = await state.iserv.get_student_info(
                helper.spectating_student_id, state.selected_schoolyear
            )
            info = await hydrate_student_info(state, info, helper.student_form or "", helper)
            book_order = info["book_order"]
            await hub.send_websocket(
                websocket, {"type": "student_info", "student": info, "spectator": True}
            )
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})
    else:
        await hub.send_websocket(
            websocket,
            {
                "type": "waiting",
                "msg": "Warte auf Schüler-Zuweisung",
                "queue_size": state.pending_count(helper.context_id),
                "queue": state.pending_queue_as_list(helper.context_id),
                "queue_all": state.queue_as_list(helper.context_id),
            },
        )

    # Host-Default „Schüler-Leihschein" (Druck-Dialog) + Bücher-Reihenfolge.
    await hub.send_websocket(
        websocket,
        {
            "type": "settings",
            "slip_second_page": state.settings.slip_second_page_default,
            "book_order": book_order,
        },
    )

    # Kontext-Übersicht (alle offenen Klassen + eigene Klasse) schicken, damit
    # ein Idle-Helfer sofort die Klassen-Reiter der Warteschlangen-Ansicht hat.
    await hub.send_websocket(
        websocket,
        {
            "type": "contexts_update",
            "contexts": state.real_contexts_summary(),
            "own_context_id": helper.context_id,
        },
    )

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Malformedes Frame (kein valides JSON) — nicht tödlich, Client
                # bleibt verbunden. Loggen und ignorieren, statt die Schleife
                # mit einem rohen Traceback sterben zu lassen.
                log.warning("Ungültiges JSON-Frame vom Scanner-WS (token=%s) — ignoriert", token)
                continue
            mtype = raw.get("type")

            handler = _SCANNER_HANDLERS.get(mtype)
            if handler is not None:
                await handler(state, hub, helper, websocket, raw)
            # Unbekannter/nicht behandelter Typ → ignorieren (wie bisher).

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn keine neue Verbindung übernommen hat.
        # Abgesichert: tests/test_ws_scanner.py::test_finally_noop_when_ownership_stolen
        if helper.ws is websocket:
            helper.ws = None
            if helper.student_id is not None:
                # Eventuell noch laufenden Grace-Task der vorigen Trennung
                # abräumen (z. B. zweite Trennung während der Frist) — synchron
                # lesen+nullen, damit ein konkurrierender Reconnect nicht den
                # neu gesetzten Task überschreibt.
                t0 = helper.end_task
                helper.end_task = None
                if t0 is not None and not t0.done():
                    t0.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t0
                helper.end_task = asyncio.create_task(
                    _deferred_end(state, hub, helper, helper.student_id)
                )
            # Absichtlich KEIN Aufräumen von `spectating_student_id`/
            # `student_spectators` hier: ein Zuschauer soll seine Warteposition
            # über einen Reconnect (Seiten-Reload) hinweg behalten (nicht
            # hinten anstellen). `pop_next_spectator` überspringt tote
            # Einträge (ws is None) bei der Beförderung ohnehin defensiv —
            # ein endgültig verlassener Spectator räumt sich so spätestens
            # bei seiner eigenen Beförderung selbst auf, ohne den Platz eines
            # echten Reconnects vorzeitig freizugeben.
        # else: Reconnect hat übernommen — nichts tun.
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Modus B — iPad-Display (nur QR, keine Schülerdaten)
# ---------------------------------------------------------------------------


@router.websocket("/ws/display")
async def ws_display(websocket: WebSocket) -> None:
    state = get_state()
    hub = get_hub()

    await websocket.accept()
    display = DisplaySession(
        display_id=uuid.uuid4().hex[:12], registration_code=gen_registration_code()
    )
    state.displays[display.display_id] = display
    display.ws = websocket
    await send_display_update(state, display)  # zeigt zunächst den Registrierungscode
    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            # Display sendet nichts Inhaltliches; receive dient der Trennungserkennung.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.displays.pop(display.display_id, None)
        await safe_broadcast(hub, state)


# ---------------------------------------------------------------------------
# Modus B — Schüler-Session (Pairing-Gate + Scan)
# ---------------------------------------------------------------------------


@router.websocket("/ws/student/{session_token}")
async def ws_student(websocket: WebSocket, session_token: str) -> None:
    state = get_state()
    hub = get_hub()

    session = state.student_sessions.get(session_token)
    if not session or session.state not in ("pending_pairing", "paired"):
        # Ungültiger/entwerteter Token → neutrale "Vorgang abgeschlossen"-Seite.
        # accept() VOR close(), damit der Browser den 4006-Code zuverlässig
        # erhält (sonst sieht er nur 1006 und kann Token-Tod nicht erkennen).
        await websocket.accept()
        await websocket.close(code=4006, reason="Session ungültig")
        return

    await websocket.accept()
    # Reconnect: vorherige Verbindung derselben Session sauber schließen.
    await _take_over_ws(session, websocket)
    session.last_activity = datetime.now()

    if session.state == "pending_pairing":
        await hub.send_websocket(
            websocket, {"type": "pending", "pairing_code": session.pairing_code}
        )
    elif session.state == "paired" and session.student_id is not None:
        # Reconnect nach Pairing: Identität (ohne Bücher) erneut senden; die
        # Bücherliste kommt mit `worker_ready` — sofort, wenn der Worker bereits
        # steht, sonst liefert sie der noch laufende Lade-Task.
        try:
            info = await state.iserv.get_student_info(session.student_id, state.selected_schoolyear)
            qs = state.find_student(session.student_id)
            info = await hydrate_student_info(state, info, qs.form if qs else "", session)
            books = info.get("books", [])
            await hub.send_websocket(
                websocket,
                {
                    "type": "student_info",
                    "student": {**info, "books": []},
                    "payment_overridden": session.payment_overridden,
                },
            )
            load_inflight = session.load_task is not None and not session.load_task.done()
            worker_present = state.student_worker_sessions.get(session.student_id) is not None
            if not load_inflight or worker_present:
                await hub.send_websocket(websocket, {"type": "worker_ready", "books": books})
            # Blockierendes Ausgemustert-Hinweis-Modal überlebt einen Reconnect
            # (z. B. Seiten-Reload) — erst der Host darf es per Button schließen.
            if session.book_alert_open and session.book_alert_payload:
                await hub.send_websocket(websocket, session.book_alert_payload)
        except Exception as e:
            await hub.send_websocket(websocket, {"type": "error", "msg": str(e)})

    await hub.broadcast_host(state.state_snapshot())

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                # Malformedes Frame — loggen und weiterlauschen, statt die
                # Schleife mit Traceback sterben zu lassen.
                log.warning(
                    "Ungültiges JSON-Frame vom Schüler-WS (session=%s) — ignoriert",
                    getattr(session, "session_token", "?"),
                )
                continue
            session.last_activity = datetime.now()
            mtype = raw.get("type")

            if mtype == "scan":
                barcode = str(raw.get("value", "")).strip()
                if not barcode:
                    continue
                if session.state != "paired" or session.student_id is None:
                    await hub.send_websocket(
                        websocket,
                        {
                            "type": "scan_result",
                            "barcode": barcode,
                            "status": "error",
                            "msg": "Noch nicht freigegeben",
                        },
                    )
                    continue
                if session.book_alert_open:
                    # Blockierendes Hinweis-Modal (ausgemustertes Buch) noch offen —
                    # erst der Host darf per Button freigeben. Barcode ignorieren.
                    continue
                session.last_scan = barcode
                # Scan verarbeiten: Buchungs-Vorabprüfung → buchen (Enter) oder
                # — Gate aus — stagen. Nicht erfüllt → Feld wird NICHT berührt.
                result = await process_scan(
                    state,
                    session.student_id,
                    session.vormerk_isbns,
                    session.lent_isbns,
                    session.lent_codes,
                    barcode,
                )
                payload = {"type": "scan_result", "barcode": barcode, **result}
                # Ausgemustert ODER an jemand anderen verliehen → blockierendes
                # Hinweis-Modal am Schüler-Client (kein eigener Schließen-Button,
                # Host gibt per /api/clear-book-alert frei). „An sich selbst
                # verliehen" (book_already_lent/series_already_lent) ist nur ein
                # Hinweis und nicht blockierend — der Schüler schließt ihn selbst.
                if result.get("status") in ("book_deleted", "not_in_stock"):
                    session.book_alert_open = True
                    session.book_alert_payload = payload
                await hub.send_websocket(websocket, payload)
                await hub.broadcast_host(state.state_snapshot())

            elif mtype == "finish":
                # Schüler schließt selbst ab → harter Zugriffsentzug.
                if session.student_id is not None:
                    await end_student(
                        state,
                        hub,
                        session.student_id,
                        queue_status="done",
                        session_state="completed",
                    )
                else:
                    from ..sessions import invalidate_session

                    await invalidate_session(state, session, "completed", reason="self-finish")
                break

    except WebSocketDisconnect:
        pass
    finally:
        # WS-Referenz nur lösen, wenn es noch unsere Verbindung ist.
        if session.ws is websocket:
            session.ws = None
        await safe_broadcast(hub, state)
