---
title: |
  Nachfolge-Anleitung:

  1. Mehrere Handys anstatt der USB-Scanner zum Einscannen der Bücherstapel verwenden

  ```{=latex}
  \vspace{0.3cm}
  ```

  ```{=latex}
  {\normalfont\large
  ```

  - Mehrere Personen können gleichzeitig arbeiten
  - Automatisches Drucken der Leihscheine vom Handy aus
  - Korrektur der Klassenangaben auf den Leihscheinen

  ```{=latex}
  }
  ```

  ```{=latex}
  \begin{center}
  \&
  \end{center}
  \vspace{0.25cm}
  ```

  2. Automatische Erstellung / Aktualisierung der Excel-Bestands- \& Nachbestellungslisten

  ```{=latex}
  \vspace{0.3cm}
  ```

  ```{=latex}
  {\normalfont\large
  ```

  - Einfach und übersichtlich erkennen, welche Bücher in welcher Menge nachbestellt werden müssen
  - ...

  ```{=latex}
  }
  ```
author: SBA-Team
date: "Stand: August 2026"
---

# Nachfolge-Anleitung

**Schulbuchausleihe-Tool „Ausleihe-Ausgabe"**: Modus A (Bücherstapel) und
Bestand-/Nachbestellungs-Excel

Diese Anleitung richtet sich an Nachfolgerinnen und Nachfolger im
Schulbuchausleihe-Team (SBA), die das Werkzeug **ohne die ursprünglichen
Entwickler (Niklas Müller & Lukas Podleschny)** weiterbetreiben wollen. Es wird
kein Technik-Vorwissen vorausgesetzt. Alle Schritte sind ausführlich beschrieben.

> **Eine PDF-Version dieser Anleitung** liegt zusätzlich im SBA-Team aus
> (ausdrucken und zum Laptop legen). Die Markdown-Originaldatei lebt im Repo
> unter `docs/nachfolge-anleitung.md` und ist die Quelle der Wahrheit. Bei
> Änderungen dort aktualisieren und neu als PDF exportieren.

---

## Teil 0: Wichtig vorab

### Was dieses Werkzeug kann

