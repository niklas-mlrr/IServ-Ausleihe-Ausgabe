"""Unit-Tests für den Scanner-Reconnect/Disconnect-Pfad (server/routes/ws.py).

Geprüft wird die „Grace-Period"-Logik: beim Trennen des Helfer-WS wird der
Schüler-Teardown (end_student) nicht sofort, sondern verzögert angestoßen.
Ein Reconnect innerhalb der Frist cancelt den Task und lädt den Schüler neu
(im ws_scanner-Reconnect-Pfad); hier wird isoliert geprüft, dass ``_deferred_end``
in den drei Szenarien korrekt entscheidet. Dazu kommt ``StudentSession.reload()``.

Rein logisch (RAM-State + Fake-Page) — kein Browser, kein IServ, kein WS.
"""

from __future__ import annotations

import asyncio

import server.routes.ws as ws_module
from automation.worker import StudentSession
from server.state import AppState, HelperSession, QueueStudent
import server.sessions as sessions


# ---------------------------------------------------------------------------
# Test-Doubles
# ---------------------------------------------------------------------------

class _FakeHub:
    def __init__(self) -> None:
        self.host_broadcasts = 0
        self.scanner_msgs: list[tuple[str, dict]] = []

    async def broadcast_host(self, snapshot) -> None:
        self.host_broadcasts += 1

    async def send_scanner(self, token: str, msg: dict) -> None:
        self.scanner_msgs.append((token, msg))


class _FakeWorker:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakeIServ:
    async def get_student_info(self, student_id, schoolyear):
        return {"student_id": student_id, "books": []}


def _state_with_iserv() -> AppState:
    st = AppState()
    st.iserv = _FakeIServ()
    return st


def _add_active_student(st: AppState, sid: int) -> QueueStudent:
    s = QueueStudent(student_id=sid, lastname=f"N{sid}", firstname="V", form="10a", status="active")
    st.queue.append(s)
    return s


def _helper_with_student(st: AppState, sid: int) -> HelperSession:
    helper = HelperSession(token="h1", name="Helfer", student_id=sid)
    st.helper_sessions["h1"] = helper
    return helper


async def _run_deferred(st, hub, helper, sid):
    """_deferred_end als Task starten und bis zur Completion ablaufen lassen
    (Grace-Frist wurde per monkeypatch verkürzt)."""
    task = asyncio.create_task(ws_module._deferred_end(st, hub, helper, sid))
    await asyncio.sleep(0)
    return task


# ---------------------------------------------------------------------------
# _deferred_end — Re-Check 1: Reconnect während Grace → kein Teardown
# ---------------------------------------------------------------------------

def test_deferred_end_noop_on_reconnect(monkeypatch):
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None  # beim Trennen vom finally gelöscht

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        # Während der Grace-Frist: neue Verbindung übernimmt (Reconnect).
        helper.ws = object()  # „neuer" WS
        await task  # Grace verstreichen lassen
        return task

    asyncio.run(run())

    assert student.status == "active", "Reconnect darf Schüler nicht abbrechen"
    assert helper.student_id == 7, "Reconnect darf Helfer-Zuweisung nicht lösen"
    assert 7 in st.student_worker_sessions, "Worker muss bei Reconnect bleiben"
    assert not worker.closed, "Worker-Context darf bei Reconnect nicht zu sein"


# ---------------------------------------------------------------------------
# _deferred_end — Re-Check 2: Schüler zwischenzeitlich gewechselt → kein Teardown
# ---------------------------------------------------------------------------

def test_deferred_end_noop_on_student_changed(monkeypatch):
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        # Während Grace: Helfer wurde weitergeschaltet (z. B. /api/skip → None).
        helper.student_id = None
        await task
        return task

    asyncio.run(run())

    # Der Teardown für Schüler 7 darf NICHT laufen — ein anderer Zustand ist
    # zwischenzeitlich eingetreten. Schüler 7 bleibt active (sein Status wurde
    # ja vom skip-Pfad selbst gesetzt, hier nur simuliert durch student_id=None).
    assert 7 in st.student_worker_sessions, "Worker darf nicht freigegeben werden"
    assert not worker.closed


# ---------------------------------------------------------------------------
# _deferred_end — echte Trennung → Teardown läuft
# ---------------------------------------------------------------------------

