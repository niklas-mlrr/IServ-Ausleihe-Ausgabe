from __future__ import annotations

import asyncio
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from automation.worker import StudentSession, WorkerPool

    from .iserv_client import IsServClient
    from .print_queue import PrintQueue

# Modus-B-Lebenszyklus einer Schüler-Session.
StudentSessionState = Literal[
    "pending_pairing",  # QR gescannt, Code angezeigt, wartet auf Host-Zuordnung
    "paired",  # vom Host einem Schüler zugeordnet → Daten/Scan frei
    "completed",  # regulär abgeschlossen
    "expired",  # Timeout
    "revoked",  # vom Host abgebrochen / Ausgabe geschlossen
]

# Duplex-Modus eines Druckers (nur gespeichert, nicht ans Backend weitergereicht —
# die Druck-Backends können Duplex CLI-seitig nicht zuverlässig steuern).
# `one_sided` = einseitig, `two_sided_long` = doppelseitig über lange Seite,
# `two_sided_short` = doppelseitig über kurze Seite.
DuplexMode = Literal["one_sided", "two_sided_long", "two_sided_short"]
DUPLEX_MODES: tuple[str, ...] = ("one_sided", "two_sided_long", "two_sided_short")


def _new_printer_id() -> str:
    """Stabile Druckerkennung (für Slot-Zuordnung im Pool-Scheduler)."""
    return uuid.uuid4().hex[:12]


@dataclass
class PrinterConfig:
    """Ein Drucker im Leihschein-Drucker-Pool.

    `name=None` bedeutet den Standarddrucker des Geräts (OS-/.env-Default).
    `name` ist die technische Identität (Dedup, Dispatch an `lp`/Sumatra) und
    bleibt unverändert. `label` ist ein optionaler, frei wählbarer
    Anzeigename (rein kosmetisch, z. B. „Drucker Raum 104") — beeinflusst
    keinen Druck-Pfad; `None` = kein Anzeigename gesetzt (Verhalten wie heute).
    `duplex` wird pro Drucker konfiguriert, aber derzeit nur gespeichert
    (s. DuplexMode-Kommentar oben). Die Reihenfolge in `RuntimeSettings.printers`
    bestimmt die Verteilungspriorität (linkester freie Drucker zuerst)."""

    id: str = field(default_factory=_new_printer_id)
    name: str | None = None
    label: str | None = None
    duplex: DuplexMode = "one_sided"


@dataclass
class QueueStudent:
    student_id: int
    lastname: str
    firstname: str
    form: str
    status: Literal["pending", "active", "done", "skipped"] = "pending"
    assigned_helper: str | None = None
    # Fortschritt „X/Y Bücher" für die Host-Queue: Y = angemeldete Bücher des
    # Schülers OHNE die ausgeblendeten Reihen (also exakt die Liste, die auch
    # Helfer-/Schüler-Client sehen — `apply_hidden_books` lief schon), X = davon
    # erledigte (bei Hydration bereits ausgeliehen + in dieser Session
    # gescannt/gebucht). Gefüllt in `hydrate_student_info`, fortgeschrieben in
    # `process_scan`; 0/None solange der Schüler noch nie geladen wurde.
    books_total: int | None = None
    done_isbns: set[str] = field(default_factory=set)
    # Bei Hydration bereits ausgeliehene Bücher (status == „ausgeliehen" beim
    # Laden) — die Grundlage für den session-basierten Fortschritt in der Host-
    # Status-Spalte: „seit Aufrufen ausgeliehene" = books_done - loaned_at_load,
    # „beim Aufrufen noch offene vorgemerkte" = books_total - loaned_at_load
    # (dieselben Zahlen wie im Druck-/Nächster-Schüler-Hinweis des Helfers,
    # s. scan-state.computeOpenBooks). Gefüllt in `init_book_progress`, auf 0
    # wenn der Schüler noch nie geladen wurde.
    loaned_at_load: int = 0
    # Anmelde-/Zahlstatus aus IServ (`get_student_info`) — rein INFORMATIV für
    # die Queue-Anzeige, ohne Einfluss auf `status` (die Zuweisungs-Zustands-
    # maschine). `None` = noch nicht abgefragt. Gefüllt beim Klassen-Laden
    # (`_load_student_flags`) und bei jeder Hydration aufgefrischt.
    enrolled: bool | None = None
    paid: bool | None = None
    # Noch offener Betrag in Euro (IServ `amountOpen`) — die Queue zeigt statt
    # eines pauschalen „nicht bezahlt" den konkreten Rest („40,54 € offen").
    amount_open: float | None = None
    remission_pending: bool | None = None
    exemption_pending: bool | None = None
    # Leihschein wurde für diesen Schüler gedruckt (`print_loan_slip_for`).
    # Bewusst ein eigenes Flag statt eines fünften `status`-Werts: der Status
    # ist die Zuweisungs-Zustandsmaschine (pending/active/done/skipped) und
    # bleibt davon unberührt; „Leihschein" ist eine reine Info-Anzeige.
    slip_printed: bool = False
    # Leihschein wurde von der Lehrkraft (unterschrieben) entgegengenommen —
    # von der Lehrkraft-Ansicht (`/teacher`) gesetzt, s. routes/teacher.py
    # ::teacher_set_slip_collected. Eigenes Flag wie `slip_printed`: rein
    # informativ, ändert nie die Zuweisungs-Zustandsmaschine `status`.
    slip_collected: bool = False
    # Beim Klassen-Laden automatisch übersprungen (z. B. nicht angemeldet oder
    # ohne offene vorgemerkte Bücher). Bleibt intern, damit die Host-Queue ihre
    # bestehende `done`-/Info-Badge-Darstellung behält; die Lehreransicht kann
    # den Eintrag dadurch korrekt als übersprungen ausweisen.
    auto_skipped: bool = False
    # Schülerclient (Modus B) hat alle vorgemerkten Bücher gescannt und ist in
    # den Druckmodus gewechselt (WS `print_mode`, s. routes/ws.py). Bei
    # `slip_trigger == "helper"` druckt hier ausschließlich ein Helfer/Host
    # stellvertretend — dieses Flag steuert, ob der Helfer-Client (scan.html)
    # in der Klassenliste vor dem „Aufrufen"-Button einen Druckbutton zeigt
    # (s. `real_contexts_summary`). Bleibt bei anderen `slip_trigger`-Werten
    # ungenutzt (dort läuft der Druck über den Schülerclient selbst).
    print_mode: bool = False
    # Leihschein wurde gedruckt UND die Klasse hat „Leihschein unterschreiben"
    # aktiv (`ClassContext.done_signed`) → der Schülerclient zeigt den
    # Unterschriften-Modus (`sessions.confirm_slip_received`, setzt zugleich
    # `session.loan_slip_mode`). Steuert den „Leihschein unterschreiben"-
    # Button vor dem „Aufrufen"-Button im Helfer-Client (s.
    # `real_contexts_summary`) — Klick schließt den Schüler ab (analog dem
    # Host-Button „Abschließen"), sobald der Helfer die Unterschrift auf dem
    # physischen Leihschein entgegengenommen hat.
    slip_signing: bool = False

    def as_dict(self, *, slip_printing: bool = False) -> dict:
        return {
            "student_id": self.student_id,
            "lastname": self.lastname,
            "firstname": self.firstname,
            "form": self.form,
            "status": self.status,
            "assigned_helper": self.assigned_helper,
            "books_total": self.books_total,
            "books_done": len(self.done_isbns),
            "loaned_at_load": self.loaned_at_load,
            "slip_printed": self.slip_printed,
            # Leihschein-Druck läuft gerade (Auftrag in der Print-Queue, noch
            # nicht finalisiert) — dynamisch aus der Print-Queue im Snapshot
            # abgeleitet (s. `AppState.state_snapshot`), nicht auf dem Schüler
            # gemutet. Default False hält die Helfer-Client-Pfade unverändert.
            "slip_printing": slip_printing,
            "print_mode": self.print_mode,
            "slip_signing": self.slip_signing,
            "slip_collected": self.slip_collected,
            "enrolled": self.enrolled,
            "paid": self.paid,
            "amount_open": self.amount_open,
            "remission_pending": self.remission_pending,
            "exemption_pending": self.exemption_pending,
        }

    def set_info_flags(self, info: dict) -> None:
        """Anmelde-/Zahlstatus aus einem `get_student_info`-Payload übernehmen
        (informative Queue-Anzeige). Ohne Anmeldung liefert IServ für die
        übrigen Felder keine belastbaren Werte — die bleiben dann `None`,
        statt „nicht bezahlt" o. ä. vorzutäuschen (gleiche Logik wie beim
        Auto-Fertig-Filter, s. `_load_student_flags`)."""
        self.enrolled = bool(info.get("enrolled"))
        if not self.enrolled:
            self.paid = self.remission_pending = self.exemption_pending = None
            self.amount_open = None
            return
        self.paid = bool(info.get("paid"))
        self.remission_pending = bool(info.get("remission_pending"))
        self.exemption_pending = bool(info.get("exemption_pending"))
        # `amountOpen` fehlt in manchen Einschreibungen → None (die UI zeigt
        # dann „Bezahlung ausstehend" ohne Betrag, statt „0,00 € offen" zu
        # behaupten).
        raw = info.get("amount_open")
        try:
            self.amount_open = None if raw is None else float(raw)
        except (TypeError, ValueError):
            self.amount_open = None

    def reset_progress(self) -> None:
        """Buch-Fortschritt und Leihschein-Marker zurücksetzen — beim Zurück-
        setzen auf „Wartend" (Trennen/Reset), damit ein neuer Durchlauf nicht
        mit den Zählern des alten startet."""
        self.books_total = None
        self.done_isbns = set()
        self.loaned_at_load = 0
        self.slip_printed = False
        self.slip_collected = False
        self.auto_skipped = False
        self.print_mode = False
        self.slip_signing = False

    @classmethod
    def from_iserv(cls, d: dict, *, form: str) -> QueueStudent:
        """Aus einem IServ-Schüler-Dict (`student_id`/`lastname`/`firstname`)
        bauen — `form` immer explizit übergeben, da sie je nach Aufrufer entweder
        die Klasse des Kontexts (`open_class`) oder eine pro Schüler hinterlegte
        Form (`open_test_config`) ist, nie aus `d` selbst übernommen wird."""
        return cls(
            student_id=d["student_id"],
            lastname=d["lastname"],
            firstname=d["firstname"],
            form=form,
        )


