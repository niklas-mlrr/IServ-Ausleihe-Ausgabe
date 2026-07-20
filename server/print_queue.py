"""Server-interne Druckerwarteschlange mit Drucker-Pool-Verteilung.

Serialisiert alle Leihschein-Druckaufträge (Host, Helfer, künftig Schüler) und
verteilt sie auf den konfigurierten Drucker-Pool (`RuntimeSettings.printers`):

  - Zentrale Warteschlange (`waiting`): rollen-gerecht geordnet
    (HOST > HELFER > SCHÜLER), Aufträge ohne zugewiesenen Drucker.
  - Pro Drucker Kapazität 2 (max 2 gesendete Aufträge): ein OS-aktiv druckender
    (Status ``printing``, Position 0) + ein an OS gesendeter, noch wartender
    (Status ``spooled``, Position 1). Der erste zentrale Wartende bei vollem
    Drucker ist Position 2.
  - Verteilung (level-weise, Allowlist-gerecht): erst alle idle-Drucker (Last 0)
    einen Auftrag bekommen, dann Drucker auf Last 1 — so bekommt kein Drucker
    einen 2. Auftrag, solange ein anderer *erlaubter* Drucker noch idle ist
    (Parallelismus statt nacheinander). Pro Level picken die Drucker in der
    konfigurierten Reihenfolge (linkester zuerst); jeder zieht den ranghöchsten
    Auftrag, der ihn erlaubt (`job.allowed_printers`: `None` = alle erlaubt,
    sonst nur IDs darin). Sind alle Drucker voll, warten weitere Aufträge
    zentral, bis ein Drucker wieder Kapazität hat.

**Parallele Verteilung / OS-getriebener Status:** jeder gesendete Auftrag läuft
in einem eigenen Hintergrund-Task (`_track_job`), der nach dem Dispatch zyklisch
den OS-Druckstatus pollt (`printing.read_job_state`) und daraus den Job-Status
treibt — ``spooled`` (gesendet, wartet) → ``printing`` (OS druckt aktiv) →
``done`` (OS-Job weg = physisch fertig). Der Scheduler-Worker blockiert nicht
auf Completion-Polls: er dispatcht in einem nicht-blockierenden Schritt und
schläft, bis ein finalisierter Tracker ihn weckt. So drucken mehrere Drucker
wirklich parallel, und „wird gedruckt" erscheint erst, wenn das OS aktiv druckt
(nicht schon bei logischer Slot-Beförderung).

**Position** (für Notifications + zentrale-Queue-Anzeige): je Job das Minimum
über alle erlaubten Drucker, wie viele Aufträge dort noch vor ihm liegen —
0 = druckt, 1 = gesendet/wartet, 2+ = in der zentralen Warteschlange (s.
`_compute_positions`).

Leerer Pool (`state.settings.printers == []`): der Scheduler dispatcht nichts —
Aufträge bleiben in `waiting`. Die Enqueue-Stellen (Host-Endpoint / Scanner-WS)
verweigern den Druck vorab mit „Kein Drucker konfiguriert", damit kein Auftrag
endlos wartet.

Notifications (nur an den jeweiligen Urheber):
  Helfer-WS  — `print_progress` (status/position/printer) + `print_result` (ok/fehlgeschlagen)
  Host-WS    — zielgerichtet an den startenden Host (`host_sid`), gleiche Payload
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

Role = Literal["host", "helper", "student"]
JobStatus = Literal[
    "queued", "dispatching", "spooled", "printing", "done", "failed", "stalled", "peer_error"
]
JobState = Literal["absent", "spooled", "printing"]

# Rangfolge für die Einfügung in die zentrale Warteschlange (`waiting`):
# niedrigerer Wert = höherer Vorrang. Bereits gespoolte/druckende Aufträge sind
# am OS verbindlich und werden von der Umsortierung nicht mehr berührt.
_RANK: dict[Role, int] = {"host": 0, "helper": 1, "student": 2}

# Kapazität je Drucker: max 2 gesendete Aufträge (1 druckend + 1 gespoolt).
_PRINTER_CAPACITY = 2

# OS-Polling im Tracker: Intervall und maximale Wartezeit bis zum Finalisieren
# (Timeout wird wie bisher als „fertig" gewertet, damit die interne Warteschlange
# nicht blockiert — der physische Druck läuft am OS ohnehin weiter).
_TRACK_POLL_S = 0.7
_TRACK_TIMEOUT_S = 90.0

# Inaktivitäts-Schwelle: bleibt der OS-Status eines gesendeten Auftrags länger
# als diese Zeit ohne Wechsel (spooled→printing→absent) stehen, gilt der
# Drucker als hängend → `_handle_stall` markiert ihn fehlerhaft, der Urheber
# bekommt „Es dauert ungewöhnlich lange …", Mit-Betroffene am selben Drucker
# „Fehler bei vorigem Auftrag - <Position>". Kürzer als `_TRACK_TIMEOUT_S`,
# damit ein hängender (nicht nur langsamer) Drucker als Fehler erkannt wird,
# bevor der Absolute-Cap ihn stillfertig-wertet.
_INACTIVITY_TIMEOUT_S = 30.0


def slip_name(lastname: str | None, firstname: str | None, form: str | None) -> str:
    """„Nachname, Vorname (Form)" für das Host-Druck-Popup — der Klassen-Präfix
    „Klasse " wird abgeschnitten (IServ liefert teils „Klasse 5a", gezeigt wird
    „5a"). Fehlt die Klasse, entfällt der Klammerzusatz."""
    last = (lastname or "").strip()
    first = (firstname or "").strip()
    form_clean = (form or "").removeprefix("Klasse ").strip()
    base = ", ".join(p for p in (last, first) if p)
    return f"{base} ({form_clean})" if form_clean else base


