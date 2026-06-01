#!/usr/bin/env python3
"""
ERGO Brand Bidding – Auswertung, Report & Zeitreihen (Standardlib-only).

Datenquellen (alle ueber DataForSEO, vom Scanner zusammengefuehrt):
  - source="adv"  : Google Ads Advertisers  -> WER bietet (vollstaendige Liste,
                    approx_ads_count, verified, Transparency-Rang)
  - source="serp" : Google Organic Paid-Block -> echte Anzeigentexte, Landingpage,
                    echte Seitenposition (nur was live geschaltet ist)
  - Kreative je Anbieter (ads_search) liegen separat in CREATIVES_FILE.

Aufgaben:
  1. Wochen-/Lauf-CSV einlesen, in Master-Historie anhaengen (dedupliziert).
  2. Snapshot des letzten Laufs: Intensitaets-Score, Praesenz, Ueber-ERGO,
     Live-Anzeigentexte + Landingpages, Kreativ-Infos je Bieter.
  3. Zeitreihen ueber ALLE Laeufe (Bieterdichte, ERGO-Sichtbarkeit,
     Top-Wettbewerber-Intensitaet, Trademark-Treffer).
  4. Markdown-Report schreiben + 5-Zeilen-Zusammenfassung.

Scoring-Formel (0-100) je Advertiser x Cluster (auf adv-Daten):
  Score = 100 * (0.5*Praesenz + 0.3*Positionsgewicht + 0.2*Persistenz)
"""

import os
import csv
import sys
import glob
import json
import datetime as dt
from collections import defaultdict

HISTORY_FILE = "ergo_brand_bidding_history.csv"
CREATIVES_FILE = "ergo_brand_bidding_creatives.csv"

FIELDS = [
    "run_timestamp", "run_date", "iso_week", "provider", "device",
    "cluster", "keyword", "source",
    "advertiser_key", "advertiser_name", "advertiser_domain", "advertiser_id",
    "rank", "approx_ads_count", "headline", "description", "url",
    "verified", "brand_in_copy",
]
CREATIVE_FIELDS = [
    "run_timestamp", "run_date", "iso_week", "advertiser_id", "advertiser_name",
    "n_creatives", "formats", "first_shown", "last_shown",
    "sample_preview_url", "transparency_url",
]

# ERGO-eigene Domains; zusaetzlich gilt jeder Advertiser-Name mit "ergo" als ERGO.
ERGO_OWN = {"ergo.de", "ergo-direkt.de", "ergodirekt.de", "dkv.de", "das.de"}

W_PRESENCE, W_POSITION, W_PERSIST = 0.5, 0.3, 0.2


# --- IO ------------------------------------------------------------------

def _to_bool(v):
    return str(v).strip().lower() in ("true", "1", "yes", "ja")


def _to_int(v, default=99):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def is_ergo(row):
    dom = (row.get("advertiser_domain") or "").lower()
    name = (row.get("advertiser_name") or "").lower()
    if dom in ERGO_OWN:
        return True
    # Wortgrenze grob: "ergo" als eigenstaendiges Token im Namen
    return any(tok == "ergo" or tok.startswith("ergo")
               for tok in name.replace("|", " ").replace("-", " ").split())


def pick_latest_week_csv():
    cands = sorted(
        p for p in glob.glob("ergo_brand_bidding_*.csv")
        if os.path.basename(p) not in (HISTORY_FILE, CREATIVES_FILE)
        and "creatives" not in os.path.basename(p)
    )
    if not cands:
        raise SystemExit("Keine Lauf-CSV (ergo_brand_bidding_*.csv) gefunden.")
    return cands[-1]


def _row_key(r):
    return (
        r.get("run_timestamp", ""), r.get("source", ""), r.get("device", ""),
        r.get("keyword", ""), r.get("advertiser_key", ""), str(r.get("rank", "")),
    )


