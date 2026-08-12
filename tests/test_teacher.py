"""Tests für die Lehrkraft-Statusansicht (`/teacher`, docs/teacher_status_page_plan.md).

Pairing-Flow (QR minten/ersetzen, Autorisieren, Trennen), den token-
authentifizierten Statuswechsel-Endpunkten (`pending <-> absent`), die
`teacher_snapshot`-Privacy (keine anderen Klassen/Bücher/Zahldaten/Drucker/
Host-Einstellungen), Klassen-Isolation, WebSocket-Pairing/Reconnect/
Entwertung sowie die Teardown-Pfade (Klasse schließen, Schuljahreswechsel).
Über echtes HTTP (`starlette.testclient.TestClient`) gegen `create_app()`
ohne Lifespan, plus ein paar Einheitstests direkt gegen `AppState`/`Hub`.
"""

from __future__ import annotations

import asyncio

import pytest

import server.hub as hub_module
import server.routes.auth as auth_routes
import server.routes.booklists as booklists_routes
import server.routes.classes as classes_routes
import server.routes.helpers as helpers_routes
import server.routes.modus_b as modus_b_routes
import server.routes.queue as queue_routes
import server.routes.settings as settings_routes
import server.routes.slips as slips_routes
import server.routes.teacher as teacher_routes
import server.sessions as sessions
from server.config import Config
from server.hub import Hub
from server.routes import _deps as deps_routes
from server.state import AppState, ClassContext, QueueStudent, TeacherSession


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_json(self, msg) -> None:
        self.sent.append(msg)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code


class _FakeHub:
    def __init__(self) -> None:
        self.broadcasts: list[dict] = []

    async def broadcast_host(self, snapshot) -> None:
        self.broadcasts.append(snapshot)

    async def broadcast_settings(self, *a, **kw) -> None:
        pass

    async def send_scanner(self, *a, **kw) -> None:
        pass

    async def send_websocket(self, ws, msg) -> bool:
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001
            return False
        return True


def _make_config(**over) -> Config:
    base = dict(
        iserv_domain="example.org",
        iserv_username="u",
        iserv_password="p",
        host_password="secret",
        allow_booking=False,
    )
    base.update(over)
    return Config(**base)


_ROUTE_MODULES = [
    deps_routes,
    auth_routes,
    classes_routes,
    booklists_routes,
    helpers_routes,
    queue_routes,
    slips_routes,
    modus_b_routes,
    settings_routes,
    teacher_routes,
]


