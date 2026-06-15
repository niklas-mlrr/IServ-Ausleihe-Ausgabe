"""E2E-Smoke-Test (Modus A): treibt leitstand.html + scan.html headless durch.

Voraussetzung: Server laeuft (`uv run python -m server.main`).
Aufruf:        `uv run python -m automation.e2e_smoke`

Read-only gegenueber IServ: laedt nur eine Schuelerkartei (kein Submit/Enter).
Liest LEITSTAND_PASSWORD aus .env. Keine Schuelernamen in dauerhaften Logs
(PLAN 3.7) — der Name erscheint nur fluechtig in der Test-Ausgabe.
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
PW = os.environ["LEITSTAND_PASSWORD"]

console_errors: list[str] = []


def log(*a):
    print(*a, flush=True)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()
        page.on("console", lambda m: console_errors.append(f"[leitstand:{m.type}] {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: console_errors.append(f"[leitstand:pageerror] {e}"))

        # 1. Login
        await page.goto(f"{BASE}/leitstand.html", wait_until="domcontentloaded")
        await page.fill("#pw-input", PW)
        await page.click("#login-btn")
        await page.wait_for_selector("#main-view", state="visible", timeout=10_000)
        log("OK  Login -> main-view sichtbar")

        # 2. Leitstand-WS verbunden?
        await page.wait_for_function("document.getElementById('ws-dot').className.includes('ok')", timeout=10_000)
        log("OK  Leitstand-WS verbunden")

        # 3. Klassen geladen, Queue aufbauen
        await page.wait_for_function("document.querySelectorAll('#class-select option').length > 1", timeout=15_000)
        opts = await page.eval_on_selector_all("#class-select option", "els => els.map(e => e.value).filter(Boolean)")
        log(f"OK  {len(opts)} Klassen geladen")
        assert opts, "keine Klassen"

        chosen = opts[0]
        await page.select_option("#class-select", chosen)
        await page.click("text=Queue aufbauen")
        await page.wait_for_function(
            "document.querySelectorAll('#queue-tbody tr').length > 0 && !document.querySelector('#queue-tbody td[colspan]')",
            timeout=15_000,
        )
        nrows = await page.eval_on_selector_all("#queue-tbody tr", "els => els.length")
        log(f"OK  Klasse '{chosen}' -> Queue mit {nrows} Schuelern")

        # 4. Helfer + QR/Scan-URL
        await page.fill("#helper-name", "E2E-Helfer")
        await page.click("text=+ Helfer")
        await page.wait_for_selector("#qr-modal.show", timeout=10_000)
        qr_src = await page.get_attribute("#qr-img", "src")
        scan_url = await page.inner_text("#qr-url")
        assert qr_src and qr_src.startswith("data:image/png;base64,"), "QR fehlt"
        assert "/scan.html?token=" in scan_url, "Scan-URL falsch"
        log("OK  Helfer angelegt (QR + Scan-URL ok)")
        await page.click("#qr-modal button")
        token = scan_url.split("token=")[1]

        # 5. Scanner-Seite in separatem Context (= anderes Geraet)
        sctx = await browser.new_context(ignore_https_errors=True)
        spage = await sctx.new_page()
        spage.on("console", lambda m: console_errors.append(f"[scan:{m.type}] {m.text}") if m.type == "error" else None)
        spage.on("pageerror", lambda e: console_errors.append(f"[scan:pageerror] {e}"))
        await spage.goto(f"{BASE}/scan.html?token={token}", wait_until="domcontentloaded")
        await spage.wait_for_function("document.getElementById('dot').className.includes('ok')", timeout=10_000)
        log("OK  Scanner-WS verbunden")

        await page.wait_for_function(
            "Array.from(document.querySelectorAll('#helper-tbody td')).some(t => t.textContent.includes('verbunden'))",
            timeout=10_000,
        )
        log("OK  Helfer im Leitstand als 'verbunden' sichtbar")

        # 6. Naechster Schueler -> Worker laedt Kartei (read-only) -> Scanner zeigt Schueler
        await page.click("#helper-tbody button.success")
        log("..  'Naechster' geklickt -> Worker laedt Schuelerkartei (read-only)")
        await spage.wait_for_selector("#student-panel", state="visible", timeout=60_000)
        sname = await spage.inner_text("#s-name")
        smeta = await spage.inner_text("#s-meta")
        log(f"OK  Scanner zeigt Schueler ('{sname}' | {smeta})")

        active = await page.eval_on_selector_all(
            "#queue-tbody tr", "els => els.filter(r => r.textContent.includes('Aktiv')).length"
        )
        assert active == 1, f"erwartet 1 aktiv, war {active}"
        log("OK  Queue: 1 Schueler 'Aktiv'")

        # 7. Simulierter Scan -> staged (kein Enter)
        await spage.evaluate("() => ws.send(JSON.stringify({type:'scan', value:'TEST-BARCODE-0000'}))")
        await spage.wait_for_selector("#scan-results .scan-item", timeout=15_000)
        res = await spage.inner_text("#scan-results .scan-item")
        assert "staged" in (await spage.get_attribute("#scan-results .scan-item", "class")), f"nicht staged: {res}"
        log(f"OK  Scan staged (kein Submit): '{res}'")

        await browser.close()

    log("\n=== Konsolen-Fehler/Warnungen ===")
    # Der 403 beim /api/state-Probe vor Login ist erwartet (Login-Erkennung).
    for e in console_errors:
        log("  " + e)
    if not console_errors:
        log("  keine")
    log("\nE2E-SMOKE BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
