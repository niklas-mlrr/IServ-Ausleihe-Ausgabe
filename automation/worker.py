"""Playwright-Worker: Context-Pool für die IServ-Ausleihe-Counter-Seite.

Ein WorkerPool hält N vorinitialisierte Browser-Contexts (je eigener Cookie-Jar,
je eigener Login). Pro zugewiesenem Schüler wird eine StudentSession geöffnet:
Login → App-Root → #/counter → Schüler per Typeahead öffnen → Barcode-Feld fokussieren.

SICHERHEITSREGELN (CLAUDE.md / PLAN §6):
  - Lukas' Admin-Account ist AUSSCHLIESSLICH lesend zu verwenden.
  - submit_barcode() füllt das Feld mit fill(), drückt NIEMALS Enter.
  - Kein Submit, keine Buchung, bis Niklas + Lukas explizit freigegeben haben.
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

    def __init__(self, context, page: Page, domain: str, student_id: int, student_name: str) -> None:
        self._context = context
        self._page = page
        self._domain = domain
        self._student_id = student_id
        self._student_name = student_name
        self._card_loaded = False

    async def load_card(self) -> None:
        """App-Root laden, Counter öffnen, Schüler per Typeahead suchen (read-only)."""
        page = self._page
        domain = self._domain

        # App-Root laden, Angular initialisiert dabei die Session (Spike A/B-Muster).
        await page.goto(f"https://ausleihe.{domain}/", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        await page.goto(f"https://ausleihe.{domain}/#/counter", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

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
            log.warning("Kein Typeahead-Vorschlag für '%s'", search_name)
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

    async def submit_barcode(self, barcode: str) -> dict:
        """Barcode ins Eingabefeld einfüllen — KEIN ENTER, keine Buchung.

        fill() überschreibt das Feld ohne Submit auszulösen. Enter würde
        c.evaluateInput() feuern und eine Buchung erzeugen (PLAN §6).
        """
        if not self._card_loaded:
            return {
                "status": "error",
                "msg": "Schülerkartei noch nicht geladen",
            }

        input_field = self._page.locator('input.tt-input[placeholder*="Buch scannen"]')
        try:
            await input_field.wait_for(state="visible", timeout=5_000)
            await input_field.fill(barcode)
            log.info("Barcode '%s' ins Feld gefüllt (kein Submit)", barcode)
        except PlaywrightTimeout:
            return {"status": "error", "msg": "Barcode-Feld nicht erreichbar"}

        return {
            "status": "staged",
            "msg": "Barcode im Feld — Submit zurückgestellt bis Freigabe (PLAN §6)",
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
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Playwright starten, N Contexts öffnen, alle einloggen."""
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)

        t0 = time.monotonic()
        contexts = [
            await self._browser.new_context(ignore_https_errors=True)
            for _ in range(self._n)
        ]
        pages = [await ctx.new_page() for ctx in contexts]

        login_results = await asyncio.gather(
            *[self._login(pages[i], f"worker_{i}") for i in range(self._n)],
            return_exceptions=True,
        )
        for i, r in enumerate(login_results):
            if isinstance(r, Exception):
                log.warning("Worker %d Login fehlgeschlagen: %s", i, r)
            else:
                self._contexts.append(contexts[i])
                await pages[i].close()

        elapsed = (time.monotonic() - t0) * 1000
        log.info("%d/%d Worker-Contexts eingeloggt (%.0f ms)", len(self._contexts), self._n, elapsed)

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
        session = StudentSession(context, page, self._domain, student_id, student_name)
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