def test_deferred_end_teardowns_on_genuine_disconnect(monkeypatch):
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None  # alte Verbindung vom finally gelöscht, keine neue gekommen

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        await task
        # release_worker plant worker.close() als Task → einmal ticken lassen.
        await asyncio.sleep(0)
        return task

    asyncio.run(run())

    assert student.status == "pending", "Echte Trennung → Schüler zurück auf 'pending'"
    assert student.assigned_helper is None
    assert helper.student_id is None
    assert 7 not in st.student_worker_sessions, "Worker muss freigegeben werden"
    assert worker.closed, "Worker-Context muss geschlossen sein"
    assert hub.host_broadcasts >= 1


# ---------------------------------------------------------------------------
# _deferred_end — Cancel (Reconnect im ws_scanner) → kein Teardown
# ---------------------------------------------------------------------------

def test_deferred_end_cancel_is_noop(monkeypatch):
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 10.0)  # groß, damit Cancel zuerst kommt
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        task.cancel()
        await asyncio.sleep(0)
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())

    assert student.status == "active"
    assert 7 in st.student_worker_sessions
    assert not worker.closed


# ---------------------------------------------------------------------------
# StudentSession.reload() — re-navigiert die bestehende Page (read-only GET)
# ---------------------------------------------------------------------------

class _FakeLocator:
    def __init__(self, sel: str) -> None:
        self.sel = sel

    async def count(self) -> int:
        return 0  # kein Login-Feld → _on_login_page() False, kein Re-Login

    async def wait_for(self, state=None, timeout=None) -> None:
        return None


class _FakePage:
    def __init__(self) -> None:
        self.url = ""
        self.gotos: list[str] = []

    async def goto(self, url, wait_until=None) -> None:
        self.url = url
        self.gotos.append(url)

    async def wait_for_timeout(self, ms) -> None:
        pass

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(sel)


def test_reload_renavigates_existing_page():
    page = _FakePage()
    session = StudentSession(
        context=None, page=page, domain="example.test",
        student_id=42, student_name="Test, Tina",
    )

    asyncio.run(session.reload())

    # load_card navigiert App-Root + Schüler-Route (zwei GETs) auf der
    # bestehenden Page — kein neuer Context.
    assert page.gotos == [
        "https://ausleihe.example.test/",
        "https://ausleihe.example.test/#/counter/student/42",
    ]
    assert session._card_loaded is True


# ---------------------------------------------------------------------------
# _deferred_end — In-Flight load_task wird vor dem Teardown gecancelt
# ---------------------------------------------------------------------------

def test_deferred_end_cancels_inflight_load_task(monkeypatch):
    """Der verzögerte Teardown muss einen noch laufenden Lade-Task abbrechen,
    sonst leakt dessen Worker-Context (s. end_student-Kommentar)."""
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None  # echte Trennung

    async def run():
        load_task = asyncio.Future()  # läuft nie von selbst zu Ende
        helper.load_task = load_task
        task = await _run_deferred(st, hub, helper, 7)
        await task
        # release_worker plant worker.close() als Task → einmal ticken lassen.
        await asyncio.sleep(0)
        return load_task

    load_task = asyncio.run(run())

    assert load_task.cancelled(), "In-flight load_task muss cancelt werden"
    assert helper.load_task is None, "load_task nach Teardown zurückgesetzt"
    assert student.status == "pending"
    assert worker.closed, "Worker trotz laufendem Lade-Task freigegeben"


# ---------------------------------------------------------------------------
# _deferred_end — schluckt end_student-Ausnahme (Sweeper-Robustheit)
# ---------------------------------------------------------------------------

async def _raising_end(state, hub, sid, *, queue_status, session_state, **kw):
    raise RuntimeError("end_student boom")


def test_deferred_end_swallows_end_student_exception(monkeypatch):
    """Schlägt end_student fehl, darf der Task nicht crashen — und der
    nachfolgende Host-Broadcast muss trotzdem versucht werden."""
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    monkeypatch.setattr(ws_module, "end_student", _raising_end)
    st = _state_with_iserv()
    hub = _FakeHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        await task  # wirft nicht — Exception wird intern gefangen
        return task

    asyncio.run(run())

    # end_student lief (und schlug fehl) — Student-Zustand bleibt unangetastet,
    # aber der Broadcast wurde trotzdem ausgeführt.
    assert hub.host_broadcasts >= 1, "Broadcast muss trotz end_student-Fehler laufen"


# ---------------------------------------------------------------------------
# _deferred_end — schluckt broadcast_host-Ausnahme
# ---------------------------------------------------------------------------

class _BroadcastRaisingHub(_FakeHub):
    async def broadcast_host(self, snapshot) -> None:
        raise RuntimeError("broadcast boom")


