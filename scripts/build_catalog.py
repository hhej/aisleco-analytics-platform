"""Data catalog page (M8): catalog/tables.yml + catalog/kpis.yml → docs/v2/catalog.html (one offline HTML file).

First checks the catalog against what really exists, and stops with the list of problems if anything is missing:
  * every column of every CRM table, Silver Parquet table, Gold table, mart view, feature/ml/etl table is documented
    (and no documented column has disappeared);
  * every lineage reference (from, built_from, examples) points to a documented table.column;
  * every KPI id used by a tile in app/web/src exists in kpis.yml, and every KPI's source names real mart columns.

    .venv/bin/python scripts/build_catalog.py            check, then write docs/v2/catalog.html
    .venv/bin/python scripts/build_catalog.py --check    check only
"""
import argparse
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import psycopg
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.config import Config  # noqa: E402

CATALOG, WEB, OUT = ROOT / "catalog", ROOT / "app/web/src", ROOT / "docs/v2/catalog.html"
DW_SCHEMAS = ("gold", "mart_sales", "mart_ops", "mart_exec", "mart_platform", "sales_pii", "feature", "ml", "etl")
PAGES = {"Today": "Sales: Today", "SalesPerformance": "Sales performance", "Capacity": "Capacity planner",
         "Overview": "Business overview", "Health": "Data & ML health"}
KPI_RX = re.compile(r'(?:\bkpi=|<Info id=)\{?"([a-z0-9_]+)"')


# ---------------------------------------------------------------- inputs
def load():
    cat = yaml.safe_load((CATALOG / "tables.yml").read_text())
    kpis = yaml.safe_load((CATALOG / "kpis.yml").read_text())
    return cat, kpis


def introspect(cfg=None) -> dict[str, list[str]]:
    """Column names of every live table: {table id: [columns in order]}."""
    cfg = cfg or Config()
    live = {}
    q = """SELECT table_schema, table_name, column_name FROM information_schema.columns WHERE table_schema = ANY(%s)
           ORDER BY table_schema, table_name, ordinal_position"""
    for dsn, schemas, prefix in ((cfg.crm_dsn, ["master", "transaction"], "crm."), (cfg.dw_dsn, list(DW_SCHEMAS), "")):
        with psycopg.connect(dsn) as c:
            for s, t, col in c.execute(q, (schemas,)):
                live.setdefault(f"{prefix}{s}.{t}", []).append(col)
    for zone in ("silver", "silver_restricted"):
        for p in sorted((cfg.lake_dir / zone).glob("*/data.parquet")):
            if not p.parent.name.startswith("_"):
                live[f"{zone}.{p.parent.name}"] = [r[0] for r in duckdb.sql(f"DESCRIBE SELECT * FROM '{p}'").fetchall()]
    return live


def app_kpi_ids() -> dict[str, list[str]]:
    """KPI id → dashboard pages that show it (from the React source)."""
    used = {}
    for f in sorted(WEB.rglob("*.tsx")):
        for k in KPI_RX.findall(f.read_text()):
            used.setdefault(k, [])
            if f.stem in PAGES and PAGES[f.stem] not in used[k]:
                used[k].append(PAGES[f.stem])
    order = list(PAGES.values())
    return {k: sorted(v, key=order.index) for k, v in used.items()}


# ---------------------------------------------------------------- model
def expand(cat, live):
    """tables by id; copy_of filled in (columns of the source table that the live object has)."""
    tables = {t["id"]: dict(t, columns=dict(t.get("columns") or {})) for t in cat["tables"]}
    for t in tables.values():
        src = t.get("copy_of")
        if not src:
            continue
        own, names = t["columns"], live.get(t["id"]) or list(tables[src]["columns"])
        t["columns"] = {c: own.get(c) or {"meaning": (tables[src]["columns"].get(c) or {}).get("meaning", ""),
                                           "rule": f"copied from {src}", "from": [f"{src}.{c}"]} for c in names}
        t["columns"].update({c: v for c, v in own.items() if c not in t["columns"]})
    return tables


def split_ref(ref, ids):
    """'gold.FactInquiry.IsBooked' → ('gold.FactInquiry', 'IsBooked'); the longest table id wins (JSON paths contain dots)."""
    for i in ids:
        if ref.startswith(i + "."):
            return i, ref[len(i) + 1:]
    return None, None


