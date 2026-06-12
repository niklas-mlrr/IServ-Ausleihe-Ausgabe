# Spike B — Parallele Playwright-Contexts mit demselben Account

> Status: **erfolgreich abgeschlossen (2026-06-12)** — klärt O2 (Session-Invalidierung bei Mehrfach-Login).
> Skript: `automation/spike_b_parallel.py` · Bezug: `docs/PLAN.md` §5 Phase 1, O2.

## Ziel & Risiko

O2: Erlaubt IServ mehrere gleichzeitige Browser-Sessions desselben Accounts?
Werden bestehende Sessions beim Zweit-Login invalidiert?

Das ist relevant für den Context-Pool im Playwright-Worker (Phase 2 §5): Wenn
IServ beim Login eines zweiten Contexts den ersten ausloggt, muss Plan B
(Cookies teilen, kein Mehrfach-Login) oder Plan C (Login-Rotation) her.

**Scope:** Rein lesend — nur Login, Navigation, Schülerkartei laden. Kein
Barcode-Submit, keine Buchung.

## Vorgehen

**Szenario 1 — Unabhängige Logins (schlechtester Fall für O2):**
N Contexts mit je eigenem Cookie-Jar, jeder loggt sich separat ein.
Frage: Werden frühere Sessions invalidiert?

**Szenario 2 — Cookie-Sharing (Plan-B-Option):**
1 Login, dessen Storage-State auf N Contexts kopiert.
Frage: Können N Contexts gleichzeitig mit denselben Cookies auf die
Ausleihe-App zugreifen?

Aufruf:
```bash
uv run python -m automation.spike_b_parallel --student <Nachname> [--count 3]
```

## Sicherheitsregeln (verbindlich)

- Keine Buchung, kein Submit außer dem Login-Formular.
- Nur Lukas' Admin-Account (`.env`) — ausschließlich lesend.

## Ergebnisse (nach Lauf ausfüllen)

### Szenario 1 — Unabhängige Logins

Lauf: `uv run python -m automation.spike_b_parallel --count 3 --scenarios 1,2`

| Frage | Befund |
|-------|--------|
| Laufdatum/-zeit | 2026-06-12 21:21 |
| N parallele Logins erfolgreich? | **3/3 OK** — alle drei gleichzeitig gestartet, alle durchgelaufen |
| Timing: Login-Dauer (parallel gemessen) | ~4200 ms gesamt für 3 parallele Logins (einzeln je ~4200 ms) |
| Session-Invalidierung beobachtet? | **Nein** — Post-Check: alle 3 Contexts nach Szenario 1 noch auf `#/counter`, kein Redirect zum Login |
| Counter-Seite in allen Contexts erreichbar? | **3/3 OK** — `input.tt-input[name="input"]` sichtbar in allen Contexts |
| Timing: Counter-Check parallel | ~6640 ms für 3 Contexts |
| Fehler/Exceptions | keine |

**Hinweis Navigation:** Direkt auf `#/counter` reicht nicht — Angular-App muss erst von der App-Root (`/`) initialisiert werden (gleicher Befund wie Spike A). Reihenfolge: Root laden (4s) → `#/counter` navigieren → auf `input.tt-input` warten.

### Szenario 2 — Cookie-Sharing

| Frage | Befund |
|-------|--------|
| N Contexts mit geteiltem Storage-State funktional? | **3/3 OK** — alle drei mit denselben 5 Cookies ohne eigenen Login |
| Counter-Seite gleichzeitig erreichbar? | **3/3 OK** |
| Timing: Counter-Check parallel | ~6665 ms (vergleichbar mit Szenario 1) |
| Unterschied zu Szenario 1? | Keiner sichtbar — Cookie-Sharing ist gleichwertig zu unabhängigem Login |
| Fehler/Exceptions | keine |

### Bewertung O2

**O2 ist geklärt: IServ erlaubt beliebig viele parallele Sessions desselben Accounts.**

- Kein Single-Session-Zwang, keine Invalidierung beim Zweit-Login.
- Sowohl N unabhängige Logins als auch N Contexts mit geteiltem Storage-State funktionieren.

**Empfehlung für Context-Pool (Phase 2):**

Plan A (unabhängige Logins, `N × login()`) ist einfacher und robuster:
- Jeder Context ist unabhängig; Re-Login eines einzelnen Contexts beeinflusst die anderen nicht.
- Kein geteilter Zustand zwischen Contexts.

Plan B (Cookie-Sharing) ist ein gültiger Plan B wenn Login-Dauer ein Bottleneck wird — aktuell kein Grund dafür (4.2 s/Context parallel ist schnell genug).

**Implementierungsfolge für den Worker:**
- Context-Pool mit `N` unabhängigen Contexts (je eigener Cookie-Jar).
- Login beim Pool-Startup; bei 401/Redirect-zu-Login: automatischer Re-Login.
- Kein Bedarf für Cookie-Sync zwischen Contexts.