@dataclass
class SpectatorWaiter:
    """FIFO-Warteintrag für einen Helfer, der einen bei einem ANDEREN Helfer
    aktiven Schüler nur zuschaut (s. `sessions.spectate_student`). Trägt
    lastname/firstname/form redundant zum QueueStudent, damit auch ein
    transienter Lupe-Ziel-Schüler (steht in KEINER Queue) beim Freiwerden ohne
    erneuten IServ-Katalog-Lookup als neuer QueueStudent wiederaufgebaut
    werden kann (s. `end_student`s Beförderungs-Zweig)."""

    token: str
    lastname: str
    firstname: str
    form: str
    # Herkunft der Zuschauer-Registrierung: True, wenn der Spectator per Lupe
    # (`search_call`) kam — wird bei der Beförderung (Übernahme des Schülers
    # nach Wartezeit) an `HelperSession.student_via_search` vererbt, sodass der
    # Host auch dann die Klasse in Klammern zeigt. Rationale: docs/PLAN.md.
    via_search: bool = False


@dataclass
class HelperSession:
    token: str
    name: str
    student_id: int | None = None
    # Klasse (form) des zugew. Schülers; Quelle für book_order/info["form"] beim
    # Reconnect ohne QueueStudent. Rationale: docs/PLAN.md § State-Feld-Rationale
    student_form: str | None = None
    # Name des zugew. Schülers — redundant zum QueueStudent, aber für transient
    # Lupe-Schüler (stehen in KEINER Queue, s. `_handle_search_call`) die einzige
    # Namensquelle im Host-Snapshot: `findStudentInState` findet sie sonst nicht
    # und die Helferliste zeigte „–". Rationale: docs/PLAN.md § State-Feld-Rationale
    student_lastname: str | None = None
    student_firstname: str | None = None
    # True, wenn der Schüler per Lupe (`search_call`) zugewiesen wurde — der
    # Host zeigt in der Helferliste in dem Fall die Klasse in Klammern hinter
    # dem Namen (bei Queue-Aufrufen nicht, da die Klasse dort der Tab impliziert).
    # Wird bei Beförderung aus einer Spectator-Warteliste vererbt (s.
    # `SpectatorWaiter.via_search`). Rationale: docs/PLAN.md § State-Feld-Rationale
    student_via_search: bool = False
    # Schüler, den dieser Helfer nur ZUSCHAUT (read-only, kein eigener Worker),
    # weil er bei einem ANDEREN Helfer aktiv ist — getrennt von `student_id`
    # (das bleibt strikt „ich besitze Worker + Queue-Slot"). Niemals
    # `student_id` für einen Spectator setzen, s. `sessions.spectate_student`.
    spectating_student_id: int | None = None
    ws: object | None = None  # WebSocket (avoid import cycle)
    created_at: datetime = field(default_factory=datetime.now)
    last_scan: str | None = None
    # Klasse (Kontext), die dieser Helfer bedient; None = keiner zugewiesen.
    # Rationale: docs/PLAN.md § State-Feld-Rationale
    context_id: str | None = None
    # ISBNs des aktuell zugewiesenen Schülers (Anmeldung + bereits ausgeliehen),
    # für die Scan-Vorabprüfung (analog Modus B).
    expected_isbns: set[str] = field(default_factory=set)
    # Buchungs-Vorabprüfung: vormerk = buchbar, lent = Reihe schon ausgeliehen.
    # Rationale: docs/PLAN.md § State-Feld-Rationale
    vormerk_isbns: set[str] = field(default_factory=set)
    lent_isbns: set[str] = field(default_factory=set)
    # Codes (Barcodes) der aktuell ausgeliehenen Exemplare — unterscheidet bei
    # der Vorabprüfung "dieses genaue Exemplar bereits an dich verliehen"
    # (book_already_lent) von "ein ANDERES Exemplar derselben Reihe" (series_
    # already_lent). Rationale: docs/PLAN.md § State-Feld-Rationale
    lent_codes: set[str] = field(default_factory=set)
    # In-flight Lade-Task (load_and_push_helper_student); cancel bei end_student,
    # sonst leakt der Worker-Context. Rationale: docs/PLAN.md § State-Feld-Rationale
    load_task: object | None = None
    # Verzögerter Disconnect-Teardown („Grace"): end_student erst nach
    # _RECONNECT_GRACE_S. Rationale: docs/PLAN.md § State-Feld-Rationale
    end_task: object | None = None
    # View-Toggle „Menü": Queue-Ansicht offen, Schüler bleibt im Hintergrund →
    # weiter Live-queue_updates. Rationale: docs/PLAN.md § State-Feld-Rationale
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
            # Name/Klasse + Lupe-Herkunft des zugew. Schülers — für die
            # Helferliste im Host (s. `renderHelpers`), bes. bei transienten
            # Lupe-Schülern, die `findStudentInState` nicht findet.
            "student_lastname": self.student_lastname,
            "student_firstname": self.student_firstname,
            "student_form": self.student_form,
            "student_via_search": self.student_via_search,
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
    # Buchungs-Vorabprüfung — s. HelperSession.
    vormerk_isbns: set[str] = field(default_factory=set)
    lent_isbns: set[str] = field(default_factory=set)
    lent_codes: set[str] = field(default_factory=set)
    # Ausgemustertes/verliehenes Buch gescannt → blockierendes Hinweis-Modal,
    # nur Host gibt frei. Rationale: docs/PLAN.md § State-Feld-Rationale
    book_alert_open: bool = False
    book_alert_payload: dict | None = None  # letztes scan_result-Payload (für Reconnect)
    # In-flight Lade-Task (load_and_push_paired_student) — cancel bei
    # invalidate_session, sonst leakt der Worker-Context (s. HelperSession).
    load_task: object | None = None
    # Leihschein nach abgeschlossenem Druck unterschreiben/abgeben. Bleibt
    # über Reconnects erhalten, bis der Host den Modus-B-Schüler abschließt.
    # `loan_slip_recipient` ist bewusst nur eine UI-Zielangabe, kein Schüler-
    # oder Lehrkraftname.
    loan_slip_mode: bool = False
    loan_slip_recipient: str | None = None  # "helper" oder "teacher"
    # Schülerclient hat „Leihschein erhalten" bestätigt (WS `slip_received`,
    # s. `sessions.confirm_slip_received`). Erst dann wechselt die Session in
    # den Unterschriften-Modus bzw. schließt automatisch ab — ein
    # Reload/Reconnect zwischen physischem Druckende und dieser Bestätigung
    # darf den Wechsel nicht vorwegnehmen. Idempotenz-Guard, falls der Client
    # `slip_received` doppelt sendet.
    slip_receipt_confirmed: bool = False
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
class PrinterDisplaySession:
    """Drucker-Display (`/drucker-display`) — Warteschlangen-Anzeige für einen
    Bildschirm neben den Druckern. Pairing-Flow wie `DisplaySession` (zeigt
    zunächst nur den Registrierungs-Code), plus Host-Zuweisung, *welche*
    Pool-Drucker dieses Display sehen soll.

    ``assigned_printer_ids``: ``None`` = alle Pool-Drucker (Default direkt nach
    Authorize); eine explizite geordnete Liste (auch leer) beschränkt auf genau
    diese IDs in der vom Host gewählten Reihenfolge (Display zeigt die Drucker
    in dieser Reihenfolge). Einmal explizit, immer explizit — nur der Default
    ist ``None``.
    ``label``: frei wählbarer Display-Name (vom Host setzbar), der im Reiter
    und als Überschrift auf dem Display erscheint. Leer = Short-ID/Default.
    ``theme``: ``'dark'`` (Default) oder ``'light'`` — Darstellung auf dem
    Display. Schüler-Namen werden — anders als beim iPad-Display — gezeigt,
    aber erst nach Pairing, sodass die offene Seite vorab keine Daten preisgibt."""

    display_id: str
    registration_code: str
    authorized: bool = False
    assigned_printer_ids: list[str] | None = None
    label: str = ""
    theme: str | None = None  # None = folgt System-Einstellung (prefers-color-
                              # scheme); 'light'/'dark' = Host hat überschrieben.
    ws: object | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class TeacherSession:
    """Lehrkraft-Statusansicht (`/teacher`) — genau eine Klasse (Kontext) im
    Blick. Pairing-Flow wie `PrinterDisplaySession`/`DisplaySession`: vor der
    Host-Freischaltung liefert die WS nur den Registrierungscode, danach
    ausschließlich Daten der gebundenen Klasse (nie `state_snapshot()`, s.
    `AppState.teacher_snapshot`).

    `token` ist der eigentliche Zugangs-Credential (lang, zufällig, in der
    URL `/teacher?token=...`) — analog `StudentSessionB.session_token`.
    `registration_code` ist nur die menschlich vermittelte Zuordnungshilfe am
    Host (visueller Abgleich vor der Freischaltung) und gewährt für sich
    genommen nie Datenzugriff. `context_id` bindet die Session unveränderlich
    an genau eine Klasse — kein Wechsel nach dem Minten."""

    token: str
    context_id: str
    registration_code: str
    authorized: bool = False
    ws: object | None = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class RuntimeSettings:
    """Die vier Host-/Entwickler-Bool-Toggles + der Drucker-Pool — im
    Einstellungen-Dialog gesetzt, gemeinsam in `routes/settings.py::_BOOL_SETTINGS`
    (die Bool-Toggles) bzw. `routes/slips.py` (der Pool) verwaltet. Zugriff
    ausschließlich über `state.settings.<name>`."""

    # Header-Toggle „Tailscale-IP": erzwingt die Tailscale/CGNAT-IP in
    # QR-/Join-URLs statt der Auto-Auswahl (LAN-first). False = Auto.
    force_tailscale_ip: bool = False
    # Entwickler-Toggle „PDF lokal speichern": erzwingt das `file`-Druck-Backend.
    # Rationale: docs/PLAN.md § State-Feld-Rationale
    save_pdf_locally: bool = False
    # Entwickler-Toggle „Klasse auf Leihschein korrigieren" (lokale PDF-Bearbeitung,
    # kein IServ-Write). Rationale: docs/PLAN.md § State-Feld-Rationale
    fix_class_on_slip: bool = False
    # Host-Toggle „Schüler-Leihschein" (2. Seite): Default für den Druck-
    # Dialog im Helferclient. Wird vom Host gesetzt und an Helfer gesynct.
    slip_second_page_default: bool = False
    # Leihschein-Drucker-Pool (geordnet; linkester freier Drucker bekommt den
    # nächsten Auftrag). Leer = kein Drucker → Druck verweigert. Default beim
    # ersten Start (vor Persistenz-Load): nur der Standarddrucker (name=None).
    # Persistiert in `data/printers.json` (server/printer_store.py).
    printers: list[PrinterConfig] = field(
        default_factory=lambda: [PrinterConfig(name=None)]
    )