def kpi_refs(k, tables):
    """Parse the free-text `source` of a KPI into mart column refs. Returns (refs, problems)."""
    refs, bad = [], []
    for m in re.finditer(r'(mart_\w+\.v_\w+)(\.[\w, ]+|\s*\([^)]*\))?|transaction\."(\w+)"', k.get("source", "")):
        if m.group(3):
            refs.append(f"crm.transaction.{m.group(3)}")
            continue
        view, cols = m.group(1), m.group(2)
        if view not in tables:
            bad.append(f"KPI {k['id']}: source names {view}, which is not in tables.yml")
            continue
        found = [f"{view}.{w}" for w in re.findall(r"\w+", cols or "") if w in tables[view]["columns"]]
        if cols and not found:
            bad.append(f"KPI {k['id']}: none of '{cols.strip()}' is a column of {view}")
        refs += found or [view]
    if not refs and not bad:
        bad.append(f"KPI {k['id']}: source names no mart view")
    return refs, bad


def check(cat, kpis, live, used=None) -> list[str]:
    tables = expand(cat, live)
    ids = sorted(tables, key=len, reverse=True)
    errs = []
    for tid, cols in live.items():
        if tid not in tables:
            errs.append(f"table {tid} exists but is not in catalog/tables.yml")
            continue
        doc = tables[tid]["columns"]
        errs += [f"column {tid}.{c} exists but is not documented" for c in cols if c not in doc]
        errs += [f"column {tid}.{c} is documented but does not exist" for c in doc if c not in cols]
    for tid, t in tables.items():
        for c, v in t["columns"].items():
            if not (v or {}).get("meaning"):
                errs.append(f"column {tid}.{c} has no meaning")
            for r in (v or {}).get("from", []):
                rt, rc = split_ref(r, ids)
                if rt is None or rc not in tables[rt]["columns"]:
                    errs.append(f"{tid}.{c}: lineage reference {r} is not a documented table.column")
        errs += [f"{tid}: built_from {b} is not in tables.yml" for b in t.get("built_from", []) if b not in tables]
    for ex in cat.get("examples", []):
        for s in ex["steps"]:
            for r in s.get("refs", []):
                rt, rc = split_ref(r, ids)
                if rt is None or rc not in tables[rt]["columns"]:
                    errs.append(f"example {ex['id']}: reference {r} is not a documented table.column")
    known = {k["id"] for k in kpis["kpis"]}
    errs += [f"KPI {k} is shown in app/web/src but missing from catalog/kpis.yml" for k in sorted(used or app_kpi_ids()) if k not in known]
    for k in kpis["kpis"]:
        errs += kpi_refs(k, tables)[1]
    return errs


# ---------------------------------------------------------------- page
def page_data(cat, kpis, live):
    tables = expand(cat, live)
    used = app_kpi_ids()
    k_out = []
    for k in kpis["kpis"]:
        k_out.append(dict(k, refs=kpi_refs(k, tables)[0], pages=used.get(k["id"], [])))
    crm_bronze = {src: t["id"] for t in tables.values() if t["layer"] == "bronze" for src in t.get("built_from", [])}
    n_cols = sum(len(t["columns"]) for t in tables.values())
    return {"layers": cat["layers"], "tables": list(tables.values()), "kpis": k_out, "examples": cat.get("examples", []),
            "thresholds": kpis.get("status_thresholds", {}), "via": crm_bronze, "pages": list(PAGES.values()),
            "counts": {"tables": len(tables), "columns": n_cols, "kpis": len(k_out)},
            "generated": datetime.now().strftime("%d %b %Y %H:%M")}


