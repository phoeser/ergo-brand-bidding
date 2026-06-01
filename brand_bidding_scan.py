#!/usr/bin/env python3
"""
ERGO Brand-Bidding-Scanner — kombiniert 3 DataForSEO-Quellen:

  1) serp/google/organic/live/advanced  -> Paid-Block:
        ECHTE Anzeigentexte, Beschreibung, Landingpage-URL, Live-Seitenposition
        (nur was aktuell live geschaltet ist)  -> source = "serp"
  2) serp/google/ads_advertisers/live/advanced -> Ads-Transparency:
        VOLLSTAENDIGE Bieterliste (wer bietet), approx_ads_count, verified,
        Transparency-Rang  -> source = "adv"
  3) serp/google/ads_search/live/advanced -> je Top-Anbieter (advertiser_id):
        Kreativ-Infos (Anzahl, Formate, aktiv-seit/bis, Vorschau, Transparency-Link)
        -> separate Datei ergo_brand_bidding_creatives.csv

Provider:  SERP_PROVIDER = "dataforseo" (3-Quellen) | "serper" (nur Paid-Text, Fallback)
Auth:      DATAFORSEO_B64 (Base64 login:password)  ODER  DATAFORSEO_LOGIN/PASSWORD
           bzw. SERPER_API_KEY.
"""

import os
import csv
import json
import time
import datetime as dt
from urllib.parse import urlparse

import requests

import build_report as br  # gemeinsames Zeilen-Schema (FIELDS / CREATIVE_FIELDS)


# --- Optionale lokale Secrets-Datei --------------------------------------

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
    "Marke allgemein": ["ergo versicherung", "ergo login", "ergo kundenportal",
                        "ergo direkt", "ergo krankenversicherung", "ergo rechtsschutz",
                        "ergo zahnversicherung"],
    "Zahnzusatz": ["ergo zahnzusatzversicherung", "ergo zahnzusatz", "zahnzusatzversicherung ergo",
                   "zahnzusatzversicherung", "zahnzusatzversicherung vergleich", "zahnzusatzversicherung test",
                   "beste zahnzusatzversicherung", "zahnzusatzversicherung ohne wartezeit"],
    "Sterbegeld": ["ergo sterbegeld", "ergo sterbegeldversicherung", "sterbegeldversicherung ergo",
                   "sterbegeldversicherung", "sterbegeldversicherung vergleich", "sterbegeldversicherung test",
                   "beste sterbegeldversicherung"],
}

DEVICES = ["desktop", "mobile"]
BRAND_TOKEN = "ergo"
LOCATION_NAME = "Germany"      # Serper
LOCATION_CODE = 2276           # DataForSEO Location Code DE
LANG = "de"
REQUEST_PAUSE = 1.0
TOP_ADS_SEARCH_PER_CLUSTER = 20   # fuer wieviele Anbieter je Cluster Kreative holen
ADS_SEARCH_CAP = 50               # globale Obergrenze (Kostenbremse)

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
    return BRAND_TOKEN in f"{headline or ''} {description or ''}".lower()


def _slug(s: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in (s or ""))
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def advertiser_key(domain: str, name: str) -> str:
    if domain:
        d = domain.lower()
        return d[4:] if d.startswith("www.") else d
    return "b:" + _slug(name)


# --- DataForSEO Auth + POST ----------------------------------------------

def _df_auth():
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
    if b64:
        headers["Authorization"] = "Basic " + b64
        return headers, None
    return headers, (login, password)


def _df_post(path: str, payload: list):
    headers, auth = _df_auth()
    resp = requests.post("https://api.dataforseo.com/v3/" + path,
                         auth=auth, headers=headers, json=payload, timeout=90)
    resp.raise_for_status()
    return resp.json()


def _df_items(data):
    try:
        return data["tasks"][0]["result"][0]["items"] or []
    except (KeyError, IndexError, TypeError):
        return []


# --- Quelle 1: Live-SERP Paid-Block --------------------------------------

def df_serp_paid(keyword: str, device: str) -> list:
    data = _df_post("serp/google/organic/live/advanced",
                    [{"keyword": keyword, "location_code": LOCATION_CODE,
                      "language_code": LANG, "device": device}])
    ads, seq = [], 0
    for item in _df_items(data):
        if item.get("type") != "paid":
            continue
        seq += 1
        dom = (item.get("domain") or "").strip().lower()
        if dom.startswith("www."):
            dom = dom[4:]
        url = (item.get("url") or "").strip()
        if not dom:
            dom = domain_of(url)
        ads.append({
            "rank": seq,
            "advertiser_domain": dom,
            "advertiser_name": (item.get("website_name") or dom),
            "headline": (item.get("title") or "").strip(),
            "description": (item.get("description") or "").strip(),
            "url": url,
        })
    return ads


# --- Quelle 2: Ads Advertisers (Bieterliste) -----------------------------

