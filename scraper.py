"""
Mittagsmenü-Scraper für Restaurants nahe Hofmannstraße 7a, 81379 München.

Liest die Tages-/Wochenkarten von 4 Restaurants aus und schreibt das
Ergebnis nach menus.json, das von index.html angezeigt wird.

WICHTIG: Dieses Skript braucht normalen Internetzugriff auf die jeweiligen
Restaurant-Websites. Es ist gedacht, um lokal (z.B. per Cron-Job jeden
Morgen) oder auf einem kleinen Server ausgeführt zu werden - NICHT in einer
Sandbox mit eingeschränktem Netzwerkzugriff.

Ausführen:
    pip install -r requirements.txt
    python3 scraper.py

Ergebnis: menus.json im selben Ordner (wird von index.html per fetch()
geladen, also am besten über einen kleinen lokalen Webserver öffnen:
    python3 -m http.server 8000
    -> http://localhost:8000/index.html
"""

import json
import re
import socket
import time
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

# GitHub Actions Runner haben gelegentlich fehlerhaftes IPv6-Routing zu
# einzelnen Hosts ("Network is unreachable"), obwohl IPv4 problemlos
# funktioniert. requests/urllib3 bevorzugt IPv6, wenn ein Host beides anbietet
# - das erzwingt IPv4-only für alle Verbindungen dieses Skripts.
import urllib3.util.connection as _urllib3_cn


def _force_ipv4():
    return socket.AF_INET


_urllib3_cn.allowed_gai_family = _force_ipv4

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DPM-Mittagsmenu/1.0; "
    "internal office tool; contact: philipp.hoffmann@produktmacher.com)"
}


def _get(url, retries=3, **kwargs):
    """GET mit ein paar Wiederholungsversuchen, damit eine einzelne
    transiente Netzwerkstörung (Timeout, DNS-Hänger, o.ä.) nicht gleich die
    ganze Karte als 'Fehler' markiert."""
    last_exc = None
    for attempt in range(retries):
        try:
            return requests.get(url, headers=HEADERS, timeout=15, **kwargs)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    raise last_exc


_PRICE_RE = re.compile(
    r"(?:(\d{1,2}(?:[.,]\d{1,2})?)\s*€)"       # "9,50 €" / "9,5 €" / "10 €"
    r"|(?:€\s*(\d{1,2}(?:[.,]\d{1,2})?))"      # "€ 9,90" (z.B. Augustiner-PDF)
)


def _format_price(num_str):
    """Bringt eine Preiszahl (egal ob '9', '9,5', '9.50', ...) einheitlich
    auf das Format 'xx,xx €' mit immer genau zwei Nachkommastellen."""
    try:
        value = float(num_str.replace(",", "."))
    except (ValueError, AttributeError):
        return None
    return f"{value:.2f}".replace(".", ",") + " €"


def _price_from_text(text):
    """Findet einen Preis in beiden auf den Seiten vorkommenden Schreibweisen
    (Zahl-vor-€ oder €-vor-Zahl) und normalisiert ihn auf 'xx,xx €'."""
    m = _PRICE_RE.search(text)
    if not m:
        return None
    return _format_price(m.group(1) or m.group(2))


_CLOSURE_KEYWORDS = ("betriebsferien", "geschlossen", "urlaub", "ruhetag")


def _looks_closed(text):
    """Erkennt Ankündigungen wie 'Betriebsferien'/'Urlaub'/'geschlossen' in
    einem Linktext, Dateinamen o.ä. - kein Scraper-Fehler, sondern schlicht
    'diese Woche kein Mittagstisch'."""
    low = text.lower()
    return any(k in low for k in _CLOSURE_KEYWORDS)


