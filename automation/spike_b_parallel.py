"""Spike B: Parallele Playwright-Contexts mit demselben Account (O2).

Klärt: Erlaubt IServ mehrere gleichzeitige Sessions desselben Accounts?
Werden bestehende Sessions invalidiert wenn ein zweiter Context einloggt?

Szenario 1: N unabhängige Logins (jeder Context hat seinen eigenen Cookie-Jar).
Szenario 2: 1 Login, Storage-State auf N Contexts geteilt (Plan-B-Option aus PLAN §4 O2).

Aufruf: uv run python -m automation.spike_b_parallel --student <Nachname> [--count 3]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

OUT_DIR = Path(__file__).parent / "out" / "spike_b"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Fehler: {name} fehlt — .env prüfen (.env.example als Vorlage).")
    return value


async def snap(page: Page, name: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=True)
    (OUT_DIR / f"{name}.html").write_text(await page.content(), encoding="utf-8")
    print(f"    [snap] {name}  ({page.url})")


async def async_login(page: Page, domain: str, username: str, password: str, label: str) -> float:
    """Login und gibt die Dauer in ms zurück. Schlägt fehl mit Exception."""
    t0 = time.monotonic()
    await page.goto(f"https://{domain}/iserv/login", wait_until="domcontentloaded")
    await page.wait_for_timeout(1000)
    await page.fill('input[name="_username"]', username)
    await page.fill('input[name="_password"]', password)
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2500)
    # OR statt AND: ein Fehl-Login landet u. U. nur auf iserv/login ODER
    # iserv/auth, nicht zwingend auf beidem (konsistent zu worker.py:_login).
    if "iserv/login" in page.url or "iserv/auth" in page.url:
        await snap(page, f"{label}_login_fehlgeschlagen")
        raise RuntimeError(f"[{label}] Login fehlgeschlagen — noch auf {page.url}")
    elapsed_ms = (time.monotonic() - t0) * 1000
    print(f"  [{label}] Login OK  ({elapsed_ms:.0f} ms)")
    return elapsed_ms


async def load_student_card(page: Page, domain: str, student_name: str, label: str) -> bool:
    """Counter-Seite öffnen, Schüler per Typeahead suchen, Kartei laden. Read-only."""
    await page.goto(f"https://ausleihe.{domain}/#/counter", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    search = page.locator('input.tt-input[name="input"]')
    if not await search.count():
        print(f"  [{label}] WARN: Counter-Eingabefeld nicht gefunden")
        await snap(page, f"{label}_counter_no_input")
        return False

    await search.press_sequentially(student_name, delay=50)
    await page.wait_for_timeout(2000)

    suggestion = page.locator(".tt-suggestion").first
    if not await suggestion.count():
        print(f"  [{label}] WARN: Kein Typeahead-Vorschlag")
        await snap(page, f"{label}_no_suggestion")
        return False

    await suggestion.click()
    try:
        await page.locator('input.tt-input[placeholder*="Buch scannen"]').wait_for(
            state="visible", timeout=20_000
        )
    except PlaywrightTimeout:
        print(f"  [{label}] WARN: Schülerkartei nach 20s nicht geladen")
        await snap(page, f"{label}_kartei_timeout")
        return False

    await page.wait_for_timeout(500)
    await snap(page, f"{label}_kartei_geladen")
    print(f"  [{label}] Schülerkartei geladen OK")
    return True


async def check_counter_accessible(page: Page, domain: str, label: str) -> bool:
    """App-Root laden (Angular init), dann #/counter navigieren und auf Eingabefeld warten."""
    # Angular braucht erst die App-Root (gleiche Logik wie Spike A).
    await page.goto(f"https://ausleihe.{domain}/", wait_until="domcontentloaded")
    await page.wait_for_timeout(4000)
    await page.goto(f"https://ausleihe.{domain}/#/counter", wait_until="domcontentloaded")
    await page.wait_for_timeout(2000)
    search = page.locator('input.tt-input[name="input"]')
    try:
        await search.wait_for(state="visible", timeout=15_000)
        ok = True
    except PlaywrightTimeout:
        ok = False
    await snap(page, f"{label}_counter")
    print(f"  [{label}] Counter {'OK' if ok else 'NICHT ERREICHBAR'}")
    return ok