@dataclass
class IservCaches:
    """Die fünf jahrgangs-/schuljahrbezogenen IServ-Caches — gemeinsam von
    `AppState.reset_booklist_orders()` beim Schuljahreswechsel geleert
    (`clear_all()`). Zugriff ausschließlich über `state.caches.<name>`."""

    # Jahrgangsweite Bücher-Reihenfolgen (grade -> ISBN-Sequenz), speist den
    # Kontext-book_order. Rationale: docs/PLAN.md § State-Feld-Rationale
    book_orders_by_grade: dict[int, list[str]] = field(default_factory=dict)
    # Ausgeblendete Buchreihen pro Jahrgang (nicht vorgemerkt/buchbar).
    # Rationale: docs/PLAN.md § State-Feld-Rationale
    hidden_isbns_by_grade: dict[int, set[str]] = field(default_factory=dict)
    # Katalog-Cache für klassenübergreifende Warteschlangen
    # (form -> (grade, catalog_isbns)). Rationale: docs/PLAN.md § State-Feld-Rationale
    form_catalog_cache: dict[str, tuple[int | None, list[str]]] = field(default_factory=dict)
    # Caches der Helfer-Lupensuche (Klassennamen + Schüler pro Klasse).
    # Rationale: docs/PLAN.md § State-Feld-Rationale
    class_names_cache: dict[str, list[str]] = field(default_factory=dict)
    form_students_cache: dict[str, list[dict]] = field(default_factory=dict)

    def clear_all(self) -> None:
        """Alle fünf Caches leeren (Schuljahreswechsel: andere Booklists,
        ISBNs passen nicht mehr)."""
        self.book_orders_by_grade = {}
        self.hidden_isbns_by_grade = {}
        self.form_catalog_cache = {}
        self.class_names_cache = {}
        self.form_students_cache = {}


