"""Tests für den Live-Nachzug der Bücherliste nach dem Ausblenden
(`repush_booklist`): ausgeblendete Reihen fallen sofort aus der g-push-ten
Liste, die ISBN-Vorabmengen auf dem Helfer werden neu gerechnet, und der
Session-Scan-Fortschritt (X/Y-Zählung) bleibt für sichtbare Bücher erhalten.

Rein logisch (RAM-State + Fake-Hub/IServ) — kein echter WebSocket, kein Worker.
"""

from __future__ import annotations

import asyncio

import server.sessions as sessions
from server.state import AppState, HelperSession, QueueStudent


class _FakeHub:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_scanner(self, token, msg, state=None) -> None:
        self.sent.append((token, msg))

    async def send_websocket(self, ws, msg) -> bool:
        self.sent.append((getattr(ws, "token", None), msg))
        return True


class _FakeIServ:
    def __init__(self, info: dict) -> None:
        self._info = info

    async def get_student_info(self, student_id, schoolyear):
        return dict(self._info)


def _setup() -> tuple[AppState, HelperSession, QueueStudent]:
    st = AppState()
    st.selected_schoolyear = "2025"
    ctx = st.open_context("10a")
    s = QueueStudent(student_id=7, lastname="N", firstname="V", form="10a", status="active")
    ctx.queue.append(s)
    # Jahrgang 10, Katalog-ISBNs A/B/HIDE — Cache wird schon beim Schülerladen
    # befüllt, daher hier vorabsetzen (spart den IServ-Katalog-Roundtrip).
    st.caches.form_catalog_cache["10a"] = (10, ["A", "B", "HIDE"])
    st.caches.book_orders_by_grade[10] = ["A", "B", "HIDE"]
    st.caches.hidden_isbns_by_grade[10] = {"HIDE"}
    h = HelperSession(token="tok", name="H")
    h.student_id = 7
    h.ws = object()  # verbunden
    st.helper_sessions["tok"] = h
    st.iserv = _FakeIServ(
        {
            "enrolled": True,
            "paid": True,
            "books": [
                {"isbn": "A", "status": "vorgemerkt"},
                {"isbn": "B", "status": "ausgeliehen"},
                {"isbn": "HIDE", "status": "vorgemerkt"},
            ],
        }
    )
    return st, h, s


def test_repush_filters_hidden_and_recomputes_vormerk():
    """Ausgeblendete ISBN fällt aus der gepushten Liste UND aus der
    buchbaren Menge (vormerk_isbns) auf dem Helfer — beides live, ohne Reload."""
    st, h, _s = _setup()
    hub = _FakeHub()
    asyncio.run(sessions.repush_booklist(st, hub, 7, h, helper=True))
    assert len(hub.sent) == 1
    token, msg = hub.sent[0]
    assert token == "tok"
    assert msg["type"] == "booklist_update"
    pushed_isbns = {b["isbn"] for b in msg["books"]}
    assert pushed_isbns == {"A", "B"}  # HIDE ausgeblendet
    assert "HIDE" not in h.vormerk_isbns  # nicht mehr buchbar
    assert "A" in h.vormerk_isbns


def test_repush_preserves_session_scan_progress_for_visible_books():
    """Ein in dieser Session gescanntes, noch sichtbares Buch bleibt in der
    X/Y-Zählung (done_isbns); ein ausgeblendetes Buch fällt aus BOTH X und Y."""
    st, h, s = _setup()
    # B ist ausgeliehen (zählt ohnehin), A wurde in der Session gescannt,
    # HIDE wurde ebenfalls gescannt → HIDE fällt beim Repush raus.
    s.books_total = 3
    s.done_isbns = {"B", "A", "HIDE"}
    hub = _FakeHub()
    asyncio.run(sessions.repush_booklist(st, hub, 7, h, helper=True))
    # Y = sichtbare Bücher (A, B), X = davon erledigt (A + B; HIDE fällt weg).
    assert s.books_total == 2
    assert s.done_isbns == {"A", "B"}


class _SessionTarget:
    """Minimaler Stand-in für StudentSessionB (Modus B) in repush_booklist."""

    def __init__(self) -> None:
        self.student_id = 7
        self.ws: object | None = None
        self.token = "stok"
        self.expected_isbns: set[str] = set()
        self.vormerk_isbns: set[str] = set()
        self.lent_isbns: set[str] = set()
        self.lent_codes: set[str] = set()


def test_repush_skips_session_without_ws():
    """Modus B: ohne Verbindung (ws=None) wird nicht gesendet — repush_booklist
    bewacht das selbst (``elif target.ws is not None``), kein Crash."""
    st, _h, _s = _setup()
    sess = _SessionTarget()
    sess.ws = None
    hub = _FakeHub()
    asyncio.run(sessions.repush_booklist(st, hub, 7, sess, helper=False))
    assert hub.sent == []


def test_repush_survives_iserv_error():
    """IServ-Fehler beim Repush wird geloggt, nicht geworfen — der Save-Endpoint
    darf nicht crashen, der Helfer behält seine (ggf. stale) Liste."""
    st, h, _s = _setup()

    class _BoomIServ:
        async def get_student_info(self, student_id, schoolyear):
            raise RuntimeError("IServ down")

    st.iserv = _BoomIServ()
    hub = _FakeHub()
    asyncio.run(sessions.repush_booklist(st, hub, 7, h, helper=True))
    assert hub.sent == []  # kein Send, aber auch keine Exception
