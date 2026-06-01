#!/usr/bin/env python3
"""ERGO Brand Bidding - HTML-Dashboard-Generator (Standardlib-only).

Snapshot (letzter Lauf) + Zeitreihen ueber ALLE Laeufe + ausklappbare
Bieter mit Live-Anzeigentexten/Landingpages und Kreativ-Infos.
"""

import os
import json
import datetime as dt

import build_report as br

HISTORY_FILE = br.HISTORY_FILE
ROUTINE_URL = "https://claude.ai/code/routines/trig_01AWBd3vmVJ3sLBC2USFVHGE"


def build_data():
    history = br.read_csv(HISTORY_FILE) if os.path.exists(HISTORY_FILE) else []
    if not history:
        raise SystemExit(HISTORY_FILE + " fehlt oder ist leer.")
    creatives = br.read_creatives_latest()

    current_run = br.latest_run(history)
    cur_rows = [r for r in history if r["run_timestamp"] == current_run]
    current_week = cur_rows[0]["iso_week"]
    run_date = cur_rows[0].get("run_date", "")
    provider = cur_rows[0].get("provider", "")
    clusters = list(dict.fromkeys(r["cluster"] for r in cur_rows
                                  if r.get("source") == "adv")) or \
        list(dict.fromkeys(r["cluster"] for r in cur_rows))

    scores = {cl: br.score_cluster(cur_rows, history, cl, current_week, creatives)
              for cl in clusters}
    delta = {}
    for cl in clusters:
        new, gone, pr = br.delta_bidders(cur_rows, history, cl, current_run)
        delta[cl] = {"prev_run": pr, "new": new, "gone": gone}

    tm = br.trademark_candidates(cur_rows)
    trademark = [{"name": r.get("advertiser_name") or r.get("advertiser_domain"),
                  "cluster": r["cluster"], "keyword": r["keyword"],
                  "headline": (r.get("headline") or "")[:120]} for r in tm]

    n_adv = len({r["advertiser_key"] for r in cur_rows
                 if r.get("source") == "adv" and r["advertiser_key"]})
    n_serp = sum(1 for r in cur_rows if r.get("source") == "serp")

    return {
        "generated": dt.date.today().isoformat(),
        "current_week": current_week,
        "run_date": run_date,
        "current_run": current_run,
        "provider": provider,
        "routine_url": ROUTINE_URL,
        "kpis": {"bidders": n_adv, "live_ads": n_serp, "trademark": len(trademark)},
        "clusters": clusters,
        "scores": scores,
        "delta": delta,
        "trademark": trademark,
        "ts": br.time_series(history),
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
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px;}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px;}
  .tab{border:1px solid var(--line);background:#fff;border-radius:999px;padding:6px 14px;font-size:13px;cursor:pointer;color:var(--ink);}
  .tab.active{background:var(--accent);color:#fff;border-color:var(--accent);}
  table{width:100%;border-collapse:collapse;font-size:13px;}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);}
  th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;}
  td.num{text-align:right;font-variant-numeric:tabular-nums;}
  .ergo{color:var(--accent);font-weight:600;}
  .badge{display:inline-block;background:#fde7ea;color:var(--accent);border-radius:6px;padding:1px 7px;font-size:11px;margin-left:6px;}
  .vbadge{display:inline-block;background:#e7f0fd;color:#2563eb;border-radius:6px;padding:1px 6px;font-size:10px;margin-left:6px;}
  .delta{font-size:13px;color:var(--muted);margin-top:6px;}
  .new{color:#15803d;} .gone{color:#b91c1c;}
  .note{color:var(--muted);font-size:12px;margin-top:8px;}
  canvas{max-height:300px;}
  tr.bid{cursor:pointer;}
  tr.bid:hover{background:#faf7f8;}
  td.exp{width:18px;color:var(--muted);text-align:center;}
  .addet{padding:2px 0 6px;}
  .adrow{padding:8px 10px;border-left:3px solid var(--line);margin:6px 0;background:#fafbfc;border-radius:6px;}
  .adkw{font-size:12px;color:var(--muted);margin-bottom:2px;}
  .adh{font-weight:600;font-size:13px;}
  .add{font-size:12px;color:#444;margin-top:2px;}
  .adl{margin-top:4px;font-size:12px;}
  .adl a{color:var(--accent);}
  .ab{display:inline-block;background:#fde7ea;color:var(--accent);border-radius:6px;padding:0 6px;font-size:11px;margin-left:4px;}
  .cre{font-size:12px;color:#333;background:#f3f6fb;border-radius:6px;padding:8px 10px;margin:6px 0;}
  .cre img{max-height:90px;border:1px solid var(--line);border-radius:4px;margin-top:6px;display:block;}
  @media(max-width:760px){.grid2{grid-template-columns:1fr}}
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
    <h2>Intensitaet je Cluster (letzter Lauf)</h2>
    <div class="tabs" id="tabs"></div>
    <canvas id="barChart"></canvas>
    <div class="delta" id="delta"></div>
  </div>

  <div class="card">
    <h2>Top-Bieter <span id="clName"></span></h2>
    <div class="note">Zeile anklicken: Live-Anzeigentexte, Landingpages &amp; Kreativ-Infos. &bdquo;Ueber ERGO&ldquo; = Anteil gemeinsamer Keywords, in denen der Bieter ueber ERGO rankt. &bdquo;~Ads&ldquo; = ungefaehre Zahl laufender Anzeigen (Transparency).</div>
    <table id="bidTable"><thead><tr>
      <th></th><th>#</th><th>Bieter</th><th class="num">Score</th><th class="num">Praesenz</th>
      <th class="num">Best-Rang</th><th>Ueber ERGO</th><th class="num">~Ads</th><th>Persistenz</th>
    </tr></thead><tbody></tbody></table>
  </div>

  <h2 style="font-size:16px;margin:24px 0 10px">Zeitreihen ueber die Laeufe</h2>
  <div class="grid2">
    <div class="card"><h2>Bieterdichte je Cluster</h2><canvas id="tsDensity"></canvas></div>
    <div class="card"><h2>ERGO-Sichtbarkeit</h2><canvas id="tsErgo"></canvas></div>
    <div class="card"><h2>Top-Wettbewerber-Intensitaet</h2><canvas id="tsBidder"></canvas></div>
    <div class="card"><h2>Trademark-Treffer</h2><canvas id="tsTm"></canvas></div>
  </div>

  <div class="card">
    <h2>Trademark-Pruefkandidaten (letzter Lauf)</h2>
    <div class="note">Hinweis zur menschlichen/juristischen Pruefung - keine rechtliche Bewertung.</div>
    <table id="tmTable"><thead><tr>
      <th>Bieter</th><th>Cluster</th><th>Keyword</th><th>Anzeigentitel</th>
    </tr></thead><tbody></tbody></table>
  </div>
  <div class="note">Automatisch erzeugt am <span id="gen"></span> - Quellen: Ads Advertisers (Abdeckung) + Live-SERP (Texte/Landingpages/Position) + ads_search (Kreative). Scoring: 0,5 Praesenz + 0,3 Position + 0,2 Persistenz (x100).</div>
</div>
<script>
const DATA = __DATA__;
let current = DATA.clusters[0];
let barChart;
function escapeHtml(s){return (s||'').replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function fmtAbove(r){
  if(r.is_ergo) return '–';
  if(r.common_n) return r.above_ergo_n+'/'+r.common_n+' ('+Math.round(r.above_ergo_pct)+'%)';
  return '–';
}
function fmtRun(ts){ return (ts||'').replace('T',' ').slice(5,16); }
function adDetail(r){
  var h='<div class="addet">';
  if(r.live_ads && r.live_ads.length){
    h += r.live_ads.map(function(a){
      var er = (a.ergo_rank==null) ? 'ERGO nicht live' : ('ERGO Pos '+a.ergo_rank);
      var above = a.above_ergo ? '<span class="ab">ueber ERGO</span>' : '';
      var link = a.url ? '<div class="adl"><a href="'+escapeHtml(a.url)+'" target="_blank" rel="noopener">Landingpage ↗</a></div>' : '';
      return '<div class="adrow"><div class="adkw">'+escapeHtml(a.keyword)+' · Pos '+a.rank+' ('+er+') '+above+'</div>'
        + '<div class="adh">'+escapeHtml(a.headline||'(kein Titel)')+'</div>'
        + (a.description?'<div class="add">'+escapeHtml(a.description)+'</div>':'') + link + '</div>';
    }).join('');
  } else {
    h += '<div class="adkw">Keine Live-Anzeige im letzten Lauf erfasst.</div>';
  }
  var c = r.creatives;
  if(c){
    var act = (c.first_shown||c.last_shown) ? ('aktiv '+escapeHtml((c.first_shown||'').slice(0,10))+' – '+escapeHtml((c.last_shown||'').slice(0,10))) : '';
    h += '<div class="cre"><b>Kreative (Ads Transparency):</b> '+(c.n||0)+' Anzeige(n)'
      + (c.formats?' · Formate: '+escapeHtml(c.formats):'') + (act?' · '+act:'')
      + (c.transparency?' · <a href="'+escapeHtml(c.transparency)+'" target="_blank" rel="noopener">in Google Ads Transparency ansehen ↗</a>':'')
      + (c.preview?'<img src="'+escapeHtml(c.preview)+'" alt="Anzeigen-Vorschau">':'') + '</div>';
  }
  return h + '</div>';
}
document.getElementById('sub').textContent = 'Stand: ' + DATA.current_week + ' - Lauf ' + fmtRun(DATA.current_run) + ' - Provider ' + DATA.provider;
document.getElementById('gen').textContent = DATA.generated;
document.getElementById('runBtn').href = DATA.routine_url;
var kpiDefs = [['bidders','Bieter (Transparency)'],['live_ads','Live-Anzeigen'],['trademark','Trademark-Kandidaten']];
document.getElementById('kpis').innerHTML = kpiDefs.map(function(k){
  return '<div class="kpi"><div class="n">'+DATA.kpis[k[0]]+'</div><div class="l">'+k[1]+'</div></div>';
}).join('');
document.getElementById('tabs').innerHTML = DATA.clusters.map(function(c){
  return '<button class="tab" data-c="'+escapeHtml(c)+'">'+escapeHtml(c)+'</button>';
}).join('');
Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){
  b.addEventListener('click', function(){ current = b.dataset.c; render(); });
});
function render(){
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function(b){
    b.classList.toggle('active', b.dataset.c === current);
  });
  document.getElementById('clName').textContent = '- ' + current;
  var rows = (DATA.scores[current]||[]).slice(0,20);
  var barRows = rows.slice(0,12);
  var labels = barRows.map(function(r){return r.name||r.domain;});
  var vals = barRows.map(function(r){return r.score;});
  var colors = barRows.map(function(r){return r.is_ergo ? '#9aa0a6' : '#c8102e';});
  if(barChart) barChart.destroy();
  barChart = new Chart(document.getElementById('barChart'), {
    type:'bar',
    data:{labels:labels,datasets:[{data:vals,backgroundColor:colors,borderRadius:4}]},
    options:{indexAxis:'y',plugins:{legend:{display:false}},scales:{x:{max:100,title:{display:true,text:'Score'}}}}
  });
  var d = DATA.delta[current] || {};
  document.getElementById('delta').innerHTML = d.prev_run
    ? '<b>vs Vorlauf</b> - <span class="new">NEU: '+(d.new.length?d.new.join(', '):'-')+'</span> - <span class="gone">WEG: '+(d.gone.length?d.gone.join(', '):'-')+'</span>'
    : 'Kein Vorlauf (Basislauf).';
  var tb = document.querySelector('#bidTable tbody');
  tb.innerHTML = rows.map(function(r,i){
    var nm = escapeHtml(r.name||r.domain) + (r.is_ergo?'<span class="badge">ERGO</span>':'') + (r.verified?'<span class="vbadge">verifiziert</span>':'');
    var main = '<tr class="bid"><td class="exp">&#9656;</td><td>'+(i+1)+'</td><td class="'+(r.is_ergo?'ergo':'')+'">'+nm+'</td>'
      + '<td class="num">'+r.score+'</td><td class="num">'+Math.round(r.presence_pct)+'%</td>'
      + '<td class="num">'+r.best_rank+'</td><td>'+fmtAbove(r)+'</td><td class="num">'+(r.approx_ads||'–')+'</td><td>'+r.persistence_wk+'</td></tr>';
    var det = '<tr class="det" style="display:none"><td colspan="9">'+adDetail(r)+'</td></tr>';
    return main+det;
  }).join('');
  Array.prototype.forEach.call(document.querySelectorAll('#bidTable tbody tr.bid'), function(tr){
    tr.addEventListener('click', function(){
      var dd = tr.nextElementSibling, open = dd.style.display==='none';
      dd.style.display = open?'table-row':'none';
      tr.querySelector('.exp').innerHTML = open?'&#9662;':'&#9656;';
    });
  });
}
function lineChart(id, labels, datasets, yTitle){
  return new Chart(document.getElementById(id), {
    type:'line',
    data:{labels:labels, datasets:datasets},
    options:{plugins:{legend:{position:'bottom',labels:{boxWidth:12,font:{size:11}}}},
      scales:{y:{beginAtZero:true,title:{display:true,text:yTitle}}},
      elements:{point:{radius:2}}}
  });
}
function renderTS(){
  var ts = DATA.ts; var labels = ts.runs.map(fmtRun);
  var pal = ['#c8102e','#2563eb','#15803d','#d97706','#7c3aed','#0891b2'];
  lineChart('tsDensity', labels, ts.clusters.map(function(c,i){
    return {label:c,data:ts.density[c],borderColor:pal[i%pal.length],backgroundColor:'transparent',tension:.25};
  }), 'Wettbewerber');
  lineChart('tsErgo', labels, [
    {label:'% Keywords mit ERGO',data:ts.ergo_presence,borderColor:'#c8102e',backgroundColor:'transparent',tension:.25,yAxisID:'y'},
    {label:'Ø ERGO-Rang',data:ts.ergo_avg_rank,borderColor:'#2563eb',backgroundColor:'transparent',tension:.25,spanGaps:true,yAxisID:'y1'}
  ], '% sichtbar');
  // zweite Achse fuer ERGO-Rang
  if(window.Chart){ var ec = Chart.getChart('tsErgo'); if(ec){ ec.options.scales.y1={position:'right',beginAtZero:true,reverse:true,title:{display:true,text:'Ø Rang'},grid:{drawOnChartArea:false}}; ec.update(); } }
  var bk = Object.keys(ts.bidder_intensity);
  lineChart('tsBidder', labels, bk.map(function(k,i){
    return {label:(ts.bidder_names[k]||k),data:ts.bidder_intensity[k],borderColor:pal[i%pal.length],backgroundColor:'transparent',tension:.25};
  }), 'Intensitaet');
  lineChart('tsTm', labels, [
    {label:'Trademark-Treffer',data:ts.trademark,borderColor:'#c8102e',backgroundColor:'rgba(200,16,46,.08)',fill:true,tension:.25}
  ], 'Treffer');
}
function renderTM(){
  var tb = document.querySelector('#tmTable tbody');
  if(!DATA.trademark.length){ tb.innerHTML='<tr><td colspan="4">Keine Treffer.</td></tr>'; return; }
  tb.innerHTML = DATA.trademark.map(function(r){
    return '<tr><td>'+escapeHtml(r.name)+'</td><td>'+escapeHtml(r.cluster)+'</td><td>'+escapeHtml(r.keyword)+'</td><td>'+escapeHtml(r.headline)+'</td></tr>';
  }).join('');
}
render(); renderTS(); renderTM();
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