def append_history(week_rows):
    existing = read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else []
    # Alte Schema-Zeilen ohne gueltige Quelle verwerfen (sauberer Zeitreihen-Start)
    existing = [r for r in existing if r.get("source") in ("adv", "serp")]
    seen = {_row_key(r) for r in existing}
    added = [r for r in week_rows if _row_key(r) not in seen]
    all_rows = existing + added
    with open(HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return all_rows, len(added)


def read_creatives_latest():
    """Pro advertiser_id die Kreativ-Infos des juengsten Laufs."""
    if not os.path.exists(CREATIVES_FILE):
        return {}
    rows = read_csv(CREATIVES_FILE)
    out = {}
    for r in sorted(rows, key=lambda x: x.get("run_timestamp", "")):
        out[r.get("advertiser_id", "")] = r  # spaetere Laeufe ueberschreiben
    out.pop("", None)
    return out


# --- Lauf-/Wochenlogik ---------------------------------------------------

def latest_run(history):
    return max((r["run_timestamp"] for r in history), default="")


def runs_sorted(history):
    return sorted({r["run_timestamp"] for r in history if r["run_timestamp"]})


def last_n_weeks(history, current_week, n=4):
    weeks = sorted({r["iso_week"] for r in history})
    if current_week not in weeks:
        weeks.append(current_week)
        weeks = sorted(set(weeks))
    idx = weeks.index(current_week)
    return weeks[max(0, idx - n + 1): idx + 1]


def prev_run(history, current_run):
    rs = sorted({r["run_timestamp"] for r in history if r["run_timestamp"] < current_run})
    return rs[-1] if rs else None


# --- Scoring-Snapshot (letzter Lauf, adv-Daten) --------------------------

def ergo_rank_by_keyword(rows, source):
    """Beste (kleinste) ERGO-Position je Keyword fuer die gegebene Quelle."""
    out = {}
    for r in rows:
        if r.get("source") != source:
            continue
        if is_ergo(r):
            kw = r["keyword"]
            rk = _to_int(r["rank"])
            if kw not in out or rk < out[kw]:
                out[kw] = rk
    return out


def score_cluster(cur_rows, history, cluster, current_week, creatives=None):
    creatives = creatives or {}
    adv = [r for r in cur_rows if r["cluster"] == cluster and r.get("source") == "adv"]
    serp = [r for r in cur_rows if r["cluster"] == cluster and r.get("source") == "serp"]

    keywords = {r["keyword"] for r in adv}
    total_kw = len(keywords) or 1

    ergo_adv = ergo_rank_by_keyword(
        [r for r in cur_rows if r["cluster"] == cluster], "adv")
    ergo_serp = ergo_rank_by_keyword(
        [r for r in cur_rows if r["cluster"] == cluster], "serp")

    # Persistenz: in wievielen der letzten 4 Wochen war der Advertiser im Cluster
    persist_weeks = last_n_weeks(history, current_week, 4)
    n_persist = len(persist_weeks) or 1
    weeks_present = defaultdict(set)
    for r in history:
        if (r["cluster"] == cluster and r.get("source") == "adv"
                and r["iso_week"] in persist_weeks):
            weeks_present[r["advertiser_key"]].add(r["iso_week"])

    # Live-Details je Domain (aus serp)
    serp_by_dom = defaultdict(list)
    for r in serp:
        if r.get("advertiser_domain"):
            serp_by_dom[r["advertiser_domain"]].append(r)

    by_key = defaultdict(list)
    for r in adv:
        by_key[r["advertiser_key"]].append(r)

    scored = []
    for key, drows in by_key.items():
        if not key:
            continue
        name = drows[0].get("advertiser_name") or key
        dom = drows[0].get("advertiser_domain") or ""
        adv_id = next((d.get("advertiser_id") for d in drows if d.get("advertiser_id")), "")
        ergo_flag = is_ergo(drows[0])

        kw_rank = {}
        for r in drows:
            kw = r["keyword"]
            rk = _to_int(r["rank"])
            if kw not in kw_rank or rk < kw_rank[kw]:
                kw_rank[kw] = rk
        presence = len(kw_rank) / total_kw
        ranks = list(kw_rank.values())
        position = sum(1.0 / max(1, rk) for rk in ranks) / len(ranks)
        persistence = len(weeks_present.get(key, set())) / n_persist
        score = 100 * (W_PRESENCE * presence + W_POSITION * position + W_PERSIST * persistence)
        approx = max((_to_int(r.get("approx_ads_count"), 0) for r in drows), default=0)

        # Ueber ERGO (Transparency-Rang)
        common = [kw for kw in kw_rank if kw in ergo_adv]
        above = [kw for kw in common if kw_rank[kw] < ergo_adv[kw]]
        above_pct = round(100 * len(above) / len(common), 0) if common else None

        # Live-Anzeigen (echter Text + Landingpage + Live-Position vs ERGO)
        live_ads = []
        for r in serp_by_dom.get(dom, []):
            kw = r["keyword"]
            rk = _to_int(r["rank"])
            er = ergo_serp.get(kw)
            live_ads.append({
                "keyword": kw, "rank": rk, "ergo_rank": er,
                "above_ergo": (er is not None and rk < er),
                "headline": r.get("headline", "") or "",
                "description": r.get("description", "") or "",
                "url": r.get("url", "") or "",
            })
        live_ads.sort(key=lambda a: (a["rank"], a["keyword"]))

        cre = creatives.get(adv_id) if adv_id else None

        scored.append({
            "key": key,
            "domain": dom,
            "name": name,
            "advertiser_id": adv_id,
            "score": round(score, 1),
            "presence_pct": round(100 * presence, 0),
            "best_rank": min(ranks),
            "avg_rank": round(sum(ranks) / len(ranks), 1),
            "approx_ads": approx,
            "verified": _to_bool(drows[0].get("verified")),
            "persistence_wk": f"{len(weeks_present.get(key, set()))}/{n_persist}",
            "is_ergo": ergo_flag,
            "above_ergo_n": len(above),
            "common_n": len(common),
            "above_ergo_pct": above_pct,
            "live_ads": live_ads,
            "creatives": ({
                "n": _to_int(cre.get("n_creatives"), 0),
                "formats": cre.get("formats", ""),
                "first_shown": cre.get("first_shown", ""),
                "last_shown": cre.get("last_shown", ""),
                "preview": cre.get("sample_preview_url", ""),
                "transparency": cre.get("transparency_url", ""),
            } if cre else None),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def delta_bidders(cur_rows, history, cluster, current_run):
    pr = prev_run(history, current_run)
    cur = {r["advertiser_key"] for r in cur_rows
           if r["cluster"] == cluster and r.get("source") == "adv" and r["advertiser_key"]}
    if not pr:
        return sorted(cur), [], None
    prev = {r["advertiser_key"] for r in history
            if r["cluster"] == cluster and r.get("source") == "adv"
            and r["run_timestamp"] == pr and r["advertiser_key"]}
    return sorted(cur - prev), sorted(prev - cur), pr


def trademark_candidates(cur_rows):
    out, seen = [], set()
    for r in cur_rows:
        if r.get("source") != "serp" or not _to_bool(r.get("brand_in_copy")):
            continue
        key = (r["advertiser_key"], r["keyword"], r.get("headline", ""))
        if key in seen or is_ergo(r):
            continue
        seen.add(key)
        out.append(r)
    return out


# --- Zeitreihen ueber alle Laeufe ----------------------------------------

def time_series(history):
    runs = runs_sorted(history)
    clusters = list(dict.fromkeys(r["cluster"] for r in history))
    density = {cl: [] for cl in clusters}
    ergo_presence, ergo_avg_rank, trademark = [], [], []

    # Top-Bieter ueber alle Laeufe (nach juengstem Score-Proxy) fuer Intensitaetsreihe
    latest = runs[-1] if runs else ""
    proxy = defaultdict(float)
    for r in history:
        if r["run_timestamp"] == latest and r.get("source") == "adv" and not is_ergo(r):
            proxy[r["advertiser_key"]] += 1.0 / max(1, _to_int(r["rank"]))
    top_keys = [k for k, _ in sorted(proxy.items(), key=lambda x: x[1], reverse=True)[:6]]
    bidder_intensity = {k: [] for k in top_keys}
    bidder_names = {}

    for run in runs:
        rr = [r for r in history if r["run_timestamp"] == run]
        adv = [r for r in rr if r.get("source") == "adv"]
        serp = [r for r in rr if r.get("source") == "serp"]
        for cl in clusters:
            comp = {r["advertiser_key"] for r in adv
                    if r["cluster"] == cl and r["advertiser_key"] and not is_ergo(r)}
            density[cl].append(len(comp))

        kws = {r["keyword"] for r in adv}
        ekws = {r["keyword"] for r in adv if is_ergo(r)}
        ergo_presence.append(round(100 * len(ekws) / len(kws), 0) if kws else 0)
        eranks = [_to_int(r["rank"]) for r in adv if is_ergo(r)]
        ergo_avg_rank.append(round(sum(eranks) / len(eranks), 1) if eranks else None)

        tmseen = set()
        for r in serp:
            if _to_bool(r.get("brand_in_copy")) and not is_ergo(r):
                tmseen.add((r["advertiser_key"], r["keyword"], r.get("headline", "")))
        trademark.append(len(tmseen))

        # Intensitaet je Top-Bieter (Praesenz x Position, ueber alle Cluster)
        total_kw = len(kws) or 1
        for k in top_keys:
            krows = [r for r in adv if r["advertiser_key"] == k]
            if krows:
                bidder_names.setdefault(k, krows[0].get("advertiser_name") or k)
                kw_rank = {}
                for r in krows:
                    kw = r["keyword"]
                    rk = _to_int(r["rank"])
                    if kw not in kw_rank or rk < kw_rank[kw]:
                        kw_rank[kw] = rk
                pres = len(kw_rank) / total_kw
                pos = sum(1.0 / max(1, v) for v in kw_rank.values()) / len(kw_rank)
                bidder_intensity[k].append(round(100 * (0.6 * pres + 0.4 * pos), 1))
            else:
                bidder_intensity[k].append(0)

    return {
        "runs": runs,
        "clusters": clusters,
        "density": density,
        "ergo_presence": ergo_presence,
        "ergo_avg_rank": ergo_avg_rank,
        "trademark": trademark,
        "bidder_intensity": bidder_intensity,
        "bidder_names": {k: bidder_names.get(k, k) for k in top_keys},
    }


# --- Report --------------------------------------------------------------

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _fmt_above(s):
    if s.get("is_ergo"):
        return "–"
    if s.get("common_n"):
        return f"{s['above_ergo_n']}/{s['common_n']} ({int(s['above_ergo_pct'])}%)"
    return "–"


def build_report(week_csv):
    week_rows = read_csv(week_csv)
    if not week_rows:
        raise SystemExit(f"{week_csv} enthaelt keine Zeilen.")
    history, n_added = append_history(week_rows)
    creatives = read_creatives_latest()

    current_run = latest_run(history)
    cur_rows = [r for r in history if r["run_timestamp"] == current_run]
    current_week = cur_rows[0]["iso_week"]
    run_date = cur_rows[0]["run_date"]
    provider = cur_rows[0]["provider"]
    clusters = list(dict.fromkeys(r["cluster"] for r in cur_rows))

    adv_rows = [r for r in cur_rows if r.get("source") == "adv"]
    serp_rows = [r for r in cur_rows if r.get("source") == "serp"]
    n_adv = len({r["advertiser_key"] for r in adv_rows if r["advertiser_key"]})
    n_serp = len(serp_rows)
    tm = trademark_candidates(cur_rows)

    lines = [f"# ERGO Brand Bidding – {current_week}", ""]
    lines.append(f"*Lauf: {run_date} ({current_run}) · Provider: {provider} · "
                 f"Bieter (Transparency): {n_adv} · Live-Anzeigen: {n_serp} · "
                 f"Trademark-Pruefkandidaten: {len(tm)}*")
    lines.append("")
    lines.append("> Datenquellen: **Ads Advertisers** (vollstaendige Bieterliste) · "
                 "**Live-SERP** (echte Anzeigentexte/Landingpages/Position) · "
                 "**ads_search** (Kreativ-Infos je Anbieter).")
    lines.append("")

    cluster_scores = {}
    for cl in clusters:
        scored = score_cluster(cur_rows, history, cl, current_week, creatives)
        cluster_scores[cl] = scored
        lines.append(f"## {cl} – Top-20 Bieter")
        lines.append("")
        if scored:
            top = scored[:20]
            rows = [[
                i + 1,
                (s["name"] or s["domain"]) + (" (ERGO)" if s["is_ergo"] else ""),
                s["score"], f'{int(s["presence_pct"])}%', s["best_rank"],
                _fmt_above(s), s["approx_ads"] or "–", s["persistence_wk"],
            ] for i, s in enumerate(top)]
            lines.append(md_table(
                ["#", "Bieter", "Score", "Praesenz", "Best-Rang",
                 "Ueber ERGO", "~Ads", "Persistenz"], rows))
        else:
            lines.append("*Keine Bieter erfasst.*")
        lines.append("")

        detail = [s for s in scored if not s["is_ergo"] and s["live_ads"]][:5]
        if detail:
            lines.append(f"### {cl} – Live-Anzeigentexte & Landingpages")
            lines.append("")
            for s in detail:
                lines.append(f"**{s['name'] or s['domain']}** — Ueber ERGO: {_fmt_above(s)}")
                for a in s["live_ads"][:6]:
                    er = a["ergo_rank"]
                    pos = (f"Pos {a['rank']} vs ERGO {er}" if er is not None
                           else f"Pos {a['rank']} (ERGO nicht live)")
                    lines.append(f"- *{a['keyword']}* — {pos} — „{(a['headline'] or '(kein Titel)')[:90]}“")
                    if a["description"]:
                        lines.append(f"  {a['description'][:140]}")
                    if a["url"]:
                        lines.append(f"  Landingpage: {a['url']}")
                lines.append("")

    # Veraenderungen ggue. Vorlauf
    lines.append("## Veraenderungen ggue. Vorlauf")
    lines.append("")
    any_prev = None
    for cl in clusters:
        new, gone, pr = delta_bidders(cur_rows, history, cl, current_run)
        any_prev = pr
        if pr is None:
            lines.append(f"- **{cl}**: kein Vorlauf (Basislauf).")
        else:
            lines.append(f"- **{cl}**: NEU: {', '.join(new) if new else '–'} · "
                         f"WEG: {', '.join(gone) if gone else '–'}")
    lines.append("")

    lines.append("## Trademark-Pruefkandidaten")
    lines.append("")
    lines.append("> Hinweis zur menschlichen/juristischen Pruefung – **keine** rechtliche "
                 "Bewertung. Markenname \"ERGO\" steht im Anzeigentitel/-text.")
    lines.append("")
    if tm:
        rows = [[r["advertiser_name"] or r["advertiser_domain"], r["cluster"],
                 r["keyword"], (r.get("headline") or "")[:80]] for r in tm]
        lines.append(md_table(["Bieter", "Cluster", "Keyword", "Anzeigentitel"], rows))
    else:
        lines.append("*Keine Live-Anzeige mit Markenname im Text.*")
    lines.append("")

    lines.append("---")
    lines.append(f"*Automatisch erzeugt am {dt.date.today().isoformat()}. "
                 f"Scoring: 0,5·Praesenz + 0,3·Position + 0,2·Persistenz (x100).*")

    out_path = f"ERGO_Brand_Bidding_{current_week}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    summary = build_summary(current_week, n_adv, n_serp, tm, cluster_scores, n_added)
    return out_path, summary


def build_summary(week, n_adv, n_serp, tm, cluster_scores, n_added):
    tops = []
    for cl, scored in cluster_scores.items():
        comp = [s for s in scored if not s["is_ergo"]]
        if comp:
            tops.append(f"{cl}: {comp[0]['name'] or comp[0]['domain']} ({comp[0]['score']})")
    return "\n".join([
        f"ERGO Brand Bidding {week}: {n_adv} Bieter (Transparency), {n_serp} Live-Anzeigen.",
        f"{n_added} neue Zeilen in der Historie.",
        ("Top-Wettbewerber je Cluster: " + " · ".join(tops) + "."
         if tops else "Keine Wettbewerber erfasst."),
        f"Trademark-Pruefkandidaten: {len(tm)} (Hinweis, keine rechtl. Bewertung).",
        f"Report: ERGO_Brand_Bidding_{week}.md",
    ])


if __name__ == "__main__":
    week_csv = sys.argv[1] if len(sys.argv) > 1 else pick_latest_week_csv()
    print(f"== Report aus {week_csv} ==")
    out_path, summary = build_report(week_csv)
    print(f"\nReport geschrieben: {out_path}\n")
    print("--- 5-Zeilen-Zusammenfassung ---")
    print(summary)
