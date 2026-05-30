#!/usr/bin/env python3
"""
ERGO Brand Bidding – Auswertung & Report (Standardlib-only, kein Extra-Paket).

Aufgaben:
  1. Wochen-CSV von brand_bidding_scan.py einlesen.
  2. In die Master-Historie (ergo_brand_bidding_history.csv) anhaengen + dedupen.
  3. Intensitaets-Score je Advertiser-Domain und Cluster berechnen
     (Praesenz + Positionsgewicht + Persistenz).
  4. NEUE / VERSCHWUNDENE Bieter ggue. Vorwoche markieren.
  5. Trademark-Pruefkandidaten (brand_in_copy = true) auflisten.
  6. Markdown-Report  ERGO_Brand_Bidding_<ISO-Woche>.md  schreiben.

Aufruf:
  python build_report.py [wochen_csv]
  (ohne Argument: nimmt automatisch die neueste ergo_brand_bidding_*.csv)

Scoring-Formel (0-100), pro Domain x Cluster:
  Score = 100 * (0.5*Praesenz + 0.3*Positionsgewicht + 0.2*Persistenz)
    Praesenz        = Anteil der (Keyword x Geraet)-Kombis im Cluster,
                      in denen die Domain diese Woche auftauchte.
    Positionsgewicht= Mittel aus 1/rank ueber alle Treffer der Domain
                      (obere Plaetze zaehlen hoeher).
    Persistenz      = in wie vielen der letzten 4 Wochenlaeufe die Domain
                      im Cluster war / 4.
"""

import os
import csv
import sys
import glob
import json
import datetime as dt
from collections import defaultdict

HISTORY_FILE = "ergo_brand_bidding_history.csv"
FIELDS = [
    "run_timestamp", "run_date", "iso_week", "provider", "device",
    "cluster", "keyword", "rank", "block", "advertiser_domain",
    "advertiser_display", "headline", "description", "url", "brand_in_copy",
]
# ERGO-eigene Domains (werden im Report als (ERGO) markiert, nicht als Wettbewerber)
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


def pick_latest_week_csv():
    cands = sorted(
        p for p in glob.glob("ergo_brand_bidding_*.csv")
        if os.path.basename(p) != HISTORY_FILE
    )
    if not cands:
        raise SystemExit("Keine Wochen-CSV (ergo_brand_bidding_*.csv) gefunden.")
    return cands[-1]


def _row_key(r):
    """Identitaet einer Zeile innerhalb eines Laufs (fuer Dedup)."""
    return (
        r.get("run_timestamp", ""), r.get("device", ""), r.get("keyword", ""),
        r.get("advertiser_domain", ""), str(r.get("rank", "")),
    )


def append_history(week_rows):
    existing = read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else []
    seen = {_row_key(r) for r in existing}
    added = [r for r in week_rows if _row_key(r) not in seen]
    all_rows = existing + added
    with open(HISTORY_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_rows)
    return all_rows, len(added)


# --- Scoring -------------------------------------------------------------

def last_n_weeks(history, current_week, n=4):
    weeks = sorted({r["iso_week"] for r in history})
    if current_week not in weeks:
        weeks.append(current_week)
        weeks = sorted(set(weeks))
    idx = weeks.index(current_week)
    return weeks[max(0, idx - n + 1): idx + 1]


def prev_week(history, current_week):
    weeks = sorted({r["iso_week"] for r in history if r["iso_week"] < current_week})
    return weeks[-1] if weeks else None