def test_deferred_end_swallows_broadcast_exception(monkeypatch):
    """Schlägt der finale Host-Broadcast fehl, darf das den Teardown-Task
    nicht crashen (s. except-Pass in _deferred_end)."""
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _BroadcastRaisingHub()
    student = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student.assigned_helper = "h1"
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        await task  # wirft nicht
        await asyncio.sleep(0)  # release-Task ticken lassen
        return task

    asyncio.run(run())

    # Teardown lief trotz Broadcast-Fehler durch.
    assert student.status == "pending"
    assert worker.closed


# ---------------------------------------------------------------------------
# _deferred_end — Worker wird auch freigegeben, wenn der Schüler nicht (mehr)
# in der Queue steht (Nur-Worker-Zweig von end_student)
# ---------------------------------------------------------------------------

def test_deferred_end_releases_worker_when_student_missing(monkeypatch):
    """Schüler zwischenzeitlich aus der Queue entfernt (z. B. Queue-Reset
    während Grace, aber helper.student_id noch unverändert): end_student
    überspringt den Queue-Block, gibt aber den Worker trotzdem frei."""
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    # Kein QueueStudent für sid=7 — nur der Worker steht noch.
    helper = _helper_with_student(st, 7)
    worker = _FakeWorker()
    st.student_worker_sessions[7] = worker
    helper.ws = None

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        await task
        await asyncio.sleep(0)  # release-Task ticken lassen
        return task

    asyncio.run(run())

    assert 7 not in st.student_worker_sessions, "Worker muss freigegeben werden"
    assert worker.closed


# ---------------------------------------------------------------------------
# StudentSession.reload() — Re-Login bei abgelaufener Session
# ---------------------------------------------------------------------------

class _ExpiredPage:
    """Simuliert einen abgelaufenen Context: jeder goto landet zunächst auf
    der IServ-Login-Seite; erst nach ``relogin()`` sind Folge-Navigationen
    authed. Die Login-Erkennung läuft rein über die URL (``_on_login_page``
    prüft ``iserv/login``)."""

    def __init__(self) -> None:
        self.url = ""
        self.gotos: list[str] = []
        self._authed = False
        self.relogin_calls = 0

    async def goto(self, url, wait_until=None) -> None:
        self.gotos.append(url)
        if not self._authed:
            # Realistischer IServ-Login-Pfad („iserv/login" als Pfadsegment,
            # wie _on_login_page es erwartet — nicht „iserv.example.test/...").
            self.url = "https://ausleihe.example.test/iserv/login"
        else:
            self.url = url

    async def wait_for_timeout(self, ms) -> None:
        pass

    def locator(self, sel: str) -> _FakeLocator:
        return _FakeLocator(sel)


async def _fake_relogin(page, label):
    page._authed = True
    page.relogin_calls += 1


def test_reload_performs_relogin_on_expired_session():
    """Bei abgelaufener Session führt reload() (via load_card → _goto_authed)
    den Re-Login aus und navigiert danach erfolgreich — alles auf derselben
    Page, kein neuer Context."""
    page = _ExpiredPage()
    session = StudentSession(
        context=None, page=page, domain="example.test",
        student_id=42, student_name="Test, Tina",
        relogin=_fake_relogin,
    )

    asyncio.run(session.reload())

    # Genau ein Re-Login (beim ersten _goto_authed, App-Root), danach authed.
    assert page.relogin_calls == 1
    # gotos: App-Root (→ Login) → Re-Login → App-Root (Retry) → Schüler-Route.
    assert page.gotos == [
        "https://ausleihe.example.test/",
        "https://ausleihe.example.test/",
        "https://ausleihe.example.test/#/counter/student/42",
    ]
    assert session._card_loaded is True


def test_reload_raises_when_session_expired_and_no_relogin():
    """Ohne Re-Login-Callable muss reload() bei abgelaufener Session
    kontrolliert werfen (statt stillschweigend eine tote Kartei zu behalten)."""
    page = _ExpiredPage()
    session = StudentSession(
        context=None, page=page, domain="example.test",
        student_id=42, student_name="Test, Tina",
        relogin=None,  # kein Re-Login verfügbar
    )

    raised = False
    try:
        asyncio.run(session.reload())
    except RuntimeError as e:
        raised = True
        assert "kein Re-Login" in str(e)

    assert raised, "reload() muss RuntimeError bei fehlendem Re-Login werfen"
    # Abbruch nach dem ersten goto — kein zweiter Versuch, keine Schüler-Route.
    assert page.gotos == ["https://ausleihe.example.test/"]
    assert page.relogin_calls == 0
    assert session._card_loaded is False


