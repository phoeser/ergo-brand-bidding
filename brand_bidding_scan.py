#!/usr/bin/env python3
"""
ERGO Brand-Bidding-Scanner — provider-agnostisch (Serper / DataForSEO)

Fragt definierte Keyword-Cluster bei Google (DE) ab, extrahiert die bezahlten
Anzeigen (Brand Bidding) und schreibt sie normalisiert als CSV + JSON.
Die Auswertung (Intensitaet, Trademark-Flags, Report) uebernimmt der
agentische Lauf in Cowork.

Provider-Umschaltung:  SERP_PROVIDER = "serper" | "dataforseo"
Keys entweder als Environment-Variablen ODER in einer Datei
"serp_secrets.env" (KEY=VALUE pro Zeile) neben diesem Script.
"""

import os
import csv
import json
import time
import datetime as dt
from urllib.parse import urlparse

import requests


# --- Optionale lokale Secrets-Datei (kein Extra-Paket noetig) -------------

def _load_local_env(path="serp_secrets.env"):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_local_env()


# --- Konfiguration -------------------------------------------------------

KEYWORD_CLUSTERS = {
    "Marke allgemein": ["ergo", "ergo versicherung", "ergo login", "ergo kundenportal",
                        "ergo direkt", "ergo krankenversicherung", "ergo rechtsschutz"],
    "Zahnzusatz": ["ergo zahnzusatzversicherung", "ergo zahnzusatz", "zahnzusatzversicherung ergo",
                   "zahnzusatzversicherung", "zahnzusatzversicherung vergleich", "zahnzusatzversicherung test",
                   "beste zahnzusatzversicherung", "zahnzusatzversicherung ohne wartezeit"],
    "Sterbegeld": ["ergo sterbegeld", "ergo sterbegeldversicherung", "sterbegeldversicherung ergo",
                   "sterbegeldversicherung", "sterbegeldversicherung vergleich", "sterbegeldversicherung test",
                   "beste sterbegeldversicherung"],
}

DEVICES = ["desktop", "mobile"]
BRAND_TOKEN = "ergo"          # Heuristik fuer Trademark-Flag im Anzeigentext
LOCATION_NAME = "Germany"     # Serper
LOCATION_CODE = 2276          # DataForSEO Location Code DE
LANG = "de"
REQUEST_PAUSE = 1.5           # Sekunden zwischen Calls (hoeflich bleiben)

PROVIDER = os.getenv("SERP_PROVIDER", "serper").lower()


# --- Helfer --------------------------------------------------------------

def domain_of(url_or_link: str) -> str:
    if not url_or_link:
        return ""
    s = url_or_link.strip()
    if not s.startswith("http"):
        s = "https://" + s
    try:
        net = urlparse(s).netloc.lower()
        return net[4:] if net.startswith("www.") else net
    except Exception:
        return url_or_link.lower()


def brand_in_copy(headline: str, description: str) -> bool:
    text = f"{headline or ''} {description or ''}".lower()
    return BRAND_TOKEN in text


# --- Provider: Serper ----------------------------------------------------