async def scenario_1(domain: str, username: str, password: str, student: str | None, count: int) -> None:
    """Szenario 1: N unabhängige Logins gleichzeitig."""
    print(f"\n=== Szenario 1: {count} unabhängige Logins ===")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        contexts = [await browser.new_context(ignore_https_errors=True) for _ in range(count)]
        pages = [await ctx.new_page() for ctx in contexts]

        # Alle Logins parallel
        t_start = time.monotonic()
        login_tasks = [
            async_login(pages[i], domain, username, password, f"s1_ctx{i}")
            for i in range(count)
        ]
        login_results = await asyncio.gather(*login_tasks, return_exceptions=True)
        t_login = (time.monotonic() - t_start) * 1000

        failed_logins = [r for r in login_results if isinstance(r, Exception)]
        print(f"  Logins: {count - len(failed_logins)}/{count} OK  (parallel, gesamt {t_login:.0f} ms)")
        for e in failed_logins:
            print(f"  FEHLER: {e}")

        # Counter-Seite in allen Contexts prüfen (immer, auch ohne Student-Name)
        t_start = time.monotonic()
        counter_tasks = [
            check_counter_accessible(pages[i], domain, f"s1_ctx{i}")
            for i in range(count)
        ]
        counter_results = await asyncio.gather(*counter_tasks, return_exceptions=True)
        t_counter = (time.monotonic() - t_start) * 1000
        ok_counter = sum(1 for r in counter_results if r is True)
        print(f"  Counter: {ok_counter}/{count} OK  (parallel, gesamt {t_counter:.0f} ms)")

        # Schülerkarteien (nur wenn --student angegeben)
        if student:
            t_start = time.monotonic()
            card_tasks = [
                load_student_card(pages[i], domain, student, f"s1_ctx{i}")
                for i in range(count)
            ]
            card_results = await asyncio.gather(*card_tasks, return_exceptions=True)
            t_cards = (time.monotonic() - t_start) * 1000

            ok = sum(1 for r in card_results if r is True)
            print(f"  Karteien: {ok}/{count} OK  (parallel, gesamt {t_cards:.0f} ms)")
            for r in card_results:
                if isinstance(r, Exception):
                    print(f"  FEHLER: {r}")

        # Post-Check: Contexts noch eingeloggt?
        print("  Post-Check: Sind alle Contexts noch eingeloggt?")
        for i, page in enumerate(pages):
            url = page.url
            still_logged_in = "login" not in url or "auth" not in url
            print(f"    ctx{i}: {url}  -> {'OK' if still_logged_in else 'AUSGELOGGT'}")

        await browser.close()


async def scenario_2(domain: str, username: str, password: str, student: str | None, count: int) -> None:
    """Szenario 2: 1 Login, Storage-State auf N Contexts teilen."""
    print(f"\n=== Szenario 2: Cookie-Sharing auf {count} Contexts ===")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)

        # Erst-Login
        ctx0 = await browser.new_context(ignore_https_errors=True)
        page0 = await ctx0.new_page()
        await async_login(page0, domain, username, password, "s2_ctx0_primary")
        # Ausleihe-App kurz besuchen damit Session-Cookies gesetzt werden
        await page0.goto(f"https://ausleihe.{domain}/#/counter", wait_until="domcontentloaded")
        await page0.wait_for_timeout(3000)
        storage_state = await ctx0.storage_state()
        print(f"  Storage-State: {len(storage_state.get('cookies', []))} Cookies")

        # N Contexts mit demselben Storage-State (kein eigener Login)
        contexts = [
            await browser.new_context(ignore_https_errors=True, storage_state=storage_state)
            for _ in range(count)
        ]
        pages = [await ctx.new_page() for ctx in contexts]

        # Counter-Seite in allen Contexts prüfen
        t_start = time.monotonic()
        counter_tasks = [
            check_counter_accessible(pages[i], domain, f"s2_ctx{i}")
            for i in range(count)
        ]
        counter_results = await asyncio.gather(*counter_tasks, return_exceptions=True)
        t_counter = (time.monotonic() - t_start) * 1000
        ok_counter = sum(1 for r in counter_results if r is True)
        print(f"  Counter: {ok_counter}/{count} OK  (parallel, gesamt {t_counter:.0f} ms)")

        # Schülerkarteien (nur wenn --student angegeben)
        if student:
            t_start = time.monotonic()
            card_tasks = [
                load_student_card(pages[i], domain, student, f"s2_ctx{i}")
                for i in range(count)
            ]
            card_results = await asyncio.gather(*card_tasks, return_exceptions=True)
            t_cards = (time.monotonic() - t_start) * 1000

            ok = sum(1 for r in card_results if r is True)
            print(f"  Karteien: {ok}/{count} OK  (parallel, gesamt {t_cards:.0f} ms)")
            for r in card_results:
                if isinstance(r, Exception):
                    print(f"  FEHLER: {r}")

        await browser.close()


async def main_async(args: argparse.Namespace) -> None:
    domain = env("ISERV_DOMAIN")
    username = env("ISERV_USERNAME")
    password = env("ISERV_PASSWORD")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Spike B — parallele Contexts ({stamp}) — read-only, keine Buchung.")
    print(f"Szenarien: {args.scenarios}  count={args.count}  student='{args.student}'")

    scenarios = [s.strip() for s in args.scenarios.split(",")]

    if "1" in scenarios:
        await scenario_1(domain, username, password, args.student, args.count)
    if "2" in scenarios:
        await scenario_2(domain, username, password, args.student, args.count)

    print(f"\nErgebnisse (Screenshots/HTML) in {OUT_DIR}/")
    print("Befunde in docs/spikes/spike_b_protokoll.md eintragen.")


def main() -> None:
    load_dotenv(Path(__file__).parent.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--student", default=None, help="Nachname für Typeahead-Suche (optional; ohne: nur Login + Counter-Check)")
    parser.add_argument("--count", type=int, default=3, help="Anzahl paralleler Contexts (default: 3)")
    parser.add_argument("--scenarios", default="1,2", help="Komma-getrennt: 1, 2 oder 1,2 (default: 1,2)")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