def df_ads_advertisers(keyword: str) -> list:
    data = _df_post("serp/google/ads_advertisers/live/advanced",
                    [{"keyword": keyword, "location_code": LOCATION_CODE}])
    out = []
    for item in _df_items(data):
        t = item.get("type")
        rank = item.get("rank_group") or item.get("rank_absolute") or 99
        if t == "ads_domain":
            dom = (item.get("domain") or "").strip().lower()
            if dom.startswith("www."):
                dom = dom[4:]
            if not dom:
                continue
            out.append({"name": dom, "domain": dom, "advertiser_id": "",
                        "approx_ads_count": "", "verified": "", "rank": rank})
        elif t in ("ads_advertiser", "ads_multi_account_advertiser"):
            name = (item.get("title") or "").strip()
            if not name:
                continue
            adv_id = item.get("advertiser_id") or ""
            verified = item.get("verified", "")
            if not adv_id and item.get("advertisers"):
                sub = item["advertisers"][0]
                adv_id = sub.get("advertiser_id", "")
                verified = sub.get("verified", verified)
            out.append({"name": name, "domain": "", "advertiser_id": adv_id,
                        "approx_ads_count": item.get("approx_ads_count", ""),
                        "verified": verified, "rank": rank})
    return out


# --- Quelle 3: Ads Search (Kreative je Anbieter, batched) ----------------

def df_ads_search(advertiser_ids: list) -> dict:
    """Liefert je advertiser_id eine Aggregation der Kreative."""
    agg = {}
    for i in range(0, len(advertiser_ids), 25):
        chunk = advertiser_ids[i:i + 25]
        try:
            data = _df_post("serp/google/ads_search/live/advanced",
                            [{"advertiser_ids": chunk, "location_code": LOCATION_CODE,
                              "depth": 100}])
        except Exception as e:
            print(f"  ! ads_search Fehler: {e}")
            continue
        for item in _df_items(data):
            if item.get("type") != "ads_search":
                continue
            aid = item.get("advertiser_id", "")
            if not aid:
                continue
            a = agg.setdefault(aid, {"n": 0, "formats": set(), "first": None,
                                     "last": None, "preview": "", "url": ""})
            a["n"] += 1
            if item.get("format"):
                a["formats"].add(item["format"])
            fs, ls = item.get("first_shown"), item.get("last_shown")
            if fs and (a["first"] is None or fs < a["first"]):
                a["first"] = fs
            if ls and (a["last"] is None or ls > a["last"]):
                a["last"] = ls
            if not a["preview"]:
                pi = item.get("preview_image") or {}
                if isinstance(pi, dict):
                    a["preview"] = pi.get("url", "") or ""
                elif isinstance(pi, list) and pi:
                    a["preview"] = (pi[0] or {}).get("url", "") or ""
            if not a["url"]:
                a["url"] = item.get("url", "") or ""
        time.sleep(REQUEST_PAUSE)
    return agg


# --- Hauptlauf: DataForSEO (3 Quellen) -----------------------------------

def _meta():
    now = dt.datetime.now(dt.timezone.utc)
    iso = now.isocalendar()
    return {
        "run_timestamp": now.isoformat(timespec="seconds"),
        "run_date": now.date().isoformat(),
        "iso_week": f"{iso.year}-KW{iso.week:02d}",
    }


def run_dataforseo():
    meta = _meta()
    rows = []
    adv_by_cluster = {}   # cluster -> {advertiser_id: (name, approx)}
    adv_name = {}         # advertiser_id -> name

    for cluster, keywords in KEYWORD_CLUSTERS.items():
        adv_by_cluster.setdefault(cluster, {})
        for kw in keywords:
            # 1) Live-SERP Paid (Desktop)
            try:
                serp = df_serp_paid(kw, "desktop")
            except Exception as e:
                print(f"  ! serp '{kw}': {e}")
                serp = []
            for a in serp:
                rows.append({**meta, "provider": "dataforseo", "device": "desktop",
                             "cluster": cluster, "keyword": kw, "source": "serp",
                             "advertiser_key": advertiser_key(a["advertiser_domain"], a["advertiser_name"]),
                             "advertiser_name": a["advertiser_name"],
                             "advertiser_domain": a["advertiser_domain"], "advertiser_id": "",
                             "rank": a["rank"], "approx_ads_count": "",
                             "headline": a["headline"], "description": a["description"],
                             "url": a["url"], "verified": "",
                             "brand_in_copy": brand_in_copy(a["headline"], a["description"])})
            time.sleep(REQUEST_PAUSE)

            # 2) Ads Advertisers (Bieterliste)
            try:
                advs = df_ads_advertisers(kw)
            except Exception as e:
                print(f"  ! advertisers '{kw}': {e}")
                advs = []
            for a in advs:
                key = advertiser_key(a["domain"], a["name"])
                rows.append({**meta, "provider": "dataforseo", "device": "desktop",
                             "cluster": cluster, "keyword": kw, "source": "adv",
                             "advertiser_key": key, "advertiser_name": a["name"],
                             "advertiser_domain": a["domain"], "advertiser_id": a["advertiser_id"],
                             "rank": a["rank"], "approx_ads_count": a["approx_ads_count"],
                             "headline": "", "description": "", "url": "",
                             "verified": a["verified"], "brand_in_copy": False})
                if a["advertiser_id"]:
                    adv_name[a["advertiser_id"]] = a["name"]
                    prev = adv_by_cluster[cluster].get(a["advertiser_id"], 0)
                    adv_by_cluster[cluster][a["advertiser_id"]] = max(prev, br._to_int(a["approx_ads_count"], 0))
            print(f"  {cluster} | {kw}: {len(serp)} live / {len(advs)} Bieter")
            time.sleep(REQUEST_PAUSE)

    # 3) Ads Search fuer Top-N je Cluster (dedupliziert, gedeckelt)
    pick = []
    for cluster, ids in adv_by_cluster.items():
        top = sorted(ids.items(), key=lambda x: x[1], reverse=True)[:TOP_ADS_SEARCH_PER_CLUSTER]
        pick += [aid for aid, _ in top]
    seen, uniq = set(), []
    for aid in pick:
        if aid not in seen:
            seen.add(aid)
            uniq.append(aid)
    uniq = uniq[:ADS_SEARCH_CAP]
    print(f"  ads_search fuer {len(uniq)} Anbieter ...")
    agg = df_ads_search(uniq) if uniq else {}

    creatives = []
    for aid, a in agg.items():
        creatives.append({**meta, "advertiser_id": aid,
                          "advertiser_name": adv_name.get(aid, ""),
                          "n_creatives": a["n"],
                          "formats": ",".join(sorted(a["formats"])),
                          "first_shown": a["first"] or "", "last_shown": a["last"] or "",
                          "sample_preview_url": a["preview"], "transparency_url": a["url"]})
    return rows, creatives


