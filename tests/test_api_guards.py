"""Unit-Tests für die HTTP-Endpunkt-Logik (server/routes/api.py).

Läuft über einen echten HTTP-Client (`starlette.testclient.TestClient`, Fixture
`client` aus `conftest.py`) gegen `create_app()` — OHNE den Lifespan zu starten
(der würde einen echten Playwright-Worker gegen die IServ-PRODUKTION einloggen).
Anders als ein direkter Python-Aufruf der Endpoint-Coroutinen durchläuft das den
ECHTEN ASGI-Request-Pfad inkl. `Depends`/`Cookie`-Injection — insbesondere die
Auth-Guards werden dadurch tatsächlich geprüft, nicht nur scheinbar. Geprüft
werden Auth-Guard, Validierung, Idempotenz und das Buchungs-Gate auf HTTP-Ebene;
IServ/Worker bleiben außen vor (State/Config/Hub werden gemockt).
"""

from __future__ import annotations

import pytest

import server.routes.api as api
import server.routes.auth as auth_routes
import server.routes.booklists as booklists_routes
import server.routes.classes as classes_routes
import server.routes.helpers as helpers_routes
import server.routes.modus_b as modus_b_routes
import server.routes.queue as queue_routes
import server.routes.settings as settings_routes
import server.routes.slips as slips_routes
from server.config import Config
from server.routes import _deps as deps_routes
from server.state import AppState

# Jedes Endpoint-Modul importiert get_state/get_config/get_hub selbst (eigener
# from-Import) und löst die Namen im Namespace IHRES Moduls auf — ein Patch nur
# an zentraler Stelle würde die Endpunkte daher nicht erreichen. Deshalb
# patchen wir alle Route-Module (inkl. `_deps`, wo `require_host`/`_base_url`
# sitzen). Ein vergessenes Modul fällt LAUT auf (403/echter Singleton statt
# Fixture-State), nicht still — kein Risiko eines scheinbar grünen Tests.
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
]

# ---------------------------------------------------------------------------
# Fixtures: frische Singletons pro Test (Host-Login bereits gültig)
# ---------------------------------------------------------------------------


class _FakeHub:
    async def broadcast_host(self, snapshot) -> None:
        pass

    async def broadcast_settings(self, *a, **kw) -> None:
        pass

    async def send_scanner(self, token, msg) -> None:
        pass


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


@pytest.fixture
def ctx(monkeypatch):
    """Frischer State + Config + Fake-Hub; gültige Host-Session 'sid'."""
    state = AppState()
    state.add_host_session("sid")
    cfg = _make_config()
    hub = _FakeHub()
    for mod in _ROUTE_MODULES:
        if hasattr(mod, "get_state"):
            monkeypatch.setattr(mod, "get_state", lambda: state)
        if hasattr(mod, "get_config"):
            monkeypatch.setattr(mod, "get_config", lambda: cfg)
        if hasattr(mod, "get_hub"):
            monkeypatch.setattr(mod, "get_hub", lambda: hub)
    return state, cfg, hub


# ---------------------------------------------------------------------------
# Auth-Guard (_require_host) — über echtes HTTP, damit die Depends/Cookie-
# Injection tatsächlich greift statt nur bei direktem Funktionsaufruf simuliert
# zu werden.
# ---------------------------------------------------------------------------


def test_require_host_rejects_missing_cookie(client, ctx):
    r = client.get("/api/state")
    assert r.status_code == 403


def test_require_host_rejects_unknown_session(client, ctx):
    r = client.get("/api/state", cookies={"session_id": "bogus"})
    assert r.status_code == 403


def test_require_host_accepts_valid_session(client, ctx):
    r = client.get("/api/state", cookies={"session_id": "sid"})
    assert r.status_code == 200
    assert r.json()["type"] == "state"


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


def test_login_wrong_password(client, ctx):
    r = client.post("/api/login", json={"password": "nope"})
    assert r.status_code == 403