@dataclass
class PrintJob:
    id: str
    role: Role
    student_id: int
    pages: str | None
    name: str  # „Nachname, Vorname (Form)" fürs Host-Popup
    helper_token: str | None = None  # Urheber ist ein Helfer (WS-Ziel)
    host_sid: str | None = None  # Urheber ist ein Host-Browser (sid-Ziel)
    status: JobStatus = "queued"
    result: dict | None = None  # Druck-Result (mit job_handle); Finalresult für HTTP
    job_handle: dict | None = None  # OS-Job-Handle für Status-Polling
    assigned_printer_id: str | None = None  # None = in zentraler Warteschlange
    # Erlaubte Drucker für diesen Auftrag (Snapshot der Klassen-Allowlist zum
    # Enqueue-Zeitpunkt). `None` = jeder Pool-Drucker erlaubt (Default, keine
    # Einschränkung); eine Menge (auch leer) beschränkt auf genau diese IDs.
    # Bereits wartende Aufträge behalten ihre Allowlist, auch wenn die Klasse
    # später umkonfiguriert wird (gewollt: „mit in der Warteschlange gespeichert").
    allowed_printers: set[str] | None = None
    created_at: datetime = field(default_factory=datetime.now)
    done: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def create(cls, **kw) -> PrintJob:
        kw.setdefault("id", uuid.uuid4().hex[:12])
        return cls(**kw)


@dataclass
class _Slots:
    """Kapazität eines Druckers: max 2 gesendete Aufträge (FIFO nach
    Dispatch-Reihenfolge). Keine Status-Unterscheidung mehr — der Job-Status
    wird OS-getrieben vom Tracker gesetzt, nicht per Slot-Position."""

    jobs: list[PrintJob] = field(default_factory=list)

    @property
    def load(self) -> int:
        return len(self.jobs)


