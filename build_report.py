#!/usr/bin/env python3
"""
ERGO Brand Bidding – Auswertung, Report & Zeitreihen (Standardlib-only).

HAUPTSIGNAL = Live-Suche (Paid-Block): WER taucht wirklich als Anzeige auf,
wenn jemand den Begriff googelt -> echtes Brand-Bidding (Text, Landingpage,
Position ggue. ERGO).  source = "serp"

ZUSATZ-LAYER = Ads-Transparency (ads_advertisers): Werbetreibende, deren NAME
zum Begriff passt -> "Markenraum / Namensvettern" (KEIN Nachweis von
Brand-Bidding!).  source = "adv".  Kreative je Anbieter (ads_search) in
CREATIVES_FILE.
"""

import os
import csv
import sys
import glob
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

ERGO_OWN = {"ergo.de", "ergo-direkt.de", "ergodirekt.de", "dkv.de", "das.de"}
# Enge Namensliste fuer ERGO-eigene Konten/Agenturen (kein "ergo*"-Wildcard!)
ERGO_NAME_HINTS = (
    "ergo versicherung", "ergo group", "ergo direkt", "ergo direct",
    "ergo deutschland", "ergo vorsorge", "ergo krankenversicherung",
    "ergo lebensversicherung", "ergo reiseversicherung", "ergo beratung",
    "dkv deutsche", "d.a.s", "das rechtsschutz",
)

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
    """Praezise: ERGO nur bei eigener Domain oder eindeutigem Marken-Namen."""
    dom = (row.get("advertiser_domain") or "").lower()
    if dom in ERGO_OWN:
        return True
    name = (row.get("advertiser_name") or "").lower().strip()
    if name == "ergo":
        return True
    return any(h in name for h in ERGO_NAME_HINTS)


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
    if not os.path.exists(CREATIVES_FILE):
        return {}
    out = {}
    for r in sorted(read_csv(CREATIVES_FILE), key=lambda x: x.get("run_timestamp", "")):
        out[r.get("advertiser_id", "")] = r
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
    rs = sorted({r["run_timestamp"] for r in history
                 if r["run_timestamp"] < current_run and r.get("source") == "serp"})
    return rs[-1] if rs else None


# --- HAUPTSIGNAL: Live-Bieter (serp) -------------------------------------

def _ergo_live_rank(serp_rows):
    out = {}
    for r in serp_rows:
        if is_ergo(r):
            kw = r["keyword"]
            rk = _to_int(r["rank"])
            if kw not in out or rk < out[kw]:
                out[kw] = rk
    return out


