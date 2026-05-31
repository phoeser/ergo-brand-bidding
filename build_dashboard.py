#!/usr/bin/env python3
"""ERGO Brand Bidding - HTML-Dashboard-Generator (Standardlib-only)."""

import os
import json
import datetime as dt

import build_report as br

HISTORY_FILE = br.HISTORY_FILE
ERGO_OWN = br.ERGO_OWN
ROUTINE_URL = "https://claude.ai/code/routines/trig_01AWBd3vmVJ3sLBC2USFVHGE"


def build_data():
    history = br.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else []
    if not history:
        now_utc = dt.datetime.now(dt.timezone.utc)
        iso = now_utc.isocalendar()
        current_week = f"{iso.year}-KW{iso.week:02d}"
        return {
            "generated": dt.date.today().isoformat(),
            "current_week": current_week,
            "run_date": dt.date.today().isoformat(),
            "provider": os.getenv("SERP_PROVIDER", "serper"),
            "routine_url": ROUTINE_URL,
            "kpis": {"ads": 0, "advertisers": 0, "trademark": 0},
            "clusters": [],
            "scores": {},
            "delta": {},
            "trend_weeks": [current_week],
            "total_trend": [0],
            "by_cluster_trend": {},
            "trademark": [],
            "weeks": [current_week],
        }
    weeks = sorted({r["iso_week"] for r in history})
    current_week = weeks[-1]
    week_rows = [r for r in history if r["iso_week"] == current_week]
    run_date = week_rows[0].get("run_date", "")
    provider = week_rows[0].get("provider", "")
    clusters = list(dict.fromkeys(r["cluster"] for r in history))

    scores = {cl: br.score_cluster(week_rows, history, cl, current_week) for cl in clusters}

    delta = {}
    for cl in clusters:
        new, gone, pw = br.delta_bidders(week_rows, history, cl, current_week)
        delta[cl] = {"prev_week": pw, "new": new, "gone": gone}

    trend_weeks = weeks[-8:]
    total_trend, by_cluster_trend = [], {cl: [] for cl in clusters}
    for wk in trend_weeks:
        wk_rows = [r for r in history if r["iso_week"] == wk]
        comp = {r["advertiser_domain"] for r in wk_rows
                if r["advertiser_domain"] and r["advertiser_domain"] not in ERGO_OWN}
        total_trend.append(len(comp))
        for cl in clusters:
            c = {r["advertiser_domain"] for r in wk_rows
                 if r["cluster"] == cl and r["advertiser_domain"]
                 and r["advertiser_domain"] not in ERGO_OWN}
            by_cluster_trend[cl].append(len(c))

    tm = br.trademark_candidates(week_rows)
    trademark = [{"domain": r["advertiser_domain"], "cluster": r["cluster"],
                  "keyword": r["keyword"], "headline": (r["headline"] or "")[:120]}
                 for r in tm]

    n_ads = len(week_rows)
    n_adv = len({r["advertiser_domain"] for r in week_rows if r["advertiser_domain"]})

    return {
        "generated": dt.date.today().isoformat(),
        "current_week": current_week,
        "run_date": run_date,
        "provider": provider,
        "routine_url": ROUTINE_URL,
        "kpis": {"ads": n_ads, "advertisers": n_adv, "trademark": len(trademark)},
        "clusters": clusters,
        "scores": scores,
        "delta": delta,
        "trend": {"weeks": trend_weeks, "total": total_trend, "byCluster": by_cluster_trend},
        "trademark": trademark,
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ERGO Brand Bidding - Dashboard</title>
<link rel="icon" href="favicon.svg" type="image/svg+xml">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#c8102e">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="ERGO BB">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root{--bg:#f5f6f8;--card:#fff;--ink:#1c2330;--muted:#6b7280;--line:#e6e8ec;--accent:#c8102e;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;}
  .wrap{max-width:1080px;margin:0 auto;padding:24px 18px 60px;}
  h1{font-size:22px;margin:0 0 2px;}
  .sub{color:var(--muted);font-size:13px;margin-bottom:14px;}
  .actions{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px;}
  .actions button,.actions a{font-family:inherit;font-size:13px;border:1px solid var(--line);background:#fff;color:var(--ink);border-radius:999px;padding:9px 18px;cursor:pointer;text-decoration:none;}
  .actions a.prim{background:var(--accent);color:#fff;border-color:var(--accent);}
  .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px;}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;}
  .kpi .n{font-size:26px;font-weight:700;}
  .kpi .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em;}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:18px;}
  .card h2{font-size:15px;margin:0 0 12px;}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
  .tab{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 14px;font-size:13px;cursor:pointer;color:var(--ink);}
  .tab.active{background:var(--accent);color:#fff;border-color:var(--accent);}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;}
  td.num{text-align:right;font-variant-numeric:tabular-nums;}
  .ergo{color:var(--accent);font-weight:600;}
  .badge{display:inline-block;background:#fde7ea;color:var(--accent);border-radius:6px;padding:1px 7px;font-size:11px;margin-left:6px;}
  .delta{font-size:13px;color:var(--muted);margin-top:6px;}
  .new{color:#15803d;} .gone{color:#b91c1c;}
  .note{color:var(--muted);font-size:12px;margin-top:8px;}
  canvas{max-height:300px;}
  @media(max-width:640px){.kpis{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="wrap">
  <h1>ERGO Brand Bidding - Dashboard</h1>
  <div class="sub" id="sub"></div>
  <div class="actions">
    <button onclick="location.reload()">&#128260; Aktualisieren</button>
    <a class="prim" id="runBtn" target="_blank" rel="noopener">&#9654;&#65039; Neuen Scan starten</a>
  </div>
  <div class="kpis" id="kpis"></div>
  <div class="card">
    <h2>Intensitaets-Score je Cluster</h2>
    <div class="tabs" id="tabs"></div>
    <canvas id="barChart"></canvas>
    <div class="delta" id="delta"></div>
  </div>
  <div class="card">
    <h2>Top-Bieter <span id="clName"></span></h2>
    <table id="bidTable"><thead><tr>
      <th>#</th><th>Domain</th><th class="num">Score</th><th class="num">Praesenz</th>
      <th class="num">Best-Rank</th><th>Persistenz</th>
    </tr></thead><tbody></tbody></table>
  </div>
  <div class="card">
    <h2>Wochentrend - Anzahl Wettbewerber-Domains</h2>
    <canvas id="trendChart"></canvas>
  </div>
  <div class="card">
    <h2>Trademark-Pruefkandidaten</h2>
    <div class="note">Hinweis zur menschlichen/juristischen Pruefung - keine rechtliche Bewertung.</div>
    <table id="tmTable"><thead><tr>
      <th>Domain</th><th>Cluster</th><th>Keyword</th><th>Anzeigentitel</th>
    </tr></thead><tbody></tbody></table>
  </div>
  <div class="note">Automatisch erzeugt am <span id="gen"></span> - Scoring: 0,5 Praesenz + 0,3 Position + 0,2 Persistenz (x100).</div>
</div>
<script>
const DATA = __DATA__;
let current = DATA.clusters[0];
let barChart, trendChart;
document.getElementById('sub').textContent = 'Stand: ' + DATA.current_week + ' - Lauf ' + DATA.run_date + ' - Provider ' + DATA.provider;
document.getElementById('gen').textContent = DATA.generated;
document.getElementById('runBtn').href = DATA.routine_url;
const kpiDefs = [['ads','Anzeigen-Treffer'],['advertisers','Unique Advertiser'],['trademark','Trademark-Kandidaten']];
document.getElementById('kpis').innerHTML = kpiDefs.map(function(k){
  return '<div class="kpi"><div class="n">'+DATA.kpis[k[0]]+'</div><div class="l">'+k[1]+'</div></div>';
}).join('');
document.getElementById('tabs').innerHTML = DATA.clusters.map(function(c){
  return '<button class="tab" data-c="'+c+'">'+c+'</button>';
}).join('');
Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){
  b.addEventListener('click', function(){ current = b.dataset.c; render(); });
});
function render(){
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){
    b.classList.toggle('active', b.dataset.c === current);
  });
  document.getElementById('clName').textContent = '- ' + current;
  const rows = (DATA.scores[current]||[]).slice(0,20);
  const barRows = rows.slice(0,12);
  const labels = barRows.map(function(r){return r.domain;});
  const vals = barRows.map(function(r){return r.score;});
  const colors = barRows.map(function(r){return r.is_ergo ? '#9aa0a6' : '#c8102e';});
  if(barChart) barChart.destroy();
  barChart = new Chart(document.getElementById('barChart'), {
    type:'bar',
    data:{labels:labels,datasets:[{data:vals,backgroundColor:colors,borderRadius:4}]},
    options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,title:{display:true,text:'Score'}}}}
  });
  const d = DATA.delta[current] || {};
  let dh = '';
  if(d.prev_week){
    dh = '<b>vs '+d.prev_week+'</b> - <span class="new">NEU: '+(d.new.length?d.new.join(', '):'-')+'</span> - <span class="gone">VERSCHWUNDEN: '+(d.gone.length?d.gone.join(', '):'-')+'</span>';
  } else { dh = 'Keine Vorwoche (Basislauf).'; }
  document.getElementById('delta').innerHTML = dh;
  const tb = document.querySelector('#bidTable tbody');
  tb.innerHTML = rows.map(function(r,i){
    return '<tr><td>'+(i+1)+'</td><td class="'+(r.is_ergo?'ergo':'')+'">'+r.domain+(r.is_ergo?'<span class="badge">ERGO</span>':'')+'</td><td class="num">'+r.score+'</td><td class="num">'+Math.round(r.presence_pct)+'%</td><td class="num">'+r.best_rank+'</td><td>'+r.persistence_wk+'</td></tr>';
  }).join('');
}
function renderTrend(){
  const palette = ['#c8102e','#2563eb','#15803d','#d97706','#7c3aed'];
  const ds = DATA.clusters.map(function(c,i){
    return {label:c,data:DATA.trend.byCluster[c],borderColor:palette[i%palette.length],backgroundColor:'transparent',tension:.25};
  });
  ds.push({label:'Gesamt',data:DATA.trend.total,borderColor:'#1c2330',borderDash:[5,4],backgroundColor:'transparent',tension:.25});
  trendChart = new Chart(document.getElementById('trendChart'), {
    type:'line',
    data:{labels:DATA.trend.weeks,datasets:ds},
    options:{plugins:{legend:{position:'bottom'}},scales:{y:{beginAtZero:true,title:{display:true,text:'Domains'}}}}
  });
}
function renderTM(){
  const tb = document.querySelector('#tmTable tbody');
  if(!DATA.trademark.length){ tb.innerHTML='<tr><td colspan="4">Keine Treffer.</td></tr>'; return; }
  tb.innerHTML = DATA.trademark.map(function(r){
    return '<tr><td>'+r.domain+'</td><td>'+r.cluster+'</td><td>'+r.keyword+'</td><td>'+r.headline+'</td></tr>';
  }).join('');
}
render(); renderTrend(); renderTM();
</script>
</body>
</html>
"""


def main():
    data = build_data()
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("index.html geschrieben (Stand " + data["current_week"] + ").")


if __name__ == "__main__":
    main()