class PrintQueue:
    """Einzige Serialisierungs-/Verteilungsstelle für Leihschein-Drucke."""

    def __init__(self) -> None:
        self.waiting: list[PrintJob] = []
        self.slots: dict[str, _Slots] = {}
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()  # laufende Tracker-Tasks
        # Job-Id → Tracker-Task, um Peer-Tracker beim Stall gezielt zu canceln.
        self._job_tasks: dict[str, asyncio.Task] = {}
        self._stopped = False
        # Als fehlerhaft (hängend) markierte Drucker-IDs: der Scheduler
        # dispatcht nichts mehr dorthin (`_claim_fills` überspringt sie), und
        # `_compute_positions` zählt sie für Aufträge mit Ersatzdrucker nicht
        # mit. Wird per `reactivate()` (Host-Einstellungen „Wieder aktivieren")
        # oder beim Entfernen/Verwaisten-Lauf des Druckers zurückgesetzt.
        self.faulty_printers: set[str] = set()

    # ---- Lebenszyklus --------------------------------------------------

    def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._wake.set()  # falls schon Aufträge vor Start enqueued wurden
        self._task = asyncio.create_task(self._run(), name="print-queue-worker")

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            self._job_tasks.clear()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ---- Enqueue -------------------------------------------------------

    async def enqueue(self, job: PrintJob) -> int:
        """Auftrag rollen-gerecht in die zentrale Warteschlange einreihen;
        0-basierte Position in `waiting` zurückgeben."""
        async with self._lock:
            idx = len(self.waiting)
            for i, j in enumerate(self.waiting):
                if _RANK[j.role] > _RANK[job.role]:
                    idx = i
                    break
            self.waiting.insert(idx, job)
            position = idx
        self._wake.set()
        await self._notify_all()
        return position

    def wake(self) -> None:
        """Scheduler wecken — aufrufen, wenn ein Drucker hinzugefügt wurde
        (wartende Aufträge können jetzt verteilt werden)."""
        self._wake.set()

    # ---- Worker --------------------------------------------------------

    async def _run(self) -> None:
        while not self._stopped:
            try:
                await self._step()
            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001 — Worker darf nicht crashen
                log.exception("Print-Queue-Worker-Schritt fehlgeschlagen")
                await asyncio.sleep(1)

    async def _step(self) -> None:
        from .state import get_state

        printers = list(get_state().settings.printers)

        # Pipeline füllen: wartende Aufträge auf freie Drucker-Kapazität verteilen
        # (level-weise, linkester Tie-Break, Allowlist-gerecht). Claims sammeln
        # und je einen Hintergrund-Tracker spawnen — die langsamen Dispatches
        # und OS-Polls laufen dort, der Worker blockiert nicht.
        async with self._lock:
            self._reconcile(printers)
            claims = self._claim_fills(printers)
        for pid, _pname, job in claims:
            self._spawn_tracker(pid, job)
        if claims:
            await self._notify_all()
            return  # sofort weiter, bis nichts mehr befüllbar ist

        # Nichts befüllbar → auf neuen Auftrag / Config-Wechsel / freigegebenen
        # Drucker (Tracker weckt nach Finalize) warten.
        await self._wait_for_work()

    def _spawn_tracker(self, printer_id: str, job: PrintJob) -> None:
        task = asyncio.create_task(
            self._track_job(printer_id, job), name=f"print-track-{job.id}"
        )
        self._tasks.add(task)
        self._job_tasks[job.id] = task

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            # Nur löschen, wenn noch derselbe Task eingetragen ist (ein
            # ggf. neuer Resume-Tracker würde sonst weggekickt).
            if self._job_tasks.get(job.id) is t:
                self._job_tasks.pop(job.id, None)

        task.add_done_callback(_done)

    # ---- Reconcile -----------------------------------------------------

    def _reconcile(self, printers: list) -> None:
        """Slots an die aktuelle Drucker-Konfiguration anpassen: für jeden
        konfigurierten Drucker einen Slot-Eintrag sicherstellen. Entfernte
        Drucker mit noch aktiven Jobs bleiben als verwaiste Slots erhalten,
        bis sie ausgedrained sind (neue Zuweisungen bekommen sie nicht, da
        `_claim_fills` nur über `printers` iteriert). Leer laufende verwaiste
        Slots werden aufgeräumt.

        Reaktivierte Drucker: beim Stall im Slot belassene Aufträge
        (``stalled``/``peer_error``) werden hier weggeräumt, sobald der Drucker
        nicht mehr fehlerhaft ist — so erhält er seine Kapazität zurück und der
        Scheduler kann neue Aufträge dorthin dispatchen. Läuft unter dem Lock,
        daher raced es nicht gegen `_claim_fills` (das ebenfalls das Lock hält)."""
        for p in printers:
            self.slots.setdefault(p.id, _Slots())
        configured_ids = {p.id for p in printers}
        orphaned_empty = [
            pid for pid, s in self.slots.items()
            if pid not in configured_ids and s.load == 0
        ]
        for pid in orphaned_empty:
            del self.slots[pid]
            self.faulty_printers.discard(pid)  # verwaister Drucker → Marke weg
        # Tote Aufträge (Stall/Peer-Error) aus reaktivierten Slots räumen.
        for pid in configured_ids:
            if pid in self.faulty_printers:
                continue  # noch fehlerhaft → blockierte Aufträge stehen lassen
            s = self.slots.get(pid)
            if not s:
                continue
            if any(j.status in ("stalled", "peer_error", "failed") for j in s.jobs):
                s.jobs = [j for j in s.jobs if j.status not in ("stalled", "peer_error", "failed")]

    # ---- Füllen --------------------------------------------------------

    def _claim_fills(self, printers: list) -> list[tuple[str, str | None, PrintJob]]:
        """Wartende Aufträge auf freie Drucker-Kapazität verteilen — level-weise
        und Allowlist-gerecht (s. Modul-Docstring). Liefert Claims
        `(printer_id, printer_name, job)`; der Job wird aus `waiting` entfernt,
        an `slots[pid].jobs` angehängt und auf `dispatching` gesetzt. Der
        eigentliche Dispatch + OS-Polling läuft im Hintergrund-Tracker
        (`_track_job`), nicht-blockierend für den Worker.

        Level-weise (erst Last 0, dann Last 1): so bekommt kein Drucker einen
        2. Auftrag, solange ein anderer *erlaubter* Drucker noch idle ist
        (Parallelismus). Pro Level in der konfigurierten Reihenfolge
        (linkester zuerst); jeder Drucker zieht den ranghöchsten Auftrag, der
        ihn erlaubt (`allowed_printers is None` = alle, sonst ID darin). Ist der
        Kopf der Warteschlange für mehrere freie Drucker erlaubt, druckt der
        linkeste — weil er zuerst pickt."""
        claims: list[tuple[str, str | None, PrintJob]] = []
        for target_load in range(_PRINTER_CAPACITY):
            for printer in printers:
                if printer.id in self.faulty_printers:
                    continue  # hängend — keine neuen Aufträge dorthin
                s = self.slots.get(printer.id)
                load = s.load if s else 0
                if load != target_load or load >= _PRINTER_CAPACITY:
                    continue  # nur Drucker auf diesem Füll-Level, mit Kapazität
                # ersten ranghöchsten Auftrag suchen, der diesen Drucker erlaubt.
                picked = None
                for i, job in enumerate(self.waiting):
                    if job.allowed_printers is None or printer.id in job.allowed_printers:
                        picked = i
                        break
                if picked is None:
                    continue  # kein Auftrag erlaubt diesen Drucker → idle bleiben
                job = self.waiting.pop(picked)
                job.assigned_printer_id = printer.id
                job.status = "dispatching"
                self.slots.setdefault(printer.id, _Slots()).jobs.append(job)
                claims.append((printer.id, printer.name, job))
        return claims

    async def _track_job(self, printer_id: str, job: PrintJob) -> None:
        """Hintergrund-Task je gesendeten Auftrag: Dispatch ans Druck-Backend,
        dann zyklisches OS-Status-Polling → Status `spooled`→`printing`→`done`.
        Läuft unabhängig pro Job, sodass mehrere Drucker parallel drucken und
        der Worker neue Aufträge dispatchen kann, während hier gepollt wird."""
        from .printing import read_job_state

        res = await self._dispatch(job, printer_name=self._printer_name_by_id(printer_id))
        async with self._lock:
            job.result = res
            job.job_handle = res.get("job_handle") if res.get("ok") else None
            if not res.get("ok"):
                job.status = "failed"
                self._remove_from_slot(printer_id, job)
                job.done.set()
                finalized = job
            else:
                job.status = "spooled"  # an OS gesendet, wartet auf den Drucker
                finalized = None
        await self._notify_result_if(finalized)
        await self._notify_all()
        if finalized is not None:
            self._wake.set()
            return

        # OS-Status pollen, bis der Job weg ist (absent) oder Inaktivität/
        # Absolute-Cap zuschlagen. Inaktivität = der OS-Status wechselt länger
        # als `_INACTIVITY_TIMEOUT_S` nicht („es passiert nichts") → Stall.
        last_state: JobState = "spooled"
        last_change = time.monotonic()
        deadline = time.monotonic() + _TRACK_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(_TRACK_POLL_S)
            if self._stopped:
                return
            try:
                state: JobState = await read_job_state(job.job_handle)
            except Exception:  # noqa: BLE001 — Poll darf den Tracker nicht killen
                log.exception("OS-Status-Poll fehlgeschlagen (job %s)", job.id)
                continue
            if state == "absent":
                break
            if state != last_state:
                last_state = state
                last_change = time.monotonic()
                if state == "printing" and job.status != "printing":
                    async with self._lock:
                        job.status = "printing"
                    await self._notify_all()
            elif time.monotonic() - last_change > _INACTIVITY_TIMEOUT_S:
                # Hängender Drucker: kein Statuswechsel seit `_INACTIVITY_TIMEOUT_S`.
                await self._handle_stall(printer_id, job, last_state)
                return

        # Finalize: Job ist aus der OS-Queue verschwunden → gedruckt.
        async with self._lock:
            job.status = "done"
            self._remove_from_slot(printer_id, job)
            job.done.set()
            finalized = job
        await self._notify_result(finalized)
        await self._notify_all()
        self._wake.set()  # Kapazität frei → Scheduler füllt nach.

    def _remove_from_slot(self, printer_id: str, job: PrintJob) -> None:
        """Job aus der Kapazitäts-Liste seines Druckers nehmen (falls noch
        vorhanden). Idempotent — mehrfacher Aufruf schadet nicht."""
        s = self.slots.get(printer_id)
        if s is None:
            return
        try:
            s.jobs.remove(job)
        except ValueError:
            pass

    @staticmethod
    def _stall_label(job_status: str, pos: int) -> str:
        """Warteschlangen-Label eines Auftrags für die Inaktivitäts-Meldung,
        konsistent mit dem clientseitigen `print_progress`-Label (keine +1):
        Pos. 0 + ``printing`` → „wird gedruckt", Pos. 0 sonst → „gesendet,
        wartet auf Druck", Pos. ≥ 1 → „an X. Druckerwarteschlangenposition"
        (X = Position). So bleibt die Positionsnummer beim Fehler stabil —
        der Auftrag, der an Position 0 war, bleibt an 0, statt auf 1 hoch-
        gezählt zu werden."""
        if pos == 0:
            return "wird gedruckt" if job_status == "printing" else "gesendet, wartet auf Druck"
        return f"an {pos}. Druckerwarteschlangenposition"

    def _stall_msg(self, job_status: str, pos: int, pname: str | None) -> str:
        """Vollständige Inaktivitäts-Meldung für einen Auftrag am hängenden
        Drucker: „Es dauert ungewöhnlich lange … - Drucker <Name>: <Label>"
        (bzw. ohne Drucker-Präfix, wenn kein Name gegeben). `job_status` ist
        der Pre-Error-Status (für Pos. 0), `pos` die Warteschlangen-Position."""
        label = self._stall_label(job_status, pos)
        ctx = f"Drucker {pname}: {label}" if pname else label
        return (
            "Es dauert ungewöhnlich lange, vielleicht liegt ein Fehler vor. "
            f"Bitte überprüfe dies. - {ctx}"
        )

    def _error_msg(self, job_status: str, pos: int, pname: str | None) -> str:
        """Fehlermeldung für einen Auftrag am hängenden Drucker,
        positionsbasiert (nicht nach Urheber/Peer):

        - Pos. 0 (der vordere, druckende/gesendete Auftrag am Drucker) →
          Inaktivitäts-Hinweismeldung „Es dauert ungewöhnlich lange … -
          Drucker <Name>: <Label>" (Label aus `_stall_msg`).
        - Pos. ≥ 1 (die dahinter wartenden Aufträge) → „Fehler bei vorigem
          Druckauftrag - X. Warteschlangenposition" (X = Position, ohne +1,
          konsistent mit der normalen „an X. Druckerwarteschlangenposition"-
          Anzeige).

        Aufträge mit Ersatzdrucker (ein erlaubter Drucker ist nicht fehlerhaft)
        gelangen hier gar nicht erst rein — sie werden normal bedient, keine
        Fehlermeldung (s. `_is_peer_error` / Slot-Markierung)."""
        if pos == 0:
            return self._stall_msg(job_status, 0, pname)
        return f"Fehler bei vorigem Druckauftrag - {pos}. Warteschlangenposition"

    async def _handle_stall(self, printer_id: str, job: PrintJob, last_state: JobState) -> None:
        """Inaktivität auf `printer_id`: der Drucker gilt ab hier als hängend
        („es dauert ungewöhnlich lange"). Der Urheber wird ``stalled``, alle
        **anderen** Aufträge am selben Drucker (Slot 1 etc.) werden
        ``peer_error`` (Tracker cancelt). Die Fehlermeldung ist positionsbasiert
        (`_error_msg`): nur der **erste** Auftrag (Pos. 0, der vorn am Drucker
        druckende/gesendete) bekommt den Inaktivitäts-Hinweis („Es dauert
        ungewöhnlich lange … - Drucker <Name>: <Label>"); alle **weiteren**
        (Pos. ≥ 1) bekommen „Fehler bei vorigem Druckauftrag - X.
        Warteschlangenposition" (X = Position, ohne +1). So bleibt die
        Positionsnummer stabil — der an Pos. 0 druckende Auftrag rutscht beim
        Fehler nicht auf „1." hoch. Die Aufträge bleiben **im Slot** des
        Druckers (werden nicht entfernt), damit sie für die Warteschlange
        weiter mitzählen — Last und Positionen der No-Alternative-Jobs
        (Allowlist nur auf diesen fehlerhaften Drucker) beruhen auf der
        unverändert vollen Slot-Belegung. Weggeräumt werden sie erst bei der
        Reaktivierung (``_reconcile`` für nicht-fehlerhafte Drucker). Zentrale-
        Warteschlangen-Jobs ohne Ersatzdrucker bekommen ihr ``peer_error``
        (gleiche positionsbasierte Meldung) über das anschließende
        ``_notify_all`` (sie bleiben in `waiting`, bis der Drucker reaktiviert
        wird); Aufträge mit Ersatzdrucker werden normal bedient (keine
        Fehlermeldung)."""
        pname = self._printer_name_by_id(printer_id)

        cancelled: list[asyncio.Task] = []
        peer_results: list[PrintJob] = []
        async with self._lock:
            # Idempotent: falls ein Parallel-Staller den Drucker schon markiert
            # hat, behandeln wir diesen Job trotzdem als ersten (er ist ja auch
            # inaktiv) — Doppelmarkierung ist harmlos.
            self.faulty_printers.add(printer_id)
            # Positionen einmalig im fehlerhaften Kontext berechnen (für Urheber
            # + Peers — Meldung und Positionsnummer daraus).
            from .state import get_state
            printers = list(get_state().settings.printers)
            positions = self._compute_positions(printers, faulty_ids={printer_id})
            # Urheber-Stall: finalisieren — aber **im Slot belassen**, damit der
            # Drucker für die Warteschlange weiter mitzählt (Last und Positionen
            # der No-Alternative-Jobs). Weggeräumt wird erst bei der Reaktivierung
            # (`_reconcile` für nicht-fehlerhafte Drucker). Meldung positionsbasiert.
            job_pos = positions.get(job.id, 0)
            job.status = "stalled"
            job.result = {
                "ok": False,
                "stalled": True,
                "msg": self._error_msg(last_state, job_pos, pname),
            }
            job.done.set()
            # Peer-Slot-Jobs: finalisieren als peer_error, Tracker canceln —
            # ebenfalls im Slot belassen (mitzählen). Den Urheber überspringen
            # (er steht noch vorne in s.jobs und ist oben schon stalled). Die
            # Peer-Meldung ist positionsbasiert (`_error_msg`); das Label für
            # Pos. 0 nutzt den Pre-Error-Status (vor dem Überschreiben).
            s = self.slots.get(printer_id)
            if s:
                for other in list(s.jobs):
                    if other is job:
                        continue
                    pre_status = other.status
                    pos = positions.get(other.id, 0)
                    other.status = "peer_error"
                    other.result = {
                        "ok": False,
                        "peer_error": True,
                        "msg": self._error_msg(pre_status, pos, pname),
                    }
                    other.done.set()
                    peer_results.append(other)
                    t = self._job_tasks.pop(other.id, None)
                    if t is not None:
                        cancelled.append(t)
        # Tracker außerhalb des Locks canceln + joinen.
        for t in cancelled:
            t.cancel()
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        # Urheber + Peers über ihr Ergebnis benachrichtigen (jeweils nur an
        # den eigenen Urheber adressiert).
        await self._notify_result_if(job)
        for peer in peer_results:
            await self._notify_result_if(peer)
        # Zentrale Warteschlange aktualisieren (Ersatzdrucker-Positionen +
        # peer_error für No-Alternative-Jobs) + Snapshot an alle Hosts.
        await self._notify_all()
        self._wake.set()  # Scheduler füllt Ersatzdrucker nach.

    def reactivate(self, printer_id: str) -> bool:
        """Fehlerhafte Drucker-Marke zurücknehmen (Host-Einstellungen
        „Wieder aktivieren"). Weckt den Scheduler, sodass wartende Aufträge
        wieder dorthin dispatcht werden; der nächste `_reconcile`-Lauf räumt
        die beim Stall im Slot belassenen (blockierten) Aufträge und gibt so
        die Kapazität frei. Liefert True, wenn die Marke aktiv war (und jetzt
        entfernt wurde)."""
        was_faulty = printer_id in self.faulty_printers
        self.faulty_printers.discard(printer_id)
        if was_faulty:
            self._wake.set()
        return was_faulty

    async def _dispatch(self, job: PrintJob, *, printer_name: str | None) -> dict:
        """`print_loan_slip_for` aufrufen, Exceptions in ein ok=False-Result wandeln."""
        from .sessions import print_loan_slip_for
        from .state import get_state

        try:
            return await print_loan_slip_for(
                get_state(), job.student_id, pages=job.pages, printer_name=printer_name
            )
        except Exception as e:  # noqa: BLE001 — Fehler als Ergebnis weiterreichen
            log.exception("Druckauftrag dispatch fehlgeschlagen (student_id=%s)", job.student_id)
            return {"ok": False, "msg": str(e)}

    async def _wait_for_work(self) -> None:
        self._wake.clear()
        await self._wake.wait()

    # ---- Positionen ----------------------------------------------------

    def _compute_positions(
        self, printers: list, faulty_ids: set[str] | None = None
    ) -> dict[str, int]:
        """Pro Job seine Warteschlangen-Position als Minimum über die
        relevanten erlaubten Drucker, wie viele Aufträge dort noch vor ihm
        liegen.

        - Gesendete Jobs eines Druckers (FIFO `slots.jobs`): Position = Slot-
          Index (0 = ältester gesendeter = druckt / druckt als nächstes,
          1 = zweiter gesendeter = wartet). **Nicht** OS-Status-abhängig —
          ob der erste Job schon physisch druckt, bestimmt nur das Label
          (``printing`` → „wird gedruckt", ``spooled`` → „gesendet, wartet"),
          nicht die Positionsnummer. Sonst würde ein noch nicht aktiv
          druckender erster Job den zweiten fälschlich auf Position 2 schieben.
        - Zentrale-Warteschlangen-Job `waiting[i]`: ``min`` über die
          **nicht-fehlerhaften** erlaubten Drucker P von ``load(P) + (Anzahl
          früherer waiting-Jobs, die für P erlaubt sind)``. Hat der Job
          *keinen* Ersatzdrucker (alle erlaubten Drucker sind fehlerhaft),
          wird die Position stattdessen über die fehlerhaften erlaubten
          Drucker berechnet (seine Position in der hängenden Schlange —
          informativ, da er ohnehin nicht bedient wird, solange der Drucker
          fehlerhaft ist). ``allowed_printers is None`` = alle Pool-Drucker.
          Fallback (gar kein erlaubter Drucker im Pool): globaler Index in
          `waiting`.

        ``faulty_ids`` leer/None = Normalbetrieb (alle Drucker zählen). So
        zählt ein hängender Drucker für Aufträge mit Ersatzdrucker **nicht**
        mit, für No-Alternative-Jobs liefert er ihre Warteposition.

        Semantik: 0 = druckt (bzw. druckt als nächstes), 1 = gesendet/wartet,
        2 = erster zentraler Wartender bei vollem Drucker (load 2), usw."""
        faulty = faulty_ids or set()
        positions: dict[str, int] = {}
        for p in printers:
            s = self.slots.get(p.id)
            if not s:
                continue
            for idx, j in enumerate(s.jobs):
                positions[j.id] = idx
        for ci, j in enumerate(self.waiting):
            # Nicht-fehlerhafte Drucker, die dieser Job erlaubt (Ersatzdrucker).
            usable = [
                p for p in printers
                if p.id not in faulty
                and (j.allowed_printers is None or p.id in j.allowed_printers)
            ]
            if usable:
                consider = usable
            else:
                # Kein Ersatzdrucker → nur fehlerhafte erlaubte Drucker zählen
                # (Position in der hängenden Schlange).
                consider = [
                    p for p in printers
                    if p.id in faulty
                    and (j.allowed_printers is None or p.id in j.allowed_printers)
                ]
            best: int | None = None
            for p in consider:
                s = self.slots.get(p.id)
                load = s.load if s else 0
                ahead = 0
                for k in range(ci):
                    other = self.waiting[k]
                    if other.allowed_printers is None or p.id in other.allowed_printers:
                        ahead += 1
                pos = load + ahead
                if best is None or pos < best:
                    best = pos
            positions[j.id] = best if best is not None else ci
        return positions

    # ---- Pool-Snapshot (für state_snapshot) ---------------------------

    def pool_printers(self, printers: list) -> list[dict]:
        """Pro Drucker den Live-Status für den Host-Snapshot: Last, OS-aktiv
        druckender Job (`printing_name`), ältester gesendeter Nicht-Druck-Job
        (`spooled_name`) sowie alle gesendeten Nicht-Druck-Jobs (`spooled_names`).
        Iteriert in der konfigurierten Reihenfolge (bestimmt die
        Verteilungspriorität).

        Lock-frei (synchrone Lesefunktion, aufgerufen vom sync `state_snapshot`
        — das async-Lock ist dort nicht verfügbar). Konsistenz ist für die
        Statusanzeige ausreichend: Referenz-Reads sind in CPython atomar, ein
        konkurrierender Scheduler-Schritt liefert allenfalls einen minimal
        veralteten, aber nie zerrissenen Stand."""
        out: list[dict] = []
        for p in printers:
            s = self.slots.get(p.id)
            printing_name: str | None = None
            spooled_names: list[str] = []
            if s:
                for j in s.jobs:
                    if j.status == "printing":
                        printing_name = j.name
                    elif j.status in ("stalled", "peer_error", "failed"):
                        # Blockierte (fehlgeschlagene) Aufträge zählen über
                        # `load` mit, werden aber nicht als aktiv druckend /
                        # wartend gelistet — der Drucker ist `faulty`, die
                        # Blockade zeigt das Host-UI separat an.
                        continue
                    else:
                        spooled_names.append(j.name)
            out.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "duplex": p.duplex,
                    "is_default": p.name is None,
                    "load": s.load if s else 0,
                    "printing_name": printing_name,
                    "spooled_name": spooled_names[0] if spooled_names else None,
                    "spooled_names": spooled_names,
                    "faulty": p.id in self.faulty_printers,
                }
            )
        return out

    def pool_summary(self) -> dict:
        """Aggregat über die Warteschlange (für den Host-Snapshot)."""
        # Kein Lock nötig — `len()` ist atomar genug für die Anzeige; ein
        # gleichzeitiger Druckauftrag kann die Zahl um 1 verfälschen, was für
        # die Statusanzeige irrelevant ist.
        return {"waiting": len(self.waiting)}

    def waiting_list(self, state) -> list[dict]:
        """Wartende Aufträge (zentrale Warteschlange, noch ohne zugewiesenen
        Drucker) für den Host-Snapshot: Position (Minimum über erlaubte
        Drucker, s. `_compute_positions`), Schüler, Klasse, Auftraggeber und
        die erlaubten Drucker (Allowlist der Klasse zum Enqueue-Zeitpunkt).
        Lock-frei (Anzeige-Konsistenz reicht, s. `pool_printers`).

        Schüler-/Klassen-/Urheber-Lookup live aus dem State, nicht zum
        Enqueue-Zeitpunkt eingefroren — ein Helfer kann sich zwischenzeitlich
        umbenannt haben, ein Schüler aus der Kontext-Queue gerutscht sein
        (dann Fallback auf den am Auftrag gespeicherten `name`). Die Allowlist
        hingegen ist am Auftrag gespeichert und bleibt stabil, auch wenn die
        Klasse später umkonfiguriert wird (s. PrintJob.allowed_printers)."""

        printers = list(state.settings.printers)
        positions = self._compute_positions(printers, faulty_ids=self.faulty_printers)
        out: list[dict] = []
        for j in self.waiting:
            student = state.find_student(j.student_id)
            if student is not None:
                student_name = slip_name(student.lastname, student.firstname, None)
                form_clean = (student.form or "").removeprefix("Klasse ").strip()
                form = form_clean or None
            else:
                # Schüler nicht mehr in einer aktiven Kontext-Queue — der am
                # Auftrag hinterlegte `name` trägt die Form in Klammern.
                student_name = j.name
                form = None
            # Erlaubte Drucker in Pool-Priorität (linkester zuerst). `None` =
            # alle Pool-Drucker; sonst nur die IDs darin. Verwaiste IDs (Drucker
            # nach dem Enqueue entfernt) fallen raus — `all_allowed=False` mit
            # leerer Liste signalisiert dem Host einen nicht bedienbaren Auftrag.
            if j.allowed_printers is None:
                all_allowed = True
                allowed_names = [self._printer_display(p) for p in printers]
            else:
                all_allowed = False
                allowed_names = [
                    self._printer_display(p) for p in printers if p.id in j.allowed_printers
                ]
            out.append(
                {
                    "position": positions.get(j.id, 0),
                    "student": student_name,
                    "form": form,
                    "originator": self._originator_label(state, j),
                    "all_allowed": all_allowed,
                    "allowed_printers": allowed_names,
                }
            )
        return out

    @staticmethod
    def _printer_display(p) -> str:
        """Anzeige-Label eines Pool-Druckers für die Warteschlangen-Liste:
        `name=None` ist der Standarddrucker des Geräts (s. PrinterConfig)."""
        return "Standarddrucker" if p.name is None else p.name

    def _originator_label(self, state, job: PrintJob) -> str:
        """Auftraggeber für die Warteschlangen-Anzeige: Helfer namentlich
        (Token-Lookup), Host als „Host", Schüler als „Schüler" (derzeit nicht
        enqueueiert, s. Modul-Docstring „künftig"). Fallback auf die Rolle."""
        if job.helper_token:
            h = state.helper_sessions.get(job.helper_token)
            name = getattr(h, "name", None) if h is not None else None
            return name or "Helfer"
        if job.host_sid:
            return "Host"
        if job.role == "student":
            return "Schüler"
        return job.role or "–"

    # ---- Notifications -------------------------------------------------

    async def _notify_all(self) -> None:
        """Allen Aufträgen ihre aktuelle Position + Status pushen (nur an den
        jeweiligen Urheber). Position aus `_compute_positions`; für Jobs an
        fehlerhaften Druckern bzw. ohne Ersatzdrucker wird `peer_error` wahr
        → der Client zeigt die Inaktivitäts-Meldung („Es dauert ungewöhnlich
        lange … - <Label>") statt einer normalen Warteposition. Die Meldung
        liefert der Server im `msg`-Feld; der Client baut sie nicht mehr aus
        der Position zusammen (keine +1-Hochzählung mehr)."""
        from .hub import get_hub
        from .state import get_state

        state = get_state()
        printers = list(state.settings.printers)
        async with self._lock:
            positions = self._compute_positions(printers, faulty_ids=self.faulty_printers)
            snapshot: list[tuple[PrintJob, int, JobStatus, str | None, bool, str | None]] = []
            for j in self.waiting:
                pname = self._printer_name(j.assigned_printer_id)
                peer = self._is_peer_error(j, printers, in_slot=False)
                msg = (
                    self._error_msg(j.status, positions.get(j.id, 0), self._peer_pname(j, printers))
                    if peer else None
                )
                snapshot.append((j, positions.get(j.id, 0), j.status, pname, peer, msg))
            for s in self.slots.values():
                for j in s.jobs:
                    # Finalisierte (blockierte) Aufträge nicht mehr mit Progress
                    # pushen — sie haben ihr print_result bereits und bleiben
                    # nur zum Mitzählen im Slot (werden bei Reaktivierung
                    # geräumt).
                    if j.status in ("stalled", "peer_error", "failed"):
                        continue
                    pname = self._printer_name(j.assigned_printer_id)
                    # Slot-Job an fehlerhaftem Drucker → peer_error (kurzes
                    # Fenster, bis der Stall ihn finalisiert aus dem Slot nimmt).
                    peer = j.assigned_printer_id in self.faulty_printers
                    msg = (
                        self._error_msg(j.status, positions.get(j.id, 0), pname)
                        if peer else None
                    )
                    snapshot.append((j, positions.get(j.id, 0), j.status, pname, peer, msg))
        hub = get_hub()
        for job, position, status, printer, peer_error, msg in snapshot:
            await self._send_progress(
                hub, state, job, position, status, printer, peer_error, msg
            )
        # Druck-Übergänge (dispatch/spool/druckt/fertig) als vollen State an
        # alle verbundenen Hosts pushen, damit deren Druckerwarteschlangen-Box
        # live folgt — der Snapshot spiegelt über `pool_printers`/`pool_summary`
        # Last und zentrale Warteschlange. Ohne verbundene Hosts (auch im Test)
        # entfällt der Snapshot-Aufwand komplett.
        if state.host_ws_connections:
            await hub.send_all_hosts(state.state_snapshot())

    def _is_peer_error(self, job: PrintJob, printers: list, *, in_slot: bool) -> bool:
        """Zentraler Wartender ohne Ersatzdrucker (alle erlaubten Drucker sind
        fehlerhaft) → peer_error. Slot-Jobs werden gesondert behandelt
        (`assigned_printer_id in faulty_printers`), daher hier nur für
        `waiting`-Jobs verwendet."""
        faulty = self.faulty_printers
        if not faulty:
            return False
        return not any(
            p.id not in faulty
            and (job.allowed_printers is None or p.id in job.allowed_printers)
            for p in printers
        )

    def _peer_pname(self, job: PrintJob, printers: list) -> str | None:
        """Druckername für die Inaktivitäts-Meldung eines zentralen
        No-Alternative-Jobs: der (einzige) erlaubte fehlerhafte Drucker, wenn
        eindeutig bestimmbar; sonst None (die Meldung zeigt dann nur die
        Warteschlangenposition ohne Drucker-Präfix)."""
        allowed = job.allowed_printers
        if allowed is not None:
            if len(allowed) == 1:
                return self._printer_name_by_id(next(iter(allowed)))
            return None  # mehrere erlaubte Drucker → kein einzelner Name
        # allowed=None (alle Pool-Drucker erlaubt): nur eindeutig bei Pool-Größe 1.
        if len(printers) == 1:
            return self._printer_name_by_id(printers[0].id)
        return None

    def _printer_name(self, printer_id: str | None) -> str | None:
        """Druckername zur Kennung (für Notifications). None = Standarddrucker
        (Name None). Verwaiste Kennungen → None."""
        return self._printer_name_by_id(printer_id)

    def _printer_name_by_id(self, printer_id: str | None) -> str | None:
        if printer_id is None:
            return None
        from .state import get_state

        for p in get_state().settings.printers:
            if p.id == printer_id:
                return p.name
        return None

    async def _send_progress(
        self, hub, state, job: PrintJob, position: int, status: JobStatus, printer: str | None,
        peer_error: bool = False, text: str | None = None,
    ) -> None:
        msg = {
            "type": "print_progress",
            "job_id": job.id,
            "status": status,
            "position": position,
            "name": job.name,
            "printer": printer,
            "peer_error": peer_error,
        }
        if text is not None:
            msg["msg"] = text
        if job.helper_token:
            await hub.send_scanner(job.helper_token, msg)
        if job.host_sid:
            for ws in list(state.host_ws_by_sid.get(job.host_sid, [])):
                await hub.send_websocket(ws, msg)

    async def _notify_result(self, job: PrintJob) -> None:
        await self._notify_result_if(job)

    async def _notify_result_if(self, job: PrintJob | None) -> None:
        """Urheber über Druckergebnis benachrichtigen (nur wenn Job finalisiert
        wurde — failed oder done). `None` ist ein No-op."""
        if job is None:
            return
        from .hub import get_hub
        from .state import get_state

        res = job.result or {}
        base = {
            "type": "print_result",
            "job_id": job.id,
            "ok": bool(res.get("ok")),
            "name": job.name,
        }
        # Spezielle Fehlerarten durchreichen, damit der Client sie gesondert
        # darstellt (lang Stall-Text bzw. „Fehler bei vorigem Auftrag") und
        # nicht als generisches „Druck fehlgeschlagen" + Auto-Advance-Pfad.
        if res.get("stalled"):
            base["stalled"] = True
        if res.get("peer_error"):
            base["peer_error"] = True
        if res.get("ok"):
            base["detail"] = res.get("detail", "gedruckt")
        else:
            base["msg"] = res.get("msg") or res.get("detail") or "Druck fehlgeschlagen"
        hub = get_hub()
        state = get_state()
        if job.helper_token:
            await hub.send_scanner(job.helper_token, base)
        if job.host_sid:
            for ws in list(state.host_ws_by_sid.get(job.host_sid, [])):
                await hub.send_websocket(ws, base)