def build_html(data) -> str:
    blob = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    return TEMPLATE.replace("/*DATA*/", blob).replace("{{generated}}", html.escape(data["generated"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="check only, do not write the page")
    a = ap.parse_args()
    cat, kpis = load()
    try:
        live = introspect()
    except psycopg.OperationalError as e:
        sys.exit(f"cannot reach the databases to check the catalog: {e}")
    errs = check(cat, kpis, live)
    if errs:
        print(f"catalog incomplete: {len(errs)} problem(s)", *errs, sep="\n  ", file=sys.stderr)
        sys.exit(1)
    data = page_data(cat, kpis, live)
    c = data["counts"]
    print(f"catalog complete: {c['tables']} tables, {c['columns']} columns, {c['kpis']} KPIs")
    if not a.check:
        OUT.write_text(build_html(data), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size // 1024} KB)")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aisle &amp; Co. data catalog</title>
<style>
:root{--page:#FAF7F2;--surface:#fff;--line:#ECE6DD;--hair:#F3EFE9;--ink:#1F1B18;--ink2:#5A534C;--ink3:#857D74;
  --wine:#7A2E3B;--wine50:#F8EFF1;--wine100:#F0DDE1;--wine300:#C98893;--wine600:#62232F;--stone:#EFEAE3;--cell:#F6F3EE}
*{box-sizing:border-box}
html{scroll-padding-top:84px}
body{margin:0;background:var(--page);color:var(--ink);font:14px/1.55 Inter,"Noto Sans Thai",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
code,.mono{font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
a{color:var(--wine);text-decoration:none}a:hover{text-decoration:underline}
h1,h2,h3{letter-spacing:-.01em;margin:0}
:focus-visible{outline:2px solid var(--wine);outline-offset:2px;border-radius:4px}
.wrap{display:flex;min-height:100vh}
nav.side{width:250px;flex-shrink:0;position:sticky;top:0;height:100vh;overflow:auto;background:var(--surface);border-right:1px solid var(--line);padding:0 10px 40px}
.brand{display:flex;align-items:center;gap:10px;height:64px;padding:0 8px;border-bottom:1px solid var(--line);margin:0 -10px 10px;padding-left:18px}
.logo{width:32px;height:32px;border-radius:8px;background:var(--wine);color:#fff;display:grid;place-items:center;font:17px Georgia,serif}
.side h4{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3);margin:16px 8px 4px;font-weight:600}
.side a{display:block;padding:3px 8px;border-radius:6px;color:var(--ink2);font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.side a:hover{background:var(--hair);color:var(--ink);text-decoration:none}
.side a.top{font-weight:600;color:var(--ink);font-size:13px}
main{flex:1;min-width:0;padding:0 32px 120px}
header.bar{position:sticky;top:0;z-index:20;background:rgba(250,247,242,.92);backdrop-filter:blur(6px);border-bottom:1px solid var(--line);
  display:flex;align-items:center;gap:16px;height:64px;margin:0 -32px 24px;padding:0 32px}
header.bar .t{font-weight:600}
header.bar .s{color:var(--ink3);font-size:12.5px}
#q{margin-left:auto;width:min(360px,45vw);border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:7px 12px;font:inherit}
#q:focus{border-color:var(--wine300);outline:none}
.hits{font-size:12px;color:var(--ink3);white-space:nowrap}
section{margin:0 0 40px}
section>h2{font-size:20px;margin:0 0 4px}
.lead{color:var(--ink2);margin:0 0 16px;max-width:900px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:0 1px 2px rgba(31,27,24,.04);margin:0 0 16px}
.card>.h{padding:14px 18px 8px;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.card>.h h3{font-size:15px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:600}
.card .b{padding:0 18px 14px}
dl.meta{display:grid;grid-template-columns:120px 1fr;gap:3px 14px;margin:0 0 12px;font-size:13px}
dl.meta dt{color:var(--ink3)}dl.meta dd{margin:0;color:var(--ink2)}
.badge{display:inline-block;font-size:10.5px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;padding:1px 7px;border-radius:999px;background:var(--stone);color:var(--ink2);vertical-align:1px;white-space:nowrap}
.badge.source{background:#E8EEF3;color:#2D4A63}.badge.bronze{background:#F3E6D8;color:#7A4A1E}.badge.silver,.badge.silver_restricted{background:#ECEDEF;color:#4A4F57}
.badge.gold{background:#F7EDCF;color:#7A5B00}.badge.mart{background:var(--wine50);color:var(--wine)}.badge.feature{background:#E7F0EA;color:#2F5D3D}.badge.govern{background:var(--stone);color:var(--ink2)}
.badge.kpi{background:var(--wine);color:#fff}.badge.tile{background:var(--ink);color:#fff}
.tw{overflow-x:auto}
table.cols{width:100%;border-collapse:collapse;font-size:13px}
table.cols th{text-align:left;font-weight:500;color:var(--ink3);font-size:12px;padding:6px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
table.cols td{padding:6px 10px;border-bottom:1px solid var(--hair);vertical-align:top}
table.cols tr:last-child td{border-bottom:0}
table.cols td:first-child{white-space:nowrap}
table.cols td code{color:var(--ink);background:var(--cell);padding:1px 4px;border-radius:4px;white-space:pre-wrap;word-break:break-word}
tr.flash td{animation:fl 2.2s ease-out}@keyframes fl{0%{background:var(--wine100)}100%{background:transparent}}
button.col{all:unset;cursor:pointer;font:600 12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--wine);border-bottom:1px dashed var(--wine300)}
button.col:hover{color:var(--wine600)}
.ref{font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:nowrap}
.muted{color:var(--ink3)}.small{font-size:12px}
.btn{all:unset;cursor:pointer;display:inline-flex;align-items:center;gap:6px;border:1px solid var(--line);background:var(--surface);border-radius:8px;padding:4px 10px;font-size:12.5px;font-weight:500;color:var(--ink2)}
.btn:hover{background:var(--hair)}.btn.primary{background:var(--wine);border-color:var(--wine);color:#fff}.btn.primary:hover{background:var(--wine600)}
svg.flow{width:100%;height:auto;display:block}
svg.flow .box{fill:var(--surface);stroke:var(--line);stroke-width:1.2}svg.flow a:hover .box{stroke:var(--wine);fill:var(--wine50)}
svg.flow .lbl{font:600 13px Inter,system-ui,sans-serif;fill:var(--ink)}svg.flow .sub{font:11px Inter,system-ui,sans-serif;fill:var(--ink3)}
svg.flow .arr{stroke:var(--wine300);stroke-width:1.6;fill:none}svg.flow .dash{stroke-dasharray:4 3}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}
.kpi .h h3{font-family:inherit;font-size:15px}
.kpi .f{background:var(--cell);border-radius:8px;padding:8px 10px;margin:6px 0 10px}
.kpi dl.meta{grid-template-columns:90px 1fr}
ol.steps{margin:0;padding:0 0 0 20px}ol.steps li{margin:0 0 8px}ol.steps .w{font-weight:600}
/* drawer */
#drawer{position:fixed;top:0;right:0;height:100vh;width:min(760px,100vw);background:var(--surface);border-left:1px solid var(--line);
  box-shadow:0 8px 30px rgba(31,27,24,.14);z-index:50;transform:translateX(105%);transition:transform .18s ease;display:flex;flex-direction:column}
#drawer.open{transform:none}
#drawer .dh{display:flex;align-items:center;gap:10px;padding:14px 20px;border-bottom:1px solid var(--line)}
#drawer .dh h3{font-size:15px;flex:1;min-width:0;word-break:break-word}
#drawer .db{overflow:auto;padding:16px 20px 60px}
#drawer h4{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--ink3);margin:18px 0 8px}
ul.tree,ul.tree ul{list-style:none;margin:0;padding:0}
ul.tree ul{margin-left:14px;padding-left:14px;border-left:1.5px solid var(--wine100)}
ul.tree li{margin:6px 0}
ul.tree .n{background:var(--cell);border:1px solid var(--hair);border-radius:8px;padding:6px 10px}
ul.tree .n .rule{display:block;margin-top:3px;font:11.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--ink2);white-space:pre-wrap;word-break:break-word}
ul.tree .n .mean{display:block;font-size:12.5px;color:var(--ink2)}
ul.tree li.end>.n{background:#EEF3F7;border-color:#DCE6EE}
details>summary{cursor:pointer;color:var(--wine);font-weight:500;font-size:13px}
#scrim{position:fixed;inset:0;background:rgba(31,27,24,.18);z-index:40;display:none}#scrim.open{display:block}
.hide{display:none!important}
@media(max-width:1000px){nav.side{display:none}main{padding:0 16px 80px}header.bar{margin:0 -16px 16px;padding:0 16px}.grid2{grid-template-columns:1fr}header.bar .s{display:none}}
</style>
</head>
<body>
<div class="wrap">
<nav class="side" id="side" aria-label="Catalog contents"></nav>
<main>
<header class="bar">
  <div><div class="t">Data catalog</div><div class="s">Every table, column and KPI: what it means, how it is calculated, where it comes from.</div></div>
  <input id="q" type="search" placeholder="Search tables, columns, KPIs…" aria-label="Search the catalog" autocomplete="off">
  <span class="hits" id="hits"></span>
</header>
<div id="app"></div>
<p class="muted small">Generated {{generated}} by <code>scripts/build_catalog.py</code> from <code>catalog/tables.yml</code> and <code>catalog/kpis.yml</code>, after checking them against the live databases and the Silver files.</p>
</main>
</div>
<div id="scrim"></div>
<aside id="drawer" aria-label="Lineage" aria-hidden="true"><div class="dh"><h3 id="dt"></h3><button class="btn" id="dx">Close</button></div><div class="db" id="db"></div></aside>
<script type="application/json" id="data">/*DATA*/</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const T = Object.fromEntries(D.tables.map(t => [t.id, t]));
const IDS = Object.keys(T).sort((a, b) => b.length - a.length);
const K = Object.fromEntries(D.kpis.map(k => [k.id, k]));
const LN = Object.fromEntries(D.layers.map(l => [l.id, l.name]));
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const aid = r => 'x-' + r.replace(/[^A-Za-z0-9_-]/g, m => '_' + m.charCodeAt(0).toString(16));
const split = r => { for (const i of IDS) if (r.startsWith(i + '.')) return [i, r.slice(i.length + 1)]; return [T[r] ? r : null, null]; };
const badge = l => `<span class="badge ${l}">${esc(LN[l] || l)}</span>`;
const refLink = r => `<a class="ref" href="#${aid(r)}">${esc(r)}</a>`;
const colBtn = r => `<button class="col" data-ref="${esc(r)}" title="Show lineage">${esc(split(r)[1] || r)}</button>`;

// downstream index: column → columns and KPIs built from it
const DOWN = {};
for (const t of D.tables) for (const [c, v] of Object.entries(t.columns)) for (const r of (v.from || [])) (DOWN[r] ||= []).push(t.id + '.' + c);
for (const k of D.kpis) for (const r of k.refs) (DOWN[r] ||= []).push('kpi:' + k.id);

// ------------------------------------------------------------ overview diagram
function flow() {
  const n = l => D.tables.filter(t => l.includes(t.layer)).length;
  const landing = D.tables.filter(t => t.landing).length;
  const st = [
    ['Sources', `${n(['source'])} systems / tables`, 'L-source'], ['Landing', `${landing} drop folders`, 'L-bronze'],
    ['Bronze', `${n(['bronze'])} raw feeds`, 'L-bronze'], ['Silver', `${n(['silver', 'silver_restricted'])} tables`, 'L-silver'],
    ['Gold', `${n(['gold'])} tables`, 'L-gold'], ['Marts', `${n(['mart'])} views`, 'L-mart'],
    ['Dashboards', `${D.pages.length} pages, ${D.kpis.length} KPIs`, 'kpis']];
  const W = 124, G = 22, Y = 18, H = 64;
  let s = `<svg class="flow" viewBox="0 0 ${7 * W + 6 * G + 4} 200" role="img" aria-label="Data flow from sources to dashboards">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0L10,5L0,10z" fill="#C98893"/></marker></defs>`;
  st.forEach(([t, sub, href], i) => {
    const x = 2 + i * (W + G);
    s += `<a href="#${href}"><rect class="box" x="${x}" y="${Y}" width="${W}" height="${H}" rx="10"/>
      <text class="lbl" x="${x + W / 2}" y="${Y + 28}" text-anchor="middle">${t}</text><text class="sub" x="${x + W / 2}" y="${Y + 47}" text-anchor="middle">${sub}</text></a>`;
    if (i < 6) s += `<path class="arr" d="M${x + W + 2},${Y + H / 2} L${x + W + G - 3},${Y + H / 2}" marker-end="url(#ah)"/>`;
  });
  const fx = 2 + 3 * (W + G) + W / 2 + G / 2, ex = 2 + 5 * (W + G);
  s += `<a href="#L-feature"><rect class="box dash" x="${fx}" y="128" width="${W + 20}" height="54" rx="10"/>
    <text class="lbl" x="${fx + (W + 20) / 2}" y="152" text-anchor="middle">Features &amp; models</text><text class="sub" x="${fx + (W + 20) / 2}" y="169" text-anchor="middle">${n(['feature'])} objects</text></a>
    <path class="arr dash" d="M${2 + 3 * (W + G) + W / 2},${Y + H + 2} L${fx + 14},126" marker-end="url(#ah)"/>
    <path class="arr dash" d="M${fx + W + 6},126 L${2 + 4 * (W + G) + W / 2},${Y + H + 3}" marker-end="url(#ah)"/>
    <a href="#L-govern"><rect class="box dash" x="${ex}" y="128" width="${W}" height="54" rx="10"/>
    <text class="lbl" x="${ex + W / 2}" y="152" text-anchor="middle">Run log (etl)</text><text class="sub" x="${ex + W / 2}" y="169" text-anchor="middle">${n(['govern'])} tables</text></a>
    <path class="arr dash" d="M${ex + W / 2},126 L${ex + W / 2},${Y + H + 3}" marker-end="url(#ah)"/></svg>`;
  return s;
}

// ------------------------------------------------------------ lineage trees
function node(ref, extra = '') {
  const [t, c] = split(ref), tb = T[t];
  if (!tb) return `<div class="n">${esc(ref)}</div>`;
  const col = c ? tb.columns[c] : null;
  return `<div class="n">${badge(tb.layer)} ${refLink(ref)}${col ? `<span class="mean">${esc(col.meaning)}</span>` : `<span class="mean">${esc(tb.grain)}</span>`}${col && col.rule ? `<span class="rule">${esc(col.rule)}</span>` : ''}${extra}</div>`;
}
function up(ref, seen) {
  const [t, c] = split(ref), tb = T[t];
  if (!tb) return `<li>${node(ref)}</li>`;
  const col = c ? tb.columns[c] : null;
  if (seen.has(ref)) return `<li>${node(ref, '<span class="small muted"> · shown above</span>')}</li>`;
  seen.add(ref);
  const kids = col ? (col.from || []) : [];
  let inner = kids.map(k => {
    const kt = split(k)[0];
    if ((tb.layer === 'silver' || tb.layer === 'silver_restricted') && D.via[kt]) {
      const b = T[D.via[kt]];
      return `<li><div class="n">${badge('bronze')} ${refLink(b.id)} <span class="mean">${esc(b.format)}: <code>${esc(b.path)}</code></span></div><ul>${up(k, seen)}</ul></li>`;
    }
    return up(k, seen);
  }).join('');
  let end = '';
  if (!kids.length && tb.layer === 'bronze')
    inner = (tb.built_from || []).map(s => `<li class="end"><div class="n">${badge('source')} ${refLink(s)}<span class="mean">${esc(T[s] && T[s].grain)}</span>${tb.landing ? `<span class="rule">via landing: ${esc(tb.landing)}</span>` : ''}</div></li>`).join('');
  else if (!kids.length && tb.layer === 'source') end = ' end';
  else if (!kids.length && col) inner = `<li class="end"><div class="n small muted">Created at this step (${esc(tb.built_by || '')})</div></li>`;
  return `<li class="${end}">${node(ref)}${inner ? `<ul>${inner}</ul>` : ''}</li>`;
}
function down(ref, seen) {
  return (DOWN[ref] || []).map(d => {
    if (d.startsWith('kpi:')) { const k = K[d.slice(4)]; return `<li class="end"><div class="n">${badge('kpi')} <a href="#k-${k.id}">${esc(k.label)}</a> <span class="small muted">${esc(k.pages.join(', ') || 'catalog only')}</span></div></li>`; }
    if (seen.has(d)) return `<li>${node(d, '<span class="small muted"> · shown above</span>')}</li>`;
    seen.add(d);
    const sub = down(d, seen);
    return `<li>${node(d)}${sub ? `<ul>${sub}</ul>` : ''}</li>`;
  }).join('');
}
function openColumn(ref) {
  const [t, c] = split(ref), col = T[t].columns[c];
  const dn = down(ref, new Set());
  show(`${esc(ref)}`, `${badge(T[t].layer)} <span class="muted small">${esc(T[t].grain)}</span>
    <p>${esc(col.meaning)}</p>${col.rule ? `<p><code>${esc(col.rule)}</code></p>` : ''}${col.additivity ? `<p class="small">Additivity: <b>${esc(col.additivity)}</b></p>` : ''}
    <h4>Where it comes from (upstream, to the source)</h4><ul class="tree">${up(ref, new Set())}</ul>
    <h4>What it feeds (downstream)</h4>${dn ? `<details${dn.length < 4000 ? ' open' : ''}><summary>Tables, views and KPIs built from it</summary><ul class="tree">${dn}</ul></details>` : '<p class="muted small">Nothing downstream reads this column.</p>'}`);
}
function openKpi(id) {
  const k = K[id], seen = new Set();
  const tiles = k.pages.length ? k.pages.map(p => `<span class="badge tile">${esc(p)}</span>`).join(' ') : '<span class="muted small">not a tile of its own</span>';
  show(`${esc(k.label)}`, `<p>${badge('kpi')} <code>${esc(k.id)}</code> · ${esc(k.unit)} · shown on ${tiles}</p><p>${esc(k.definition)}</p>
    <div class="kpi"><div class="f"><code>${esc(k.formula)}</code></div></div><p class="small">Level: ${esc(k.level)}</p>
    <h4>Lineage: dashboard tile → mart → Gold → Silver → Bronze → source</h4>
    <ul class="tree"><li><div class="n">${badge('tile')} <b>${esc(k.label)}</b><span class="mean">${esc(k.pages.join(', ') || 'catalog')}; reads ${esc(k.source)}</span></div><ul>${k.refs.map(r => up(r, seen)).join('')}</ul></li></ul>`);
}
function show(title, body) {
  document.getElementById('dt').innerHTML = title;
  document.getElementById('db').innerHTML = body;
  document.getElementById('db').scrollTop = 0;
  for (const id of ['drawer', 'scrim']) document.getElementById(id).classList.add('open');
  document.getElementById('drawer').setAttribute('aria-hidden', 'false');
}
function hideDrawer() { for (const id of ['drawer', 'scrim']) document.getElementById(id).classList.remove('open'); document.getElementById('drawer').setAttribute('aria-hidden', 'true'); }

// ------------------------------------------------------------ page
function tableCard(t) {
  const cols = Object.entries(t.columns), add = cols.some(([, v]) => v.additivity);
  const meta = [['Grain', esc(t.grain)], ['Built from', (t.built_from || []).map(refLink).join(', ')], ['Built by', t.built_by && `<code>${esc(t.built_by)}</code>`],
    ['Load rule', esc(t.load)], ['Access', esc(t.access)], ['Landing', t.landing && `<code>${esc(t.landing)}</code>`], ['Path', t.path && `<code>${esc(t.path)}</code>`],
    ['Format', esc(t.format)], ['Role-playing', esc(t.role_playing)], ['Copy of', t.copy_of && refLink(t.copy_of)], ['Rule', t.rule && `<code>${esc(t.rule)}</code>`], ['Note', esc(t.note)]]
    .filter(([, v]) => v).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
  const rows = cols.map(([c, v]) => `<tr id="${aid(t.id + '.' + c)}" data-s="${esc((c + ' ' + v.meaning + ' ' + (v.rule || '') + ' ' + (v.from || []).join(' ')).toLowerCase())}">
    <td>${colBtn(t.id + '.' + c)}</td><td>${esc(v.meaning)}</td><td>${v.rule ? `<code>${esc(v.rule)}</code>` : ''}</td>
    <td>${(v.from || []).map(refLink).join('<br>')}</td>${add ? `<td class="small">${esc(v.additivity || '')}</td>` : ''}</tr>`).join('');
  return `<div class="card tbl" id="${aid(t.id)}" data-s="${esc((t.id + ' ' + t.grain + ' ' + (t.built_by || '')).toLowerCase())}">
    <div class="h"><h3>${esc(t.id)}</h3>${badge(t.layer)}<span class="muted small">${cols.length} column${cols.length === 1 ? '' : 's'}</span></div>
    <div class="b"><dl class="meta">${meta}</dl>${cols.length ? `<div class="tw"><table class="cols"><thead><tr><th>Column</th><th>Meaning</th><th>Rule / formula</th><th>From (lineage)</th>${add ? '<th>Additivity</th>' : ''}</tr></thead><tbody>${rows}</tbody></table></div>` : ''}</div></div>`;
}
function kpiCard(k) {
  return `<div class="card kpi" id="k-${k.id}" data-s="${esc((k.id + ' ' + k.label + ' ' + k.definition + ' ' + k.formula + ' ' + k.source).toLowerCase())}">
    <div class="h"><h3>${esc(k.label)}</h3><code class="muted">${esc(k.id)}</code></div><div class="b">
    <div>${esc(k.definition)}</div><div class="f"><code>${esc(k.formula)}</code></div>
    <dl class="meta"><dt>Unit</dt><dd>${esc(k.unit)}</dd><dt>Level</dt><dd>${esc(k.level)}</dd>${k.target != null ? `<dt>Target</dt><dd>${esc(k.target)}</dd>` : ''}
    <dt>Reads</dt><dd>${k.refs.map(refLink).join(', ')}</dd>${k.pages.length > 1 ? `<dt>Also on</dt><dd>${esc(k.pages.slice(1).join(', '))}</dd>` : ''}</dl>
    <button class="btn primary" data-kpi="${k.id}">Show lineage to the source</button></div></div>`;
}
function render() {
  const groups = [...D.pages.map(p => [p, D.kpis.filter(k => k.pages[0] === p)]), ['Defined in the catalog (parts of other tiles)', D.kpis.filter(k => !k.pages.length)]];
  const th = Object.entries(D.thresholds).map(([n, v]) => `<tr><td><b>${esc(n)}</b></td><td>${Object.entries(v).map(([a, b]) => `<b>${esc(a)}</b> ${esc(b)}`).join(' · ')}</td></tr>`).join('');
  const ex = D.examples.map(e => `<div class="card"><div class="h"><h3 style="font-family:inherit">${esc(e.title)}</h3><span class="muted small">${esc(e.source)}</span></div><div class="b">
    <p class="lead">${esc(e.intro)}</p><ol class="steps">${e.steps.map(s => `<li><span class="w">${esc(s.where)}.</span> ${esc(s.what)} ${(s.refs || []).map(colBtn).join(' ')}</li>`).join('')}</ol></div></div>`).join('');
  const byLayer = D.layers.filter(l => l.id !== 'landing').map(l => {
    const ts = D.tables.filter(t => t.layer === l.id);
    return ts.length ? `<section class="layer" id="L-${l.id}"><h2>${esc(l.name)}</h2><p class="lead">${esc(l.note)}</p>${ts.map(tableCard).join('')}</section>` : '';
  }).join('');
  document.getElementById('app').innerHTML = `
    <section id="overview"><h2>How the data flows</h2>
      <p class="lead">${D.counts.tables} tables and views, ${D.counts.columns} columns, ${D.counts.kpis} KPIs. Click any column name (dashed underline) or a KPI's lineage button to follow a number back to the source column it is calculated from. Click a box to jump to that layer.</p>
      <div class="card"><div class="b" style="padding-top:14px">${flow()}</div></div></section>
    <section id="examples"><h2>Worked examples</h2><p class="lead">Three paths through the platform, from the specs. The column names in each step open their lineage.</p>${ex}</section>
    <section id="kpis"><h2>KPIs by dashboard page</h2><p class="lead">Every number shown in the web app. "Level" says where the division happens: a ratio of sums adds the numerator and the denominator over the whole group, then divides once.</p>
      ${groups.filter(([, ks]) => ks.length).map(([p, ks]) => `<h3 style="margin:18px 0 10px;font-size:15px" id="P-${esc(p.replace(/\W+/g, '-'))}">${esc(p)}</h3><div class="grid2">${ks.map(kpiCard).join('')}</div>`).join('')}
      <div class="card"><div class="h"><h3 style="font-family:inherit">Status thresholds</h3></div><div class="b"><table class="cols">${th}</table></div></div></section>
    ${byLayer}`;
  document.getElementById('side').innerHTML = `<div class="brand"><span class="logo">A</span><b>Aisle &amp; Co.</b></div>
    <a class="top" href="#overview">Data flow</a><a class="top" href="#examples">Worked examples</a><a class="top" href="#kpis">KPIs (${D.kpis.length})</a>
    ${D.layers.filter(l => l.id !== 'landing').map(l => { const ts = D.tables.filter(t => t.layer === l.id); return ts.length ? `<h4><a href="#L-${l.id}" style="padding:0;color:inherit">${esc(l.name)}</a></h4>${ts.map(t => `<a href="#${aid(t.id)}" title="${esc(t.id)}">${esc(t.id)}</a>`).join('')}` : ''; }).join('')}`;
}
render();

// ------------------------------------------------------------ search, clicks, hash
const q = document.getElementById('q');
q.addEventListener('input', () => {
  const s = q.value.trim().toLowerCase();
  let nt = 0, nc = 0, nk = 0;
  for (const id of ['overview', 'examples']) document.getElementById(id).classList.toggle('hide', !!s);
  for (const card of document.querySelectorAll('.card.tbl')) {
    const head = !s || card.dataset.s.includes(s);
    let any = false;
    for (const tr of card.querySelectorAll('tbody tr')) { const m = head || tr.dataset.s.includes(s); tr.classList.toggle('hide', !m); if (m) { any = true; if (s) nc++; } }
    const vis = head || any; card.classList.toggle('hide', !vis); if (vis) nt++;
  }
  for (const card of document.querySelectorAll('.card.kpi')) { const m = !s || card.dataset.s.includes(s); card.classList.toggle('hide', !m); if (m) nk++; }
  for (const sec of document.querySelectorAll('section.layer')) sec.classList.toggle('hide', !sec.querySelector('.card.tbl:not(.hide)'));
  document.getElementById('hits').textContent = s ? `${nt} tables · ${nc} columns · ${nk} KPIs` : '';
});
document.addEventListener('click', e => {
  const b = e.target.closest('button.col'); if (b) { openColumn(b.dataset.ref); return; }
  const k = e.target.closest('[data-kpi]'); if (k) { openKpi(k.dataset.kpi); return; }
  const a = e.target.closest('#drawer a[href^="#"]'); if (a) hideDrawer();
});
document.getElementById('dx').onclick = hideDrawer;
document.getElementById('scrim').onclick = hideDrawer;
document.addEventListener('keydown', e => { if (e.key === 'Escape') hideDrawer(); if (e.key === '/' && document.activeElement !== q) { e.preventDefault(); q.focus(); } });
function flash() {
  const el = location.hash && document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (!el) return;
  if (el.closest('.hide')) { q.value = ''; q.dispatchEvent(new Event('input')); }
  el.scrollIntoView({block: 'center'});
  if (el.tagName === 'TR') { el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash'); }
}
window.addEventListener('hashchange', flash); flash();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
