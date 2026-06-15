"""E2E-Paralleltest (Modus A): zwei Helfer, zwei Schüler GLEICHZEITIG aktiv.

Verifiziert den Context-Pool unter echter Last (WORKER_CONTEXTS >= 2): beide
Schülerkarten laden parallel, beide Scanner stagen unabhängig einen Barcode.

Voraussetzung: Server läuft (`uv run python -m server.main`) mit
WORKER_CONTEXTS >= 2. Read-only gegenüber IServ (kein Submit/Enter).

Aufruf: `uv run python -m automation.e2e_parallel`
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
PORT = os.environ.get("PORT", "3443")
BASE = f"https://localhost:{PORT}"
PW = os.environ["HOST_PASSWORD"]


def log(*a):
    print(*a, flush=True)


async def add_helper(page, name: str) -> str:
    """Helfer über die UI anlegen, Token aus der QR-Modal-URL zurückgeben."""
    await page.fill("#helper-name", name)
    await page.click("text=+ Helfer")
    await page.wait_for_selector("#qr-modal.show", timeout=10_000)
    url = await page.inner_text("#qr-url")
    await page.click("#qr-modal button")
    await page.wait_for_selector("#qr-modal.show", state="hidden", timeout=5_000)
    assert "/scan.html?token=" in url, f"Scan-URL falsch: {url}"
    return url.split("token=")[1]


async def open_scanner(browser, token: str):
    ctx = await browser.new_context(ignore_https_errors=True)
    page = await ctx.new_page()
    await page.goto(f"{BASE}/scan.html?token={token}", wait_until="domcontentloaded")
    await page.wait_for_function("document.getElementById('dot').className.includes('ok')", timeout=10_000)
    return page


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()

        # Login + Klasse + Queue
        await page.goto(f"{BASE}/host.html", wait_until="domcontentloaded")
        await page.fill("#pw-input", PW)
        await page.click("#login-btn")
        await page.wait_for_selector("#main-view", state="visible", timeout=10_000)
        await page.wait_for_function("document.querySelectorAll('#class-select option').length > 1", timeout=15_000)
        opts = await page.eval_on_selector_all("#class-select option", "els => els.map(e => e.value).filter(Boolean)")
        await page.select_option("#class-select", opts[0])
        await page.click("text=Queue aufbauen")
        await page.wait_for_function(
            "document.querySelectorAll('#queue-tbody tr').length > 0 && !document.querySelector('#queue-tbody td[colspan]')",
            timeout=15_000,
        )
        log(f"OK  Login + Klasse '{opts[0]}' + Queue")

        # Zwei Helfer anlegen
        token_a = await add_helper(page, "Helfer-A")
        token_b = await add_helper(page, "Helfer-B")
        log("OK  2 Helfer angelegt")

        # Zwei Scanner verbinden (getrennte Contexts = zwei Geräte)
        scan_a = await open_scanner(browser, token_a)
        scan_b = await open_scanner(browser, token_b)
        log("OK  2 Scanner verbunden")

        # Beiden parallel den nächsten Schüler zuweisen (echte Client-Funktion)
        await page.evaluate("(t) => nextStudent(t)", token_a)
        await page.evaluate("(t) => nextStudent(t)", token_b)
        log("..  2× 'Nächster' -> Worker laden 2 Karteien parallel (read-only)")

        # Beide Scanner müssen ihren Schüler zeigen
        await asyncio.gather(
            scan_a.wait_for_selector("#student-panel", state="visible", timeout=60_000),
            scan_b.wait_for_selector("#student-panel", state="visible", timeout=60_000),
        )
        name_a = await scan_a.inner_text("#s-name")
        name_b = await scan_b.inner_text("#s-name")
        assert name_a != name_b, "Beide Scanner zeigen denselben Schüler!"
        log(f"OK  Beide Scanner zeigen unterschiedliche Schüler ('{name_a}' / '{name_b}')")

        # Host: genau 2 aktiv
        active = await page.eval_on_selector_all(
            "#queue-tbody tr", "els => els.filter(r => r.textContent.includes('Aktiv')).length"
        )
        assert active == 2, f"erwartet 2 aktiv, war {active}"
        log("OK  Host: 2 Schüler 'Aktiv'")

        # Beide scannen parallel -> beide staged, unabhängig
        await scan_a.evaluate("() => ws.send(JSON.stringify({type:'scan', value:'PARALLEL-A-0001'}))")
        await scan_b.evaluate("() => ws.send(JSON.stringify({type:'scan', value:'PARALLEL-B-0002'}))")
        await asyncio.gather(
            scan_a.wait_for_selector("#scan-results .scan-item.staged", timeout=15_000),
            scan_b.wait_for_selector("#scan-results .scan-item.staged", timeout=15_000),
        )
        res_a = await scan_a.inner_text("#scan-results .scan-item")
        res_b = await scan_b.inner_text("#scan-results .scan-item")
        assert "PARALLEL-A-0001" in res_a and "PARALLEL-B-0002" in res_b, f"Barcodes vertauscht? {res_a!r} {res_b!r}"
        log("OK  Beide Barcodes unabhängig gestaged (kein Submit, keine Vermischung)")

        await browser.close()
    log("\nE2E-PARALLEL BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