- **Modus A, Bücherstapel:** In den Sommerferien Bücherstapel für ganze Klassen
  zusammenstellen. Ein Laptop (der „Host") zeigt die Klasse und die Schülerliste;
  Helfer scannen mit dem Handy Bücher zu; das Werkzeug bucht automatisch über das
  offizielle IServ-Frontend und druckt den Leihschein.
- **Bestand-Excel:** Einmal jährlich die Excel-Bestands- und
  Nachbestellungsliste aus IServ aktualisieren (Angemeldet, Bezahlt, Bestand,
  Bestellt) und den Nachbestellbedarf ausrechnen lassen.

### Was es *nicht* kann / wo die Grenze ist

- Es **ergänzt** die IServ-Schulbuchausleihe, es ersetzt sie **nicht**.
- Es greift auf eine **undokumentierte** IServ-API zu. Wenn IServ die API oder
  die Website ändert, kann das Werkzeug plötzlich nicht mehr funktionieren.
  **Das ist dann kein Fehler von euch** (siehe Teil 5).

### Der dauerhafte Notnagel: USB-Handscanner am IServ-Frontend

Das Werkzeug ist eine Hilfe, kein Muss. **Fällt es einmal aus** (z. B. nach
einer IServ-Aktualisierung), arbeitet ihr einfach weiter wie vorher: USB-
Handscanner an einen Laptop, offizielles IServ-Ausleihe-Frontend im Browser,
Bücher von Hand zuordnen. **Das ist kein Scheitern**, sondern der dauerhaft
verfügbare Weg. Behaltet ihn im Kopf, dann nutzt ihr das Werkzeug gelassen.

### Die goldene Regel

**Niemals Daten in IServ verändern, die ihr nicht ändern wollt.** Das Werkzeug
setzt Buchungen **nur** dann ab, wenn `ALLOW_BOOKING=true` in der `.env` steht
*und* das gescannte Buch zwei Bedingungen erfüllt (Buch liegt im Lager + Schüler
hat es bestellt und hat von der Reihe noch keins). Steht `ALLOW_BOOKING=false`,
wird beim Scannen **gar nichts** gebucht. Der Scan wird nur vorgemerkt. Zum
 reinen Ausprobieren immer `false` lassen; nur für den echten Einsatz `true`
 (Teil 1, Schritt 4).

---

## Teil 1: Ersteinrichtung (einmalig, ca. 30 bis 60 Min)

Diese Schritte **einmalig** pro Laptop durchführen. Danach genügt für jeden
Einsatz Teil 2.

### Voraussetzungen

- Ein **privater Windows-Laptop**. Schulgeräte brauchen wiederkehrend
  Administratorrechte und funktionieren nicht zuverlässig.
- Zugang zum **Schul-WLAN**.
- Einen **USB-Drucker** für die Leihscheine.
- Die **IServ-Zugangsdaten** eines SBA-Admin-Accounts (irgendein Helfer der SBA
  hat immer einen). Die braucht ihr für die `.env`-Datei.

> **Wichtig: Keinen Schul-Laptop verwenden.** Auf einem Schulgerät werden für
> die benötigten Programme und Einstellungen immer wieder Administratorrechte
> verlangt. Dann müsste bei jedem Start Hr. Fischer oder Hr. Riecher das
> Admin-Passwort eingeben. Deshalb das Werkzeug nur auf einem privaten
> Windows-Laptop einrichten und einsetzen.

### Schritt 1: Git installieren

Git ist das Programm, mit dem man Code aus dem Internet herunterlädt
(„klonen").

1. Browser öffnen: <https://git-scm.com/download/win>
2. „64-bit Git for Windows Setup" herunterladen und installieren (alles auf
   Standard belassen, immer „Next").
3. Danach ein neues Fenster öffnen: Rechtsklick auf den Desktop >
   **„Open Git Bash here"** muss im Menü erscheinen. Wenn ja, ist Git fertig.

### Schritt 2: Beide Repositories herunterladen (klonen)

Das Werkzeug besteht aus **zwei** Teilen, die **direkt nebeneinander** in
einem gemeinsamen Ordner liegen müssen. Das ist wichtig, sonst findet der
eine Teil den anderen nicht.

1. Einen Ordner anlegen, z. B. `C:\SBA\` (im Windows-Explorer neu anlegen).
2. Git Bash öffnen (Rechtsklick in den `C:\SBA`-Ordner > „Open Git Bash here").
3. Nacheinander diese beiden Befehle eingeben (jeweils Enter, warten bis fertig):

```bash
git clone https://github.com/niklas-mlrr/ausleihe-ausgabe.git ausleihe-ausgabe
git clone https://github.com/niklas-mlrr/ausleihe-api.git ausleihe-api
```

Am Ende liegt vor euch:

```
C:\SBA\
|-- ausleihe-ausgabe\   -> das Werkzeug (Modus A)
`-- ausleihe-api\       -> die IServ-Schnittstelle (wird von oben benutzt)
```

### Schritt 3: Erstinstallation ausführen

1. Im Windows-Explorer in `C:\SBA\ausleihe-ausgabe\` gehen.
2. **`setup.bat`** doppelt anklicken.
   - Das installiert automatisch das benötigte Programm `uv` (falls es fehlt),
     lädt die Python-Umgebung und den Playwright-Browser herunter und legt eine
     `.env`-Datei aus der Vorlage an.
   - Dauert einige Minuten (Internet nötig). Das Fenster nicht schließen.
   - Am Ende steht „Fertig."; eine Taste drücken, dann schließt sich das
     Fenster.

> Tritt ein Fehler auf, zeigt das Skript ihn auf Deutsch an. Meistens fehlt dann
> Internet oder es gab einen Download-Fehler. `setup.bat` einfach nochmal
> starten.

### Schritt 4: `.env` ausfüllen (wichtigste Datei)

Die `.env`-Datei enthält die Zugangsdaten. Sie liegt in
`C:\SBA\ausleihe-ausgabe\.env` und ist **vertraulich**. Niemals verschicken,
hochladen oder jemandem außerhalb des SBA-Teams geben.

1. `ausleihe-ausgabe`-Ordner öffnen. Ist `.env` nicht sichtbar: im Explorer
   oben auf „Anzeigen" > „Ausgeblendete Elemente" aktivieren.
2. `.env` mit dem Editor (Rechtsklick > Öffnen mit > Editor/Notepad) öffnen.
3. Diese Zeilen ausfüllen (nur die Werte hinter dem `=` ersetzen):

```
ISERV_DOMAIN=iserv-trg-oha.de
# Loginname des SBA-Admin-Accounts (irgendein Helfer der SBA hat ihn):
ISERV_USERNAME=...
# Passwort des SBA-Admin-Accounts:
ISERV_PASSWORD=...
# Selbst ausdenken - das Passwort, mit dem ihr den Host später öffnet:
HOST_PASSWORD=...
# Für den echten Einsatz auf true. Zum reinen Ausprobieren: false
ALLOW_BOOKING=true
```

4. Speichern (Strg+S), schließen.

> **`ALLOW_BOOKING` ist der eine sicherheitsrelevante Schalter:**
> - `false` = beim Scannen passiert **nichts** in IServ (nur Vormerkung). Gut zum
>   Testen und Ausprobieren.
> - `true` = ein Buch wird automatisch gebucht, **aber nur**, wenn das Buch im
>   Lager liegt **und** der Schüler es bestellt hat und von der Reihe noch keins
>   ausgeliehen hat. Sind die Bedingungen nicht erfüllt, wird nicht gebucht.
> Für den echten Stapel-Einsatz auf `true` setzen. Beim ersten Mal ruhig erst
> mit `false` probieren, bis alles läuft.

### Schritt 5: USB-Drucker einrichten

1. USB-Drucker an den Laptop anschließen, Treiber installieren.
2. In Windows den Drucker als **Standarddrucker** setzen
   (Einstellungen > Geräte > Drucker & Scanner > „Als Standard").
3. **SumatraPDF** installieren (für stilles Drucken ohne Dialogfenster):
   - Entweder `start.bat` (Teil 2) installiert es beim ersten Start automatisch
     via `winget`, **oder**
   - manuell von <https://www.sumatrapdfreader.org/> herunterladen und
     installieren.

### Schritt 6: Server starten und Zertifikat bestätigen

1. Im Ordner `ausleihe-ausgabe` **`start.bat`** doppelt anklicken.
   Ein schwarzes Fenster öffnet sich; unten steht die Adresse.
2. Im Browser (Edge/Firefox) öffnen: **`https://localhost:3443/host`**
   (das `https` ist Absicht).
3. Es erscheint eine Warnung „Ihre Verbindung ist nicht privat" / „potenzielles
   Sicherheitsrisiko". Das ist normal (selbstsigniertes Zertifikat, nur fürs
   Schul-WLAN).
   - **Edge:** „Details" > „Auf … trotzdem fortfahren" > „Weiter zu
     localhost (unsicher)".
   - **Firefox:** „Erweitert" > „Risiko akzeptieren und fortfahren".
4. Jetzt erscheint die Host-Anmeldemaske. Mit dem `HOST_PASSWORD` aus der `.env`
   einloggen.
5. **Auf jedem Helfer-Handy** dasselbe einmalig machen: die Handys scannen
   später einen QR-Code, der auf die Laptop-Adresse führt. Beim ersten Öffnen
   kommt dieselbe Zertifikat-Warnung. Einmalig „trotzdem fortfahren" bestätigen.

> **Läuft der Host?** Dann ist die Ersteinrichtung fertig. Das schwarze Fenster
> (der Server) muss während des ganzen Einsatzes offen bleiben. Beenden mit
> Strg+C im schwarzen Fenster oder einfach Fenster schließen.

### Wenn es nicht klappt

| Symptom | Lösung |
|---------|--------|
| `start.bat` meldet „uv nicht gefunden" | erst `setup.bat` ausführen (Teil 1, Schritt 3) |
| `start.bat` meldet „.env fehlt" | `setup.bat` ausführen, oder `.env.example` zu `.env` kopieren und ausfüllen |
| Browser zeigt gar nichts unter `https://localhost:3443/host` | schwarzes Server-Fenster prüfen: Läuft es noch? Ist Port 3443 frei? |
| Zertifikat-Warnung taucht immer wieder | einmalig „trotzdem fortfahren" bestätigen; danach speichern sich die Browser das |
| Host-Anmeldung schlägt fehl | `HOST_PASSWORD` in `.env` kontrollieren, Server neu starten |

---

## Teil 2: Normalbetrieb Modus A (jeder Stapel-Einsatz)

Für jeden Einsatz in den Sommerferien (oder eine Generalprobe) diese Schritte.

### Vorbereitung

1. Laptop ins **Schul-WLAN** einbinden, USB-Drucker angeschlossen und als
   Standarddrucker gesetzt.
2. `start.bat` doppelklicken. Das schwarze Fenster muss offen bleiben, solange
   der Server läuft.
3. Am Laptop im Browser **`https://localhost:3443/host`** öffnen, mit
   `HOST_PASSWORD` einloggen.

### Eine Klasse öffnen

4. Oben das richtige **Schuljahr** wählen (Default = laufendes Jahr; meist
   stimmt das schon).
5. Auf **„Neue Klasse öffnen"** (oder Klassen-Tab) klicken und die Klasse
   auswählen.
6. Die Schüler der Klasse erscheinen alphabetisch als **Warteschlange**.
   - Optional: Filter „Sofort fertig" aktivieren (Schüler, deren Bücherreihen
     schon alle ausgeliehen sind, werden direkt grün).
   - Optional: Buchreihen ausblenden (Einstellungen > Häkchen bei Reihen, die
     nicht bearbeitet werden sollen; wirkt sofort auf allen Geräten).

