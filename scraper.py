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
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DPM-Mittagsmenu/1.0; "
    "internal office tool; contact: philipp.hoffmann@produktmacher.com)"
}


def _get(url, **kwargs):
    return requests.get(url, headers=HEADERS, timeout=15, **kwargs)


def _price_from_text(text):
    m = re.search(r"(\d{1,2}[.,]\d{2})\s*€", text)
    return m.group(1).replace(".", ",") + " €" if m else None


# ---------------------------------------------------------------------------
# 1. Egg Haus Café — feste Wochenkarte als HTML-Text, gilt Mo-Fr identisch
# ---------------------------------------------------------------------------
def scrape_egghaus():
    url = "https://egghauscafe.de/lunch-menu/"
    result = {
        "id": "egghaus",
        "name": "Egg Haus Café",
        "address": "Hofmannstraße 23, 81379 München",
        "walk_minutes": 3,
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
            price = _price_from_text(h.replace(",", "."))
            if re.fullmatch(r"\d{1,2}[.,]\d{2}\s*€?", h.strip()):
                if pending_name:
                    dishes.append({"dish": pending_name, "description": None, "price": h.strip()})
                    pending_name = None
            elif "mittagsmenü" in h.lower() or "business lunch" in h.lower() or re.search(r"\d{1,2}\.\d{2}", h):
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
            for day in WEEKDAYS_DE[:4]:  # Mo-Do laut Website, Fr geschlossen
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
        "walk_minutes": 8,
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
            price = _price_from_text(line.replace(".", ","))
            if price and current_dish:
                current_dish["price"] = price
            elif len(line) < 60 and not price and current_dish is None:
                current_dish = {"dish": line, "description": None, "price": None}
                result["days"][current_day].append(current_dish)
            elif current_dish and current_dish.get("description") is None and not price:
                current_dish["description"] = line

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
        "walk_minutes": 10,
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
        "walk_minutes": 10,
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

        dishes = []
        for line in text.splitlines():
            line = line.strip()
            price = _price_from_text(line.replace(".", ","))
            if price and 3 < len(line) < 120:
                dish_line = re.sub(r"[-–—]?\s*\d{1,2}[.,]\d{2}\s*€?\s*$", "", line).strip()
                if dish_line:
                    dishes.append({"dish": dish_line, "description": None, "price": price})

        if dishes and weekday_name:
            result["days"][weekday_name] = dishes
            result["status"] = "ok"
        else:
            result["error"] = "PDF gefunden, aber keine Gerichte erkannt."
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


SCRAPERS = [scrape_egghaus, scrape_lotus_asia, scrape_moccasola, scrape_augustiner]


def main():
    restaurants = []
    for fn in SCRAPERS:
        print(f"Scraping {fn.__name__} ...")
        r = fn()
        status = r["status"]
        print(f"  -> {status}" + (f" ({r['error']})" if r.get("error") else ""))
        restaurants.append(r)

    data = {
        "generated_at": date.today().isoformat(),
        "restaurants": restaurants,
    }
    with open("menus.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\nGeschrieben: menus.json")


if __name__ == "__main__":
    main()