def test_login_correct_password_sets_session(client, ctx):
    state, _, _ = ctx
    r = client.post("/api/login", json={"password": "secret"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    # Genau eine neue Host-Session zusätzlich zur fixture-'sid'.
    assert len(state.host_sessions) == 2


# ---------------------------------------------------------------------------
# add-student: Validierung & Duplikat-Schutz
# ---------------------------------------------------------------------------


def test_add_student_invalid_id(client, ctx):
    """`student_id` ist ein Pydantic-Feld (`int | None`) — ein Wert vom
    falschen Typ (hier ein nicht-numerischer String) lässt die Body-Validierung
    bereits vor dem Funktionsrumpf mit 422 abbrechen. Kein Client wertet den
    Statuscode aus — bewusst akzeptierte Verschärfung."""
    r = client.post(
        "/api/add-student",
        json={"student_id": "x", "lastname": "M"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 422


def test_add_student_missing_name(client, ctx):
    r = client.post("/api/add-student", json={"student_id": 1}, cookies={"session_id": "sid"})
    assert r.status_code == 400


def test_add_student_without_open_context_rejected(client, ctx):
    """Ohne offenen Klassen-Tab (kein aktiver Kontext) schlägt der Request mit
    400 fehl — kein stiller Geister-Kontext mehr (implizite Kontexte wurden
    entfernt)."""
    r = client.post(
        "/api/add-student",
        json={"student_id": 1, "lastname": "Müller", "firstname": "N", "form": "10a"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 400


def test_add_student_success_then_duplicate(client, ctx):
    state, _, _ = ctx
    state.open_context("10a")
    r = client.post(
        "/api/add-student",
        json={"student_id": 1, "lastname": "Müller", "firstname": "N", "form": "10a"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "count": 1}
    assert state.active_context.form == "10a"  # erste Klasse übernommen
    r2 = client.post(
        "/api/add-student",
        json={"student_id": 1, "lastname": "Müller"},
        cookies={"session_id": "sid"},
    )
    assert r2.status_code == 409


# ---------------------------------------------------------------------------
# open-test-config: Idempotenz (offener Punkt aus docs/test_status.md) —
# ein bereits in einer Queue stehender Testschüler wird bei erneutem Öffnen
# nicht doppelt eingefügt (state.find_student-Check in open_test_config).
# ---------------------------------------------------------------------------


def test_open_test_config_idempotent_when_student_already_queued(client, ctx):
    state, _, _ = ctx
    # Einen der Testschüler bereits in einer anderen Klasse einreihen, bevor
    # der Test-Config-Tab geöffnet wird — open_test_config muss ihn dann
    # überspringen (find_student-Check), statt ihn doppelt einzufügen.
    from server.state import QueueStudent

    other = api.TEST_STUDENTS[0]
    other_ctx = state.open_context("Andere Klasse")
    other_ctx.queue.append(
        QueueStudent(
            student_id=other["student_id"],
            lastname=other["lastname"],
            firstname=other["firstname"],
            form=other["form"],
        )
    )

    first = client.post("/api/open-test-config", cookies={"session_id": "sid"}).json()
    ctx_id = first["context_id"]
    context = state.contexts[ctx_id]
    # Alle Testschüler außer dem bereits eingereihten landen im Test-Config-Tab.
    assert len(context.queue) == len(api.TEST_STUDENTS) - 1
    assert other["student_id"] not in {s.student_id for s in context.queue}

    # Erneutes Öffnen (bestehender Tab) fügt nichts doppelt hinzu.
    second = client.post("/api/open-test-config", cookies={"session_id": "sid"}).json()
    assert second["context_id"] == ctx_id
    assert second["reused"] is True
    assert len(state.contexts[ctx_id].queue) == len(api.TEST_STUDENTS) - 1


# ---------------------------------------------------------------------------
# open-test-config: dedizierter Tab, sofort befüllt, Wieder-Öffnen reaktiviert
# ---------------------------------------------------------------------------


def test_open_test_config_populates_and_reuses(client, ctx):
    state, _, _ = ctx
    first = client.post("/api/open-test-config", cookies={"session_id": "sid"}).json()
    assert first["count"] == len(api.TEST_STUDENTS)
    ctx_id = first["context_id"]
    context = state.contexts[ctx_id]
    assert context.form == api.TEST_CONFIG_FORM
    assert len(context.queue) == len(api.TEST_STUDENTS)

    # Zweiter Aufruf (z. B. erneutes "+" -> "Test Config öffnen") reaktiviert
    # denselben Kontext statt eine zweite Queue anzulegen.
    second = client.post("/api/open-test-config", cookies={"session_id": "sid"}).json()
    assert second["context_id"] == ctx_id
    assert second["reused"] is True
    assert len(state.contexts) == 1
    assert state.active_context_id == ctx_id


# ---------------------------------------------------------------------------
# select-schoolyear: Guard muss über ALLE Klassen-Kontexte prüfen, nicht nur
# den aktiven Tab (Bugfix — AppState.active_students() statt nur dem aktiven
# Kontext).
# ---------------------------------------------------------------------------


def test_select_schoolyear_blocks_on_active_student_in_inactive_context(client, ctx):
    from server.state import QueueStudent

    state, _, _ = ctx
    # Zwei Klassen-Tabs offen; der aktive Schüler steht im NICHT-aktiven Tab.
    inactive_ctx = state.open_context("Klasse A")
    inactive_ctx.queue.append(
        QueueStudent(student_id=1, lastname="M", firstname="N", form="Klasse A", status="active")
    )
    active_ctx = state.open_context("Klasse B")  # wird zum aktiven Kontext
    assert state.active_context_id == active_ctx.id

    r = client.post(
        "/api/select-schoolyear",
        json={"schoolyear": "2026/2027"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["reason"] == "active_sessions"
    # Kontexte bleiben unangetastet, solange der Guard blockiert.
    assert state.contexts

    # Mit force=True darf der Wechsel trotzdem durch.
    r2 = client.post(
        "/api/select-schoolyear",
        json={"schoolyear": "2026/2027", "force": True},
        cookies={"session_id": "sid"},
    )
    assert r2.status_code == 200
    assert r2.json() == {"ok": True, "selected": "2026/2027"}
    assert state.contexts == {}


# ---------------------------------------------------------------------------
# skip / finish: Validierung
# ---------------------------------------------------------------------------


def test_skip_missing_student_id(client, ctx):
    r = client.post("/api/skip", json={}, cookies={"session_id": "sid"})
    assert r.status_code == 400


def test_skip_unknown_student(client, ctx):
    r = client.post("/api/skip", json={"student_id": 999}, cookies={"session_id": "sid"})
    assert r.status_code == 404


def test_finish_unknown_student(client, ctx):
    r = client.post("/api/finish", json={"student_id": 999}, cookies={"session_id": "sid"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Buchungs-Gate auf HTTP-Ebene (V10 testet nur handle_commit direkt)
# ---------------------------------------------------------------------------


def test_commit_book_blocked_when_flag_off(client, ctx):
    """Gate 1 (Server-Flag) greift vor confirm/Barcode — Default false."""
    r = client.post(
        "/api/commit-book",
        json={"student_id": 1, "confirm": True, "barcode": "B1"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 403


def test_commit_book_requires_auth(client, ctx):
    r = client.post("/api/commit-book", json={"student_id": 1, "confirm": True})
    assert r.status_code == 403


def test_commit_book_flag_off_wins_even_with_missing_confirm(client, ctx):
    """Gate-Reihenfolge Gate1 (403, Server-Flag) -> Gate3 (400, confirm) bleibt
    erhalten, obwohl `confirm` jetzt ein Pydantic-Feld ist: ein komplett
    fehlendes `confirm` darf NICHT vorab mit 422 abbrechen (das würde Gate 1
    umgehen) — `confirm` hat deshalb bewusst `bool = False` als Default."""
    r = client.post("/api/commit-book", json={"student_id": 1}, cookies={"session_id": "sid"})
    assert r.status_code == 403
    assert "ALLOW_BOOKING" in r.json()["detail"]


def test_commit_book_requires_confirm_when_flag_on(client, ctx, monkeypatch):
    """Mit `allow_booking=true` (Gate 1 offen) muss ein fehlendes `confirm`
    weiterhin 400 liefern (Gate 3), nicht 422 — die Validierung darf nicht vor
    die manuelle Prüfung im Funktionsrumpf rutschen."""
    state, cfg, _ = ctx
    cfg.allow_booking = True
    r = client.post("/api/commit-book", json={"student_id": 1}, cookies={"session_id": "sid"})
    assert r.status_code == 400
    assert "confirm" in r.json()["detail"]


# ---------------------------------------------------------------------------
# Pure Helfer (keine Endpoints — direkter Aufruf bleibt hier angemessen)
# ---------------------------------------------------------------------------


def test_last_scan_for_prefers_session(ctx):
    import server.sessions as sessions

    state, _, _ = ctx
    sess = sessions.create_student_session(state)
    sess.student_id = 4
    sess.state = "paired"
    sess.last_scan = "B-SESSION"
    assert api._last_scan_for(state, 4) == "B-SESSION"


def test_last_scan_for_empty_when_nothing(ctx):
    state, _, _ = ctx
    assert api._last_scan_for(state, 123) == ""


class _FakeRequest:
    def __init__(self, host: str):
        self.headers = {"host": host}


def test_base_url_ignores_spoofed_host_header_uses_config_ip(ctx):
    """Der Host-Header-Hostname darf NICHT in die QR-URL wandern (trägt join_secret).
    Nur der Port aus dem Host-Header übernommen; Hostname aus cfg.host_ip."""
    _, cfg, _ = ctx
    cfg.host_ip = "10.0.0.9"  # deterministischer Override
    url = api._base_url(_FakeRequest("evil.example:3443"))
    assert url == "https://10.0.0.9:3443"
    assert "evil.example" not in url


def test_base_url_rewrites_localhost(ctx, monkeypatch):
    _, cfg, _ = ctx
    cfg.host_ip = "10.0.0.9"  # expliziter Override → deterministisch
    url = api._base_url(_FakeRequest("localhost"))
    assert url == f"https://10.0.0.9:{cfg.port}"


# ---------------------------------------------------------------------------
# POST /api/settings/{key} — gebündelter Endpoint für die drei Bool-Toggles
# (save-pdf-locally/fix-class-on-slip/slip-default). Prüft die Whitelist, die
# 404 für unbekannte Keys, und dass jeder Key sein eigenes Body-Feld liest
# (enabled vs. second_page) und unter dem jeweiligen State-Attributnamen
# antwortet.
# ---------------------------------------------------------------------------


def test_settings_unknown_key_404(client, ctx):
    r = client.post(
        "/api/settings/does-not-exist", json={"enabled": True}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 404


def test_settings_save_pdf_locally(client, ctx):
    state, _, _ = ctx
    r = client.post(
        "/api/settings/save-pdf-locally", json={"enabled": True}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "save_pdf_locally": True}
    assert state.settings.save_pdf_locally is True


def test_settings_fix_class_on_slip(client, ctx):
    state, _, _ = ctx
    r = client.post(
        "/api/settings/fix-class-on-slip", json={"enabled": True}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "fix_class_on_slip": True}
    assert state.settings.fix_class_on_slip is True


def test_settings_slip_default_reads_second_page_field(client, ctx):
    """slip-default liest bewusst `second_page`, nicht `enabled` (historisch
    anderer Feldname im Client-Body, siehe SettingsToggleRequest-Docstring)."""
    state, _, _ = ctx
    r = client.post(
        "/api/settings/slip-default", json={"second_page": True}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "slip_second_page_default": True}
    assert state.settings.slip_second_page_default is True


def test_settings_requires_auth(client, ctx):
    r = client.post("/api/settings/save-pdf-locally", json={"enabled": True})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Kontext-Lifecycle: open-class (Doppel-Öffnen), close-class (Teardown +
# Helfer-Lösung + Kontext-Wechsel), set-active-context (unbekannte ID → 404).
# ---------------------------------------------------------------------------


class _FakeIServForClasses:
    """Minimaler IServ-Stub für open-class: liefert zwei Schüler."""

    async def get_students_for_form(self, form, schoolyear):
        return [
            {"student_id": 1, "lastname": "A", "firstname": "a"},
            {"student_id": 2, "lastname": "B", "firstname": "b"},
        ]


def test_open_class_creates_context_and_populates_queue(client, ctx):
    state, _, _ = ctx
    state.iserv = _FakeIServForClasses()
    r = client.post("/api/open-class", json={"form": "10a"}, cookies={"session_id": "sid"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert "reused" not in body
    ctx_id = body["context_id"]
    assert state.contexts[ctx_id].form == "10a"
    assert {s.student_id for s in state.contexts[ctx_id].queue} == {1, 2}
    assert state.active_context_id == ctx_id


def test_open_class_reused_no_second_queue(client, ctx):
    """Doppel-Öffnen derselben Klasse aktiviert den bestehenden Kontext wieder
    (reused: true) — es entsteht KEINE zweite Queue."""
    state, _, _ = ctx
    state.iserv = _FakeIServForClasses()
    first = client.post(
        "/api/open-class", json={"form": "10a"}, cookies={"session_id": "sid"}
    ).json()
    ctx_id = first["context_id"]

    # Aktiven Kontext umschalten, damit der zweite Aufruf ihn nachweislich
    # wieder AKTIVIERT (statt bloß unangetastet zu lassen).
    other = state.open_context("10b")
    assert state.active_context_id == other.id

    second = client.post("/api/open-class", json={"form": "10a"}, cookies={"session_id": "sid"})
    assert second.status_code == 200
    body = second.json()
    assert body["context_id"] == ctx_id
    assert body["reused"] is True
    assert body["count"] == 2
    # Nur EIN Kontext für "10a" — keine zweite Queue angelegt.
    assert sum(1 for c in state.contexts.values() if c.form == "10a") == 1
    assert state.active_context_id == ctx_id


def test_close_class_ends_students_and_releases_helper_bindings(client, ctx):
    from server.state import HelperSession, QueueStudent

    state, _, _ = ctx
    class_ctx = state.open_context("10a")
    class_ctx.queue.append(
        QueueStudent(
            student_id=1,
            lastname="A",
            firstname="a",
            form="10a",
            status="active",
            assigned_helper="h1",
        )
    )
    class_ctx.queue.append(
        QueueStudent(student_id=2, lastname="B", firstname="b", form="10a", status="pending")
    )
    helper = HelperSession(token="h1", name="Helfer", student_id=1, context_id=class_ctx.id)
    state.helper_sessions["h1"] = helper

    r = client.post(
        "/api/close-class", json={"context_id": class_ctx.id}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "context_id": class_ctx.id}

    # Kontext komplett weg — keine Reste der Queue.
    assert class_ctx.id not in state.contexts
    # Helfer-Bindung an diesen Kontext gelöst und Schüler-Zuweisung aufgeräumt.
    assert helper.context_id is None
    assert helper.student_id is None


def test_close_class_switches_active_context_when_active_one_closed(client, ctx):
    state, _, _ = ctx
    a = state.open_context("10a")
    b = state.open_context("10b")
    # `b` ist jetzt der aktive Kontext (zuletzt geöffnet).
    assert state.active_context_id == b.id

    r = client.post("/api/close-class", json={"context_id": b.id}, cookies={"session_id": "sid"})
    assert r.status_code == 200
    # Aktiver Kontext wechselt auf den verbleibenden Kontext `a`.
    assert state.active_context_id == a.id


def test_close_class_unknown_context_404(client, ctx):
    r = client.post(
        "/api/close-class", json={"context_id": "does-not-exist"}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 404


def test_set_active_context_unknown_id_404(client, ctx):
    r = client.post(
        "/api/set-active-context",
        json={"context_id": "does-not-exist"},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 404


def test_set_active_context_switches(client, ctx):
    state, _, _ = ctx
    a = state.open_context("10a")
    b = state.open_context("10b")
    assert state.active_context_id == b.id

    r = client.post(
        "/api/set-active-context", json={"context_id": a.id}, cookies={"session_id": "sid"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "active_context_id": a.id}
    assert state.active_context_id == a.id


# ---------------------------------------------------------------------------
# student/pair — Pairing-TOCTOU (Aufgabe 2, wertvollster Test): zwischen dem
# `await get_student_info(...)` und der verbindlichen Bindung liegt ein
# Re-Check. Eine parallele Anfrage kann in genau diesem Fenster den Zustand
# ändern (Code neu vergeben, Session entwertet, Schüler nicht mehr pending) —
# der Endpoint MUSS dann 409 liefern statt zu binden.
# ---------------------------------------------------------------------------


class _FakeIServInfo:
    """get_student_info liefert normale Info UND führt dabei eine vom Test
    übergebene Mutation aus — simuliert eine Nebenläufigkeit während des Awaits."""

    def __init__(self, mutate) -> None:
        self._mutate = mutate

    async def get_student_info(self, student_id, schoolyear):
        self._mutate()
        return {"student_id": student_id, "books": [], "enrolled": False}


def _pair_setup(state):
    """Session (pending_pairing) + wartender Schüler, bereit zum Pairing."""
    import server.sessions as sessions
    from server.state import QueueStudent

    session = sessions.create_student_session(state)
    session.pairing_code = "1234"
    class_ctx = state.open_context("10a")
    student = QueueStudent(student_id=1, lastname="A", firstname="a", form="10a", status="pending")
    class_ctx.queue.append(student)
    return session, student


def test_student_pair_toctou_session_revoked_during_await(client, ctx):
    """Während des IServ-Awaits wird die Session entwertet (z. B. Timeout/
    Ausgabe geschlossen) — der Re-Check muss das erkennen, nicht binden."""
    state, _, _ = ctx
    session, student = _pair_setup(state)

    def mutate():
        session.state = "revoked"

    state.iserv = _FakeIServInfo(mutate)

    r = client.post(
        "/api/student/pair",
        json={"pairing_code": "1234", "student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
    # Darf NICHT gebunden haben.
    assert student.status == "pending"
    assert session.student_id is None


def test_student_pair_toctou_student_taken_during_await(client, ctx):
    """Während des IServ-Awaits schnappt sich eine parallele Anfrage denselben
    Schüler (Status kippt auf 'active') — Re-Check muss 409 liefern."""
    state, _, _ = ctx
    session, student = _pair_setup(state)

    def mutate():
        student.status = "active"

    state.iserv = _FakeIServInfo(mutate)

    r = client.post(
        "/api/student/pair",
        json={"pairing_code": "1234", "student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
    assert session.student_id is None


def test_student_pair_toctou_code_reassigned_during_await(client, ctx):
    """Während des IServ-Awaits wird der Pairing-Code einer anderen Session
    zugeteilt (find_session_by_code(code) ist danach nicht mehr `session`) —
    Re-Check muss 409 liefern, nicht die alte Session binden."""
    import server.sessions as sessions

    state, _, _ = ctx
    session, student = _pair_setup(state)
    other_session = sessions.create_student_session(state)

    def mutate():
        # Der Code wird "neu vergeben" — simuliert dadurch, dass ein anderer
        # Session-Token jetzt denselben Code trägt (Race: alte Session wurde
        # entwertet + neu erzeugt mit demselben freien Code).
        other_session.pairing_code = "1234"
        session.pairing_code = "9999"

    state.iserv = _FakeIServInfo(mutate)

    r = client.post(
        "/api/student/pair",
        json={"pairing_code": "1234", "student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 409
    assert student.status == "pending"
    assert session.student_id is None


def test_student_pair_happy_path_binds_when_nothing_changed(client, ctx):
    """Gegenprobe: ändert sich während des Awaits nichts, bindet der Endpoint
    ganz normal (kein falsch-positiver Guard)."""
    state, _, _ = ctx
    session, student = _pair_setup(state)
    state.iserv = _FakeIServInfo(lambda: None)

    r = client.post(
        "/api/student/pair",
        json={"pairing_code": "1234", "student_id": 1},
        cookies={"session_id": "sid"},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "student_id": 1}
    assert student.status == "active"
    assert session.student_id == 1
    assert session.state == "paired"