@dataclass
class ClassContext:
    """Eine parallel bedienbare Klasse („Klassen-Tab" am Host).

    Jeder Kontext hat eine eigene Queue + eigenen Bücher-Katalog / eigene
    Reihenfolge. Helfer werden an einen Kontext gebunden (`HelperSession.
    context_id`); Modus B, Schuljahr und jahrgangsweite Reihenfolgen bleiben
    global.
    """

    id: str
    form: str
    queue: list[QueueStudent] = field(default_factory=list)
    book_order: list[str] = field(default_factory=list)
    class_catalog: list[dict] = field(default_factory=list)
    class_catalog_form: str | None = None
    class_catalog_grade: int | None = None
    # Druck-Allowlist für diese Klasse: Menge der erlaubten Drucker-IDs
    # (`RuntimeSettings.printers`-IDs). `None` = kein Filter, alle Pool-Drucker
    # erlaubt (Default, kompatibel mit Test-Config / Öffnen ohne Auswahl). Eine
    # explizite Menge (auch leer) beschränkt den Leihschein-Druck dieser Klasse
    # auf genau diese Drucker. Beim Enqueue eines Druckauftrags wird die Allowlist
    # in den `PrintJob.allowed_printers` snapshotted (s. print_queue.py) — Ändern
    # der Klasse wirkt erst auf künftige Drucke. Rein In-Memory (Kontexte leben
    # nicht persistiert), kein DB-/IServ-Zugriff.
    allowed_printer_ids: set[str] | None = None
    # Live-Ausgabe (Modus B) für diese Klasse sichtbar? `True` (Default) → der
    # „Pairing (Modus B)"-Kasten erscheint im Klassen-Tab und Pairing-Codes
    # lassen sich Schülern dieser Klasse zuordnen; `False` → beides ausgeblendet
    # bzw. abgewiesen. Das globale Modus-B-Backend (Join-Secret/QR, pending-
    # Sessions, iPad-Freischalt) bleibt davon unberührt — nur die Sichtbarkeit
    # pro Klasse wird gesteuert. Rein In-Memory, wie `allowed_printer_ids`.
    live_ausgabe: bool = True
    # Wann der Leihschein dieser Klasse am Schülerclient (Modus B) gedruckt wird,
    # sobald alle vorgemerkten Bücher gescannt sind („Druckmodus"):
    #   "auto"    — Druckauftrag wird sofort automatisch gesendet,
    #   "student" — Druck per Button am Schülerclient,
    #   "helper"  — Hinweis „Betreuer melden"; Druck via Helfer/Host-Menü,
    #   "barcode" — Platzhalter (kein Verhalten, folgt später).
    # Nach erfolgreichem Druck geht der Schüler automatisch auf „abgeschlossen".
    # Nur Modus B; Modus A unberührt. Rein In-Memory, wie `live_ausgabe`.
    slip_trigger: str = "auto"
    # „Fertig"-Voraussetzungen für den Leihschein dieser Klasse
    # (Klasseneinstellungen-Checkboxen „Leihschein unterschreiben" / „…wird
    # vom Lehrer eingesammelt"): `done_signed` — Schüler bleibt nach dem
    # Druck im Leihscheinmodus, bis der Host ihn abschließt;
    # `done_collected` — der unterschriebene Schein wird zusätzlich vom Lehrer
    # eingesammelt (nur sinnvoll, wenn `done_signed` gesetzt ist — Endpunkt
    # erzwingt das). Die Optionen werden an den Modus-B-Client weitergereicht.
    done_signed: bool = False
    done_collected: bool = False
    # Während `open_class` Schüler/Flags/Katalog lädt, steht der Kontext schon in
    # `self.contexts` (für interne Lookups wie `_ensure_class_catalog`), soll aber
    # NOCH nicht an Clients gehen — sonst snapshottet ein nebenläufiger Broadcast
    # (Helfer hinzufügen, Modus B …) die leere Queue und der Klassen-Tab erscheint
    # am Host vorzeitig. `loading=True` hält den Kontext aus `state_snapshot` und
    # `real_contexts_summary` heraus, bis `open_class` ihn freigibt + broadcastet.
    loading: bool = False