# --- Fallback: Serper (nur Paid-Text) ------------------------------------

def run_serper():
    meta = _meta()
    key = os.environ["SERPER_API_KEY"]
    rows = []
    for cluster, keywords in KEYWORD_CLUSTERS.items():
        for kw in keywords:
            for device in DEVICES:
                try:
                    resp = requests.post("https://google.serper.dev/search",
                                         headers={"X-API-KEY": key, "Content-Type": "application/json"},
                                         json={"q": kw, "gl": LANG, "hl": LANG,
                                               "location": LOCATION_NAME, "device": device, "num": 10},
                                         timeout=30)
                    resp.raise_for_status()
                    ads = resp.json().get("ads", []) or []
                except Exception as e:
                    print(f"  ! serper '{kw}' ({device}): {e}")
                    ads = []
                for i, ad in enumerate(ads, start=1):
                    title = ad.get("title", "")
                    desc = ad.get("description") or ad.get("snippet") or ""
                    disp = ad.get("displayed_link") or ad.get("source") or ad.get("link") or ""
                    link = ad.get("link") or ""
                    dom = domain_of(disp or link)
                    rows.append({**meta, "provider": "serper", "device": device,
                                 "cluster": cluster, "keyword": kw, "source": "serp",
                                 "advertiser_key": advertiser_key(dom, dom),
                                 "advertiser_name": disp or dom, "advertiser_domain": dom,
                                 "advertiser_id": "", "rank": ad.get("position", i),
                                 "approx_ads_count": "", "headline": title, "description": desc,
                                 "url": link, "verified": "",
                                 "brand_in_copy": brand_in_copy(title, desc)})
                time.sleep(REQUEST_PAUSE)
    return rows, []


# --- Output --------------------------------------------------------------

def write_output(rows, creatives):
    stamp = dt.date.today().isoformat()
    base = f"ergo_brand_bidding_{stamp}"
    with open(f"{base}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=br.FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in br.FIELDS})
    with open(f"{base}.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
    # Kreative an Master-Datei anhaengen
    cf = br.CREATIVES_FILE
    exists = os.path.exists(cf)
    if creatives:
        with open(cf, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=br.CREATIVE_FIELDS, extrasaction="ignore")
            if not exists:
                w.writeheader()
            for r in creatives:
                w.writerow({k: r.get(k, "") for k in br.CREATIVE_FIELDS})
    return f"{base}.csv"


if __name__ == "__main__":
    print(f"== ERGO Brand-Bidding-Scan | Provider: {PROVIDER} ==")
    if PROVIDER == "dataforseo":
        rows, creatives = run_dataforseo()
    elif PROVIDER == "serper":
        rows, creatives = run_serper()
    else:
        raise SystemExit(f"Unbekannter SERP_PROVIDER: {PROVIDER!r}")
    csv_path = write_output(rows, creatives)
    n_serp = sum(1 for r in rows if r["source"] == "serp")
    n_adv = len({r["advertiser_key"] for r in rows if r["source"] == "adv"})
    print("\n--- Zusammenfassung ---")
    print(f"Zeilen gesamt        : {len(rows)}")
    print(f"Live-Anzeigen (serp) : {n_serp}")
    print(f"Bieter (advertisers) : {n_adv}")
    print(f"Kreativ-Datensaetze  : {len(creatives)}")
    print(f"CSV: {csv_path}")