### Drucker prüfen

7. In den **Einstellungen** (Zahnrad) prüfen, dass der Leihschein-Drucker
   gesetzt ist. Wer nur einen Drucker nutzt: Standarddrucker genügt. Wer
   mehrere Drucker parallel nutzt: Drucker-Pool einrichten (Reiter, wie
   Klassen-Tabs) und pro Klasse per Häkchen festlegen, welche Drucker drucken
   dürfen. Für den Anfang reicht **ein Drucker**.

### Helfer verbinden

8. Auf dem Handy des Helfers die **Kamera** öffnen (oder eine QR-App). Den
   QR-Code scannen, den der Host anzeigt (Helfer-Pairing-Code).
9. Das Handy öffnet die Scanner-Seite; Kamera-Erlaubnis erteilen („Zulassen").
10. Der Helfer wählt oben die **richtige Klasse** aus (sonst scannt er in eine
    fremde Klasse!).

### Einen Schüler bearbeiten

11. Im Host: den ersten Schüler aus der Warteschlange **aufrufen** (oder der
    Helfer ruft ihn auf seiner Liste auf).
12. Der Helfer scannt nacheinander die Buch-Barcodes der Bücher, die diesem
    Schüler gehören sollen.
13. Pro Scan erscheint eine Meldung (siehe Tabelle unten). Bei „Buch
    ausgegeben" / „gebucht" druckt der Leihschein automatisch (wenn Drucker
    konfiguriert und `ALLOW_BOOKING=true`).
14. Der Fortschritt zeigt **X/Y Bücher** (X = erledigt, Y = insgesamt
    fällig ohne ausgeblendete Reihen).
15. Wenn alle Bücher des Schülers gescannt: nächsten Schüler aufrufen. Fertige
    Schüler werden in der Liste **grün**.

### Was die Statusmeldungen bedeuten

| Meldung / Farbe | Bedeutung | Was tun? |
|-----------------|-----------|----------|
| **Buch ausgegeben** (grün) | Buch erfolgreich gebucht + Leihschein gedruckt | weiter zum nächsten Buch |
| **Wird gedruckt … > Gedruckt** | Leihschein ist in der Druckerwarteschlange / fertig | warten bis „Gedruckt", dann weiter |
| **Bereits verliehen** (orange) | genau dieses Exemplar ist schon an diesen Schüler ausgeliehen | ok, weiter |
| **Reihe bereits ausgeliehen** (orange) | ein anderes Exemplar derselben Reihe ist schon an diesen Schüler | ok, weiter (keine zweite nötig) |
| **An jemand anderen verliehen** (rot) | das Buch ist an einen anderen Schüler verliehen | Buch zurück, anderes Exemplar nehmen; Helfer nicht selbst in IServ ändern |
| **Buch ausgemustert** (orange) | Buch wurde ausgemustert, Ersatzanspruch prüfen | Helfer/Host entscheidet; evtl. Ersatz scannen |
| **Nicht angemeldet / nicht bezahlt** | Schüler hat dieses Buch nicht bestellt oder noch nicht bezahlt | Host kann per Freigabe-Dialog override erteilen (nur wenn bewusst gewollt) |
| **Unbekanntes Buch** (orange) | Barcode in IServ nicht gefunden | Barcode kontrollieren, evtl. Tippfehler |

### Mehrere Helfer parallel

Es können mehrere Helfer gleichzeitig scannen. Jeder nutzt sein Handy und
bearbeitet einen anderen Schüler. Will ein zweiter Helfer denselben Schüler
öffnen, sieht er die Bücherliste **nur lesend** (Zuschauer-Modus) und rückt
automatisch nach, sobald der erste fertig ist. Niemand doppelt.

### Einsatz beenden

16. Alle Schüler grün? Dann Klasse ist fertig. Host kann nächste Klasse öffnen
    (neuer Tab).
17. Zum Schluss: schwarzes Server-Fenster schließen (Strg+C oder Fenster zu).
    Laptop herunterfahren.

---

## Teil 3: Bestand-/Nachbestellungs-Excel (jährlich)

Einmal pro Jahr (vor oder nach der Ausleihe) die Excel-Liste aktualisieren.
Das geht auf demselben Laptop oder einem anderen Rechner mit Python.

### Voraussetzungen

- Die Repos `sba-bestand` **und** `ausleihe-api` liegen **nebeneinander** im
  selben Ordner. `ausleihe-api` kommt aus Teil 1, Schritt 2; `sba-bestand` wird
  zusätzlich geklont:

  ```bash
  git clone https://github.com/niklas-mlrr/sba-bestand.git
  ```

  Diese beiden genügen für diesen Teil — das `ausleihe-ausgabe`-Werkzeug muss
  dafür **nicht** laufen.
- **Python 3.9 oder neuer** ist installiert. Falls nicht: von
  <https://www.python.org/downloads/windows/> installieren und dabei unbedingt
  **„Add Python to PATH"** anhaken.
- Eine `.env` im `ausleihe-api`-Ordner mit denselben `ISERV_*`-Zugangsdaten
  (siehe Teil 1, Schritt 4; die gleiche Datei kann kopiert werden).
- Die Excel-Datei `Bestand- und Nachbestellungsliste 2026.xlsx` (bzw. das
  jeweilige Jahr) im Ordner
  `sba-bestand\bestand\`.

### Einrichtung (einmalig pro Rechner)

Im `sba-bestand`-Ordner einmalig die Zusatz-Bibliotheken installieren. Git Bash
im `sba-bestand`-Ordner öffnen:

```bash
pip install openpyxl isbnlib python-dotenv reportlab requests
```

(Falls `pip` nicht geht: `python -m pip install openpyxl isbnlib python-dotenv
reportlab requests` verwenden.)

Die Skripte finden den IServ-Client `ausleihe` automatisch im Nachbarordner
`ausleihe-api` — dort liegt auch die `.env` mit den Zugangsdaten.

### Aktualisierung ausführen

1. In den Ordner wechseln (Git Bash oder Eingabeaufforderung):

```bash
cd sba-bestand/bestand
```

2. **Erst Trockenlauf** (prüft alles, schreibt noch nicht in die Excel-Datei):

```bash
python update_bestand_auto.py --dry-run --excel "Bestand- und Nachbestellungsliste 2026.xlsx" -v
```

3. Die Ausgabe ansehen: sind die erkannten Fächer und Jahrgänge plausibel?
   Stimmen die Anmeldezahlen grob? Wenn ja, den echten Lauf starten:

```bash
python update_bestand_auto.py --excel "Bestand- und Nachbestellungsliste 2026.xlsx"
```

4. Die Excel-Datei ist nun aktualisiert:
   - Spalten **Angemeldet / Bezahlt / Bestand / Bestellt** pro Buchreihe
     befüllt.
   - Das Sheet **„zu Bestellen"** enthält alle Bücher, bei denen
     (Angemeldet - Bestand - Bestellt) > 0 ist, alphabetisch nach Titel, mit
     Stückzahl, Verlag, ISBN (mit Bindestrichen) und Neupreis.
   - Die „Stand"-Zelle trägt Datum/Uhrzeit des Laufs.

5. Excel-Datei öffnen und kontrollieren, dann speichern/schließen.

### Wichtig

- Das Skript liest die Excel-Struktur **selbst** aus (keine `config.json` nötig).
  Es erkennt Fächer- und Jahrgang-Zeilen automatisch.
- **Heikel:** Neue Buchreihen oder umbenannte Fächer können das Matching
  verwirren (Klammerzusätze wie „Politik (eA)" sind Hinweise auf den
  Serientitel). Wenn Zahlen fehlen oder falsch wirken: Trockenlauf mit `-v`
  ansehen und Teil 4, „Excel: Fach trifft nicht".
- Nur-Lese-Zugriff auf IServ (GET). Das Skript schreibt **nur** in die
  Excel-Datei, nie in IServ.

---

## Teil 4: Typische Fehler (ohne Technikwissen lösbar)

### IServ-Login schlägt fehl / „401" / Bücher fehlen

- Ursache: Passwort des SBA-Admin-Accounts falsch oder abgelaufen, oder IServ-
  Session abgelaufen.
- Lösung: IServ im Browser normal einloggen. Geht das? Wenn das Passwort
  abgelaufen ist, ein neues Passwort setzen und in der `.env` aktualisieren
  (`ISERV_PASSWORD`), dann Server neu starten (`start.bat`).

### API gibt 404/500 / gar keine Bücher / alles leer

- Ursache: IServ hat die API oder Website aktualisiert. Das Werkzeug trifft
  nicht mehr die richtigen Stellen. **Das ist nicht euer Fehler** und ihr könnt
  es nicht reparieren.
- Lösung: **auf den USB-Handscanner-Fallback zurückfallen** (Teil 0) und die
  Stapelerstellung im offiziellen IServ-Frontend von Hand machen. Einen Hinweis
  im SBA-Team hinterlassen („Tool ging nicht, IServ wohl geändert").

### QR-Code zeigt `localhost` statt einer IP

- Ursache: Host wurde über `https://localhost:3443/host` geöffnet, daher baut
  der QR die lokale Adresse ein.
- Lösung: Host stattdessen über die **LAN-IP** des Laptops öffnen, z. B.
  `https://192.168.x.y:3443/host` (IP des Laptops im Schul-WLAN). Dann zeigen
  die QR-Codes die richtige Adresse für die Handys. Die Laptop-IP findet ihr
  im schwarzen Server-Fenster oder in den Windows-Netzwerkeinstellungen.

### Druck geht nicht / Leihschein druckt nicht

- Erst prüfen: ist der USB-Drucker angeschlossen und als **Standarddrucker**
  gesetzt?
- Wenn SumatraPDF fehlt: manuell installieren (Teil 1, Schritt 5) oder in der
  `.env` `PRINT_BACKEND=file` setzen. Dann werden die Leihscheine als PDF
  gespeichert (Ordner `automation\out\loan_slips\`) und können von Hand
  gedruckt werden.
- Drucker „hängt" („Es dauert ungewöhnlich lange"): Drucker neu starten, im
  Host-Einstellungen den Drucker „Wieder aktivieren".

### Excel: „Fach trifft nicht" / Anmeldezahlen fehlen

- Das Auto-Script passt Fach-Labels aus der Excel-Struktur ab. Bei neuen oder
  umbenannten Fächern/Stufen kann es eine Reihe nicht zuordnen.
- Trockenlauf mit `-v` zeigt, was erkannt wurde. Fehlt eine Reihe: die
  Excel-Struktur (Fach-Zeile, Jahrgang-Zeile, Zustand-Zeile) kontrollieren.
  die Zeilen müssen das erwartete Muster haben („Fach …", „Jahrgang X",
  „Zustand …"). Wenn die Excel-Vorlage fürs neue Jahr geändert wurde, kann das
  Skript evtl. nicht mehr passen. Dann muss die Liste von Hand gepflegt werden.
  Das alte `update_bestand.py` mit `config.json` bitte **nicht** als Ersatz
  verwenden: Es ist noch fehleranfälliger (siehe Bestand-README).

### Server startet nicht

- `.env` fehlt oder ist leer: Teil 1, Schritt 4.
- „Port 3443 belegt": Ein anderes Programm nutzt den Port; in der `.env` einen
  anderen `PORT=` setzen (z. B. 3444) und die URLs entsprechend anpassen.
- Server lief, aber nach IServ-Update geht nichts mehr: siehe oben
  „API gibt 404/500".

### Helfer-Handy kann keine Verbindung zum Host

- Handy und Laptop müssen im **selben** WLAN sein.
- Schul-WLAN „Client-Isolation" kann manchmal Handys vom Laptop trennen. Mit
  einem Hotspot-Test probieren oder IT-Schule fragen.
- HTTPS-Warnung am Handy noch nicht bestätigt > einmalig „trotzdem fortfahren".

---

## Teil 5: Wartung & Lebensdauer (ehrlich)

### Das Werkzeug hat ein Verfallsdatum

Es greift auf eine **undokumentierte** IServ-API zu. IServ wird diese irgendwann
ändern. Ab dann funktioniert das Werkzeug nicht mehr, und **ohne Entwickler kann
es niemand reparieren.** Das ist der ehrliche Zustand. Und er ist in Ordnung:

- **Bis es bricht**, spart das Werkzeug bei jedem Stapel Zeit (die Entwickler
  schätzten ungefähr 20 bis 25 % pro Klasse, subjektiv und ohne Messung).
- **Wenn es bricht**, fallt ihr auf den USB-Handscanner + IServ-Frontend
  zurück. Die Ausleihe läuft weiter, nur etwas langsamer. Niemand ist
  schuld, niemand muss reparieren.

### Was jährlich zu tun ist

- **Vor der Sommer-Ausleihe:** Prüfen, ob das IServ-Modul aufs neue Schuljahr
  umgestellt wurde (früher machte das Hr. Fischer; Freischaltung kam einmal
  nach ein bis zwei Wochen Wartezeit). Erst danach funktioniert das Werkzeug fürs
  neue Jahr.
- **Credentials:** Der SBA-Admin-Account muss dauerhaft verfügbar bleiben. Ein
  Helfer der SBA hat immer einen. Klärt ab, wer das nach eurem Abgang ist, und
  gebt ihm diese Anleitung + die Zugangsdaten.
- **Excel-Datei:** das neue Jahres-Excel (z. B. „… 2027.xlsx") in den
  `sba-bestand/bestand`-Ordner legen und Teil 3 mit dem neuen Dateinamen laufen
  lassen.

### Was nicht mehr gepflegt wird

- **Modus B (Live-Ausgabe-Pilot)** ist **nicht** Teil dieser Anleitung. Er
  braucht technische Betreuung, die nach dem Abgang der Entwickler nicht
  sichergestellt ist. Falls er trotzdem laufen soll, braucht es eine
  technisch versierte Patin/einen Paten. Das ist eine eigene Entscheidung.

---

\newpage

## Teil 6: Einseitiges Cheat-Sheet (ausdrucken)

```
+---------------------------------------------------------------------+
|  SCHULBUCHAUSLEIHE - SPICKZETTEL Modus A                            |
+---------------------------------------------------------------------+
|  EINMALIG (pro Laptop):                                             |
|  Git + beide Repos nach C:\SBA\ klonen; setup.bat doppelklicken.    |
|  .env ausfüllen; für echten Einsatz: ALLOW_BOOKING=true.            |
|  USB-Drucker als Standarddrucker setzen; SumatraPDF installieren.   |
+---------------------------------------------------------------------+
|  JEDER EINSATZ:                                                     |
|  1. Schul-WLAN, Drucker an; start.bat doppelklicken.                |
|  2. https://localhost:3443/host -> HOST_PASSWORD.                   |
|  3. Schuljahr + Klasse öffnen, Drucker prüfen.                      |
|  4. Helfer scannt QR-Code; richtige Klasse wählen.                  |
|  5. Schüler aufrufen, Bücher scannen.                               |
|  6. "Buch ausgegeben" = gebucht + Leihschein druckt.                |
|  7. Schluss: schwarzes Fenster schließen.                           |
+---------------------------------------------------------------------+
|  NOTNAGEL (immer verfügbar):                                        |
|   USB-Handscanner + offizielles IServ-Frontend im Browser.          |
|   Wenn das Tool nicht geht: so weitermachen.                        |
+---------------------------------------------------------------------+
|  STOERUNGEN:                                                        |
|  401/Bücher fehlen: Passwort/.env prüfen, IServ im Browser testen.  |
|  404/500/alles leer: IServ geändert -> USB-Handscanner verwenden.   |
|  QR localhost: Host über LAN-IP öffnen (192.168.x.y:3443).          |
|  Druck nicht: Standarddrucker, SumatraPDF, PRINT_BACKEND=file.      |
+---------------------------------------------------------------------+
|  GOLDENE REGEL: ALLOW_BOOKING=false = nur testen (nix bucht).       |
|                 ALLOW_BOOKING=true  = echt buchen (mit Prüfung).    |
+---------------------------------------------------------------------+
```

---

*Pflegehinweis: Diese Datei ist die Quelle der Wahrheit (`docs/nachfolge-anleitung.md`
im `ausleihe-ausgabe`-Repo). Bei Änderungen die Datei aktualisieren und neu als
PDF exportieren:

```bash
pandoc nachfolge-anleitung.md --pdf-engine=pdflatex \
  --include-in-header=pdf-header.tex -V papersize:a4 -V geometry:margin=2cm \
  -o Nachfolge-Anleitung.pdf
```