# ---------------------------------------------------------------------------
# _deferred_end — Re-Check 2 mit *neuem* Schüler (nicht nur None): der
# Originalschüler darf nicht abgebrochen werden.
# ---------------------------------------------------------------------------

def test_deferred_end_noop_when_new_student_assigned(monkeypatch):
    """Während der Grace-Frist bekam der Helfer einen *neuen* Schüler zugewiesen
    (helper.student_id jetzt 8, nicht None — z. B. Aufruf nach /api/skip). Der
    Teardown für den Originalschüler 7 darf NICHT laufen (Re-Check 2: 8 != 7).
    Komplement zu test_deferred_end_noop_on_student_changed (dort None)."""
    monkeypatch.setattr(ws_module, "_RECONNECT_GRACE_S", 0.01)
    st = _state_with_iserv()
    hub = _FakeHub()
    student7 = _add_active_student(st, 7)
    helper = _helper_with_student(st, 7)
    student7.assigned_helper = "h1"
    worker7 = _FakeWorker()
    st.student_worker_sessions[7] = worker7

    async def run():
        task = await _run_deferred(st, hub, helper, 7)
        # Während Grace: Helfer jetzt Schüler 8 zugewiesen.
        helper.student_id = 8
        await task
        return task

    asyncio.run(run())

    assert student7.status == "active", "Originalschüler darf nicht abgebrochen werden"
    assert 7 in st.student_worker_sessions, "Worker 7 darf nicht freigegeben werden"
    assert not worker7.closed


# ---------------------------------------------------------------------------
# StudentSession.reload() — Barcode-Feld-Timeout ist nicht fatal
# ---------------------------------------------------------------------------

class _TimeoutLocator(_FakeLocator):
    async def wait_for(self, state=None, timeout=None) -> None:
        # Barcode-Feld erscheint nicht innerhalb der Frist.
        from playwright.async_api import TimeoutError as PlaywrightTimeout
        raise PlaywrightTimeout("barcode field timeout")


class _TimeoutPage(_FakePage):
    def locator(self, sel: str) -> _FakeLocator:
        return _TimeoutLocator(sel)


def test_reload_succeeds_when_barcode_field_times_out():
    """Erscheint das Barcode-Feld nach 20 s nicht, gibt load_card nur eine
    Warnung aus und setzt _card_loaded trotzdem True — reload() darf nicht
    werfen (Timeout ist im read-only Reload-Pfad nicht fatal)."""
    page = _TimeoutPage()
    session = StudentSession(
        context=None, page=page, domain="example.test",
        student_id=42, student_name="Test, Tina",
    )

    asyncio.run(session.reload())  # wirft nicht

    assert session._card_loaded is True, "Trotz Timeout als geladen markiert"
    assert page.gotos == [
        "https://ausleihe.example.test/",
        "https://ausleihe.example.test/#/counter/student/42",
    ]


# ---------------------------------------------------------------------------
# StudentSession.reload() — Re-Login bei Redirect auf der Schüler-Route
# ---------------------------------------------------------------------------

class _StudentRouteExpiredPage(_FakePage):
    """App-Root lädt authed, aber die Schüler-Route leitet auf die Login-Seite
    um — Re-Login wird erst beim zweiten _goto_authed ausgelöst."""

    def __init__(self) -> None:
        super().__init__()
        self._authed = False
        self.relogin_calls = 0

    async def goto(self, url, wait_until=None) -> None:
        self.gotos.append(url)
        if "counter/student" in url and not self._authed:
            self.url = "https://ausleihe.example.test/iserv/login"
        else:
            self.url = url

    async def wait_for_timeout(self, ms) -> None:
        pass


async def _fake_relogin_student_route(page, label):
    page._authed = True
    page.relogin_calls += 1


def test_reload_relogin_on_student_route_redirect():
    """App-Root noch authed, aber die Schüler-Route triggert den Login-Redirect
    → Re-Login feuert beim zweiten _goto_authed, danach erfolgreicher Retry
    der Schüler-Route. Alles auf derselben Page (read-only)."""
    page = _StudentRouteExpiredPage()
    session = StudentSession(
        context=None, page=page, domain="example.test",
        student_id=42, student_name="Test, Tina",
        relogin=_fake_relogin_student_route,
    )

    asyncio.run(session.reload())

    assert page.relogin_calls == 1
    # gotos: App-Root (authed) → Schüler-Route (→ Login) → Re-Login →
    # Schüler-Route (Retry, authed).
    assert page.gotos == [
        "https://ausleihe.example.test/",
        "https://ausleihe.example.test/#/counter/student/42",
        "https://ausleihe.example.test/#/counter/student/42",
    ]
    assert session._card_loaded is True