def live_bidders(cur_rows, history, cluster, current_week):
    serp = [r for r in cur_rows if r["cluster"] == cluster and r.get("source") == "serp"]
    keywords = {r["keyword"] for r in serp}
    total_kw = len(keywords) or 1
    ergo_rk = _ergo_live_rank(serp)

    persist_weeks = last_n_weeks(history, current_week, 4)
    n_persist = len(persist_weeks) or 1
    weeks_present = defaultdict(set)
    for r in history:
        if (r["cluster"] == cluster and r.get("source") == "serp"
                and r["iso_week"] in persist_weeks and r.get("advertiser_domain")):
            weeks_present[r["advertiser_domain"]].add(r["iso_week"])

    by_dom = defaultdict(list)
    for r in serp:
        if r.get("advertiser_domain"):
            by_dom[r["advertiser_domain"]].append(r)

    scored = []
    for dom, drows in by_dom.items():
        ergo_flag = dom in ERGO_OWN or is_ergo(drows[0])
        kw_rank = {}
        for r in drows:
            kw = r["keyword"]
            rk = _to_int(r["rank"])
            if kw not in kw_rank or rk < kw_rank[kw]:
                kw_rank[kw] = rk
        presence = len(kw_rank) / total_kw
        ranks = list(kw_rank.values())
        position = sum(1.0 / max(1, rk) for rk in ranks) / len(ranks)
        persistence = len(weeks_present.get(dom, set())) / n_persist
        score = 100 * (W_PRESENCE * presence + W_POSITION * position + W_PERSIST * persistence)

        common = [kw for kw in kw_rank if kw in ergo_rk]
        above = [kw for kw in common if kw_rank[kw] < ergo_rk[kw]]
        above_pct = round(100 * len(above) / len(common), 0) if common else None

        ads = []
        for r in sorted(drows, key=lambda x: (_to_int(x["rank"]), x["keyword"])):
            kw = r["keyword"]
            er = ergo_rk.get(kw)
            ads.append({
                "keyword": kw, "rank": _to_int(r["rank"]), "ergo_rank": er,
                "above_ergo": (er is not None and _to_int(r["rank"]) < er),
                "headline": r.get("headline", "") or "",
                "description": r.get("description", "") or "",
                "url": r.get("url", "") or "",
            })
        scored.append({
            "domain": dom, "name": drows[0].get("advertiser_name") or dom,
            "score": round(score, 1), "presence_pct": round(100 * presence, 0),
            "best_rank": min(ranks), "persistence_wk": f"{len(weeks_present.get(dom, set()))}/{n_persist}",
            "is_ergo": ergo_flag, "above_ergo_n": len(above), "common_n": len(common),
            "above_ergo_pct": above_pct, "ads": ads,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def live_delta(cur_rows, history, cluster, current_run):
    pr = prev_run(history, current_run)
    cur = {r["advertiser_domain"] for r in cur_rows
           if r["cluster"] == cluster and r.get("source") == "serp"
           and r["advertiser_domain"] and not is_ergo(r)}
    if not pr:
        return sorted(cur), [], None
    prev = {r["advertiser_domain"] for r in history
            if r["cluster"] == cluster and r.get("source") == "serp"
            and r["run_timestamp"] == pr and r["advertiser_domain"] and not is_ergo(r)}
    return sorted(cur - prev), sorted(prev - cur), pr


# --- ZUSATZ-LAYER: Markenraum / Namensvettern (adv) ----------------------

def name_matches(cur_rows, cluster, creatives=None):
    creatives = creatives or {}
    adv = [r for r in cur_rows if r["cluster"] == cluster and r.get("source") == "adv"]
    by_key = defaultdict(list)
    for r in adv:
        if r["advertiser_key"]:
            by_key[r["advertiser_key"]].append(r)
    out = []
    for key, rows in by_key.items():
        adv_id = next((x.get("advertiser_id") for x in rows if x.get("advertiser_id")), "")
        cre = creatives.get(adv_id) if adv_id else None
        out.append({
            "key": key, "name": rows[0].get("advertiser_name") or key,
            "domain": rows[0].get("advertiser_domain") or "",
            "approx_ads": max((_to_int(x.get("approx_ads_count"), 0) for x in rows), default=0),
            "verified": any(_to_bool(x.get("verified")) for x in rows),
            "advertiser_id": adv_id, "is_ergo": is_ergo(rows[0]),
            "keywords": sorted({x["keyword"] for x in rows}),
            "creatives": ({
                "n": _to_int(cre.get("n_creatives"), 0), "formats": cre.get("formats", ""),
                "first_shown": cre.get("first_shown", ""), "last_shown": cre.get("last_shown", ""),
                "preview": cre.get("sample_preview_url", ""), "transparency": cre.get("transparency_url", ""),
            } if cre else None),
        })
    out.sort(key=lambda x: x["approx_ads"], reverse=True)
    return out


def trademark_candidates(cur_rows):
    out, seen = [], set()
    for r in cur_rows:
        if r.get("source") != "serp" or not _to_bool(r.get("brand_in_copy")) or is_ergo(r):
            continue
        k = (r["advertiser_domain"], r["keyword"], r.get("headline", ""))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# --- Zeitreihen ueber alle Laeufe (auf LIVE-Daten) -----------------------

def time_series(history):
    runs = runs_sorted(history)
    clusters = list(dict.fromkeys(r["cluster"] for r in history))
    density = {cl: [] for cl in clusters}
    ergo_presence, trademark, namespace = [], [], {cl: [] for cl in clusters}

    latest = runs[-1] if runs else ""
    proxy = defaultdict(float)
    for r in history:
        if (r["run_timestamp"] == latest and r.get("source") == "serp"
                and r.get("advertiser_domain") and not is_ergo(r)):
            proxy[r["advertiser_domain"]] += 1.0 / max(1, _to_int(r["rank"]))
    top_keys = [k for k, _ in sorted(proxy.items(), key=lambda x: x[1], reverse=True)[:6]]
    bidder_intensity = {k: [] for k in top_keys}
    bidder_names = {k: k for k in top_keys}

    for run in runs:
        rr = [r for r in history if r["run_timestamp"] == run]
        serp = [r for r in rr if r.get("source") == "serp"]
        adv = [r for r in rr if r.get("source") == "adv"]
        for cl in clusters:
            comp = {r["advertiser_domain"] for r in serp
                    if r["cluster"] == cl and r["advertiser_domain"] and not is_ergo(r)}
            density[cl].append(len(comp))
            nm = {r["advertiser_key"] for r in adv
                  if r["cluster"] == cl and r["advertiser_key"] and not is_ergo(r)}
            namespace[cl].append(len(nm))
        kws = {r["keyword"] for r in serp}
        ekws = {r["keyword"] for r in serp if is_ergo(r)}
        ergo_presence.append(round(100 * len(ekws) / len(kws), 0) if kws else 0)
        tm = {(r["advertiser_domain"], r["keyword"], r.get("headline", ""))
              for r in serp if _to_bool(r.get("brand_in_copy")) and not is_ergo(r)}
        trademark.append(len(tm))
        total_kw = len(kws) or 1
        for k in top_keys:
            krows = [r for r in serp if r["advertiser_domain"] == k]
            if krows:
                bidder_names[k] = krows[0].get("advertiser_name") or k
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
        "runs": runs, "clusters": clusters, "density": density,
        "namespace": namespace, "ergo_presence": ergo_presence,
        "trademark": trademark, "bidder_intensity": bidder_intensity,
        "bidder_names": bidder_names,
    }


# --- Report (Markdown) ---------------------------------------------------

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
    empty_scan = not week_rows
    if empty_scan:
        # API-Limit oder kein Werbetraffic: Report aus vorhandener History
        print(f"  Hinweis: {week_csv} enthaelt keine Zeilen (API-Limit/kein Werbetraffic). "
              "Report wird aus bestehender History generiert.")
    history, n_added = append_history(week_rows)
    creatives = read_creatives_latest()

    current_run = latest_run(history)
    cur_rows = [r for r in history if r["run_timestamp"] == current_run]
    current_week = cur_rows[0]["iso_week"]
    # Wenn kein neuer Scan-Lauf: Wochenbeschriftung auf heutiges Datum setzen
    if empty_scan:
        today = dt.date.today()
        iso_y, iso_w, _ = today.isocalendar()
        current_week = f"{iso_y}-KW{iso_w:02d}"
    run_date = cur_rows[0]["run_date"]
    provider = cur_rows[0]["provider"]
    clusters = list(dict.fromkeys(r["cluster"] for r in cur_rows))

    n_live = sum(1 for r in cur_rows if r.get("source") == "serp")
    n_live_comp = len({r["advertiser_domain"] for r in cur_rows
                       if r.get("source") == "serp" and r["advertiser_domain"] and not is_ergo(r)})
    n_names = len({r["advertiser_key"] for r in cur_rows if r.get("source") == "adv"})
    tm = trademark_candidates(cur_rows)

    lines = [f"# ERGO Brand Bidding – {current_week}", ""]
    lines.append(f"*Lauf: {run_date} ({current_run}) · Provider: {provider} · "
                 f"Live-Wettbewerber: {n_live_comp} · Live-Anzeigen: {n_live} · "
                 f"Namensraum (Ad Transparency): {n_names} · Trademark: {len(tm)}*")
    lines.append("")
    lines.append("> **Hauptsignal = Live-Suche** (echtes Brand-Bidding: Text, Landingpage, "
                 "Position). Der Namensraum-Abschnitt listet nur Werbetreibende mit passendem "
                 "Namen aus dem Ad-Transparency-Center – das ist **kein** Nachweis von Brand-Bidding.")
    lines.append("")

    live_scores = {}
    for cl in clusters:
        scored = live_bidders(cur_rows, history, cl, current_week)
        live_scores[cl] = scored
        comp = [s for s in scored if not s["is_ergo"]]
        lines.append(f"## {cl} – Live-Bieter (echtes Brand-Bidding)")
        lines.append("")
        if scored:
            rows = [[i + 1, (s["name"] or s["domain"]) + (" (ERGO)" if s["is_ergo"] else ""),
                     s["score"], f'{int(s["presence_pct"])}%', s["best_rank"],
                     _fmt_above(s), s["persistence_wk"]] for i, s in enumerate(scored[:20])]
            lines.append(md_table(["#", "Domain", "Score", "Praesenz", "Best-Pos",
                                    "Ueber ERGO", "Persistenz"], rows))
        else:
            lines.append("*Keine Live-Anzeigen in diesem Lauf erfasst.*")
        lines.append("")
        for s in comp[:5]:
            if not s["ads"]:
                continue
            lines.append(f"**{s['name'] or s['domain']}** — Ueber ERGO: {_fmt_above(s)}")
            for a in s["ads"][:6]:
                er = a["ergo_rank"]
                pos = (f"Pos {a['rank']} vs ERGO {er}" if er is not None
                       else f"Pos {a['rank']} (ERGO nicht live)")
                lines.append(f"- *{a['keyword']}* — {pos} — „{(a['headline'] or '(kein Titel)')[:90]}“")
                if a["description"]:
                    lines.append(f"  {a['description'][:140]}")
                if a["url"]:
                    lines.append(f"  Landingpage: {a['url']}")
            lines.append("")

    lines.append("## Markenraum / Namensvettern (Ad Transparency – kein Brand-Bidding-Nachweis)")
    lines.append("")
    for cl in clusters:
        nm = [n for n in name_matches(cur_rows, cl, creatives) if not n["is_ergo"]][:15]
        lines.append(f"**{cl}** ({len(nm)} Werbetreibende mit passendem Namen):")
        if nm:
            rows = [[n["name"], n["approx_ads"] or "–", "ja" if n["verified"] else "–"] for n in nm]
            lines.append("")
            lines.append(md_table(["Werbetreibender", "~Ads", "verifiziert"], rows))
        else:
            lines.append("\n*Keine Namens-Treffer.*")
        lines.append("")

    lines.append("## Trademark-Pruefkandidaten (Live-Anzeigen)")
    lines.append("")
    lines.append("> Hinweis zur menschlichen/juristischen Pruefung – **keine** rechtliche Bewertung.")
    lines.append("")
    if tm:
        rows = [[r["advertiser_domain"], r["cluster"], r["keyword"],
                 (r.get("headline") or "")[:80]] for r in tm]
        lines.append(md_table(["Domain", "Cluster", "Keyword", "Anzeigentitel"], rows))
    else:
        lines.append("*Keine Live-Anzeige mit Markenname im Text.*")
    lines.append("")
    lines.append("---")
    lines.append(f"*Automatisch erzeugt am {dt.date.today().isoformat()}. "
                 f"Scoring (Live): 0,5·Praesenz + 0,3·Position + 0,2·Persistenz (x100).*")

    out_path = f"ERGO_Brand_Bidding_{current_week}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path, build_summary(current_week, n_live_comp, n_live, n_names, tm, live_scores, n_added)


def build_summary(week, n_live_comp, n_live, n_names, tm, live_scores, n_added):
    tops = []
    for cl, scored in live_scores.items():
        comp = [s for s in scored if not s["is_ergo"]]
        if comp:
            tops.append(f"{cl}: {comp[0]['name'] or comp[0]['domain']} ({comp[0]['score']})")
    return "\n".join([
        f"ERGO Brand Bidding {week}: {n_live_comp} Live-Wettbewerber, {n_live} Live-Anzeigen "
        f"(Namensraum/Ad-Transparency: {n_names}).",
        f"{n_added} neue Zeilen in der Historie.",
        ("Top Live-Bieter je Cluster: " + " · ".join(tops) + "."
         if tops else "Keine Live-Wettbewerber-Anzeigen in diesem Lauf."),
        f"Trademark-Pruefkandidaten (Live): {len(tm)} (Hinweis, keine rechtl. Bewertung).",
        f"Report: ERGO_Brand_Bidding_{week}.md",
    ])


if __name__ == "__main__":
    week_csv = sys.argv[1] if len(sys.argv) > 1 else pick_latest_week_csv()
    print(f"== Report aus {week_csv} ==")
    out_path, summary = build_report(week_csv)
    print(f"\nReport geschrieben: {out_path}\n")
    print("--- 5-Zeilen-Zusammenfassung ---")
    print(summary)