class AppState:
    def __init__(self) -> None:
        # --- Klassen-Kontexte (Multi-Tab) ---
        # id -> Kontext. Der aktive Kontext (`active_context_id`) ist der gerade
        # am Host fokussierte Klassen-Tab.
        self.contexts: dict[str, ClassContext] = {}
        self.active_context_id: str | None = None
        # Gewähltes Schuljahr (ID wie '2025/2026'); None = aktuelles Schuljahr.
        # Schuljahr ist global (in den Einstellungen gewählt), nicht pro Kontext.
        self.selected_schoolyear: str | None = None
        self.helper_sessions: dict[str, HelperSession] = {}
        # session_id -> letzter Zugriff (für gleitendes TTL, siehe Methoden unten).
        self.host_sessions: dict[str, datetime] = {}
        self.host_ws_connections: list[object] = []
        # sid -> verbundene Host-WebSockets (mehrere Tabs/Rechner pro sid möglich).
        # Zwingend für zielgerichtete Druck-Status-Popups: nur der Host, der den
        # Druck gestartet hat, soll das „an X. Position / wird gedruckt / gedruckt"-
        # Popup sehen — nicht alle eingeloggt-Verbundenen. HTTP-Endpoint und WS
        # authentifizieren beide über denselben `session_id`-Cookie, sodass die sid
        # den Urheber eindeutig identifiziert (s. routes/_deps.py / routes/ws.py).
        self.host_ws_by_sid: dict[str, list[object]] = {}
        self.worker_pool: WorkerPool | None = None
        self.iserv: IsServClient | None = None
        self.student_worker_sessions: dict[int, StudentSession] = {}  # student_id -> Session
        # A worker page is stateful.  All production commits for a student must
        # therefore be serialized from the read-only precheck through Enter.
        self.booking_locks: dict[int, asyncio.Lock] = {}
        # Server-interne Druckerwarteschlange (Rollen-Rangfolge, 2-in-flight,
        # OS-Completion-Polling). Lebenszyklus in app.lifespan (start/stop).
        # Zugriff ausschließlich über `state.print_queue`.
        from .print_queue import PrintQueue

        self.print_queue: PrintQueue = PrintQueue()
        # FIFO-Warteliste je Schüler: Helfer, die einen bei einem ANDEREN
        # Helfer aktiven Schüler nur zuschauen (spectate_student in
        # sessions.py), geordnet nach Anmeldereihenfolge.
        self.student_spectators: dict[int, list[SpectatorWaiter]] = {}
        # Die fünf Host-/Entwickler-Toggles + Drucker-Override (früher einzelne
        # Felder auf AppState) — siehe RuntimeSettings. Über state.settings.*
        # ansprechbar.
        self.settings = RuntimeSettings()
        # Die fünf jahrgangs-/schuljahrbezogenen IServ-Caches (früher einzelne
        # Felder auf AppState) — siehe IservCaches. Über state.caches.*
        # ansprechbar.
        self.caches = IservCaches()
        # --- Modus B (Live-Ausgabe) ---
        self.modus_b_open: bool = False
        # Neu bei jedem Öffnen der Ausgabe erzeugt; bleibt über alle
        # Zuordnungen innerhalb der Ausgabe konstant (PLAN §3).
        self.modus_b_join_secret: str | None = None
        self.modus_b_join_url: str | None = None
        self.modus_b_join_qr: str | None = None  # PNG-Data-URL für iPad/Host
        self.student_sessions: dict[str, StudentSessionB] = {}  # session_token -> Session
        self.displays: dict[str, DisplaySession] = {}  # display_id -> Display
        # Drucker-Displays (`/drucker-display`): eigene Rolle, Pairing + pro
        # Display zugewiesene Pool-Drucker-Teilmenge (s. PrinterDisplaySession).
        # Key ist der Token in der URL (= display_id); Sessions bleiben über WS-
        # Trennungen hinweg bestehen (Reload wiederverwendet sie), bis das
        # Display am Host verboten (×) wird.
        self.printer_displays: dict[str, PrinterDisplaySession] = {}
        # Per × verbotene Display-Token: ein Reload mit solchem Token bekommt
        # eine „gesperrt"-Antwort statt einer Session. Ein neu geöffnetes
        # Display (frischer Token) ist erlaubt.
        self.banned_printer_display_tokens: set[str] = set()
        # Lehrkraft-Statusansichten (`/teacher`): token -> Session. Höchstens
        # eine Session pro Kontext (durchgesetzt in routes/teacher.py, nicht
        # hier). Ein entwerteter Token wird aus diesem Dict entfernt statt auf
        # eine Bannliste gesetzt (anders als Drucker-Displays) — der Token ist
        # lang & zufällig (nie wiederverwendet), ein einfaches „unbekannt"
        # reicht, um einen Reconnect zuverlässig abzuweisen.
        self.teacher_sessions: dict[str, TeacherSession] = {}

    # -----------------------------------------------------------------
    # Kontext-Verwaltung
    # -----------------------------------------------------------------

    @property
    def active_context(self) -> ClassContext | None:
        """Der aktuell fokussierte Klassen-Tab oder None."""
        if self.active_context_id is None:
            return None
        return self.contexts.get(self.active_context_id)

    def ctx_or_active(self, context_id: str | None) -> ClassContext | None:
        if context_id is not None:
            return self.contexts.get(context_id)
        return self.active_context

    def open_context(self, form: str) -> ClassContext:
        """Neuen Klassen-Kontext öffnen und aktivieren."""
        ctx = ClassContext(id=uuid.uuid4().hex[:12], form=form)
        self.contexts[ctx.id] = ctx
        self.active_context_id = ctx.id
        return ctx

    def close_context(self, context_id: str) -> ClassContext | None:
        """Kontext entfernen; falls er aktiv war, auf einen verbleibenden
        Kontext umschalten (oder None). Gibt den entfernten Kontext zurück
        bzw. None, falls er nicht existierte."""
        ctx = self.contexts.pop(context_id, None)
        if ctx is None:
            return None
        if self.active_context_id == context_id:
            real = next(iter(self.contexts.values()), None)
            self.active_context_id = real.id if real else None
        return ctx

    def set_active_context(self, context_id: str | None) -> None:
        if context_id is None or context_id in self.contexts:
            self.active_context_id = context_id

    def book_order_of(self, context_id: str | None) -> list[str]:
        """Bücher-Reihenfolge eines EXPLIZITEN Kontexts — `[]`, wenn der Kontext
        unbekannt oder `None` ist. Fällt bewusst NICHT still auf den aktiven Tab
        zurück: ein Helfer ohne Klassen-Bindung soll nicht die Reihenfolge einer
        zufällig aktiven fremden Klasse angezeigt bekommen. Aufrufer ohne
        Kontext (z. B. ein Helfer, dessen `context_id` `None` ist) bekommen
        konsequent eine leere Liste statt einer falschen."""
        if context_id is None:
            return []
        ctx = self.contexts.get(context_id)
        return ctx.book_order if ctx is not None else []

    def reset_class_book_order(self, context_id: str | None = None) -> None:
        """Aktive Klassen-Reihenfolge + Katalog eines Kontexts leeren (Klassen-
        wechsel/Tab schließen/Queue leeren). Die jahrgangsweiten Reihenfolgen
        (`book_orders_by_grade`) bleiben bestehen — sie gelten schuljahrweit.
        `context_id=None` → aktiver Kontext (Kompat)."""
        ctx = self.ctx_or_active(context_id)
        if ctx is None:
            return
        ctx.book_order = []
        ctx.class_catalog = []
        ctx.class_catalog_form = None
        ctx.class_catalog_grade = None

    def reset_booklist_orders(self) -> None:
        """Alle jahrgangsweiten Bücher-Reihenfolgen leeren (Schuljahreswechsel:
        andere Booklists, ISBNs passen nicht mehr). Delegiert an
        `IservCaches.clear_all()`."""
        self.caches.clear_all()

    # -----------------------------------------------------------------
    # Kontextbewusste Lookups
    # -----------------------------------------------------------------

    def active_students(self) -> list[QueueStudent]:
        """Alle Schüler mit Status 'active' über ALLE Kontexte (analog
        `find_student`) — für Guards, die vor einem Kontext-Reset (Schuljahres-
        wechsel) prüfen müssen, ob irgendwo eine laufende Session hängt, nicht
        nur im gerade aktiven Klassen-Tab."""
        return [s for ctx in self.contexts.values() for s in ctx.queue if s.status == "active"]

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
        ctx = self.ctx_or_active(context_id)
        if ctx is None:
            return None
        return next((s for s in ctx.queue if s.status == "pending"), None)

    def pending_count(self, context_id: str | None = None) -> int:
        ctx = self.ctx_or_active(context_id)
        if ctx is None:
            return 0
        return sum(1 for s in ctx.queue if s.status == "pending")

    def pending_queue_as_list(self, context_id: str | None = None) -> list[dict]:
        """Nur die wartenden Schüler eines Kontexts — für die Warteschlangen-
        Anzeige im Helferclient, solange dieser keinen Schüler zugewiesen hat."""
        ctx = self.ctx_or_active(context_id)
        if ctx is None:
            return []
        return [s.as_dict() for s in ctx.queue if s.status == "pending"]

    def queue_as_list(
        self, context_id: str | None = None, *, printing_ids: set[int] | None = None
    ) -> list[dict]:
        ctx = self.ctx_or_active(context_id)
        if ctx is None:
            return []
        return [
            s.as_dict(slip_printing=(s.student_id in printing_ids))
            if printing_ids is not None
            else s.as_dict()
            for s in ctx.queue
        ]

    def real_contexts_summary(self) -> list[dict]:
        """Alle offenen Klassen-Kontexte für den Helferclient: je Kontext id,
        form und die wartenden Schüler (pending) — die Daten für die Klassen-
        Reiter im Helfer-Menü (Warteschlange je Tab mit „Aufrufen"). Einfüge-
        reihenfolge der ``dict`` bleibt erhalten = Reihenfolge wie im Host.
        Wartende (nicht active/done/skipped), weil nur diese aufrufbar sind —
        analog ``pending_queue_as_list``. Zusätzlich ``queue_all`` mit ALLEN
        Schülern (inkl. active/done/skipped) für die Gruppen-Boxen unter der
        Warteschlange im Helfer-Client — ``queue_size``/Tab-Badge bleiben
        bewusst auf ``queue`` (nur pending) gestützt.

        ``slip_trigger`` je Kontext + ``slip_printing`` je Schüler (analog dem
        Host-Snapshot) — der Helfer-Client braucht beides für den
        Betreuerauslöser-Druckbutton in der Klassenliste (nur bei
        ``slip_trigger == "helper"`` und `print_mode`, ausgeblendet sobald ein
        Auftrag bereits läuft). ``done_signed`` je Kontext + ``slip_signing``
        je Schüler (analog) steuern den „Leihschein unterschreiben"-Button
        (s. `QueueStudent.slip_signing`). ``print_default_ids`` (analog
        `own_print_defaults`, aber je Kontext statt nur für den eigenen
        Helfer-Kontext) — die Drucker-Allowlist DIESER Klasse, damit der
        Betreuerauslöser-Druck-Dialog immer die für die Klasse erlaubten
        Drucker vorauswählt, auch wenn der Helfer gerade einem anderen
        Klassen-Kontext zugeordnet ist."""
        printing_ids = self.print_queue.in_flight_student_ids()
        pool_ids = {p.id for p in self.settings.printers}
        return [
            {
                "id": c.id,
                "form": c.form,
                "slip_trigger": c.slip_trigger,
                "done_signed": c.done_signed,
                "print_default_ids": (
                    sorted(pool_ids) if c.allowed_printer_ids is None
                    else sorted(c.allowed_printer_ids & pool_ids)
                ),
                "queue": [
                    s.as_dict(slip_printing=(s.student_id in printing_ids))
                    for s in c.queue if s.status == "pending"
                ],
                "queue_all": [
                    s.as_dict(slip_printing=(s.student_id in printing_ids))
                    for s in c.queue
                ],
            }
            for c in self.contexts.values()
            if not c.loading
        ]

    def helpers_as_dict(self) -> dict:
        return {t: h.as_dict() for t, h in self.helper_sessions.items()}

    def state_snapshot(self) -> dict:
        from .config import get_config

        pool = self.worker_pool
        worker_stats = (
            pool.stats()
            if pool is not None and hasattr(pool, "stats")
            else {"total": 0, "available": 0, "in_use": 0}
        )
        ctx = self.active_context
        # student_ids mit aktuell laufendem Leihschein-Druck — für die
        # `slip_printing`-Marker in der Host-Status-Spalte („Leihschein" statt
        # X/Y, solange der Auftrag in der Print-Queue läuft). Einmal pro Snapshot
        # berechnet; `_notify_all` der Print-Queue broadcastet den Snapshot bei
        # jedem Druck-Übergang, sodass der Host live folgt.
        printing_ids = self.print_queue.in_flight_student_ids()
        contexts = {
            c.id: {
                "id": c.id,
                "form": c.form,
                "queue": [
                    s.as_dict(slip_printing=(s.student_id in printing_ids))
                    for s in c.queue
                ],
                # Drucker-Allowlist dieser Klasse — `None` = alle Pool-Drucker
                # (Default), sonst sortierte ID-Liste der erlaubten Drucker.
                # Der Host-Client rendert daraus die Checkboxen im Klassen-Tab.
                "allowed_printers": (
                    None if c.allowed_printer_ids is None else sorted(c.allowed_printer_ids)
                ),
                # Live-Ausgabe (Modus B) für diese Klasse sichtbar? S. ClassContext.
                "live_ausgabe": c.live_ausgabe,
                # Wann der Leihschein am Schülerclient gedruckt wird (Druckmodus).
                # S. ClassContext.slip_trigger.
                "slip_trigger": c.slip_trigger,
                # „Fertig"-Voraussetzungen (Leihschein unterschreiben/einsammeln).
                # S. ClassContext.done_signed/done_collected.
                "done_signed": c.done_signed,
                "done_collected": c.done_collected,
                # Lehrkraft-Statusansicht dieser Klasse (`/teacher`) — nur der
                # Verbindungs-/Freigabestand für den Host-Reiter, NIE der Token
                # (der bleibt Server-intern + in der einmalig ausgelieferten
                # QR-URL, s. routes/teacher.py). `None` = noch keine Session
                # (Host hat noch keinen QR gemintet).
                "teacher": _teacher_tab_view(self.teacher_session_for_context(c.id)),
            }
            for c in self.contexts.values()
            if not c.loading
        }
        return {
            "type": "state",
            # Flat-Felder (aus dem aktiven Kontext) — der Host-Client liest sie
            # direkt vom Snapshot (kein State-seitiges Kompat-Feld mehr nötig).
            "active_form": ctx.form if ctx and ctx.form and not ctx.loading else None,
            "active_context_id": self.active_context_id,
            "contexts": contexts,
            "selected_schoolyear": self.selected_schoolyear,
            "queue": self.queue_as_list(printing_ids=printing_ids),
            "helpers": self.helpers_as_dict(),
            "modus_b": self.modus_b_snapshot(),
            "allow_booking": get_config().allow_booking,
            "worker_pool": worker_stats,
            "force_tailscale_ip": self.settings.force_tailscale_ip,
            "save_pdf_locally": self.settings.save_pdf_locally,
            "fix_class_on_slip": self.settings.fix_class_on_slip,
            "slip_second_page_default": self.settings.slip_second_page_default,
            "printers": self.print_queue.pool_printers(self.settings.printers),
            "print_queue_summary": {
                **self.print_queue.pool_summary(),
                "waiting_list": self.print_queue.waiting_list(self),
            },
            "printer_displays": self.printer_displays_snapshot(),
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
                # Für die Freischalt-Liste am Host (Klick statt Tippen, wie beim
                # Drucker-Display): Anzeige nur zum visuellen Abgleich mit dem
                # iPad-Bildschirm, kein Credential mehr (s. display_authorize).
                "registration_code": d.registration_code,
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

    # --- Drucker-Displays -------------------------------------------------

    def printer_displays_snapshot(self) -> list[dict]:
        """Drucker-Displays für den Host-Snapshot: je Display Kennung,
        Pairing-Status, Verbindungsstatus, Display-Name (``label``), Theme
        (``'light'``/``'dark'``) und die zugewiesene Drucker-Teilmenge als
        geordnete ID-Liste (oder ``None`` = alle Pool-Drucker). Die Reihenfolge
        der Liste ist die vom Host gewählte Display-Reihenfolge (keine
        Sortierung mehr). Der Host-Client rendert daraus Code-Eingabe, Name-
        Feld, Theme-Schieberegler und die Drucker-Boxen."""
        return [
            {
                "display_id": d.display_id,
                "authorized": d.authorized,
                "connected": d.ws is not None,
                "label": d.label,
                "registration_code": d.registration_code,
                "theme": d.theme,
                "assigned_printer_ids": (
                    None if d.assigned_printer_ids is None else list(d.assigned_printer_ids)
                ),
            }
            for d in self.printer_displays.values()
        ]

    def printer_display_view(self, display: PrinterDisplaySession) -> dict:
        """Gefilterte Queue-Sicht für ein Drucker-Display: die zugewiesenen
        Pool-Drucker (Live-Status) + die zentrale Warteschlange, gefiltert auf
        Einträge, deren Allowlist die Display-Zuweisung schneidet. Logik liegt
        im ``print_queue``-Modul (dort ist die Job-Allowlist beheimatet), s.
        ``PrintQueue.display_view``."""
        return self.print_queue.display_view(self, display.assigned_printer_ids)

    # --- Lehrkraft-Statusansicht (`/teacher`) ---

    def teacher_session_for_context(self, context_id: str) -> TeacherSession | None:
        """Die (höchstens eine) Lehrer-Session einer Klasse — autorisiert oder
        nicht. Für Host-Endpunkte (QR minten/ersetzen, Autorisieren, Trennen),
        die pro Klassen-Tab genau eine Session verwalten."""
        return next(
            (s for s in self.teacher_sessions.values() if s.context_id == context_id), None
        )

    def teacher_snapshot(self, context_id: str) -> dict:
        """Minimierter, klassenscharfer Snapshot für eine autorisierte
        Lehrer-Session — bewusst NICHT `state_snapshot()`: keine anderen
        Klassen, keine Zahl-/Anmeldedaten, keine Buchdetails, keine Drucker-/
        Worker-/Host-Einstellungen, kein Pairing-/QR-Secret. Nur Klassenname,
        Summen je Status und je Schüler Name/Status/Buch-Fortschritt/Druckstatus
        — exakt das, was PLAN „Sichtbare Status" + „Erlaubte Daten" vorsieht.
        `done_collected` steuert, ob die Lehrkraft Leihschein-Eingänge erfassen
        darf; `slip_collected_count` zählt dann die abgeschlossenen Schüler,
        deren gedruckter Leihschein über `/api/teacher/slip-collected` als
        entgegengenommen markiert wurde. Automatisch beim Klassen-Laden
        übersprungene Schüler werden für diese Ansicht als `skipped` abgebildet,
        obwohl die Host-Queue ihren Ablaufstatus `done` behält."""
        ctx = self.contexts.get(context_id)
        counts = {"pending": 0, "active": 0, "done": 0, "skipped": 0}
        if ctx is None or ctx.loading:
            return {
                "class_form": None,
                "counts": counts,
                "students": [],
                "done_collected": False,
                "slip_collected_count": 0,
            }
        printing_ids = self.print_queue.in_flight_student_ids()
        students = []
        slip_collected_count = 0
        for s in ctx.queue:
            teacher_status = "skipped" if s.status == "done" and s.auto_skipped else s.status
            counts[teacher_status] += 1
            if ctx.done_collected and teacher_status == "done" and s.slip_collected:
                slip_collected_count += 1
            students.append(
                {
                    "student_id": s.student_id,
                    "lastname": s.lastname,
                    "firstname": s.firstname,
                    "status": teacher_status,
                    "auto_skipped": s.auto_skipped,
                    "books_total": s.books_total,
                    "books_done": len(s.done_isbns),
                    "slip_printing": s.student_id in printing_ids,
                    "slip_printed": s.slip_printed,
                    "slip_collected": s.slip_collected,
                }
            )
        return {
            "class_form": ctx.form,
            "counts": counts,
            "students": students,
            "done_collected": ctx.done_collected,
            "slip_collected_count": slip_collected_count,
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

    # --- Spectator-Warteliste (s. sessions.spectate_student) ---

    def add_spectator(self, student_id: int, waiter: SpectatorWaiter) -> None:
        """Helfer ans Ende der Warteliste eines Schülers anhängen (FIFO).
        Dubletten (gleicher Token, z. B. doppelter Klick) werden ignoriert."""
        waiters = self.student_spectators.setdefault(student_id, [])
        if not any(w.token == waiter.token for w in waiters):
            waiters.append(waiter)

    def remove_spectator(self, student_id: int, token: str) -> None:
        """Helfer aus der Warteliste eines Schülers entfernen (Disconnect,
        Wechsel zu einem anderen Ziel, oder er bekommt selbst einen Schüler
        zugewiesen). Leere Listen werden aus dem Dict gelöscht."""
        waiters = self.student_spectators.get(student_id)
        if not waiters:
            return
        remaining = [w for w in waiters if w.token != token]
        if remaining:
            self.student_spectators[student_id] = remaining
        else:
            self.student_spectators.pop(student_id, None)

    def pop_next_spectator(self, student_id: int) -> SpectatorWaiter | None:
        """Ältesten wartenden Helfer mit noch verbundenem WS entfernen und
        zurückgeben (FIFO) — für die Beförderung in `end_student`. Tote
        Einträge (Spectator hat sich zwischenzeitlich getrennt) werden dabei
        übersprungen und verworfen."""
        waiters = self.student_spectators.get(student_id)
        if not waiters:
            return None
        while waiters:
            waiter = waiters.pop(0)
            helper = self.helper_sessions.get(waiter.token)
            if helper is not None and helper.ws is not None:
                if not waiters:
                    self.student_spectators.pop(student_id, None)
                return waiter
        self.student_spectators.pop(student_id, None)
        return None

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

    def sweep_host_sessions(self, ttl_s: int) -> list[str]:
        """Remove expired host logins and return their session ids.

        Callers use the ids to close already accepted WebSockets as well.  A
        removed HTTP session alone is not sufficient: an open WS would
        otherwise continue receiving host snapshots.
        """
        now = datetime.now()
        expired: list[str] = []
        for sid, seen in list(self.host_sessions.items()):
            if (now - seen).total_seconds() > ttl_s:
                del self.host_sessions[sid]
                expired.append(sid)
        return expired

    def booking_lock(self, student_id: int) -> asyncio.Lock:
        """Return the per-student production-booking lock."""
        lock = self.booking_locks.get(student_id)
        if lock is None:
            lock = asyncio.Lock()
            self.booking_locks[student_id] = lock
        return lock


_fallback_state = AppState()
_bound_state: ContextVar[AppState | None] = ContextVar("bound_app_state", default=None)


def get_state() -> AppState:
    """Current app's state, or a direct-call compatibility fallback.

    Production requests are bound by ``RuntimeBindingMiddleware``.  The
    fallback remains only for small direct unit tests during the migration and
    must not be used by an ASGI request.
    """
    return _bound_state.get() or _fallback_state


def bind_state(state: AppState) -> Token[AppState | None]:
    return _bound_state.set(state)


def reset_state(token: Token[AppState | None]) -> None:
    _bound_state.reset(token)


def _teacher_tab_view(session: TeacherSession | None) -> dict | None:
    """Lehrkraft-Kachel eines Klassen-Tabs für den Host-Snapshot — bewusst
    OHNE `token` (der bleibt Server-intern/in der einmaligen QR-URL)."""
    if session is None:
        return None
    return {
        "registration_code": session.registration_code,
        "authorized": session.authorized,
        "connected": session.ws is not None,
    }


# ---- Druckerauswahl für den Helfer-Druck-Dialog ----
# `pool_light`: reduzierter Drucker-Pool (nur id/label/name/is_default) — ohne
# die Live-Last/Status-Felder des Host-Snapshots (`pool_printers`).
# `own_print_defaults`: explizite Vorauswahl-IDs der eigenen (letzten
# zugehörigen) Klasse eines Helfers (`helper.context_id`): kein Kontext → [],
# ``allowed_printer_ids is None`` (Klasse erlaubt alle) → alle Pool-IDs, sonst
# ``allowed_printer_ids`` ∩ Pool-IDs. Der Helfer wählt genau diese Menge vor;
# die „alle erlaubt"-Semantik wird serverseitig expandiert, sodass der Client
# nicht zwischen „alle" und „explizit" unterscheiden muss.
def pool_light(state: AppState) -> list[dict]:
    return [
        {"id": p.id, "name": p.name, "label": p.label, "is_default": p.name is None}
        for p in state.settings.printers
    ]


def own_print_defaults(state: AppState, helper) -> list[str]:
    cid = getattr(helper, "context_id", None)
    if cid is None:
        return []
    ctx = state.contexts.get(cid)
    if ctx is None:
        return []
    pool_ids = {p.id for p in state.settings.printers}
    if ctx.allowed_printer_ids is None:
        return sorted(pool_ids)
    return sorted(ctx.allowed_printer_ids & pool_ids)