@pytest.fixture
def ctx(monkeypatch):
    """Frischer State + Config + Fake-Hub; gültige Host-Session 'sid'."""
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub_inst = _FakeHub()
    for mod in _ROUTE_MODULES:
        if hasattr(mod, "get_state"):
            monkeypatch.setattr(mod, "get_state", lambda: state)
        if hasattr(mod, "get_config"):
            monkeypatch.setattr(mod, "get_config", lambda: cfg)
        if hasattr(mod, "get_hub"):
            monkeypatch.setattr(mod, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_hub", lambda: hub_inst)
    monkeypatch.setattr(sessions, "get_state", lambda: state)
    monkeypatch.setattr(hub_module, "get_hub", lambda: hub_inst)
    return state, cfg, hub_inst


def _open_ctx(state: AppState, form: str = "10a", students: int = 2) -> ClassContext:
    # Basis-ID von der Anzahl bereits offener Kontexte in DIESEM State ableiten
    # (jeder Test bekommt über die `ctx`-Fixture ohnehin einen frischen State) —
    # verhindert student_id-Kollisionen zwischen zwei Klassen im selben Test.
    base = 100 + 1000 * len(state.contexts)
    ctx = state.open_context(form)
    for i in range(students):
        ctx.queue.append(
            QueueStudent(student_id=base + i, lastname=f"L{i}", firstname=f"F{i}", form=form)
        )
    return ctx


# ---- QR minten -------------------------------------------------------------


def test_qr_requires_host(client, ctx):
    r = client.get("/api/teacher/qr?context_id=x")
    assert r.status_code == 403


def test_qr_missing_context_id_400(client, ctx):
    r = client.get("/api/teacher/qr", cookies={"session_id": "sid"})
    assert r.status_code == 400


def test_qr_unknown_context_404(client, ctx):
    r = client.get("/api/teacher/qr?context_id=nope", cookies={"session_id": "sid"})
    assert r.status_code == 404


def test_qr_mints_session_with_token_in_url(client, ctx):
    state, _, hub_inst = ctx
    c = _open_ctx(state)
    r = client.get(f"/api/teacher/qr?context_id={c.id}", cookies={"session_id": "sid"})
    assert r.status_code == 200
    d = r.json()
    assert "/teacher?token=" in d["url"]
    assert d["qr"].startswith("data:image/")
    token = d["url"].rsplit("token=", 1)[1]
    assert len(token) >= 32
    assert token in state.teacher_sessions
    assert state.teacher_sessions[token].context_id == c.id
    assert state.teacher_sessions[token].authorized is False
    # Regression: ohne Broadcast bliebe die Lehrkraft-Kachel im Klassen-Tab
    # beim Host auf altem Stand, bis zufällig ein unabhängiger Snapshot kommt.
    assert len(hub_inst.broadcasts) == 1
    assert hub_inst.broadcasts[0]["contexts"][c.id]["teacher"] == {
        "registration_code": state.teacher_sessions[token].registration_code,
        "authorized": False,
        "connected": False,
    }


def test_qr_replaces_unauthorized_session_and_closes_old_ws(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    old_ws = _FakeWS()
    old = TeacherSession(token="oldtok", context_id=c.id, registration_code="AAAA", ws=old_ws)
    state.teacher_sessions["oldtok"] = old

    r = client.get(f"/api/teacher/qr?context_id={c.id}", cookies={"session_id": "sid"})
    assert r.status_code == 200
    assert "oldtok" not in state.teacher_sessions
    assert old_ws.closed is True
    new_token = r.json()["url"].rsplit("token=", 1)[1]
    assert new_token in state.teacher_sessions
    assert new_token != "oldtok"


def test_qr_blocked_when_authorized_session_exists(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.get(f"/api/teacher/qr?context_id={c.id}", cookies={"session_id": "sid"})
    assert r.status_code == 409
    assert "tok" in state.teacher_sessions  # unangetastet


def test_qr_for_different_classes_do_not_collide(client, ctx):
    """Zwei Klassen können unabhängig je eine (unautorisierte) Session halten."""
    state, _, _ = ctx
    a = _open_ctx(state, form="10a")
    b = _open_ctx(state, form="10b")
    ra = client.get(f"/api/teacher/qr?context_id={a.id}", cookies={"session_id": "sid"})
    rb = client.get(f"/api/teacher/qr?context_id={b.id}", cookies={"session_id": "sid"})
    assert ra.status_code == rb.status_code == 200
    tok_a = ra.json()["url"].rsplit("token=", 1)[1]
    tok_b = rb.json()["url"].rsplit("token=", 1)[1]
    assert tok_a != tok_b
    assert state.teacher_sessions[tok_a].context_id == a.id
    assert state.teacher_sessions[tok_b].context_id == b.id


# ---- Autorisieren -----------------------------------------------------------


def test_authorize_missing_fields_400(client, ctx):
    r = client.post(
        "/api/teacher/authorize", json={"context_id": "", "registration_code": ""},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


def test_authorize_wrong_code_404(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA"
    )
    r = client.post(
        "/api/teacher/authorize",
        json={"context_id": c.id, "registration_code": "ZZZZ"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404
    assert state.teacher_sessions["tok"].authorized is False


def test_authorize_already_authorized_404(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post(
        "/api/teacher/authorize",
        json={"context_id": c.id, "registration_code": "AAAA"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_authorize_success_marks_authorized_and_pushes(client, ctx):
    state, _, hub_inst = ctx
    c = _open_ctx(state)
    ws = _FakeWS()
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", ws=ws
    )
    r = client.post(
        "/api/teacher/authorize",
        json={"context_id": c.id, "registration_code": "aaaa"},  # case-insensitive
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert state.teacher_sessions["tok"].authorized is True
    # Push an die WS: teacher_state (nicht mehr registration).
    assert ws.sent[-1]["type"] == "teacher_state"
    assert ws.sent[-1]["class_form"] == "10a"
    # Host-Snapshot ebenfalls aktualisiert.
    assert len(hub_inst.broadcasts) == 1


# ---- Trennen ----------------------------------------------------------------


def test_disconnect_unknown_404(client, ctx):
    r = client.post(
        "/api/teacher/disconnect", json={"context_id": "nope"}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 404


def test_disconnect_removes_session_and_closes_ws(client, ctx):
    state, _, hub_inst = ctx
    c = _open_ctx(state)
    ws = _FakeWS()
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True, ws=ws
    )
    r = client.post(
        "/api/teacher/disconnect", json={"context_id": c.id}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert "tok" not in state.teacher_sessions
    assert ws.closed is True
    assert ws.close_code == 4009
    assert len(hub_inst.broadcasts) == 1


def test_disconnect_works_pre_authorization(client, ctx):
    """Der Host kann eine noch nicht bestätigte Einladung auch abbrechen."""
    state, _, _ = ctx
    c = _open_ctx(state)
    ws = _FakeWS()
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", ws=ws
    )
    r = client.post(
        "/api/teacher/disconnect", json={"context_id": c.id}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert "tok" not in state.teacher_sessions


# ---- state_snapshot: Lehrkraft-Kachel je Klassen-Tab ------------------------


def test_snapshot_teacher_tile_shape_and_no_token_leak(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    state.teacher_sessions["secrettok"] = TeacherSession(
        token="secrettok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.get("/api/state", cookies={"session_id": "sid"})
    assert r.status_code == 200
    tile = r.json()["contexts"][c.id]["teacher"]
    assert tile == {"registration_code": "AAAA", "authorized": True, "connected": False}
    assert "secrettok" not in r.text  # Token wird NIE an den Host-Snapshot geleakt


def test_snapshot_teacher_tile_none_without_session(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    r = client.get("/api/state", cookies={"session_id": "sid"})
    assert r.json()["contexts"][c.id]["teacher"] is None


# ---- teacher_snapshot: Privacy + Klassen-Isolation --------------------------


def test_teacher_snapshot_shape_and_privacy(ctx):
    """Nur erlaubte Felder — keine Zahl-/Anmeldedaten, kein Helfer, kein
    Drucker-/Worker-/Host-Setting, keine andere Klasse."""
    state, _, _ = ctx
    c = _open_ctx(state, form="10a", students=1)
    student = c.queue[0]
    student.enrolled = True
    student.paid = False
    student.amount_open = 12.5
    student.assigned_helper = "geheim"
    student.books_total = 3
    student.done_isbns = {"a", "b"}
    _open_ctx(state, form="10b", students=1)  # andere Klasse — darf NICHT auftauchen

    snap = state.teacher_snapshot(c.id)
    assert snap["class_form"] == "10a"
    assert snap["counts"] == {"pending": 1, "active": 0, "done": 0, "skipped": 0, "absent": 0}
    assert snap["done_collected"] is False
    assert len(snap["students"]) == 1
    s = snap["students"][0]
    assert set(s.keys()) == {
        "student_id", "lastname", "firstname", "status",
        "auto_skipped", "helper_scanned",
        "books_total", "books_done", "slip_printing", "slip_printed", "slip_collected",
    }
    assert s["books_done"] == 2
    assert snap["slip_collected_count"] == 0
    assert "10b" not in str(snap)  # andere Klasse taucht nirgends auf
    assert "paid" not in str(snap.keys())


def test_teacher_snapshot_maps_auto_skipped_done_to_skipped(ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=2)
    c.queue[0].status = "done"
    c.queue[0].auto_skipped = True
    c.queue[1].status = "done"

    snap = state.teacher_snapshot(c.id)

    assert snap["counts"] == {"pending": 0, "active": 0, "done": 1, "skipped": 1, "absent": 0}
    by_id = {student["student_id"]: student for student in snap["students"]}
    assert by_id[c.queue[0].student_id]["status"] == "skipped"
    assert by_id[c.queue[0].student_id]["auto_skipped"] is True
    assert by_id[c.queue[1].student_id]["status"] == "done"
    assert by_id[c.queue[1].student_id]["auto_skipped"] is False


def test_teacher_snapshot_five_counts_cover_every_student_including_auto_skipped(ctx):
    """Die fünf getrennten Lehrerzähler bilden die sichtbare Schülerliste
    vollständig und überschneidungsfrei ab."""
    state, _, _ = ctx
    c = _open_ctx(state, students=6)
    c.queue[0].status = "pending"
    c.queue[1].status = "active"
    c.queue[2].status = "absent"
    c.queue[3].status = "done"
    c.queue[4].status = "skipped"
    c.queue[5].status = "done"
    c.queue[5].auto_skipped = True

    snap = state.teacher_snapshot(c.id)

    assert snap["counts"] == {
        "pending": 1,
        "active": 1,
        "done": 1,
        "skipped": 2,
        "absent": 1,
    }
    assert sum(snap["counts"].values()) == len(c.queue) == len(snap["students"])
    assert {s["status"] for s in snap["students"]} == {
        "pending", "active", "absent", "done", "skipped"
    }


def test_teacher_snapshot_counts_absent(ctx):
    """Ein von der Lehrkraft als abwesend markierter Schüler erscheint in der
    Lehreransicht als eigener Status `absent` (nicht als `skipped`)."""
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "absent"

    snap = state.teacher_snapshot(c.id)
    assert snap["counts"] == {"pending": 0, "active": 0, "done": 0, "skipped": 0, "absent": 1}
    assert snap["students"][0]["status"] == "absent"


def test_teacher_snapshot_propagates_helper_scanned(ctx):
    """Ein abwesender Schüler, dessen Bücher ein Helfer eingescant hat, ist in
    der Lehreransicht `done` mit `helper_scanned=True` — Grundlage für die
    „Leihschein & Bücherstapel entgegengenommen"-Checkbox."""
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    student = c.queue[0]
    student.status = "done"
    student.slip_printed = True
    student.helper_scanned = True

    snap = state.teacher_snapshot(c.id)
    s = snap["students"][0]
    assert s["status"] == "done"
    assert s["helper_scanned"] is True


def test_teacher_snapshot_unknown_context_is_empty(ctx):
    state, _, _ = ctx
    snap = state.teacher_snapshot("nope")
    assert snap == {
        "class_form": None,
        "counts": {"pending": 0, "active": 0, "done": 0, "skipped": 0, "absent": 0},
        "students": [],
        "done_collected": False,
        "slip_collected_count": 0,
    }


def test_teacher_snapshot_loading_context_is_empty(ctx):
    """Ein noch ladender Kontext (Öffnen läuft) liefert nichts — analog
    `state_snapshot`, das ladende Kontexte ausblendet."""
    state, _, _ = ctx
    c = _open_ctx(state)
    c.loading = True
    snap = state.teacher_snapshot(c.id)
    assert snap["students"] == []


# ---- Statuswechsel: skip / undo-skip (token-authentifiziert) ---------------


def test_skip_requires_token(client, ctx):
    r = client.post("/api/teacher/skip", json={"token": "", "student_id": 100})
    assert r.status_code == 400


def test_skip_unknown_token_403(client, ctx):
    r = client.post("/api/teacher/skip", json={"token": "nope", "student_id": 100})
    assert r.status_code == 403


def test_skip_unauthorized_session_403(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA"
    )
    r = client.post("/api/teacher/skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 403


def test_skip_pending_to_absent(client, ctx):
    state, hub_inst_cfg, hub_inst = ctx
    c = _open_ctx(state)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post("/api/teacher/skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 200
    assert c.queue[0].status == "absent"
    assert len(hub_inst.broadcasts) == 1


def test_skip_forbidden_from_active(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "active"
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post("/api/teacher/skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 409
    assert c.queue[0].status == "active"


def test_skip_forbidden_from_done(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "done"
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post("/api/teacher/skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 409
    assert c.queue[0].status == "done"


def test_skip_student_of_other_class_404(client, ctx):
    """Klassen-Isolation: ein Token darf niemals Schüler EINER ANDEREN Klasse
    steuern, selbst wenn die student_id existiert."""
    state, _, _ = ctx
    a = _open_ctx(state, form="10a", students=1)
    b = _open_ctx(state, form="10b", students=1)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=a.id, registration_code="AAAA", authorized=True
    )
    r = client.post("/api/teacher/skip", json={"token": "tok", "student_id": b.queue[0].student_id})
    assert r.status_code == 404
    assert b.queue[0].status == "pending"


def test_undo_skip_only_from_absent(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    # Noch pending -> 409
    r = client.post("/api/teacher/undo-skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 409

    # Ein vom Host übersprungener Schüler ist kein Lehrer-Abwesenheitsstatus
    # und darf über diesen Endpunkt ebenfalls nicht zurückgesetzt werden.
    c.queue[0].status = "skipped"
    r = client.post("/api/teacher/undo-skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 409

    c.queue[0].status = "absent"
    r = client.post("/api/teacher/undo-skip", json={"token": "tok", "student_id": 100})
    assert r.status_code == 200
    assert c.queue[0].status == "pending"


def test_absent_student_callable_like_pending(ctx):
    """Ein abwesender Schüler bleibt in der Warteschlange und ist aufrufbar —
    `next_pending`/`pending_queue_as_list` behandeln ihn wie einen wartenden."""
    state, _, _ = ctx
    c = _open_ctx(state, students=2)
    c.queue[0].status = "absent"
    c.queue[1].status = "pending"

    assert state.next_pending(c.id).student_id == c.queue[0].student_id
    assert state.pending_count(c.id) == 2
    ids = [s["student_id"] for s in state.pending_queue_as_list(c.id)]
    assert ids == [c.queue[0].student_id, c.queue[1].student_id]


def test_absent_student_not_pairable(client, ctx):
    """Ein abwesender Schüler kann NICHT mit einem Schülerclient gepaart werden
    (`student_pair` blockt `absent` — die einzige Einschränkung gegenüber
    `pending`)."""
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "absent"
    session = sessions.create_student_session(state)
    session.pairing_code = "1234"

    r = client.post(
        "/api/student/pair",
        json={"pairing_code": "1234", "student_id": c.queue[0].student_id},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
    assert c.queue[0].status == "absent"


def test_absent_student_helper_scan_available(client, ctx):
    """Der Helfer-Scan-Einmal-QR bleibt für abwesende Schüler verfügbar."""
    state, _, cfg = ctx
    cfg.host_ip = "10.0.0.9"
    c = _open_ctx(state, students=1)
    c.queue[0].status = "absent"

    r = client.post(
        "/api/helper-scan/start",
        json={"student_id": c.queue[0].student_id},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "h=" in r.json()["url"]


# ---- Statuswechsel: slip-collected ("Leihschein entgegengenommen") --------


def test_slip_collected_requires_authorized_session(client, ctx):
    r = client.post("/api/teacher/slip-collected", json={"token": "nope", "student_id": 100})
    assert r.status_code == 403


def test_slip_collected_requires_printed_slip(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.done_collected = True
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post(
        "/api/teacher/slip-collected", json={"token": "tok", "student_id": 100, "collected": True}
    )
    assert r.status_code == 409
    assert c.queue[0].slip_collected is False


def test_slip_collected_requires_class_option(client, ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "done"
    c.queue[0].slip_printed = True
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )

    r = client.post(
        "/api/teacher/slip-collected",
        json={"token": "tok", "student_id": 100, "collected": True},
    )

    assert r.status_code == 409
    assert c.queue[0].slip_collected is False


def test_slip_collected_sets_idempotently_and_cannot_be_unset(client, ctx):
    state, _, hub_inst = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "done"
    c.queue[0].slip_printed = True
    c.done_collected = True
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True
    )
    r = client.post(
        "/api/teacher/slip-collected", json={"token": "tok", "student_id": 100, "collected": True}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "slip_collected": True}
    assert c.queue[0].slip_collected is True
    assert len(hub_inst.broadcasts) == 1
    assert hub_inst.broadcasts[0]["contexts"][c.id]["queue"][0]["slip_collected"] is True

    # Wiederholtes Setzen bleibt erfolgreich und lässt den Marker gesetzt.
    r = client.post(
        "/api/teacher/slip-collected", json={"token": "tok", "student_id": 100, "collected": True}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "slip_collected": True}
    assert c.queue[0].slip_collected is True

    # Ein Request zum Zurücknehmen wird abgewiesen und darf den State nicht
    # verändern. Das globale reset_progress bleibt der bewusste neue Durchlauf.
    r = client.post(
        "/api/teacher/slip-collected", json={"token": "tok", "student_id": 100, "collected": False}
    )
    assert r.status_code == 409
    assert c.queue[0].slip_collected is True


def test_slip_collected_student_of_other_class_404(client, ctx):
    state, _, _ = ctx
    a = _open_ctx(state, form="10a", students=1)
    b = _open_ctx(state, form="10b", students=1)
    b.queue[0].slip_printed = True
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=a.id, registration_code="AAAA", authorized=True
    )
    r = client.post(
        "/api/teacher/slip-collected",
        json={"token": "tok", "student_id": b.queue[0].student_id, "collected": True},
    )
    assert r.status_code == 404
    assert b.queue[0].slip_collected is False


def test_teacher_snapshot_slip_collected_count(ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=2)
    c.done_collected = True
    c.queue[0].status = "done"
    c.queue[0].slip_printed = True
    c.queue[0].slip_collected = True
    snap = state.teacher_snapshot(c.id)
    assert snap["slip_collected_count"] == 1
    collected_flags = {s["student_id"]: s["slip_collected"] for s in snap["students"]}
    assert collected_flags[c.queue[0].student_id] is True
    assert collected_flags[c.queue[1].student_id] is False


def test_slip_collected_is_ignored_in_teacher_count_when_option_disabled(ctx):
    state, _, _ = ctx
    c = _open_ctx(state, students=1)
    c.queue[0].status = "done"
    c.queue[0].slip_printed = True
    c.queue[0].slip_collected = True

    snap = state.teacher_snapshot(c.id)

    assert snap["done_collected"] is False
    assert snap["slip_collected_count"] == 0


# ---- WebSocket --------------------------------------------------------------


def _patch_ws(monkeypatch, state, hub_inst):
    import server.routes.ws as ws_module

    monkeypatch.setattr(ws_module, "get_state", lambda: state)
    monkeypatch.setattr(ws_module, "get_hub", lambda: hub_inst)


def test_ws_teacher_missing_token_closes(client, ctx, monkeypatch):
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    with pytest.raises(Exception), client.websocket_connect("/ws/teacher") as ws:  # noqa: B017
        ws.receive_json()


def test_ws_teacher_unknown_token_forbidden(client, ctx, monkeypatch):
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    with client.websocket_connect("/ws/teacher?token=" + "a" * 48) as ws:
        assert ws.receive_json() == {"type": "forbidden"}


def test_ws_teacher_shows_registration_before_authorize(client, ctx, monkeypatch):
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    c = _open_ctx(state)
    token = "t" * 48
    state.teacher_sessions[token] = TeacherSession(
        token=token, context_id=c.id, registration_code="WXYZ"
    )
    with client.websocket_connect(f"/ws/teacher?token={token}") as ws:
        msg = ws.receive_json()
        assert msg == {"type": "registration", "code": "WXYZ"}
        assert hub_inst.broadcasts[-1]["contexts"][c.id]["teacher"]["connected"] is True
    # ws-Referenz nach dem Trennen gelöst, Session bleibt (Reconnect möglich).
    assert token in state.teacher_sessions
    assert state.teacher_sessions[token].ws is None
    assert hub_inst.broadcasts[-1]["contexts"][c.id]["teacher"]["connected"] is False


def test_ws_teacher_shows_class_state_when_already_authorized(client, ctx, monkeypatch):
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    c = _open_ctx(state, students=1)
    token = "t" * 48
    state.teacher_sessions[token] = TeacherSession(
        token=token, context_id=c.id, registration_code="WXYZ", authorized=True
    )
    with client.websocket_connect(f"/ws/teacher?token={token}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "teacher_state"
        assert msg["class_form"] == "10a"
        assert len(msg["students"]) == 1


def test_ws_teacher_reconnect_with_authorized_token_works(client, ctx, monkeypatch):
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    c = _open_ctx(state)
    token = "t" * 48
    state.teacher_sessions[token] = TeacherSession(
        token=token, context_id=c.id, registration_code="WXYZ", authorized=True
    )
    with client.websocket_connect(f"/ws/teacher?token={token}") as ws1:
        assert ws1.receive_json()["type"] == "teacher_state"
    # Erster Connect getrennt, Session bleibt gültig -> zweiter Connect klappt.
    with client.websocket_connect(f"/ws/teacher?token={token}") as ws2:
        assert ws2.receive_json()["type"] == "teacher_state"
    assert token in state.teacher_sessions


def test_ws_teacher_after_revoke_reconnect_forbidden(client, ctx, monkeypatch):
    """PLAN-Abnahmekriterium: Token-Entwertung stoppt Datenupdates, ein Reload
    (= neuer WS-Connect mit demselben Token) kann den Zugang nicht mehr
    wiederherstellen."""
    state, _, hub_inst = ctx
    _patch_ws(monkeypatch, state, hub_inst)
    c = _open_ctx(state)
    token = "t" * 48
    session = TeacherSession(
        token=token, context_id=c.id, registration_code="WXYZ", authorized=True
    )
    state.teacher_sessions[token] = session

    asyncio.run(sessions.revoke_teacher_session(state, session, reason="test"))
    assert token not in state.teacher_sessions

    with client.websocket_connect(f"/ws/teacher?token={token}") as ws:
        assert ws.receive_json() == {"type": "forbidden"}


# ---- Klasse schließen / Schuljahreswechsel: Teardown -----------------------


def test_close_class_revokes_teacher_session(client, ctx):
    state, _, hub_inst = ctx
    c = _open_ctx(state)
    ws = _FakeWS()
    state.teacher_sessions["tok"] = TeacherSession(
        token="tok", context_id=c.id, registration_code="AAAA", authorized=True, ws=ws
    )
    r = client.post("/api/close-class", json={"context_id": c.id}, cookies={"session_id": "sid"})
    assert r.status_code == 200
    assert "tok" not in state.teacher_sessions
    assert ws.closed is True
    assert c.id not in state.contexts


def test_select_schoolyear_revokes_all_teacher_sessions(client, ctx):
    state, _, _ = ctx
    a = _open_ctx(state, form="10a")
    b = _open_ctx(state, form="10b")
    ws_a, ws_b = _FakeWS(), _FakeWS()
    state.teacher_sessions["ta"] = TeacherSession(
        token="ta", context_id=a.id, registration_code="AAAA", authorized=True, ws=ws_a
    )
    state.teacher_sessions["tb"] = TeacherSession(
        token="tb", context_id=b.id, registration_code="BBBB", ws=ws_b
    )
    r = client.post(
        "/api/select-schoolyear",
        json={"schoolyear": "2099/2100"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert state.teacher_sessions == {}
    assert ws_a.closed is True
    assert ws_b.closed is True


# ---- Hub-Verdrahtung: jede state-verändernde Broadcast erreicht Lehrer-WS --


def test_hub_broadcast_host_pushes_teacher_updates(monkeypatch):
    """`Hub.broadcast_host` (der zentrale Aufruf hinter praktisch jeder
    Zustandsänderung) pusht sowohl die noch offene Registrierung als auch,
    für eine autorisierte Session, den frischen `teacher_state` — ohne dass
    der Aufrufer irgendeinen teacher-spezifischen Code kennen muss."""
    state = AppState()
    ctx_a = state.open_context("10a")
    ctx_a.queue.append(QueueStudent(student_id=1, lastname="A", firstname="a", form="10a"))
    ctx_a.done_collected = True
    ctx_a.queue[0].status = "done"
    ctx_a.queue[0].slip_printed = True
    ctx_a.queue[0].slip_collected = True
    ws_unauth = _FakeWS()
    ws_auth = _FakeWS()
    state.teacher_sessions["unauth"] = TeacherSession(
        token="unauth", context_id=ctx_a.id, registration_code="CODE", ws=ws_unauth
    )
    state.teacher_sessions["auth"] = TeacherSession(
        token="auth", context_id=ctx_a.id, registration_code="CODE2", authorized=True, ws=ws_auth
    )

    asyncio.run(Hub().broadcast_host({"type": "state"}, state))

    assert ws_unauth.sent[-1] == {"type": "registration", "code": "CODE"}
    assert ws_auth.sent[-1]["type"] == "teacher_state"
    assert ws_auth.sent[-1]["class_form"] == "10a"
    assert ws_auth.sent[-1]["done_collected"] is True
    assert ws_auth.sent[-1]["slip_collected_count"] == 1


def test_send_teacher_update_dead_ws_cleared(monkeypatch):
    state = AppState()
    monkeypatch.setattr(sessions, "get_hub", lambda: _FakeHub())

    class _DeadWS:
        async def send_json(self, msg):
            raise RuntimeError("tot")

    session = TeacherSession(token="t", context_id="none", registration_code="AAAA")
    session.ws = _DeadWS()
    asyncio.run(sessions.send_teacher_update(state, session))
    assert session.ws is None