def fetch_serper(keyword: str, device: str) -> list:
    key = os.environ["SERPER_API_KEY"]
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json={
            "q": keyword,
            "gl": LANG,
            "hl": LANG,
            "location": LOCATION_NAME,
            "device": device,
            "num": 10,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    ads = []
    for i, ad in enumerate(data.get("ads", []) or [], start=1):
        title = ad.get("title", "")
        desc = ad.get("description") or ad.get("snippet") or ""
        disp = ad.get("displayed_link") or ad.get("source") or ad.get("link") or ""
        link = ad.get("link") or ""
        ads.append({
            "rank": ad.get("position", i),
            "block": ad.get("block_position", ""),
            "advertiser_domain": domain_of(disp or link),
            "advertiser_display": disp,
            "headline": title,
            "description": desc,
            "url": link,
        })
    return ads


# --- Provider: DataForSEO ------------------------------------------------

def fetch_dataforseo(keyword: str, device: str) -> list:
    """Google Organic SERP (Advanced) -> Paid-Block = die echten Textanzeigen.
    Liefert pro Anzeige: Titel (Headline), Beschreibung, Landingpage-URL, Domain,
    Werbetreibenden-Name und die Position im Anzeigenblock (1 = oberste Anzeige).
    So koennen wir Anzeigentexte, Landingpages UND Position ggue. ERGO auswerten."""
    import base64 as _b64
    login = os.environ.get("DATAFORSEO_LOGIN", "").strip()
    password = os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    b64 = os.environ.get("DATAFORSEO_B64", "").strip()
    if not b64 and password:
        try:
            if ":" in _b64.b64decode(password, validate=True).decode("utf-8", "ignore"):
                b64 = password
        except Exception:
            pass
    headers = {"Content-Type": "application/json"}
    auth = None
    if b64:
        headers["Authorization"] = "Basic " + b64
    else:
        auth = (login, password)
    resp = requests.post(
        "https://api.dataforseo.com/v3/serp/google/organic/live/advanced",
        auth=auth,
        headers=headers,
        json=[{
            "keyword": keyword,
            "location_code": LOCATION_CODE,
            "language_code": LANG,
            "device": device,
        }],
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    try:
        items = data["tasks"][0]["result"][0]["items"] or []
    except (KeyError, IndexError, TypeError):
        items = []
    ads = []
    seq = 0  # Position innerhalb des Paid-Blocks (1 = oberste Anzeige)
    for item in items:
        if item.get("type") != "paid":
            continue
        seq += 1
        dom = (item.get("domain") or "").strip().lower()
        if dom.startswith("www."):
            dom = dom[4:]
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or "").strip()
        url = (item.get("url") or "").strip()
        if not dom:
            dom = domain_of(url)
        ads.append({
            "rank": seq,
            "block": item.get("rank_absolute", ""),
            "advertiser_domain": dom,
            "advertiser_display": (item.get("website_name") or dom),
            "headline": title,
            "description": desc,
            "url": url,
        })
    return ads


PROVIDERS = {"serper": fetch_serper, "dataforseo": fetch_dataforseo}


# --- Hauptlauf -----------------------------------------------------------

def run() -> list:
    if PROVIDER not in PROVIDERS:
        raise SystemExit(f"Unbekannter SERP_PROVIDER: {PROVIDER!r} (erlaubt: serper, dataforseo)")
    fetch = PROVIDERS[PROVIDER]

    now = dt.datetime.now(dt.timezone.utc)
    run_ts = now.isoformat(timespec="seconds")
    run_date = now.date().isoformat()
    iso = now.isocalendar()
    iso_week = f"{iso.year}-KW{iso.week:02d}"

    rows = []
    for cluster, keywords in KEYWORD_CLUSTERS.items():
        for kw in keywords:
            for device in (["desktop"] if PROVIDER == "dataforseo" else DEVICES):
                try:
                    ads = fetch(kw, device)
                except Exception as e:
                    print(f"  ! Fehler bei '{kw}' ({device}, {PROVIDER}): {e}")
                    ads = []
                for ad in ads:
                    rows.append({
                        "run_timestamp": run_ts,
                        "run_date": run_date,
                        "iso_week": iso_week,
                        "provider": PROVIDER,
                        "device": device,
                        "cluster": cluster,
                        "keyword": kw,
                        "rank": ad["rank"],
                        "block": ad["block"],
                        "advertiser_domain": ad["advertiser_domain"],
                        "advertiser_display": ad["advertiser_display"],
                        "headline": ad["headline"],
                        "description": ad["description"],
                        "url": ad["url"],
                        "brand_in_copy": brand_in_copy(ad["headline"], ad["description"]),
                    })
                print(f"  {cluster} | {kw} | {device}: {len(ads)} Anzeige(n)")
                time.sleep(REQUEST_PAUSE)
    return rows


def write_output(rows: list):
    stamp = dt.date.today().isoformat()
    base = f"ergo_brand_bidding_{stamp}"
    fields = [
        "run_timestamp", "run_date", "iso_week", "provider", "device",
        "cluster", "keyword", "rank", "block", "advertiser_domain",
        "advertiser_display", "headline", "description", "url", "brand_in_copy",
    ]
    csv_path = f"{base}.csv"
    json_path = f"{base}.json"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    return csv_path, json_path


if __name__ == "__main__":
    print(f"== ERGO Brand-Bidding-Scan | Provider: {PROVIDER} ==")
    rows = run()
    csv_path, json_path = write_output(rows)
    n_ads = len(rows)
    n_adv = len({r["advertiser_domain"] for r in rows})
    n_flags = sum(1 for r in rows if r["brand_in_copy"])
    print("\n--- Zusammenfassung ---")
    print(f"Anzeigen-Treffer gesamt        : {n_ads}")
    print(f"Unique Advertiser              : {n_adv}")
    print(f"Trademark-Flags (Marke im Text): {n_flags}")
    print(f"CSV : {csv_path}")
    print(f"JSON: {json_path}")