def score_cluster(week_rows, history, cluster, current_week):
    rows = [r for r in week_rows if r["cluster"] == cluster]
    # Nenner: distinkte (keyword, device)-Kombis im Cluster diese Woche
    combos = {(r["keyword"], r["device"]) for r in rows}
    total_combos = len(combos) or 1

    persist_weeks = last_n_weeks(history, current_week, 4)
    n_persist = len(persist_weeks) or 1
    # Domain -> in welchen Wochen im Cluster present
    weeks_present = defaultdict(set)
    for r in history:
        if r["cluster"] == cluster and r["iso_week"] in persist_weeks:
            weeks_present[r["advertiser_domain"]].add(r["iso_week"])

    by_domain = defaultdict(list)
    for r in rows:
        by_domain[r["advertiser_domain"]].append(r)

    scored = []
    for dom, drows in by_domain.items():
        if not dom:
            continue
        dom_combos = {(r["keyword"], r["device"]) for r in drows}
        presence = len(dom_combos) / total_combos
        ranks = [_to_int(r["rank"]) for r in drows]
        position = sum(1.0 / max(1, rk) for rk in ranks) / len(ranks)
        persistence = len(weeks_present.get(dom, set())) / n_persist
        score = 100 * (W_PRESENCE * presence + W_POSITION * position + W_PERSIST * persistence)
        scored.append({
            "domain": dom,
            "score": round(score, 1),
            "presence_pct": round(100 * presence, 0),
            "best_rank": min(ranks),
            "avg_rank": round(sum(ranks) / len(ranks), 1),
            "persistence_wk": f"{len(weeks_present.get(dom, set()))}/{n_persist}",
            "hits": len(drows),
            "is_ergo": dom in ERGO_OWN,
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


def delta_bidders(week_rows, history, cluster, current_week):
    pw = prev_week(history, current_week)
    cur = {r["advertiser_domain"] for r in week_rows
           if r["cluster"] == cluster and r["advertiser_domain"]}
    if not pw:
        return sorted(cur), [], None
    prev = {r["advertiser_domain"] for r in history
            if r["cluster"] == cluster and r["iso_week"] == pw and r["advertiser_domain"]}
    new = sorted(cur - prev)
    gone = sorted(prev - cur)
    return new, gone, pw


def trademark_candidates(week_rows):
    out, seen = [], set()
    for r in week_rows:
        if not _to_bool(r["brand_in_copy"]):
            continue
        key = (r["advertiser_domain"], r["keyword"], r["headline"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    # ERGO-eigene Domains sind keine Pruefkandidaten
    return [r for r in out if r["advertiser_domain"] not in ERGO_OWN]


# --- Report --------------------------------------------------------------

def md_table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def build_report(week_csv):
    week_rows = read_csv(week_csv)
    today = dt.date.today()
    iso = today.isocalendar()
    fallback_week = f"{iso.year}-KW{iso.week:02d}"
    if not week_rows:
        # Scan lief erfolgreich, aber keine bezahlten Ads gefunden
        current_week = fallback_week
        run_date = today.isoformat()
        provider = os.getenv("SERP_PROVIDER", "serper")
        clusters = list(KEYWORD_CLUSTERS.keys()) if hasattr(sys.modules[__name__], 'KEYWORD_CLUSTERS') else []
        # Leere Historie-Erweiterung (keine neuen Zeilen)
        history, n_added = append_history([])
        out_path = f"ERGO_Brand_Bidding_{current_week}.md"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# ERGO Brand Bidding – {current_week}\n\n")
            f.write(f"*Lauf-Datum: {run_date} · Provider: {provider} · "
                    f"Anzeigen-Treffer: 0 · Unique Advertiser: 0 · Trademark-Pruefkandidaten: 0*\n\n")
            f.write("## Ergebnis\n\n")
            f.write("*Heute wurden keine bezahlten Anzeigen (Brand Bidding) fuer die gescannten ERGO-Keywords gefunden.*\n\n")
            f.write("---\n")
            f.write(f"*Automatisch erzeugt am {today.isoformat()}. "
                    f"Scoring: 0,5·Praesenz + 0,3·Position + 0,2·Persistenz (x100).*\n")
        summary = (
            f"ERGO Brand Bidding {current_week}: 0 Anzeigen-Treffer, 0 Advertiser.\n"
            f"{n_added} neue Zeilen in der Historie.\n"
            f"Keine Wettbewerber-Anzeigen erfasst.\n"
            f"Trademark-Pruefkandidaten: 0 (Hinweis, keine rechtl. Bewertung).\n"
            f"Report: {out_path}"
        )
        return out_path, summary
    current_week = week_rows[0]["iso_week"]
    run_date = week_rows[0]["run_date"]
    provider = week_rows[0]["provider"]
    clusters = list(dict.fromkeys(r["cluster"] for r in week_rows))

    history, n_added = append_history(week_rows)

    n_ads = len(week_rows)
    n_adv = len({r["advertiser_domain"] for r in week_rows if r["advertiser_domain"]})
    tm = trademark_candidates(week_rows)

    lines = []
    lines.append(f"# ERGO Brand Bidding – {current_week}")
    lines.append("")
    lines.append(f"*Lauf-Datum: {run_date} · Provider: {provider} · "
                 f"Anzeigen-Treffer: {n_ads} · Unique Advertiser: {n_adv} · "
                 f"Trademark-Pruefkandidaten: {len(tm)}*")
    lines.append("")

    # (a) Top-5 je Cluster
    cluster_scores = {}
    for cl in clusters:
        scored = score_cluster(week_rows, history, cl, current_week)
        cluster_scores[cl] = scored
        lines.append(f"## {cl} – Top-5 Bieter")
        lines.append("")
        if scored:
            top = scored[:5]
            rows = [[
                i + 1,
                s["domain"] + (" (ERGO)" if s["is_ergo"] else ""),
                s["score"], f'{int(s["presence_pct"])}%',
                s["best_rank"], s["persistence_wk"],
            ] for i, s in enumerate(top)]
            lines.append(md_table(
                ["#", "Domain", "Score", "Praesenz", "Best-Rank", "Persistenz"], rows))
        else:
            lines.append("*Keine Anzeigen erfasst.*")
        lines.append("")

    # (b) Neue / verschwundene Bieter
    lines.append("## Veraenderungen ggue. Vorwoche")
    lines.append("")
    any_prev = None
    for cl in clusters:
        new, gone, pw = delta_bidders(week_rows, history, cl, current_week)
        any_prev = pw
        if pw is None:
            lines.append(f"- **{cl}**: keine Vorwoche in der Historie (Basislauf).")
        else:
            n = ", ".join(new) if new else "–"
            g = ", ".join(gone) if gone else "–"
            lines.append(f"- **{cl}** (vs {pw}): NEU: {n} · VERSCHWUNDEN: {g}")
    lines.append("")

    # (c) Trademark-Pruefkandidaten
    lines.append("## Trademark-Pruefkandidaten")
    lines.append("")
    lines.append("> Hinweis zur menschlichen/juristischen Pruefung – **keine** "
                 "rechtliche Bewertung. Markenname \"ERGO\" steht im Anzeigentitel/-text.")
    lines.append("")
    if tm:
        rows = [[r["advertiser_domain"], r["cluster"], r["keyword"],
                 (r["headline"] or "")[:80]] for r in tm]
        lines.append(md_table(["Domain", "Cluster", "Keyword", "Anzeigentitel"], rows))
    else:
        lines.append("*Keine Treffer mit Markenname im Anzeigentext.*")
    lines.append("")

    # (d) 3 Bullets "Was diese Woche auffaellt" (datengetrieben, Entwurf)
    lines.append("## Was diese Woche auffaellt")
    lines.append("")
    for b in auto_highlights(week_rows, cluster_scores, tm, any_prev, history, current_week):
        lines.append(f"- {b}")
    lines.append("")
    lines.append("---")
    lines.append(f"*Automatisch erzeugt am {dt.date.today().isoformat()}. "
                 f"Scoring: 0,5·Praesenz + 0,3·Position + 0,2·Persistenz (x100).*")

    out_path = f"ERGO_Brand_Bidding_{current_week}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    summary = build_summary(current_week, n_ads, n_adv, tm, cluster_scores, n_added)
    return out_path, summary


def auto_highlights(week_rows, cluster_scores, tm, pw, history, current_week):
    out = []
    # Aggressivster Wettbewerber gesamt
    best = None
    for cl, scored in cluster_scores.items():
        for s in scored:
            if s["is_ergo"]:
                continue
            if best is None or s["score"] > best[1]["score"]:
                best = (cl, s)
    if best:
        cl, s = best
        out.append(f"Staerkster Wettbewerber: **{s['domain']}** im Cluster "
                   f"*{cl}* (Score {s['score']}, Best-Rank {s['best_rank']}, "
                   f"Persistenz {s['persistence_wk']}).")
    # Neue Bieter gesamt
    if pw:
        new_all = []
        for cl in cluster_scores:
            new, _, _ = delta_bidders(week_rows, history, cl, current_week)
            new_all += [(d, cl) for d in new]
        if new_all:
            out.append("Neue Bieter diese Woche: " +
                       ", ".join(f"{d} ({cl})" for d, cl in new_all[:6]) +
                       (" u.a." if len(new_all) > 6 else "") + ".")
        else:
            out.append("Keine neuen Bieter ggue. Vorwoche.")
    # Trademark
    if tm:
        doms = sorted({r["advertiser_domain"] for r in tm})
        out.append(f"{len(tm)} Trademark-Pruefkandidat(en) bei {len(doms)} Domain(s): "
                   + ", ".join(doms[:6]) + (" u.a." if len(doms) > 6 else "") + ".")
    else:
        out.append("Keine Anzeige mit Markenname \"ERGO\" im Text.")
    return out[:3] if len(out) >= 3 else out


def build_summary(week, n_ads, n_adv, tm, cluster_scores, n_added):
    tops = []
    for cl, scored in cluster_scores.items():
        comp = [s for s in scored if not s["is_ergo"]]
        if comp:
            tops.append(f"{cl}: {comp[0]['domain']} ({comp[0]['score']})")
    lines = [
        f"ERGO Brand Bidding {week}: {n_ads} Anzeigen-Treffer, {n_adv} Advertiser.",
        f"{n_added} neue Zeilen in der Historie.",
        "Top-Wettbewerber je Cluster: " + " · ".join(tops) + "." if tops else
        "Keine Wettbewerber-Anzeigen erfasst.",
        f"Trademark-Pruefkandidaten: {len(tm)} (Hinweis, keine rechtl. Bewertung).",
        f"Report: ERGO_Brand_Bidding_{week}.md",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    week_csv = sys.argv[1] if len(sys.argv) > 1 else pick_latest_week_csv()
    print(f"== Report aus {week_csv} ==")
    out_path, summary = build_report(week_csv)
    print(f"\nReport geschrieben: {out_path}\n")
    print("--- 5-Zeilen-Zusammenfassung ---")
    print(summary)
