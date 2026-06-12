"""Spike A: Playwright gegen die offizielle IServ-Ausleihe-Counter-Seite.

Klärt das kritische Projektrisiko (PLAN §5, Phase 1 / O3): Lässt sich die
offizielle Counter-Seite zuverlässig automatisieren — Login, Schüler öffnen,
Barcode eintragen, Ergebnis aus dem DOM zurücklesen?

Zwei Stufen:

  --explore            Read-only-Browsing: Login, Ausleihe-App öffnen,
                       Screenshots + DOM-Dumps nach automation/out/ schreiben.
                       Optional --student "<Nachname>" für die Typeahead-Suche
                       (reine GETs). KEINE Buchung, kein Submit außer Login.

  --issue / --return   Buchung ausgeben/zurücknehmen. GERÜST — wird erst nach
                       Auswertung der Explore-Ergebnisse implementiert und nur
                       nach expliziter Freigabe mit einem AUSGEMUSTERTEN Buch
                       auf Niklas' Account ausgeführt (CLAUDE.md, PLAN §6).

Aufruf:  uv run python -m automation.spike_a_counter --explore [--student Müller]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

OUT_DIR = Path(__file__).parent / "out"


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Fehler: {name} fehlt — .env prüfen (.env.example als Vorlage).")
    return value


def snap(page: Page, name: str) -> None:
    """Screenshot + HTML-Dump des aktuellen Zustands nach automation/out/."""
    page.screenshot(path=str(OUT_DIR / f"{name}.png"), full_page=True)
    (OUT_DIR / f"{name}.html").write_text(page.content(), encoding="utf-8")
    print(f"  [snap] {name}  ({page.url})")


def login(page: Page, domain: str, username: str, password: str) -> None:
    """IServ-Login als echte Browser-Session (einziger Submit im Explore-Modus)."""
    # networkidle ist auf IServ unbrauchbar (Dashboard hält Long-Polling offen)
    # — daher domcontentloaded + feste Wartezeiten.
    page.goto(f"https://{domain}/iserv/login", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)
    snap(page, "01_login_seite")
    page.fill('input[name="_username"]', username)
    page.fill('input[name="_password"]', password)
    page.click('button[type="submit"]')
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(3000)
    if "login" in page.url and "auth" in page.url:
        snap(page, "01b_login_fehlgeschlagen")
        sys.exit(f"Login offenbar fehlgeschlagen — noch auf {page.url}")
    snap(page, "02_nach_login")


def dump_links(page: Page, name: str) -> None:
    """Alle sichtbaren Links/Routen der Seite als Textdatei sichern."""
    links = page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => `${(e.textContent||'').trim().slice(0,60)} -> ${e.getAttribute('href')}`)",
    )
    (OUT_DIR / f"{name}_links.txt").write_text("\n".join(links), encoding="utf-8")
    print(f"  [links] {len(links)} Links -> {name}_links.txt")


def explore(student: str | None) -> None:
    domain = env("ISERV_DOMAIN")
    username = env("ISERV_USERNAME")
    password = env("ISERV_PASSWORD")
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"Spike A --explore ({stamp}) — read-only, keine Buchung.")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        login(page, domain, username, password)

        # Offizielles Ausleihe-Frontend (eigene Subdomain, AngularJS-App).
        page.goto(f"https://ausleihe.{domain}/", wait_until="domcontentloaded")
        # Angular braucht einen Moment für Session-XHR + Rendering.
        page.wait_for_timeout(5000)
        snap(page, "03_ausleihe_app")
        dump_links(page, "03_ausleihe_app")

        # Counter-Ansicht „Aus- u. Rückgabe" (Route per Explore-Lauf 2026-06-12
        # identifiziert). Navigation ist read-only; Buchungen passieren erst
        # beim Barcode-Submit, den dieser Modus nie auslöst.
        page.goto(f"https://ausleihe.{domain}/#/counter", wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        snap(page, "04_counter")
        dump_links(page, "04_counter")

        if student:
            # Counter-Eingabefeld „Ausweis scannen oder Namen eingeben":
            # input.tt-input[name=input] (sf-typeahead). ACHTUNG: Enter in diesem
            # Feld feuert c.evaluateInput() — bei einem Buchcode wäre das eine
            # Buchung. Explore tippt daher nur den NAMEN und klickt den
            # Typeahead-Vorschlag an (read-only Navigation), niemals Enter.
            search = page.locator('input.tt-input[name="input"]')
            if search.count():
                search.press_sequentially(student, delay=50)
                page.wait_for_timeout(2500)
                snap(page, "05_schuelersuche")
                suggestion = page.locator(".tt-suggestion").first
                if suggestion.count():
                    suggestion.click()
                    # Geladene Kartei erkennt man am Eingabefeld mit neuem
                    # Placeholder („Buch scannen oder …"), nicht am Spinner.
                    try:
                        page.locator('input.tt-input[placeholder*="Buch scannen"]').wait_for(
                            state="visible", timeout=25_000
                        )
                    except PlaywrightTimeout:
                        print("  [warn] Schülerkartei nach 25s nicht geladen — Snapshot trotzdem.")
                    page.wait_for_timeout(1000)
                    snap(page, "06_counter_schueler")
                    dump_links(page, "06_counter_schueler")
                else:
                    print("  [warn] Kein Typeahead-Vorschlag erschienen — Selektor/Wartezeit anpassen.")
            else:
                print("  [warn] Counter-Eingabefeld nicht gefunden — Selektor anpassen.")

        browser.close()
    print(f"Fertig. Ergebnisse in {OUT_DIR}/ — Auswertung in docs/spikes/spike_a_protokoll.md eintragen.")


def issue_or_return(action: str, code: str) -> None:
    print(
        f"'{action}' für Buch {code} ist noch NICHT implementiert.\n"
        "Dieser Teil wird erst nach Auswertung der --explore-Ergebnisse gebaut\n"
        "und ausschließlich nach expliziter Freigabe durch Niklas mit einem\n"
        "AUSGEMUSTERTEN Buch ausgeführt (siehe CLAUDE.md / docs/PLAN.md §6)."
    )
    sys.exit(2)


def main() -> None:
    load_dotenv(Path(__file__).parent.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--explore", action="store_true", help="read-only Erkundung (keine Buchung)")
    group.add_argument("--issue", metavar="CODE", help="Buch ausgeben (Gerüst, gesperrt)")
    group.add_argument("--return", dest="return_", metavar="CODE", help="Buch zurücknehmen (Gerüst, gesperrt)")
    parser.add_argument("--student", help="Nachname für die Typeahead-Suche im Explore-Modus")
    args = parser.parse_args()

    if args.explore:
        explore(args.student)
    elif args.issue:
        issue_or_return("issue", args.issue)
    else:
        issue_or_return("return", args.return_)


if __name__ == "__main__":
    main()