# ---------------------------------------------------------------------------
# 1. Egg Haus Café — feste Wochenkarte als HTML-Text, gilt Mo-Fr identisch
# ---------------------------------------------------------------------------
def scrape_egghaus():
    url = "https://egghauscafe.de/lunch-menu/"
    result = {
        "id": "egghaus",
        "name": "Egg Haus Café",
        "address": "Hofmannstraße 23, 81379 München",
        "category": "walk",
        "travel_minutes": 3,
        "source_url": url,
        "format": "html",
        "days": {},
        "status": "error",
        "error": None,
    }
    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Dish names sit in headings near the "Mittagsmenü" section; prices
        # are plain text right after. We take all heading-level text and
        # pull out "name" + "price" pairs heuristically.
        headings = [h.get_text(strip=True) for h in soup.select("h1, h2, h3") if h.get_text(strip=True)]
        dishes = []
        pending_name = None
        for h in headings:
            bare_match = re.fullmatch(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*€?", h.strip())
            if bare_match:
                if pending_name:
                    price = _format_price(bare_match.group(1))
                    dishes.append({"dish": pending_name, "description": None, "price": price})
                    pending_name = None
            elif "mittagsmenü" in h.lower() or "business lunch" in h.lower() or re.search(r"\d{1,2}[.,]\d{1,2}", h):
                continue
            else:
                pending_name = h
        if not dishes:
            # fall back: same info via plain text scan
            text = soup.get_text("\n", strip=True)
            for line in text.splitlines():
                p = _price_from_text(line)
                if p and len(line) < 120:
                    dishes.append({"dish": line, "description": None, "price": p})
        if dishes:
            # Laut Website gilt die Karte "Monday - Friday" (Mo-Fr), nicht nur Mo-Do.
            for day in WEEKDAYS_DE:
                result["days"][day] = dishes
            result["status"] = "ok"
        else:
            result["error"] = "Keine Gerichte gefunden - Seitenstruktur ggf. geändert."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# 2. Lotus Asia — Wochenkarte als HTML-Text, klar nach Wochentag strukturiert
# ---------------------------------------------------------------------------
def scrape_lotus_asia():
    url = "https://www.lotusasia.shop/wochenkarte"
    result = {
        "id": "lotus_asia",
        "name": "Lotus Asia",
        "address": "Boschetsrieder Str. 75, 81379 München",
        "category": "walk",
        "travel_minutes": 8,
        "source_url": url,
        "format": "html",
        "days": {},
        "status": "error",
        "error": None,
    }
    # Zeilen, die eine Preis-Variante markieren ("Mit Hühnerfleisch", "Vegetarisch",
    # "Mit Tofu/Vegetarisch", ...) statt eines neuen Gerichtsnamens.
    variant_re = re.compile(r"^(mit\s|vegetarisch\b|tofu\b)", re.I)
    # Reine Würz-/Schärfe-Tags, die zwischen Beschreibung und Preis-Varianten stehen
    # und weder Beschreibung noch neues Gericht sind.
    spice_tags = {"mild", "mittelscharf", "scharf", "pikant"}

    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        lines = [l for l in text.splitlines() if l.strip()]

        current_day = None
        current_dish = None
        for line in lines:
            if line in WEEKDAYS_DE:
                current_day = line
                result["days"].setdefault(current_day, [])
                current_dish = None
                continue
            if current_day is None:
                continue

            # Nach der Speisekarte folgen auf der Seite Öffnungszeiten, Kontakt,
            # Impressum etc. - diese Abschnitte sind auf lotusasia.shop komplett
            # in GROSSBUCHSTABEN gesetzt, echte Gerichte/Beschreibungen dagegen nie.
            # Sobald wir eine solche Zeile sehen, ist die Karte zu Ende.
            letters = [c for c in line if c.isalpha()]
            if letters and all(c.isupper() for c in letters):
                break

            price = _price_from_text(line)
            stripped = line.strip()
            # Beschreibungen beginnen im Deutschen oft ebenfalls mit "mit ..."
            # (z.B. "mit Bohnen, Zucchini, ..."), das ist NICHT dasselbe wie die
            # kurze Preis-Variante "Mit Hühnerfleisch". Nur kurze, kommafreie
            # "Mit ..."-Zeilen (oder solche mit Preis dabei) zählen als Variante.
            starts_mit = bool(variant_re.match(stripped))
            short_and_simple = "," not in stripped and len(stripped) <= 40
            has_label = starts_mit and (short_and_simple or price is not None)
            is_variant = has_label or price is not None

            if is_variant:
                if current_dish is not None:
                    label = None
                    if has_label:
                        label = re.sub(r"^mit\s+", "", line.strip(), flags=re.I)
                        label = _PRICE_RE.sub("", label).strip(" :–-") or None
                    if price is None:
                        # Label und Preis stehen in getrennten Zeilen/Elementen
                        # (z.B. "Mit Hühnerfleisch" dann "9,5 €") - Label merken
                        # und auf die passende Preiszeile warten.
                        current_dish["_pending_label"] = label
                    else:
                        if label is None:
                            label = current_dish.pop("_pending_label", None)
                        else:
                            current_dish.pop("_pending_label", None)
                        current_dish["_variants"].append({"label": label, "price": price})
                continue

            if line.strip().lower() in spice_tags:
                if current_dish is not None:
                    current_dish["_spice"] = line.strip()
                continue

            # Kein Preis/Variante/Tag -> entweder neuer Gerichtsname oder Beschreibung
            if current_dish is None or current_dish.get("description") is not None:
                current_dish = {
                    "dish": line, "description": None, "price": None,
                    "_variants": [], "_spice": None, "_pending_label": None,
                }
                result["days"][current_day].append(current_dish)
            else:
                current_dish["description"] = line

        # Varianten zu einem Preis-String zusammenfassen, z.B.
        # "9,50 € (Hühnerfleisch) / 9,00 € (vegetarisch)"
        for day_dishes in result["days"].values():
            for dish in day_dishes:
                variants = dish.pop("_variants", [])
                dish.pop("_spice", None)
                dish.pop("_pending_label", None)
                priced = [v for v in variants if v["price"]]
                if priced:
                    dish["price"] = " / ".join(
                        f"{v['price']}" + (f" ({v['label']})" if v["label"] else "")
                        for v in priced
                    )

        result["days"] = {d: v for d, v in result["days"].items() if v}
        result["status"] = "ok" if result["days"] else "error"
        if result["status"] == "error":
            result["error"] = "Keine Gerichte gefunden - Seitenstruktur ggf. geändert."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# 3. Café Moccasola — Wochenkarte als PDF, Dateiname enthält Kalenderwoche
# ---------------------------------------------------------------------------
def scrape_moccasola():
    page_url = "https://www.moccasola.de/pages/moccasola-cafe"
    result = {
        "id": "moccasola",
        "name": "Café Moccasola",
        "address": "Zielstattstraße, 81379 München",
        "category": "walk",
        "travel_minutes": 10,
        "source_url": page_url,
        "format": "pdf",
        "days": {},
        "status": "error",
        "error": None,
    }
    try:
        import pdfplumber
        from io import BytesIO

        resp = _get(page_url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        pdf_link = None
        for a in soup.find_all("a", href=True):
            if "wochenkarte" in a.get_text(strip=True).lower() or re.search(r"KW\d+_\d{4}\.pdf", a["href"]):
                pdf_link = a["href"]
                break
        if not pdf_link:
            result["error"] = "Kein PDF-Link zur Wochenkarte gefunden."
            return result
        if pdf_link.startswith("//"):
            pdf_link = "https:" + pdf_link
        result["source_url"] = pdf_link

        # Manchmal verlinkt "Wochenkarte" statt einer PDF ein Ankündigungsbild
        # (z.B. Instagram-Grafik "Betriebsferien"). Das ist kein Scraper-Fehler,
        # sondern schlicht: diese Woche kein Mittagstisch.
        if not pdf_link.split("?")[0].lower().endswith(".pdf"):
            if _looks_closed(pdf_link):
                result["status"] = "closed"
                result["error"] = "Betriebsferien laut Website - diese Woche kein Mittagstisch."
            else:
                result["error"] = "Verlinkte Datei ist kein PDF (evtl. Ankündigung statt Speisekarte)."
            return result

        pdf_resp = _get(pdf_link)
        pdf_resp.raise_for_status()
        text_chunks = []
        with pdfplumber.open(BytesIO(pdf_resp.content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_chunks.append(t)
        text = "\n".join(text_chunks)

        current_day = None
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.capitalize() in WEEKDAYS_DE or line in WEEKDAYS_DE:
                current_day = line if line in WEEKDAYS_DE else line.capitalize()
                result["days"].setdefault(current_day, [])
                continue
            if current_day is None:
                continue
            price = _price_from_text(line.replace(".", ","))
            dish_line = re.sub(r"[-–—]\s*\d{1,2}[.,]\d{2}\s*€?", "", line).strip()
            if dish_line:
                result["days"][current_day].append({"dish": dish_line, "description": None, "price": price})

        result["days"] = {d: v for d, v in result["days"].items() if v}
        result["status"] = "ok" if result["days"] else "error"
        if result["status"] == "error":
            result["error"] = "PDF gefunden, aber keine Gerichte erkannt - Layout ggf. anders als erwartet."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# 4. Augustiner Schützengarten — echte Tageskarte (nur 1 Tag), PDF-Name
#    folgt dem Muster TT.MM.JJ_Tageskarte.pdf -> Link wird für heute
#    vorhergesagt, mit Fallback auf die Portfolio-Seite.
# ---------------------------------------------------------------------------
def scrape_augustiner():
    page_url = "https://augustiner-schuetzengarten.de/portfolio-item/tageskarte"
    result = {
        "id": "augustiner",
        "name": "Augustiner Schützengarten",
        "address": "Zielstattstraße, 81379 München",
        "category": "walk",
        "travel_minutes": 10,
        "source_url": page_url,
        "format": "pdf",
        "days": {},
        "status": "error",
        "error": None,
    }
    try:
        import pdfplumber
        from io import BytesIO

        today = date.today()
        weekday_name = WEEKDAYS_DE[today.weekday()] if today.weekday() < 5 else None
        pdf_url = None

        guess = today.strftime("%d.%m.%y") + "_Tageskarte.pdf"
        guess_url = f"https://augustiner-schuetzengarten.de/wp-content/uploads/{guess}"
        try:
            head = _get(guess_url)
            if head.status_code == 200 and head.headers.get("Content-Type", "").startswith("application/pdf"):
                pdf_url = guess_url
        except Exception:  # noqa: BLE001
            pass

        if not pdf_url:
            resp = _get(page_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                if a["href"].lower().endswith(".pdf"):
                    pdf_url = a["href"]
                    break

        if not pdf_url:
            result["error"] = "Kein PDF-Link zur Tageskarte gefunden."
            return result
        result["source_url"] = pdf_url

        pdf_resp = _get(pdf_url)
        pdf_resp.raise_for_status()
        text_chunks = []
        with pdfplumber.open(BytesIO(pdf_resp.content)) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                text_chunks.append(t)
        text = "\n".join(text_chunks)

        # Die Karte enthält die ganze Speisekarte (Vorspeiserl, Hauptsach, ...),
        # nicht nur den Mittagstisch. Uns interessiert ausschließlich das
        # "Tagesschmankerl" (Mo-Fr Mittagsangebot) - gezielt danach suchen,
        # statt die erste beliebige Preiszeile der Karte zu nehmen.
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        daily_special_headers = ("tagesschmankerl", "tagesangebot", "tagesempfehlung", "mittagstisch")
        next_section_re = re.compile(
            r"^(vorspeis|hauptsach|dessert|nachspeis|suppe|salat|getränk)", re.I
        )

        dishes = []
        start = None
        for i, l in enumerate(lines):
            if any(h in l.lower() for h in daily_special_headers):
                start = i + 1
                break

        # Zeitfenster-Zeile direkt unter der Überschrift, z.B.
        # "Montag bis Freitag von 11:30 – 15:00 Uhr" - gehört nicht zum Gerichtsnamen.
        time_window_re = re.compile(r"\d{1,2}[:.]\d{2}.*uhr", re.I)

        if start is not None:
            name_parts = []
            price = None
            for l in lines[start:start + 8]:
                if next_section_re.match(l):
                    break
                if time_window_re.search(l):
                    continue
                p = _price_from_text(l)
                if p:
                    leftover = _PRICE_RE.sub("", l).strip(" -–—")
                    if leftover:
                        name_parts.append(leftover)
                    price = p
                    break
                name_parts.append(l)

            if name_parts and price:
                dishes.append({
                    "dish": " ".join(name_parts),
                    "description": "Tagesangebot Mo–Fr, 11:30–15:00 Uhr",
                    "price": price,
                })

        if dishes and weekday_name:
            result["days"][weekday_name] = dishes
            result["status"] = "ok"
        else:
            result["error"] = "PDF gefunden, aber kein 'Tagesschmankerl' erkannt - Layout ggf. geändert."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


# ---------------------------------------------------------------------------
# 5. Alter Wirt Thalkirchen — eigener "Mittagsmenü"-Abschnitt (HTML-Text),
#    getrennt von der viel teureren regulären "Wochenkarte" auf derselben Seite.
#    Mit dem Rad, nicht zu Fuß erreichbar -> category "bike".
# ---------------------------------------------------------------------------
def scrape_alter_wirt():
    url = "https://www.alter-wirt-thalkirchen.de/"
    result = {
        "id": "alter_wirt",
        "name": "Alter Wirt Thalkirchen",
        "address": "Fraunbergstraße 8, 81379 München",
        "category": "bike",
        "travel_minutes": 8,
        "source_url": url + "#wochenkarte",
        "format": "html",
        "days": {},
        "status": "error",
        "error": None,
    }
    try:
        resp = _get(url)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        # Skript-/Style-Inhalte sind kein sichtbarer Text und sollen die
        # Zeilenerkennung nicht verfälschen.
        for tag in soup(["script", "style"]):
            tag.decompose()
        lines = [l.strip() for l in soup.get_text("\n", strip=True).splitlines() if l.strip()]

        # Die Seite kennzeichnet die beiden festen Mittagsmenü-Gerichte
        # explizit mit "Menu 1: ..." / "Menu 2: ...", gefolgt von je einer
        # Preis- und einer Beilagen-Zeile. Das ist ein viel zuverlässigerer
        # Anker als der umgebende Marketing-Text (WLAN-Hinweis, Lieferando-
        # Werbung, ...), der sich zwischen der "Mittagsmenü"-Überschrift und
        # den eigentlichen Gerichten schiebt und früher fälschlich als
        # Gerichtsname erfasst wurde.
        menu_label_re = re.compile(r"^Men[uü]\s*\d+\s*:\s*(.+)$", re.I)

        dishes = []
        for i, line in enumerate(lines):
            m = menu_label_re.match(line)
            if not m:
                continue
            dish_name = m.group(1).strip()
            price = _price_from_text(lines[i + 1]) if i + 1 < len(lines) else None
            description = None
            if i + 2 < len(lines):
                candidate = lines[i + 2]
                if not menu_label_re.match(candidate) and not _price_from_text(candidate):
                    description = candidate
            dishes.append({"dish": dish_name, "description": description, "price": price})

        if dishes:
            for day in WEEKDAYS_DE:
                result["days"][day] = dishes
            result["status"] = "ok"
        else:
            result["error"] = "Mittagsmenü-Abschnitt ('Menu 1: ...') nicht gefunden - Seitenstruktur ggf. anders als erwartet."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


SCRAPERS = [scrape_egghaus, scrape_lotus_asia, scrape_moccasola, scrape_augustiner, scrape_alter_wirt]


def main():
    restaurants = []
    for fn in SCRAPERS:
        print(f"Scraping {fn.__name__} ...")
        r = fn()
        status = r["status"]
        print(f"  -> {status}" + (f" ({r['error']})" if r.get("error") else ""))
        restaurants.append(r)

    # Erst zu Fuß erreichbare Restaurants, dann die mit dem Rad - innerhalb
    # jeder Gruppe alphabetisch. Das Frontend gruppiert ohnehin selbst nach
    # "category", diese Reihenfolge ist nur für eine lesbare menus.json.
    category_order = {"walk": 0, "bike": 1}
    restaurants.sort(key=lambda r: (category_order.get(r.get("category"), 9), r["name"].casefold()))

    data = {
        "generated_at": date.today().isoformat(),
        "restaurants": restaurants,
    }
    with open("menus.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\nGeschrieben: menus.json")


if __name__ == "__main__":
    main()
