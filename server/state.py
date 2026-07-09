from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from automation.worker import StudentSession, WorkerPool

    from .iserv_client import IsServClient

# Modus-B-Lebenszyklus einer Schüler-Session.
StudentSessionState = Literal[
    "pending_pairing",  # QR gescannt, Code angezeigt, wartet auf Host-Zuordnung
    "paired",           # vom Host einem Schüler zugeordnet → Daten/Scan frei
    "completed",        # regulär abgeschlossen
    "expired",          # Timeout
    "revoked",          # vom Host abgebrochen / Ausgabe geschlossen
]


@dataclass
class QueueStudent:
    student_id: int
    lastname: str
    firstname: str
    form: str
    status: Literal["pending", "active", "done", "skipped"] = "pending"
    assigned_helper: str | None = None

    def as_dict(self) -> dict:
        return {
            "student_id": self.student_id,
            "lastname": self.lastname,
            "firstname": self.firstname,
            "form": self.form,
            "status": self.status,
            "assigned_helper": self.assigned_helper,
        }


@dataclass
class HelperSession:
    token: str
    name: str
    student_id: int | None = None
    ws: object | None = None  # WebSocket (avoid import cycle)
    created_at: datetime = field(default_factory=datetime.now)
    last_scan: str | None = None
    # Klasse (Kontext), die dieser Helfer bedient. „Nächster"/„Aufrufen" zieht
    # aus der Queue dieses Kontexts; `None` = noch keiner Klasse zugewiesen
    # (Fallback auf den aktiven Kontext, s. next_pending). Rein transient — kein
    # IServ-/DB-Zustand. Umbindbar per /api/helper/{token}/class.
    context_id: str | None = None
    # ISBNs des aktuell zugewiesenen Schülers (Anmeldung + bereits ausgeliehen),
    # für die Scan-Vorabprüfung (analog Modus B).
    expected_isbns: set[str] = field(default_factory=set)
    # Buchungs-Vorabprüfung (Freigabe 2026-07-02): vorgemerkt = bestellt UND Reihe
    # noch nicht auf den Schüler ausgeliehen (= buchbar); lent = Reihe bereits
    # ausgeliehen (für klare Fehlermeldung). Getrennt gehalten, weil `expected_isbns`
    # beides vereint und die Buchbarkeit nicht unterscheiden kann.
    vormerk_isbns: set[str] = field(default_factory=set)
    lent_isbns: set[str] = field(default_factory=set)
    # In-flight Lade-Task (load_and_push_helper_student). Wird beim Abbruch
    # des Schülers (end_student) cancel'd, damit ein noch laufendes open_student
    # seinen Worker-Context zurückgibt — sonst leakt der Context, weil er erst
    # nach open_student in student_worker_sessions registriert wird.
    load_task: object | None = None
    # Verzögerter Disconnect-Teardown („Grace"): beim Trennen des Scanner-WS
    # wird end_student nicht sofort, sondern nach _RECONNECT_GRACE_S als Task
    # angestoßen. Lädt der Helfer die Seite neu (Reconnect innerhalb der Frist),
    # wird dieser Task cancel't und der Schüler stattdessen neugeladen
    # (s. ws_scanner). Ohne Reconnect → echte Trennung → Schüler zurück auf
    # 'pending', Worker zu (wie bisher inline im finally).
    end_task: object | None = None
    # View-Toggle „Menü": Helfer hat per Menü-Button die Warteschlangen-Ansicht
    # geöffnet, während sein zugewiesener Schüler im Hintergrund verbunden
    # bleibt. Solange True bekommt dieser Helfer Live-`queue_update`s (wie ein
    # unzugewiesener), damit die Queue-Ansicht aktuell bleibt. Rein transient —
    # kein Schüler-/IServ-/DB-Zustand. Reset bei Schülerwechsel/ende/Reconnect.
    peeking: bool = False

    def as_dict(self) -> dict:
        return {
            "token": self.token,
            "name": self.name,
            "student_id": self.student_id,
            "connected": self.ws is not None,
            "context_id": self.context_id,
            "last_scan": self.last_scan,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class StudentSessionB:
    """Modus-B-Schüler-Session (Live-Ausgabe).

    `session_token` ist der eigentliche Zugangs-Credential (lang, zufällig).
    `pairing_code` ist nur die menschlich vermittelte Zuordnungshilfe am
    Host und gewährt für sich genommen NIE Datenzugriff.
    """

    session_token: str
    pairing_code: str
    student_id: int | None = None
    state: StudentSessionState = "pending_pairing"
    ws: object | None = None  # WebSocket
    payment_overridden: bool = False
    last_scan: str | None = None
    # ISBNs, die der Schüler laut Anmeldung erhalten soll bzw. bereits hat.
    # Vor jedem Scan wird das gescannte Buch dagegen geprüft (Modus B).
    expected_isbns: set[str] = field(default_factory=set)
    # Buchungs-Vorabprüfung (Freigabe 2026-07-02) — s. HelperSession.
    vormerk_isbns: set[str] = field(default_factory=set)
    lent_isbns: set[str] = field(default_factory=set)
    # Ausgemustertes/verliehenes Buch gescannt → Client zeigt ein blockierendes
    # Hinweis-Modal ohne eigenen Schließen-Button; erst der Host darf es per
    # `/api/clear-book-alert` wieder freigeben. Solange True: Scans ignorieren.
    book_alert_open: bool = False
    book_alert_payload: dict | None = None  # letztes scan_result-Payload (für Reconnect)
    # In-flight Lade-Task (load_and_push_paired_student) — cancel bei
    # invalidate_session, sonst leakt der Worker-Context (s. HelperSession).
    load_task: object | None = None
    created_at: datetime = field(default_factory=datetime.now)
    paired_at: datetime | None = None
    last_activity: datetime = field(default_factory=datetime.now)

    def as_dict_public(self) -> dict:
        """Für den Host sichtbar — bewusst OHNE Schülerdaten.

        Solange nicht `paired`, kennt der Server keinen Schülerbezug; selbst
        danach reicht hier die ID (Namen kommen aus der Queue, nicht von hier).
        """
        return {
            "pairing_code": self.pairing_code if self.state == "pending_pairing" else None,
            "state": self.state,
            "student_id": self.student_id,
            "connected": self.ws is not None,
            "age_s": int((datetime.now() - self.created_at).total_seconds()),
        }


@dataclass
class DisplaySession:
    """iPad-QR-Anzeige. Eigene Rolle ohne jeden Schülerdatenzugriff."""

    display_id: str
    registration_code: str
    authorized: bool = False
    ws: object | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ClassContext:
    """Eine parallel bedienbare Klasse („Klassen-Tab" am Host).

    Jeder Kontext hat eine eigene Queue + eigenen Bücher-Katalog / eigene
    Reihenfolge. Helfer werden an einen Kontext gebunden (`HelperSession.
    context_id`); Modus B, Schuljahr und jahrgangsweite Reihenfolgen bleiben
    global. `implicit=True` markiert einen Übergangs-Kontext, der nur durch
    den Kompat-Zugriff (s. AppState) entstanden ist — er wird aus dem Snapshot
    ausgeblendet und beim ersten echten `open_context` aufgeräumt.
    """

    id: str
    form: str
    queue: list[QueueStudent] = field(default_factory=list)
    book_order: list[str] = field(default_factory=list)
    class_catalog: list[dict] = field(default_factory=list)
    class_catalog_form: str | None = None
    class_catalog_grade: int | None = None
    implicit: bool = False


class AppState:
    def __init__(self) -> None:
        # --- Klassen-Kontexte (Multi-Tab) ---
        # id -> Kontext. Der aktive Kontext (`active_context_id`) ist der gerade
        # am Host fokussierte Klassen-Tab; er ist die Quelle für die Kompat-
        # Felder `queue`/`active_form`/`book_order`/`class_catalog*` weiter unten.
        self.contexts: dict[str, ClassContext] = {}
        self.active_context_id: str | None = None
        # Gewähltes Schuljahr (ID wie '2025/2026'); None = aktuelles Schuljahr.
        # Schuljahr ist global (in den Einstellungen gewählt), nicht pro Kontext.
        self.selected_schoolyear: str | None = None
        self.helper_sessions: dict[str, HelperSession] = {}
        # session_id -> letzter Zugriff (für gleitendes TTL, siehe Methoden unten).
        self.host_sessions: dict[str, datetime] = {}
        self.host_ws_connections: list[object] = []
        self.worker_pool: "WorkerPool | None" = None
        self.iserv: "IsServClient | None" = None
        self.student_worker_sessions: dict[int, "StudentSession"] = {}  # student_id -> Session
        # --- Modus B (Live-Ausgabe) ---
        self.modus_b_open: bool = False
        # Neu bei jedem Öffnen der Ausgabe erzeugt; rotiert NICHT mehr pro
        # Zuordnung (PLAN §3, 2026-06-18).
        self.modus_b_join_secret: str | None = None
        self.modus_b_join_url: str | None = None
        self.modus_b_join_qr: str | None = None  # PNG-Data-URL für iPad/Host
        self.student_sessions: dict[str, StudentSessionB] = {}  # session_token -> Session
        self.displays: dict[str, DisplaySession] = {}           # display_id -> Display
        # Header-Toggle „Tailscale-IP": erzwingt die Tailscale/CGNAT-IP in
        # QR-/Join-URLs statt der Auto-Auswahl (LAN-first). False = Auto.
        self.force_tailscale_ip: bool = False
        # Entwickler-Toggle „PDF lokal speichern": erzwingt beim Drucken das
        # `file`-Backend (Leihschein wird ins Ausgabeverzeichnis geschrieben
        # statt an den Drucker geschickt) — unabhängig von PRINT_BACKEND. Für
        # Tests ohne physischen Drucker. False = normaler Druckweg.
        self.save_pdf_locally: bool = False
        # Experimenteller Entwickler-Toggle „Klasse auf Leihschein korrigieren":
        # ersetzt beim Drucken den (teils falschen) Klassen-Code hinter „Klasse "
        # auf dem IServ-Leihschein durch die echte Klasse des Schülers aus dem
        # Serverstate. Rein lokale PDF-Bearbeitung, kein IServ-Write. False = aus.
        self.fix_class_on_slip: bool = False
        # Host-Toggle „Schüler-Leihschein" (2. Seite): Default für den Druck-
        # Dialog im Helferclient. Wird vom Host gesetzt und an Helfer gesynct.
        self.slip_second_page_default: bool = False
        # Einstellungen-Dialog: am Host gewählter Leihschein-Drucker. None =
        # PRINTER_NAME aus der .env bzw. Systemstandard. Reiner In-Memory-State.
        self.printer_name_override: str | None = None
        # Jahrgangsweite Bücher-Reihenfolgen (im Einstellungen-Dialog vorab pro
        # Bücherliste gesetzt). grade -> ISBN-Sequenz. Speist beim Klassenladen
        # den Kontext-`book_order` (Jahrgang der Klasse). Reiner In-Memory-State,
        # kein DB-/IServ-Write. Wird erst beim Schuljahreswechsel geleert.
        self.book_orders_by_grade: dict[int, list[str]] = {}
        # Ausgeblendete Buchreihen pro Jahrgang (Einstellungen-Dialog, „Ausblenden"-
        # Button je Buch). Ausgeblendete ISBNs werden beim Scannen nicht mehr als
        # „vorgemerkt" geführt/angezeigt (weder Scanner- noch Handy-Anzeige) und
        # sind daher auch nicht buchbar. Reiner In-Memory-State, kein DB-/IServ-
        # Write — betrifft nur die lokale Anzeige/Buchungsprüfung. Wird wie
        # `book_orders_by_grade` erst beim Schuljahreswechsel geleert.
        self.hidden_isbns_by_grade: dict[int, set[str]] = {}
        # Katalog-Cache für klassenübergreifende Warteschlangen (einzeln
        # hinzugefügte Schüler/„Test Config", ggf. aus verschiedenen Jahrgängen):
        # form-Name -> (grade, catalog_isbns). Erspart einen IServ-Roundtrip pro
        # Schüler-Zuweisung; wird wie `book_orders_by_grade` erst beim
        # Schuljahreswechsel geleert.
        self.form_catalog_cache: dict[str, tuple[int | None, list[str]]] = {}
        # Caches für die Helfer-Lupensuche (read-only IServ-GETs, schuljahr-
        # bezogen): Klassennamen + Schüler pro Klasse. Sparen IServ-Roundtrips
        # beim wiederholten Öffnen der Suche. Werden wie die anderen Caches
        # beim Schuljahreswechsel geleert. Keys: schoolyear bzw. "schoolyear|form".
        self.class_names_cache: dict[str, list[str]] = {}
        self.form_students_cache: dict[str, list[dict]] = {}

    # -----------------------------------------------------------------
    # Kontext-Verwaltung
    # -----------------------------------------------------------------

    @property
    def active_context(self) -> ClassContext | None:
        """Der aktuell fokussierte Klassen-Tab oder None."""
        if self.active_context_id is None:
            return None
        return self.contexts.get(self.active_context_id)

    def _ctx_or_active(self, context_id: str | None) -> ClassContext | None:
        if context_id is not None:
            return self.contexts.get(context_id)
        return self.active_context

    def ensure_active_context(self) -> ClassContext:
        """Aktiven Kontext liefern; falls keiner existiert, einen impliziten
        anlegen (Kompat-Pfad, s. Kompat-Properties). Implizite Kontexte sind
        Übergangs-Speicher für Code, der noch nicht auf explizite Kontexte
        umgestellt ist (z. B. Unit-Tests, die `state.queue.append` nutzen); sie
        werden aus dem Snapshot ausgeblendet und beim ersten echten `open_context`
        aufgeräumt."""
        ctx = self.active_context
        if ctx is not None:
            return ctx
        ctx = ClassContext(id=uuid.uuid4().hex[:12], form="", implicit=True)
        self.contexts[ctx.id] = ctx
        self.active_context_id = ctx.id
        return ctx

    def open_context(self, form: str) -> ClassContext:
        """Neuen echten Klassen-Kontext öffnen und aktivieren. Einen noch
        leeren impliziten Übergangs-Kontext aufräumen (nicht mehr nötig)."""
        # Impliziten Kontext aufräumen, falls er noch leer/unbenutzt ist.
        for cid, c in list(self.contexts.items()):
            if c.implicit and not c.queue and not c.book_order and not c.class_catalog:
                self.contexts.pop(cid, None)
                if self.active_context_id == cid:
                    self.active_context_id = None
        ctx = ClassContext(id=uuid.uuid4().hex[:12], form=form)
        self.contexts[ctx.id] = ctx
        self.active_context_id = ctx.id
        return ctx

    def close_context(self, context_id: str) -> ClassContext | None:
        """Kontext entfernen; falls er aktiv war, auf einen verbleibenden
        echten Kontext umschalten (oder None). Gibt den entfernten Kontext
        zurück bzw. None, falls er nicht existierte."""
        ctx = self.contexts.pop(context_id, None)
        if ctx is None:
            return None
        if self.active_context_id == context_id:
            real = next(
                (c for c in self.contexts.values() if not c.implicit),
                None,
            )
            self.active_context_id = real.id if real else None
        return ctx

    def set_active_context(self, context_id: str | None) -> None:
        if context_id is None or context_id in self.contexts:
            self.active_context_id = context_id

    # -----------------------------------------------------------------
    # Kompat-Properties: `queue`/`active_form`/`book_order`/`class_catalog*`
    # delegieren auf den aktiven Kontext. Bestehender Single-Context-Code
    # (sessions/hub/ws/api) und Unit-Tests greifen weiterhin über `state.queue`
    # etc. zu; Multi-Kontext-Endpoints nutzen die expliziten Kontext-Methoden
    # (next_pending(context_id), queue_as_list(context_id), …). Übergangs-
    # Schim (Strangler-Pattern) — entfernt, sobald alle Stellen kontextbewusst
    # sind.
    # -----------------------------------------------------------------

    @property
    def queue(self) -> list[QueueStudent]:
        return self.ensure_active_context().queue

    @queue.setter
    def queue(self, value: list[QueueStudent]) -> None:
        self.ensure_active_context().queue = value

    @property
    def active_form(self) -> str | None:
        ctx = self.active_context
        return ctx.form if ctx and ctx.form else None

    @active_form.setter
    def active_form(self, value: str | None) -> None:
        self.ensure_active_context().form = value or ""

    @property
    def book_order(self) -> list[str]:
        ctx = self.active_context
        return ctx.book_order if ctx else []

    @book_order.setter
    def book_order(self, value: list[str]) -> None:
        self.ensure_active_context().book_order = value

    @property
    def class_catalog(self) -> list[dict]:
        ctx = self.active_context
        return ctx.class_catalog if ctx else []

    @class_catalog.setter
    def class_catalog(self, value: list[dict]) -> None:
        self.ensure_active_context().class_catalog = value

    @property
    def class_catalog_form(self) -> str | None:
        ctx = self.active_context
        return ctx.class_catalog_form if ctx else None

    @class_catalog_form.setter
    def class_catalog_form(self, value: str | None) -> None:
        self.ensure_active_context().class_catalog_form = value

    @property
    def class_catalog_grade(self) -> int | None:
        ctx = self.active_context
        return ctx.class_catalog_grade if ctx else None

    @class_catalog_grade.setter
    def class_catalog_grade(self, value: int | None) -> None:
        self.ensure_active_context().class_catalog_grade = value

    def reset_class_book_order(self, context_id: str | None = None) -> None:
        """Aktive Klassen-Reihenfolge + Katalog eines Kontexts leeren (Klassen-
        wechsel/Tab schließen/Queue leeren). Die jahrgangsweiten Reihenfolgen
        (`book_orders_by_grade`) bleiben bestehen — sie gelten schuljahrweit.
        `context_id=None` → aktiver Kontext (Kompat)."""
        ctx = self._ctx_or_active(context_id)
        if ctx is None:
            return
        ctx.book_order = []
        ctx.class_catalog = []
        ctx.class_catalog_form = None
        ctx.class_catalog_grade = None

    def reset_booklist_orders(self) -> None:
        """Alle jahrgangsweiten Bücher-Reihenfolgen leeren (Schuljahreswechsel:
        andere Booklists, ISBNs passen nicht mehr)."""
        self.book_orders_by_grade = {}
        self.hidden_isbns_by_grade = {}
        self.form_catalog_cache = {}
        self.class_names_cache = {}
        self.form_students_cache = {}

    # -----------------------------------------------------------------
    # Kontextbewusste Lookups
    # -----------------------------------------------------------------

    def find_student(self, student_id: int) -> QueueStudent | None:
        """Schüler über ALLE Kontexte suchen (student_id ist schulweit eindeutig,
        daher eindeutig zugeordnet). Gibt den QueueStudent zurück (lebt in
        genau einem Kontext) oder None."""
        for ctx in self.contexts.values():
            for s in ctx.queue:
                if s.student_id == student_id:
                    return s
        return None

    def find_student_with_ctx(self, student_id: int) -> tuple[ClassContext, QueueStudent] | None:
        """Wie `find_student`, zusätzlich den besitzenden Kontext."""
        for ctx in self.contexts.values():
            for s in ctx.queue:
                if s.student_id == student_id:
                    return ctx, s
        return None

    def next_pending(self, context_id: str | None = None) -> QueueStudent | None:
        """Nächsten wartenden Schüler eines Kontexts. `context_id=None` →
        aktiver Kontext (Kompat, z. B. Helfer ohne Klassen-Bindung)."""
        ctx = self._ctx_or_active(context_id)
        if ctx is None:
            return None
        return next((s for s in ctx.queue if s.status == "pending"), None)

    def pending_count(self, context_id: str | None = None) -> int:
        ctx = self._ctx_or_active(context_id)
        if ctx is None:
            return 0
        return sum(1 for s in ctx.queue if s.status == "pending")

    def pending_queue_as_list(self, context_id: str | None = None) -> list[dict]:
        """Nur die wartenden Schüler eines Kontexts — für die Warteschlangen-
        Anzeige im Helferclient, solange dieser keinen Schüler zugewiesen hat."""
        ctx = self._ctx_or_active(context_id)
        if ctx is None:
            return []
        return [s.as_dict() for s in ctx.queue if s.status == "pending"]

    def queue_as_list(self, context_id: str | None = None) -> list[dict]:
        ctx = self._ctx_or_active(context_id)
        if ctx is None:
            return []
        return [s.as_dict() for s in ctx.queue]

    def real_contexts_summary(self) -> list[dict]:
        """Alle echten (nicht-impliziten) Klassen-Kontexte für den Helferclient:
        je Kontext id, form und die wartenden Schüler (pending) — die Daten für
        die Klassen-Reiter im Helfer-Menü (Warteschlange je Tab mit „Aufrufen").
        Einfügereihenfolge der ``dict`` bleibt erhalten = Reihenfolge wie im
        Host. Wartende (nicht active/done/skipped), weil nur diese aufrufbar
        sind — analog ``pending_queue_as_list``."""
        return [
            {
                "id": c.id,
                "form": c.form,
                "queue": [s.as_dict() for s in c.queue if s.status == "pending"],
            }
            for c in self.contexts.values()
            if not c.implicit
        ]

    def helpers_as_dict(self) -> dict:
        return {t: h.as_dict() for t, h in self.helper_sessions.items()}

    def state_snapshot(self) -> dict:
        from .config import get_config
        pool = self.worker_pool
        worker_stats = (
            pool.stats() if pool is not None and hasattr(pool, "stats")
            else {"total": 0, "available": 0, "in_use": 0}
        )
        ctx = self.active_context
        # Kontexte für den Host: nur echte (keine impliziten Übergangs-Kontexte).
        contexts = {
            c.id: {
                "id": c.id,
                "form": c.form,
                "queue": [s.as_dict() for s in c.queue],
            }
            for c in self.contexts.values()
            if not c.implicit
        }
        return {
            "type": "state",
            # Kompat-Flat-Felder (alle aus dem aktiven Kontext) — bestehender
            # Host-Code liest weiter `state.queue`/`state.active_form` etc.
            "active_form": ctx.form if ctx and ctx.form else None,
            "active_context_id": self.active_context_id,
            "contexts": contexts,
            "selected_schoolyear": self.selected_schoolyear,
            "queue": self.queue_as_list(),
            "helpers": self.helpers_as_dict(),
            "modus_b": self.modus_b_snapshot(),
            "allow_booking": get_config().allow_booking,
            "worker_pool": worker_stats,
            "force_tailscale_ip": self.force_tailscale_ip,
            "save_pdf_locally": self.save_pdf_locally,
            "fix_class_on_slip": self.fix_class_on_slip,
            "slip_second_page_default": self.slip_second_page_default,
            "printer_name": self.printer_name_override,
            "book_order": list(ctx.book_order) if ctx else [],
        }

    def modus_b_snapshot(self) -> dict:
        pending = [
            s.as_dict_public()
            for s in self.student_sessions.values()
            if s.state == "pending_pairing"
        ]
        displays = [
            {
                "display_id": d.display_id,
                "authorized": d.authorized,
                "connected": d.ws is not None,
            }
            for d in self.displays.values()
        ]
        return {
            "open": self.modus_b_open,
            "join_url": self.modus_b_join_url,
            "pending": pending,
            "pending_count": len(pending),
            "displays": displays,
        }

    # --- Modus-B-Lookups ---

    def find_session_by_code(self, code: str) -> StudentSessionB | None:
        return next(
            (
                s
                for s in self.student_sessions.values()
                if s.pairing_code == code and s.state == "pending_pairing"
            ),
            None,
        )

    def find_session_by_student(self, student_id: int) -> StudentSessionB | None:
        return next(
            (
                s
                for s in self.student_sessions.values()
                if s.student_id == student_id and s.state in ("pending_pairing", "paired")
            ),
            None,
        )

    def code_in_use(self, code: str) -> bool:
        return any(
            s.pairing_code == code and s.state == "pending_pairing"
            for s in self.student_sessions.values()
        )

    def find_helper_for_student(self, student_id: int) -> HelperSession | None:
        return next(
            (h for h in self.helper_sessions.values() if h.student_id == student_id),
            None,
        )

    # --- Host-Login-Sessions (gleitendes TTL) ---

    def add_host_session(self, sid: str) -> None:
        self.host_sessions[sid] = datetime.now()

    def remove_host_session(self, sid: str) -> None:
        self.host_sessions.pop(sid, None)

    def is_host_session_valid(self, sid: str | None, ttl_s: int) -> bool:
        """Gültig, wenn bekannt und nicht abgelaufen. Bei Gültigkeit gleitend
        verlängert (aktive Hosts werden nicht ausgeloggt)."""
        if not sid:
            return False
        seen = self.host_sessions.get(sid)
        if seen is None:
            return False
        if (datetime.now() - seen).total_seconds() > ttl_s:
            self.host_sessions.pop(sid, None)
            return False
        self.host_sessions[sid] = datetime.now()
        return True

    def sweep_host_sessions(self, ttl_s: int) -> None:
        now = datetime.now()
        for sid, seen in list(self.host_sessions.items()):
            if (now - seen).total_seconds() > ttl_s:
                del self.host_sessions[sid]


_app_state = AppState()


def get_state() -> AppState:
    return _app_state