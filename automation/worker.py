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
from pathlib import Path

from playwright.async_api import Page, TimeoutError as PlaywrightTimeout, async_playwright

log = logging.getLogger(__name__)


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
        await self._goto_authed(f"https://ausleihe.{domain}/#/counter", 2000)

        search = page.locator('input.tt-input[name="input"]')
        try:
            await search.wait_for(state="visible", timeout=15_000)
        except PlaywrightTimeout:
            raise RuntimeError("Counter-Eingabefeld nicht erschienen — Login oder Routing fehlgeschlagen")

        # Nachname für Typeahead (alles vor dem ersten Komma)
        search_name = self._student_name.split(",")[0].strip()
        await search.press_sequentially(search_name, delay=50)
        await page.wait_for_timeout(2000)

        suggestion = page.locator(".tt-suggestion").first
        if not await suggestion.count():
            # Kein Schülername ins Log (PLAN §3.7) — nur die ID.
            log.warning("Kein Typeahead-Vorschlag für Schüler %d", self._student_id)
            # Kartei über direkte URL als Fallback laden
            await page.goto(
                f"https://ausleihe.{domain}/#/counter/student/{self._student_id}",
                wait_until="domcontentloaded",
            )
            await page.wait_for_timeout(3000)
        else:
            await suggestion.click()

        # Warten bis Barcode-Feld mit neuem Placeholder erscheint (= Kartei geladen)
        try:
            await page.locator('input.tt-input[placeholder*="Buch scannen"]').wait_for(
                state="visible", timeout=20_000
            )
        except PlaywrightTimeout:
            log.warning("Schülerkartei für %d nach 20s nicht voll geladen", self._student_id)

        self._card_loaded = True
        log.info("Kartei für Schüler %d geladen", self._student_id)

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
            log.warning("Barcode '%s' GEBUCHT (Enter gedrückt) für Schüler %d",
                        barcode, self._student_id)
        except PlaywrightTimeout:
            return {"status": "error", "msg": "Buchung: Barcode-Feld nicht erreichbar"}
        await self._page.wait_for_timeout(1500)
        return await self._read_booking_result(barcode)

    async def _read_booking_result(self, barcode: str) -> dict:
        """Best-effort Erfolg/Fehler aus dem DOM lesen (Spike A). UNVERIFIZIERT.

        Selektoren sind bis zum freigegebenen Buchungstest nicht final bestätigt;
        bei Unsicherheit `unknown` zurückgeben statt Erfolg vorzutäuschen.
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
        # 2) Buchcode in der Kartei-/Bücherliste aufgetaucht? (Indikator Erfolg)
        try:
            if await page.get_by_text(barcode, exact=False).count():
                return {"status": "booked",
                        "msg": "Buchung im DOM bestätigt (best-effort)", "raw": barcode}
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

    def __init__(self, n: int, domain: str, username: str, password: str) -> None:
        self._n = n
        self._domain = domain
        self._username = username
        self._password = password
        self._pw = None       # Playwright-Instanz
        self._browser = None
        self._contexts: list = []
        self._total = 0       # erfolgreich eingeloggte Contexts (für stats())
        self._lock = asyncio.Lock()

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
        self._browser = await self._pw.chromium.launch(headless=True)

        t0 = time.monotonic()
        results = await asyncio.gather(
            *[self._make_logged_in_context(f"worker_{i}") for i in range(self._n)],
            return_exceptions=True,
        )
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                log.warning("Worker %d Login fehlgeschlagen: %s", i, r)
            else:
                self._contexts.append(r)

        # Einmal nachziehen, was beim ersten Versuch gescheitert ist.
        missing = self._n - len(self._contexts)
        if missing > 0:
            log.info("%d Worker-Context(e) fehlen — Retry", missing)
            retry = await asyncio.gather(
                *[self._make_logged_in_context(f"worker_retry_{i}") for i in range(missing)],
                return_exceptions=True,
            )
            for r in retry:
                if isinstance(r, Exception):
                    log.warning("Worker-Retry Login fehlgeschlagen: %s", r)
                else:
                    self._contexts.append(r)

        elapsed = (time.monotonic() - t0) * 1000
        self._total = len(self._contexts)
        log.info("%d/%d Worker-Contexts eingeloggt (%.0f ms)", self._total, self._n, elapsed)
        if not self._contexts:
            log.error("Kein einziger Worker-Context eingeloggt — Scannen wird scheitern")

    def stats(self) -> dict:
        """Pool-Auslastung für den Host: total / frei / in Benutzung."""
        available = len(self._contexts)
        return {
            "total": self._total,
            "available": available,
            "in_use": max(0, self._total - available),
        }

    async def check_selectors(self) -> dict:
        """Read-only Drift-Check beim Start: Counter-Seite laden und prüfen, ob die
        erwarteten Selektoren noch existieren. Warnt früh, falls IServ sein Frontend
        geändert hat (der Write-Pfad hängt an diesen Selektoren).

        Reines Browsing — kein Schüler, kein Submit (CLAUDE.md erlaubt Lesen)."""
        # Context unter Lock leihen (gegen Race mit open_student) und danach zurück.
        async with self._lock:
            if not self._contexts:
                return {"ok": False, "msg": "kein Worker-Context"}
            context = self._contexts.pop(0)
        page = await context.new_page()
        sel = 'input.tt-input[name="input"]'
        try:
            await page.goto(f"https://ausleihe.{self._domain}/", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
            await page.goto(f"https://ausleihe.{self._domain}/#/counter", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
            try:
                await page.locator(sel).wait_for(state="visible", timeout=10_000)
                log.info("Selektor-Canary OK: '%s' vorhanden", sel)
                return {"ok": True, "selector": sel}
            except PlaywrightTimeout:
                log.warning(
                    "Selektor-Canary FEHLGESCHLAGEN: '%s' nicht gefunden — "
                    "IServ-DOM evtl. geändert, Write-Pfad prüfen!", sel
                )
                return {"ok": False, "selector": sel, "msg": "Selektor nicht gefunden"}
        finally:
            try:
                await page.close()
            except Exception:
                pass
            async with self._lock:
                self._contexts.append(context)

    async def stop(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def open_student(self, student_id: int, student_name: str) -> StudentSession:
        """Einen freien Context holen und Schülerkartei laden."""
        async with self._lock:
            if not self._contexts:
                raise RuntimeError("Kein freier Worker-Context verfügbar")
            context = self._contexts.pop(0)

        page = await context.new_page()
        session = StudentSession(
            context, page, self._domain, student_id, student_name, relogin=self._login
        )
        try:
            await session.load_card()
        except Exception as e:
            log.warning("load_card für %d fehlgeschlagen: %s", student_id, e)
            await page.close()
            async with self._lock:
                self._contexts.append(context)
            raise
        return session

    async def release(self, session: StudentSession) -> None:
        """Context nach Abschluss eines Schülers zurück in den Pool."""
        await session.close()
        async with self._lock:
            self._contexts.append(session._context)

    async def _login(self, page: Page, label: str) -> None:
        domain = self._domain
        await page.goto(f"https://{domain}/iserv/login", wait_until="domcontentloaded")
        await page.wait_for_timeout(1000)
        await page.fill('input[name="_username"]', self._username)
        await page.fill('input[name="_password"]', self._password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(2500)
        if "login" in page.url and "auth" in page.url:
            raise RuntimeError(f"[{label}] Login fehlgeschlagen (noch auf {page.url})")
        log.debug("[%s] Login OK", label)
