# DPM Lunchline

Zeigt das heutige Mittagsmenü der Restaurants rund um Hofmannstraße 7a, 81379 München.

Zwei HTML-Dateien im Projekt: **`index.html`** ist die eigentliche Seite (lädt
`menus.json` per `fetch()` – das ist die Version für GitHub Pages/Live-Betrieb).
**`preview.html`** ist eine Wegwerf-Version mit eingebetteten Beispieldaten,
nur zum schnellen Ansehen ohne Server – die braucht ihr fürs Deployment nicht.

## Start (mit Beispieldaten ansehen)

Die Seite braucht einen einfachen HTTP-Server (nicht direkt als Datei öffnen,
sonst blockiert der Browser den `fetch()` auf `menus.json`):

```
cd mittagsmenu
python3 -m http.server 8000
```

Dann im Browser: http://localhost:8000/index.html

Die Datei `menus.json` enthält aktuell einen **manuell erfassten Snapshot**
(KW 33, 10.–14.08.2026) als Startdaten, damit die Seite sofort etwas zeigt.

## Live-Daten holen

```
pip install -r requirements.txt
python3 scraper.py
```

Das Skript liest die vier Quellen aus und überschreibt `menus.json`:

| Restaurant | Format | Quelle |
|---|---|---|
| Egg Haus Café | HTML | egghauscafe.de/lunch-menu |
| Lotus Asia | HTML | lotusasia.shop/wochenkarte |
| Café Moccasola | PDF | moccasola.de (Wochenkarte-PDF, Dateiname mit Kalenderwoche) |
| Augustiner Schützengarten | PDF | augustiner-schuetzengarten.de (Tageskarte, Dateiname mit Datum) |

**Wichtig:** Dieses Skript braucht normalen Internetzugriff auf die Restaurant-
Websites. Es lief nicht in der Sandbox, in der dieses Projekt entstanden ist
(dort ist der Netzwerkzugriff auf externe Domains gesperrt) – ich konnte die
Scraper deshalb nicht live gegen die echten Seiten testen. Die Parsing-Logik
basiert auf der Textstruktur, die ich per Screenshot/Fetch gesehen habe.
**Nach dem ersten echten Lauf lohnt sich ein kurzer Blick in die Konsolen-
Ausgabe** (`Scraping ... -> ok/error`) – falls eine Seite ihr Layout anders
aufbaut als erwartet, gibt der Scraper eine Fehlermeldung statt falscher Daten
aus (kein "halluziniertes" Menü), und die betroffene Karte zeigt das im
Frontend als "Fehler beim Abrufen" an.

## Deployment mit GitHub Pages + GitHub Actions

Das Projekt enthält bereits `.github/workflows/update-menus.yml` – die Action
läuft werktags morgens automatisch, führt `scraper.py` aus und committet ein
aktualisiertes `menus.json`. Du musst nur noch dein Repo aufsetzen:

**1. Repo erstellen und Projekt hochladen**

Auf github.com ein neues (leeres) Repo anlegen, z. B. `dpm-lunchline` – privat
oder öffentlich, beides funktioniert mit GitHub Pages. Dann lokal im
entpackten Projekt-Ordner:

```
cd mittagsmenu
git init
git add .
git commit -m "Initial commit: DPM Lunchline"
git branch -M main
git remote add origin https://github.com/<dein-user>/dpm-lunchline.git
git push -u origin main
```

**2. Actions Schreibrechte geben**

Der Workflow committet `menus.json` selbst zurück ins Repo, dafür braucht er
Schreibrechte:
Repo → Settings → Actions → General → "Workflow permissions" →
**"Read and write permissions"** auswählen → Save.

**3. GitHub Pages aktivieren**

Repo → Settings → Pages → unter "Build and deployment" → Source:
**"Deploy from a branch"** → Branch: **main**, Ordner: **/ (root)** → Save.

Nach 1-2 Minuten ist die Seite live unter:
`https://<dein-user>.github.io/dpm-lunchline/`

**4. Ersten Scraper-Lauf antriggern**

Repo → Actions → "Update Mittagsmenüs" → "Run workflow" (manuell auslösen,
nicht erst auf den nächsten Cron-Zeitpunkt warten). Danach zeigt die Live-Seite
echte, aktuelle Daten statt des Snapshots.

Ab dann läuft es automatisch: werktags morgens aktualisiert die Action
`menus.json`, GitHub Pages liest die Datei direkt aus dem Repo – kein
zusätzlicher Deploy-Schritt nötig.

## Nächste Schritte / Ausbau

- Weitere Restaurants ergänzen: eigene `scrape_*`-Funktion in `scraper.py`
  nach demselben Muster (Ergebnis-Dict mit `name`, `address`, `days`, `status`).
- Standortwechsel: aktuell hart auf's Büro in Obersendling ausgelegt (Adressen,
  Gehzeiten). Für mehrere Standorte bräuchte man eine echte Geo-Suche
  (z. B. über die Google Places API) statt der fest hinterlegten Liste.
- Benachrichtigung: z. B. ein täglicher Slack-Post mit den Tagesangeboten,
  gespeist aus derselben `menus.json`.
