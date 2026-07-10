"""E2E-Test (Modus B / Live-Ausgabe): treibt host.html + qr-display.html +
student.html headless durch den vollen Pairing-Flow.

Voraussetzung: Server laeuft (`uv run python -m server.main`).
Aufruf:        `uv run python -m automation.e2e_modus_b`

Read-only gegenueber IServ: laedt nur eine Schuelerkartei (kein Submit/Enter).
Keine dauerhaften Schuelernamen in Logs (PLAN 3.7).

Geprueft wird die Kette:
  open -> Display registrieren+autorisieren -> student join -> Pairing
  -> Schuelerinfo+Bestellliste -> Scan (staged) -> finish
  -> harte Invalidierung (alter Token -> neutrale Seite).
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


def log(*a):
    print(*a, flush=True)


async def main():
    pw = os.environ.get("HOST_PASSWORD")
    if not pw:
        raise SystemExit("HOST_PASSWORD fehlt — .env prüfen (.env.example als Vorlage).")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(ignore_https_errors=True)
        page = await ctx.new_page()

        # 1. Host-Login + WS + Klasse/Queue
        await page.goto(f"{BASE}/host.html", wait_until="domcontentloaded")
        await page.fill("#pw-input", pw)
        await page.click("#login-btn")
        await page.wait_for_selector("#main-view", state="visible", timeout=10_000)
        await page.wait_for_function(
            "document.getElementById('ws-dot').className.includes('ok')", timeout=10_000
        )
        await page.wait_for_function(
            "document.querySelectorAll('#class-select option').length > 1", timeout=15_000
        )
        opts = await page.eval_on_selector_all(
            "#class-select option", "els => els.map(e => e.value).filter(Boolean)"
        )
        chosen = opts[0]
        await page.select_option("#class-select", chosen)
        await page.click("text=Queue aufbauen")
        await page.wait_for_function(
            "document.querySelectorAll('#queue-tbody tr').length > 0 && !document.querySelector('#queue-tbody td[colspan]')",
            timeout=15_000,
        )
        log(f"OK  Host bereit, Klasse '{chosen}' -> Queue aufgebaut")

        # 2. Modus B oeffnen
        await page.click("#mb-open-btn")
        await page.wait_for_function(
            "document.getElementById('mb-close-btn').style.display !== 'none'", timeout=10_000
        )
        join_url = await page.evaluate("() => state.modus_b.join_url")
        assert join_url and "/student.html?j=" in join_url, f"join_url falsch: {join_url}"
        secret = join_url.split("j=")[1]
        log("OK  Modus B geoeffnet (Join-URL vorhanden)")

        # 3. iPad-Display: registrieren -> am Host autorisieren -> QR erscheint
        dctx = await browser.new_context(ignore_https_errors=True)
        dpage = await dctx.new_page()
        await dpage.goto(f"{BASE}/qr-display.html", wait_until="domcontentloaded")
        await dpage.wait_for_selector("#view-register.show", timeout=10_000)
        reg_code = (await dpage.inner_text("#reg-code")).strip()
        assert len(reg_code) == 4, f"Reg-Code unerwartet: {reg_code}"
        log("OK  Display zeigt Registrierungscode")
        r = await page.evaluate(
            """async (code) => {
                const r = await fetch('/api/display/authorize', {method:'POST',
                  headers:{'Content-Type':'application/json'}, body: JSON.stringify({registration_code: code})});
                return r.ok;
            }""",
            reg_code,
        )
        assert r, "Display-Autorisierung fehlgeschlagen"
        await dpage.wait_for_selector("#view-qr.show", timeout=10_000)
        qr_src = await dpage.get_attribute("#qr-img", "src")
        assert qr_src and qr_src.startswith("data:image/png;base64,"), "Display-QR fehlt"
        log("OK  Display autorisiert -> zeigt QR (anonym, keine Schuelerdaten)")

        # 4. Schueler scannt QR -> join -> 4-stelliger Code, WS pending
        sctx = await browser.new_context(ignore_https_errors=True)
        spage = await sctx.new_page()
        await spage.goto(f"{BASE}/student.html?j={secret}", wait_until="domcontentloaded")
        await spage.wait_for_selector("#view-pending.show", timeout=10_000)
        await spage.wait_for_function(
            "document.getElementById('pair-code').textContent.trim().length === 4", timeout=10_000
        )
        pair_code = (await spage.inner_text("#pair-code")).strip()
        token = await spage.evaluate("() => sessionStorage.getItem('mb_token')")
        assert token and len(token) > 20, "session_token fehlt/zu kurz"
        log("OK  Schueler-Session: 4-stelliger Code angezeigt, langer Token gesetzt")

        # Host sieht offenen Code (ohne Schuelerdaten)
        await page.wait_for_function(
            "document.getElementById('mb-pending').textContent.includes('Offene Codes: 1')",
            timeout=10_000,
        )
        log("OK  Host zeigt 'Offene Codes: 1' (keine Schuelerdaten vor Pairing)")

        # 5. Pairing am Host: Code -> erster pending-Schueler (mit O6-Override-Fallback)
        student_id = await page.evaluate(
            "() => (state.queue.find(q => q.status === 'pending') || {}).student_id"
        )
        assert student_id, "kein pending-Schueler"
        pair_status = await page.evaluate(
            """async ({code, sid}) => {
                let r = await fetch('/api/student/pair', {method:'POST',
                  headers:{'Content-Type':'application/json'},
                  body: JSON.stringify({pairing_code: code, student_id: sid})});
                if (r.status === 409) {
                  const d = await r.json();
                  if (d.detail && d.detail.reason === 'unpaid') {
                    r = await fetch('/api/student/pair', {method:'POST',
                      headers:{'Content-Type':'application/json'},
                      body: JSON.stringify({pairing_code: code, student_id: sid, override_payment: true})});
                    return r.ok ? 'paired-override' : 'fail';
                  }
                  return 'conflict';
                }
                return r.ok ? 'paired' : 'fail';
            }""",
            {"code": pair_code, "sid": student_id},
        )
        assert pair_status in ("paired", "paired-override"), (
            f"Pairing fehlgeschlagen: {pair_status}"
        )
        log(f"OK  Pairing ({pair_status})")

        # 6. Schueler-Handy: Worker laedt Kartei (read-only) -> Bestellliste + Scanner
        await spage.wait_for_selector("#view-active.show", timeout=60_000)
        await spage.inner_text("#s-name")
        nbooks = await spage.eval_on_selector_all("#book-items .book", "els => els.length")
        log(f"OK  Schueler-UI aktiv (Bestellliste: {nbooks} Eintraege)")
        active = await page.eval_on_selector_all(
            "#queue-tbody tr", "els => els.filter(r => r.textContent.includes('Aktiv')).length"
        )
        assert active == 1, f"erwartet 1 aktiv, war {active}"
        log("OK  Queue: 1 Schueler 'Aktiv'")

        # 7. Simulierter Scan -> staged (kein Submit)
        await spage.evaluate(
            "() => ws.send(JSON.stringify({type:'scan', value:'TEST-BARCODE-MB-0000'}))"
        )
        await spage.wait_for_selector("#scan-results .scan-item", timeout=15_000)
        cls = await spage.get_attribute("#scan-results .scan-item", "class")
        assert "staged" in cls, f"nicht staged: {cls}"
        log("OK  Scan staged (kein Submit)")

        # 8. Abschluss durch Schueler -> neutrale Seite
        await spage.click("#finish-btn")
        await spage.wait_for_selector("#view-done.show", timeout=10_000)
        log("OK  'Fertig' -> neutrale 'Vorgang abgeschlossen'-Seite")

        # 9. Harte Invalidierung: alter Token erneut -> neutrale Seite, keine Daten
        ictx = await browser.new_context(ignore_https_errors=True)
        await ictx.add_init_script(f"sessionStorage.setItem('mb_token', {token!r});")
        ipage = await ictx.new_page()
        await ipage.goto(
            f"{BASE}/student.html", wait_until="domcontentloaded"
        )  # ohne j -> kein Re-Join
        await ipage.wait_for_selector("#view-done.show", timeout=10_000)
        # sicherstellen, dass keine aktive Ansicht/Schuelerdaten erscheinen
        active_shown = await ipage.eval_on_selector(
            "#view-active", "el => el.classList.contains('show')"
        )
        assert not active_shown, "Aktive Ansicht trotz entwertetem Token!"
        log("OK  Entwerteter Token -> neutrale Seite (harter Zugriffsentzug bestaetigt)")

        # 10. Aufraeumen: Schueler abschliessen ist erfolgt; Modus B schliessen
        await page.evaluate("() => fetch('/api/modus-b/close', {method:'POST'})")
        await dpage.wait_for_selector("#view-closed.show", timeout=10_000)
        log("OK  Modus B geschlossen -> Display zeigt 'Ausgabe geschlossen'")

        await browser.close()

    log("\nE2E-MODUS-B BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
