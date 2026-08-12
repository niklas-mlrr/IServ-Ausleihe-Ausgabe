"""Playwright-Worker: Context-Pool für die IServ-Ausleihe-Counter-Seite.

Ein WorkerPool hält N vorinitialisierte Browser-Contexts (je eigener Cookie-Jar,
je eigener Login). Pro zugewiesenem Schüler wird eine StudentSession geöffnet:
Login → App-Root → #/counter → Schüler per Typeahead öffnen → Barcode-Feld fokussieren.

SICHERHEITSREGELN (CLAUDE.md / PLAN §6):
  - Lukas' Admin-Account ist AUSSCHLIESSLICH lesend zu verwenden.
  - submit_barcode() füllt das Feld mit fill(), drückt NIEMALS Enter (staged).
  - commit_barcode() drückt Enter und BUCHT gegen die Produktion — gated, nur im
    freigegebenen Buchungstest (server-seitig dreifach gesperrt, s. handle_commit /
    /api/commit-book). Im Normalbetrieb (ALLOW_BOOKING=false) nie erreichbar.
  - Keine Buchung, bis Niklas + Lukas explizit freigegeben haben.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

# Rangfolge der Bewerber um einen freien Worker-Context — kleiner = zuerst
# bedient. Analog zur Rollen-Rangfolge der Druckerwarteschlange (`_RANK` in
# server/print_queue.py): Der Helfer bedient eine ganze Warteschlange und darf
# nie hinter Einzelgeräten anstehen; die Scan-Station bedient wechselnde
# Schüler ohne Handy und geht deshalb vor dem einzelnen Handy-Client.
_WORKER_RANK: dict[str, int] = {"helper": 0, "station": 1, "student": 2}

# Wie lange ein Bewerber je Rolle auf einen freien Context wartet, bevor der
# Mangel als echt gilt. Der Helfer sitzt vor einer Queue und soll schnell einen
# Fehler sehen; Station und Handy-Client stehen physisch am Gerät und warten
# lieber, als nach 12 s eine Fehlermeldung zu bekommen.
_WORKER_WAIT_S: dict[str, float] = {"helper": 12.0, "station": 60.0, "student": 60.0}


@dataclass
class _Waiter:
    """Ein wartender `open_student`-Aufruf in der Pool-Warteschlange."""

    rank: int
    seq: int
    priority: str
    future: asyncio.Future = field(repr=False)


class StudentSession:
    """Playwright-Session für einen einzelnen Schüler auf der Counter-Seite."""

    def __init__(
        self,
        context,
        page: Page,
        domain: str,
        student_id: int,
        student_name: str,
        relogin=None,
    ) -> None:
        self._context = context
        self._page = page
        self._domain = domain
        self._student_id = student_id
        self._student_name = student_name
        self._card_loaded = False
        # async callable(page, label) — loggt den Context auf derselben Page neu ein.
        self._relogin = relogin

    async def _on_login_page(self) -> bool:
        """True, wenn die Session abgelaufen ist (IServ-Login statt App)."""
        url = self._page.url
        if "iserv/login" in url or "iserv/auth" in url:
            return True
        try:
            return await self._page.locator('input[name="_username"]').count() > 0
        except Exception:
            return False

    async def _goto_authed(self, url: str, wait_ms: int) -> None:
        """Navigieren mit Re-Login-Recovery: erkennt Login-Redirect und meldet
        den Context (auf derselben Page = gleicher Cookie-Jar) neu an, dann
        erneut zur Ziel-URL. Read-only — nur Login-Submit, keine Buchung."""
        page = self._page
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(wait_ms)
        if await self._on_login_page():
            if not self._relogin:
                raise RuntimeError(f"Session abgelaufen bei {url}, kein Re-Login verfügbar")
            log.warning("Session abgelaufen bei %s — Re-Login", url)
            await self._relogin(page, f"relogin-{self._student_id}")
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_timeout(wait_ms)

    async def load_card(self) -> None:
        """App-Root laden, Counter öffnen, Schüler per Typeahead suchen (read-only)."""
        page = self._page
        domain = self._domain
        self._card_loaded = False

        # App-Root laden, Angular initialisiert dabei die Session (Spike A/B-Muster).
        # _goto_authed fängt einen Login-Redirect ab (abgelaufene Session).
        await self._goto_authed(f"https://ausleihe.{domain}/", 4000)

        # Kartei direkt über die Schüler-ID-Route öffnen (kein Nachnamen-Typeahead
        # mehr). Eindeutig pro Schüler, unabhängig von Namensgleichheit/Tippfehlern.
        await self._goto_authed(
            f"https://ausleihe.{domain}/#/counter/student/{self._student_id}", 3000
        )

        # Warten bis Barcode-Feld mit neuem Placeholder erscheint (= Kartei geladen)
        try:
            await page.locator('input.tt-input[placeholder*="Buch scannen"]').wait_for(
                state="visible", timeout=20_000
            )
        except PlaywrightTimeout:
            log.warning("Schülerkartei für %d nach 20s nicht voll geladen", self._student_id)

        self._card_loaded = True
        log.info("Kartei für Schüler %d geladen", self._student_id)

    async def reload(self) -> None:
        """Kartei auf der bereits initialisierten Page neu laden (read-only GET).

        Einsatzfall: Helfer lädt die Seite neu, während der Worker bereits bereit
        stand — die Kartei wird auf dem bestehenden Context frisch geladen statt
        einen neuen zu öffnen.

        Schneller als :meth:`load_card`: Angular steht schon (die Page wurde zuvor
        per ``load_card`` geöffnet) → der App-Root-Load (~4 s Wartezeit) entfällt.
        Stattdessen wird direkt auf die Schüler-Route gesprungen. Da ein
        **gleicher Hash** in Angular ein No-Op wäre (kein Re-Fetch → veraltete
        Buchdaten), wird vorher kurz auf ``#/counter`` (ohne Schüler) gehoppt
        und danach zurück auf die Schüler-Route — beides In-App-Hashrouten, die
        einen echten Re-Render erzwingen. ``_goto_authed`` (inkl. Re-Login-
        Recovery) bleibt erhalten; bewusst KEIN ``page.reload()`` (könnte ein
        vorheriges POST-Result re-posten, s. load_card).

        Fallback: erscheint das Barcode-Feld danach nicht (z. B. Angular doch
        nicht initialisiert — Tab-Wiederherstellung), läuft einmal das
        vollständige ``load_card()`` (Root + Schüler-Route, wie beim Öffnen).
        """
        page = self._page
        domain = self._domain
        self._card_loaded = False
        try:
            # Kurzer Hop auf #/counter erzwingt einen echten Re-Render der
            # Schülerkartei (gleicher Hash allein wäre ein Angular-No-Op).
            await self._goto_authed(f"https://ausleihe.{domain}/#/counter", 1500)
            await self._goto_authed(
                f"https://ausleihe.{domain}/#/counter/student/{self._student_id}", 2000
            )
            try:
                await page.locator('input.tt-input[placeholder*="Buch scannen"]').wait_for(
                    state="visible", timeout=8_000
                )
            except PlaywrightTimeout:
                log.warning(
                    "Barcode-Feld bei direktem Reload für %d nicht erschienen — full load_card()",
                    self._student_id,
                )
                await self.load_card()
                return
            self._card_loaded = True
            log.info("Kartei für Schüler %d reloaded (direkte Schüler-Route)", self._student_id)
        except Exception as e:  # noqa: BLE001 — direkter Reload gescheitert (z. B. Login ohne relogin) → sicherer Fallback
            log.warning(
                "Direkter Reload für %d fehlgeschlagen (%s) — full load_card()", self._student_id, e
            )
            await self.load_card()

    # Selektor des Counter-Eingabefeldes nach Schülerauswahl (Spike A).
    _BARCODE_SEL = 'input.tt-input[placeholder*="Buch scannen"]'

    async def _ensure_barcode_field(self):
        """Barcode-Feld sichtbar machen, mit einmaliger Kartei-Recovery.

        Gibt den Locator zurück oder wirft RuntimeError mit Klartext-Grund.
        Gemeinsam genutzt von submit_barcode() (staged) und commit_barcode() (Buchung).
        """
        if not self._card_loaded:
            raise RuntimeError("Schülerkartei noch nicht geladen")
        field = self._page.locator(self._BARCODE_SEL)
        try:
            await field.wait_for(state="visible", timeout=5_000)
            return field
        except PlaywrightTimeout:
            pass
        # Feld weg — evtl. Session mitten im Vorgang abgelaufen: einmal erholen.
        if await self._on_login_page() or self._relogin is not None:
            log.warning("Barcode-Feld weg (Schüler %d) — Kartei neu laden", self._student_id)
            await self.load_card()  # macht intern Re-Login bei Bedarf
            field = self._page.locator(self._BARCODE_SEL)
            await field.wait_for(state="visible", timeout=5_000)
            return field
        raise RuntimeError("Barcode-Feld nicht erreichbar")

    async def submit_barcode(self, barcode: str) -> dict:
        """Barcode ins Eingabefeld einfüllen — KEIN ENTER, keine Buchung.

        fill() überschreibt das Feld ohne Submit auszulösen. Enter würde
        c.evaluateInput() feuern und eine Buchung erzeugen (PLAN §6).
        """
        try:
            field = await self._ensure_barcode_field()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "msg": str(e)}
        try:
            await field.fill(barcode)
            log.info("Barcode '%s' ins Feld gefüllt (kein Submit)", barcode)
        except PlaywrightTimeout:
            return {"status": "error", "msg": "Barcode-Feld nicht erreichbar"}
        return {
            "status": "staged",
            "msg": "Barcode im Feld — Submit zurückgestellt bis Freigabe (PLAN §6)",
        }

    async def commit_barcode(self, barcode: str) -> dict:
        """!!! BUCHT GEGEN DIE IServ-PRODUKTION !!!  (Enter = c.evaluateInput()).

        NUR im freigegebenen Buchungstest aufrufen (Freigabe Niklas + Lukas,
        ALLOW_BOOKING=true). Diese Methode ist der reine Mechanismus und prüft das
        Gate NICHT selbst — die Absicherung liegt serverseitig (handle_commit +
        /api/commit-book, dreifach gated). Im Normalbetrieb nie erreichbar.

        TODO: Die Erfolgs-/Fehler-Selektoren in _read_booking_result() sind nach
        Spike-A-Doku best-effort und bis zum freigegebenen Test UNVERIFIZIERT.
        """
        try:
            field = await self._ensure_barcode_field()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "msg": str(e)}
        try:
            await field.fill(barcode)
            await field.press("Enter")  # löst die Buchung aus (ng-submit)
            log.warning(
                "Barcode '%s' GEBUCHT (Enter gedrückt) für Schüler %d", barcode, self._student_id
            )
        except PlaywrightTimeout:
            return {"status": "error", "msg": "Buchung: Barcode-Feld nicht erreichbar"}
        await self._page.wait_for_timeout(1500)
        return await self._read_booking_result(barcode)

    async def _read_booking_result(self, barcode: str) -> dict:
        """Best-effort Erfolg/Fehler aus dem DOM lesen (Spike A). UNVERIFIZIERT.

        Selektoren sind bis zum freigegebenen Buchungstest nicht final bestätigt;
        bei Unsicherheit `unknown` zurückgeben statt Erfolg vorzutäuschen. Beide
        bekannten Schwächen der Erfolgs-Erkennung (Substring-Vergleich gegen den
        ganzen Zeilentext statt gegen die Code-Spalte; festes 1500-ms-Fenster vor
        dem Auslesen) zeigen in Richtung `unknown`, nie in Richtung `booked` —
        und `/api/commit-book` wertet ausschließlich `booked` als Erfolg. Eine
        verpasste Erkennung heißt darum „Host prüft von Hand", nicht „stille
        Falschbuchung". Details: `docs/test_status.md`.
        """
        page = self._page
        # 1) Sichtbarer Fehlerhinweis? (Bootstrap-typische rote Meldungen)
        for sel in (".alert-danger", ".text-danger", ".help-block"):
            loc = page.locator(sel)
            try:
                if await loc.count() and await loc.first.is_visible():
                    msg = (await loc.first.inner_text()).strip()
                    if msg:
                        return {"status": "error", "msg": msg[:200], "raw": sel}
            except Exception:  # noqa: BLE001
                continue
        # 2) Buchcode in der Bücherliste aufgetaucht? (Indikator Erfolg)
        # Der Zeilentext kommt aus `inner_text()`; der Wert eines <input> ist kein
        # Textknoten und taucht darin nie auf. Das Typeahead-Feld kann die
        # Erkennung also nicht verfälschen — anders als bei einem früheren
        # `get_by_text(barcode)` über die ganze Seite, für das `input_scope`
        # ursprünglich gebaut wurde. `has_not` bleibt als Schutz gegen
        # Selektor-Drift: fiele ein Selektor künftig auf einen Wrapper, der das
        # Feld oder ein Typeahead-Dropdown (Textknoten!) umschließt, würde der
        # Barcode sonst wieder mitgelesen.
        try:
            # Eingabefeld + Typeahead-Suggestions, die den Barcode abbilden:
            input_scope = page.locator("input.tt-input, .tt-dropdown-menu, .tt-hint")
            # Bücherliste der Schülerkartei. Beide Selektoren treffen dieselben
            # `<tr ng-repeat="book in bl.books">`-Zeilen (das ng-repeat sitzt auf
            # dem <tr>) und sind gegen einen DOM-Dump der geladenen Kartei
            # verifiziert. Hier gehören nur belegte Selektoren rein: ein geratener,
            # zu weiter Selektor würde die Erfolgs-Erkennung auf Container
            # ausdehnen, die den Barcode aus anderer Quelle enthalten können.
            list_selectors = [
                "table tbody tr",
                '[ng-repeat*="book"]',
            ]
            for sel in list_selectors:
                rows = page.locator(sel).filter(has_not=input_scope)
                try:
                    count = await rows.count()
                except Exception:  # noqa: BLE001
                    continue
                for i in range(count):
                    try:
                        row_text = (await rows.nth(i).inner_text()).strip()
                    except Exception:  # noqa: BLE001
                        continue
                    if barcode in row_text:
                        return {
                            "status": "booked",
                            "msg": "Buchung im DOM bestätigt (best-effort)",
                            "raw": barcode,
                        }
        except Exception:  # noqa: BLE001
            pass
        return {
            "status": "unknown",
            "msg": "Buchung ausgelöst, Ergebnis nicht eindeutig erkennbar "
            "(Selektoren unverifiziert — freigegebener Test nötig)",
        }

    async def close(self) -> None:
        try:
            await self._page.close()
        except Exception:
            pass


class WorkerPool:
    """Pool von N Browser-Contexts — je eigener Login, je eigener Cookie-Jar."""

    def __init__(
        self,
        n: int,
        domain: str,
        username: str,
        password: str,
        headless: bool = True,
        slow_mo_ms: int = 0,
    ) -> None:
        self._n = n
        self._domain = domain
        self._username = username
        self._password = password
        self._headless = headless
        self._slow_mo_ms = slow_mo_ms
        self._pw = None  # Playwright-Instanz
        self._browser = None
        self._contexts: list = []
        self._total = 0  # erfolgreich eingeloggte Contexts (für stats())
        # Condition (wrapt ein Lock) — schützt `_contexts` und `_waiters`.
        # `open_student` kann damit auf einen freigewordenen Context warten,
        # wenn eine release()-Task noch läuft. Verhindert das „Kein freier
        # Worker-Context"-Rennen, wenn am Helfer schnell hintereinander Schüler
        # abgeschlossen werden (PLAN-Stabilität).
        self._cond = asyncio.Condition()
        # Warteschlange der Bewerber um einen freien Context, nach Rangfolge
        # (`_WORKER_RANK`) und innerhalb einer Rolle FIFO (`seq`). Ein
        # freiwerdender Context wird dem ranghöchsten Wartenden DIREKT
        # zugeteilt (s. `_put_context`) — kein „wer den Lock zuerst erwischt".
        self._waiters: list[_Waiter] = []
        self._waiter_seq = 0

    # ---- Context-Vergabe (immer unter `self._cond` aufrufen) -------------

    def _put_context(self, context) -> None:
        """Einen freien Context vergeben: an den ranghöchsten Wartenden, sonst
        zurück in den Pool. Bereits abgebrochene Wartende werden übersprungen."""
        while self._waiters:
            waiter = min(self._waiters, key=lambda w: (w.rank, w.seq))
            self._waiters.remove(waiter)
            if waiter.future.done():
                continue  # Timeout/Cancel kam zuvor — nächster Wartender
            waiter.future.set_result(context)
            return
        self._contexts.append(context)
        self._cond.notify_all()

    def _add_waiter(self, priority: str) -> _Waiter:
        """Aufrufer in die Warteschlange einreihen und seinen Platzhalter geben."""
        self._waiter_seq += 1
        waiter = _Waiter(
            rank=_WORKER_RANK.get(priority, _WORKER_RANK["student"]),
            seq=self._waiter_seq,
            priority=priority,
            future=asyncio.get_running_loop().create_future(),
        )
        self._waiters.append(waiter)
        return waiter

    def _drop_waiter(self, waiter: _Waiter) -> None:
        """Wartenden nach Timeout/Cancel entfernen. Wurde ihm im selben Tick
        schon ein Context zugeteilt, wandert dieser zum nächsten Wartenden —
        sonst würde er aus dem Pool verschwinden."""
        if waiter in self._waiters:
            self._waiters.remove(waiter)
        if waiter.future.done() and not waiter.future.cancelled():
            exc = waiter.future.exception()
            if exc is None:
                self._put_context(waiter.future.result())

    def waiting_counts(self) -> dict[str, int]:
        """Anzahl Wartender je Rolle — für den Host-Statusbalken."""
        counts = dict.fromkeys(_WORKER_RANK, 0)
        for w in self._waiters:
            counts[w.priority] = counts.get(w.priority, 0) + 1
        return counts

    def waiting_position(self, waiter: _Waiter) -> int:
        """1-basierte Position eines Wartenden in der Rangfolge."""
        ordered = sorted(self._waiters, key=lambda w: (w.rank, w.seq))
        return ordered.index(waiter) + 1 if waiter in ordered else 1

    async def _make_logged_in_context(self, label: str):
        """Einen frischen Context anlegen und einloggen. Bei Fehler Context
        wieder schließen (kein Leak) und Exception weiterreichen."""
        context = await self._browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        try:
            await self._login(page, label)
            await page.close()
            return context
        except Exception:
            try:
                await context.close()
            except Exception:
                pass
            raise

    async def start(self) -> None:
        """Playwright starten, N Contexts öffnen, alle einloggen. Fehlgeschlagene
        Logins werden einmal nachgezogen, damit der Pool möglichst die Zielgröße
        erreicht."""
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._headless, slow_mo=self._slow_mo_ms
        )
        if not self._headless:
            log.info("Playwright headful (sichtbar) — slow_mo=%d ms", self._slow_mo_ms)

        t0 = time.monotonic()
        # Wird start() während des Logins gecancel't (z. B. App-Shutdown), würde
        # asyncio.gather CancelledError werfen und der Append-Loop nie laufen →
        # die bereits erfolgreich eingeloggten Contexts wären georphan't (leak).
        # Daher: im finally alle erfolgreich erstellten Contexts entweder in den
        # Pool übernehmen oder — wenn wir abgebrochen wurden — sauber schließen.
        cancelled = False
        try:
            results = await asyncio.gather(
                *[self._make_logged_in_context(f"worker_{i}") for i in range(self._n)],
                return_exceptions=True,
            )
        except BaseException:  # noqa: BLE001 — CancelledError oder anderes
            cancelled = True
            results = []
            log.warning("start() während erstem Login abgebrochen — Contexts bereinigen")
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log.warning("Worker %d Login fehlgeschlagen: %s", i, r)
            else:
                self._contexts.append(r)

        # Einmal nachziehen, was beim ersten Versuch gescheitert ist.
        missing = self._n - len(self._contexts)
        if missing > 0 and not cancelled:
            log.info("%d Worker-Context(e) fehlen — Retry", missing)
            try:
                retry = await asyncio.gather(
                    *[self._make_logged_in_context(f"worker_retry_{i}") for i in range(missing)],
                    return_exceptions=True,
                )
            except BaseException:  # noqa: BLE001
                cancelled = True
                retry = []
                log.warning("start() während Retry-Login abgebrochen — Contexts bereinigen")
            for r in retry:
                if isinstance(r, Exception):
                    log.warning("Worker-Retry Login fehlgeschlagen: %s", r)
                else:
                    self._contexts.append(r)

        if cancelled:
            # Abbruch: aufgebaute Contexts nicht im Pool behalten (Pool wird
            # ohnehin nicht genutzt), sondern deterministisch schließen.
            log.info(
                "start() abgebrochen — schließe %d bereits erstellte Contexts", len(self._contexts)
            )
            for ctx in self._contexts:
                try:
                    await ctx.close()
                except Exception:
                    pass
            self._contexts.clear()

        elapsed = (time.monotonic() - t0) * 1000
        self._total = len(self._contexts)
        log.info("%d/%d Worker-Contexts eingeloggt (%.0f ms)", self._total, self._n, elapsed)
        if not self._contexts:
            log.error("Kein einziger Worker-Context eingeloggt — Scannen wird scheitern")

    def stats(self) -> dict:
        """Pool-Auslastung für den Host: total / frei / in Benutzung.

        Hinweis: stats() bleibt synchron, weil alle Aufrufer (state_snapshot,
        Tests) es synchron aufrufen. Der Zugriff auf len(self._contexts) ist in
        CPython unter dem GIL atomar; wirkliche Konsistenz mit den Mutationen
        (open_student/release/check_selectors) ergibt sich daraus, dass alles
        im selben Event-Loop-Thread läuft — stats() hat keine await-Punkte und
        wird daher nicht mitten in einer Mutation unterbrochen."""
        available = len(self._contexts)
        return {
            "total": self._total,
            "available": available,
            "in_use": max(0, self._total - available),
            # Wartende je Rolle (helper/station/student) — der Host-
            # Statusbalken zeigt daraus den Stau vor dem Pool.
            "waiting": self.waiting_counts(),
        }

    async def check_selectors(self) -> dict:
        """Read-only Drift-Check beim Start: Counter-Seite laden und prüfen, ob die
        erwarteten Selektoren noch existieren. Warnt früh, falls IServ sein Frontend
        geändert hat (der Write-Pfad hängt an diesen Selektoren).

        Reines Browsing — kein Schüler, kein Submit (CLAUDE.md erlaubt Lesen)."""
        # Context unter Lock leihen (gegen Race mit open_student) und danach zurück.
        async with self._cond:
            if not self._contexts:
                return {"ok": False, "msg": "kein Worker-Context"}
            context = self._contexts.pop(0)
        # new_page() kann bei transienten Playwright-Transportfehlern werfen.
        # Stand das außerhalb des protected try/finally, würde der Context leaken
        # (Pool schrumpft dauerhaft). Daher: bei Fehlern Context zurückgeben.
        try:
            page = await context.new_page()
        except BaseException:  # noqa: BLE001 — inkl. CancelledError
            async with self._cond:
                self._put_context(context)
            raise
        sel = 'input.tt-input[name="input"]'
        try:
            await page.goto(f"https://ausleihe.{self._domain}/", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            await page.goto(
                f"https://ausleihe.{self._domain}/#/counter", wait_until="domcontentloaded"
            )
            await page.wait_for_timeout(2000)
            try:
                await page.locator(sel).wait_for(state="visible", timeout=10_000)
                log.info("Selektor-Canary OK: '%s' vorhanden", sel)
                return {"ok": True, "selector": sel}
            except PlaywrightTimeout:
                log.warning(
                    "Selektor-Canary FEHLGESCHLAGEN: '%s' nicht gefunden — "
                    "IServ-DOM evtl. geändert, Write-Pfad prüfen!",
                    sel,
                )
                return {"ok": False, "selector": sel, "msg": "Selektor nicht gefunden"}
        finally:
            try:
                await page.close()
            except Exception:
                pass
            async with self._cond:
                self._put_context(context)

    async def stop(self) -> None:
        # Defensiv: bei Ctrl+C / Treiber-Abbruch ist der Transport evtl. schon
        # tot — close()/stop() sollen die App-Shutdown-Sequenz nicht crashen.
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                log.warning("Browser.close beim Shutdown fehlgeschlagen: %s", e)
        if self._pw:
            try:
                await self._pw.stop()
            except Exception as e:
                log.warning("Playwright.stop beim Shutdown fehlgeschlagen: %s", e)

    async def open_student(
        self,
        student_id: int,
        student_name: str,
        *,
        priority: str = "student",
        wait_timeout: float | None = None,
        on_wait=None,
    ) -> StudentSession:
        """Einen freien Context holen und Schülerkartei laden.

        Ist der Pool gerade leer (z. B. weil eine release()-Task nach einem
        schnellen „Weiter/ Abschließen" noch läuft), reiht sich der Aufruf in
        die Pool-Warteschlange ein, statt sofort „Kein freier Worker-Context
        verfügbar" zu werfen. Erst nach Ablauf der Frist gilt der Mangel als
        echt und wird als Fehler gemeldet.

        `priority` bestimmt die Rangfolge unter mehreren Wartenden
        (`_WORKER_RANK`: helper → station → student); innerhalb einer Rolle
        wird FIFO bedient. `wait_timeout=None` nimmt die Rollen-Vorgabe aus
        `_WORKER_WAIT_S`. `on_wait(position)` wird — sofern übergeben — einmal
        aufgerufen, wenn tatsächlich gewartet werden muss, damit der Aufrufer
        seinem Client „Warten auf freien Platz (Position n)" melden kann.
        """
        timeout = _WORKER_WAIT_S.get(priority, 60.0) if wait_timeout is None else wait_timeout
        async with self._cond:
            # Nur direkt bedienen, wenn niemand wartet — sonst würde ein
            # frisch eintreffender Aufruf die Warteschlange überholen.
            if self._contexts and not self._waiters:
                context = self._contexts.pop(0)
                waiter = None
            else:
                waiter = self._add_waiter(priority)
                position = self.waiting_position(waiter)
                context = None
        if waiter is not None:
            log.info(
                "Worker-Pool belegt für Schüler %d (%s, Position %d) — warte bis %.1fs",
                student_id, priority, position, timeout,
            )
            if on_wait is not None:
                try:
                    on_wait(position)
                except Exception:  # noqa: BLE001 — Callback darf den Pool nie killen
                    log.exception("on_wait-Callback fehlgeschlagen")
            try:
                context = await asyncio.wait_for(waiter.future, timeout=timeout)
            except TimeoutError:
                async with self._cond:
                    self._drop_waiter(waiter)
                raise RuntimeError("Kein freier Worker-Context verfügbar") from None
            except BaseException:  # noqa: BLE001 — inkl. CancelledError
                async with self._cond:
                    self._drop_waiter(waiter)
                raise

        # new_page() kann werfen (auch CancelledError, daher BaseException) —
        # Context in jedem Fall zurückgeben, sonst leakt er aus dem Pool.
        # Abgesichert: tests/test_worker_pool.py::test_open_student_cancel_returns_context
        try:
            page = await context.new_page()
        except BaseException:  # noqa: BLE001 — inkl. CancelledError!
            async with self._cond:
                self._put_context(context)
            raise
        session = StudentSession(
            context, page, self._domain, student_id, student_name, relogin=self._login
        )
        try:
            await session.load_card()
        except BaseException as e:  # noqa: BLE001 — inkl. CancelledError!
            # `except Exception` fängt CancelledError NICHT (seit Py3.8 eine
            # BaseException) — hier bewusst BaseException, damit ein Cancel
            # während load_card den Context trotzdem zurück in den Pool gibt.
            log.warning("load_card für %d fehlgeschlagen/abgebrochen: %s", student_id, e)
            try:
                await page.close()
            except Exception:
                pass
            async with self._cond:
                self._put_context(context)
            raise
        return session

    async def release(self, session: StudentSession) -> None:
        """Context nach Abschluss eines Schülers zurück in den Pool.

        Idempotent (Context wird atomar aus der Session entfernt und nur dann
        appended, wenn er noch da war).
        Abgesichert: tests/test_worker_pool.py::test_release_is_idempotent_no_double_append"""
        await session.close()
        ctx = session._context
        session._context = None
        if ctx is None:
            return  # bereits released — nichts tun (Double-Release-Schutz)
        async with self._cond:
            self._put_context(ctx)

    async def _login(self, page: Page, label: str) -> None:
        domain = self._domain
        await page.goto(f"https://{domain}/iserv/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        await page.fill('input[name="_username"]', self._username)
        await page.fill('input[name="_password"]', self._password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2500)
        # Noch auf einer Login-/Auth-Seite → Login fehlgeschlagen. Konsistent zu
        # _on_login_page() (OR statt AND): ein Fehl-Login landet u. U. nur auf
        # iserv/login ODER iserv/auth, nicht zwingend auf beidem.
        if "iserv/login" in page.url or "iserv/auth" in page.url:
            raise RuntimeError(f"[{label}] Login fehlgeschlagen (noch auf {page.url})")
        log.debug("[%s] Login OK", label)
