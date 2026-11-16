"""End-to-end replay (Ch.6 end-to-end testing): 30 simulated nights loaded incrementally must give exactly the
warehouse that a full refresh of the same final source state gives.

  .venv/bin/python scripts/e2e_replay.py [--nights 30] [--d0 2026-10-15]

Uses its own databases (aisleco_crm_e2e, aisleco_dw_e2e, aisleco_dw_e2e_ref), lakes under var/e2e/ and a gateway on
port 8011, so it never touches the main CRM, warehouse or lake. Writes the report to docs/v2/e2e_replay.md."""
import argparse
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / "var" / "e2e"
CRM, DW, DW_REF = "dbname=aisleco_crm_e2e", "dbname=aisleco_dw_e2e", "dbname=aisleco_dw_e2e_ref"
ENV = {**os.environ, "CRM_DSN": CRM, "DW_DSN": DW, "LAKE_DIR": str(E2E / "lake"), "SIM_STATE_DIR": str(E2E / "sim"),
       "GATEWAY_URL": "http://127.0.0.1:8011", "ETL_OFFLINE": "1", "PYTHONUNBUFFERED": "1"}
REF_ENV = {**ENV, "DW_DSN": DW_REF, "LAKE_DIR": str(E2E / "lake_ref")}
LOG = E2E / "replay.log"


def sh(env, *args):
    t0 = time.time()
    with open(LOG, "a") as log:
        log.write(f"\n$ {' '.join(args)}\n")
        log.flush()
        r = subprocess.run([sys.executable, "-m", *args], env=env, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f"failed: {' '.join(args)} (see {LOG})")
    return time.time() - t0


def last_run(dsn):
    with psycopg.connect(dsn, row_factory=dict_row) as c:
        run = c.execute("SELECT run_id, extract(epoch FROM finished_at - started_at)::float AS sec FROM etl.run "
                        "ORDER BY run_id DESC LIMIT 1").fetchone()
        m = {r["step"]: r["metrics"] for r in c.execute("SELECT step, metrics FROM etl.step_run WHERE run_id = %s AND status = 'success'",
                                                       (run["run_id"],))}
    changed = sum(m[s]["inserted"] + m[s]["updated"] + m[s]["deleted"] for s in m if s.startswith("fact_"))
    return run["sec"], m["ingest_crm"]["rows_in"], m["ingest_files"]["rows_in"], changed


def copy_files_to_ref_landing():
    """The reference lake gets the same chat, tablet and external files, in landing, so its ingest moves them."""
    src, dst = E2E / "lake" / "bronze", E2E / "lake_ref" / "landing"
    n = 0
    for f in sorted((src / "chat").glob("platform=*/received_date=*/hour=*/events.jsonl")):
        (dst / "chat" / f.relative_to(src / "chat")).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst / "chat" / f.relative_to(src / "chat"))
        n += 1
    for folder, pattern in (("fair_tablet", "*/*.json"), ("external", "auspicious/year=*/*.csv")):
        (dst / folder).mkdir(parents=True, exist_ok=True)
        for f in sorted((src / folder).glob(pattern)):
            shutil.copy2(f, dst / folder / f.name)
            n += 1
    return n


# ---------- comparison ----------
def dim_maps(c):
    """Surrogate key → natural value, so two warehouses whose keys were assigned in a different order compare equal."""
    return {"Channel": {r[0]: r[1] for r in c.execute('SELECT "ChannelKey", "ChannelID" FROM gold."DimChannel"')},
            "Package": {r[0]: r[1] for r in c.execute('SELECT "PackageKey", "PackageID" FROM gold."DimPackage"')},
            "Employee": {r[0]: r[1] for r in c.execute('SELECT "EmployeeKey", "ContractID" FROM gold."DimEmployee"')},
            "Outcome": {r[0]: r[1:] for r in c.execute('SELECT "OutcomeKey", "Outcome", "FurthestStage", "ClosedBy", "LostReason" '
                                                       'FROM gold."DimInquiryOutcome"')}}


def dim_of(col):
    if not col.endswith("Key") or "Date" in col:
        return None                                  # date keys are natural (YYYYMMDD)
    return next((d for d in ("Channel", "Package", "Outcome") if d in col), "Employee")


def table_rows(c, maps, schema, table):
    cur = c.execute(f'SELECT * FROM {schema}."{table}"')
    cols = [d.name for d in cur.description]
    own_key = {"DimChannel": "ChannelKey", "DimPackage": "PackageKey", "DimEmployee": "EmployeeKey", "DimInquiryOutcome": "OutcomeKey"}.get(table)
    keep = [(i, c_) for i, c_ in enumerate(cols) if c_ != own_key]
    return Counter(tuple(maps[dim_of(c_)].get(r[i], r[i]) if schema == "gold" and dim_of(c_) else r[i] for i, c_ in keep)
                   for r in cur.fetchall())


def compare(dsn_a=DW, dsn_b=DW_REF):
    """{table or view: rows that differ} and {table or view: rows} of two warehouses."""
    diffs, sizes = {}, {}
    with psycopg.connect(dsn_a) as a, psycopg.connect(dsn_b) as b:
        ma, mb = dim_maps(a), dim_maps(b)
        objects = [("gold", r[0]) for r in a.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'gold' "
                                                      "AND table_type = 'BASE TABLE' ORDER BY 1")]
        objects += [(r[0], r[1]) for r in a.execute("SELECT table_schema, table_name FROM information_schema.views "
                                                    "WHERE table_schema IN ('mart_ops', 'mart_sales', 'mart_exec') ORDER BY 1, 2")]
        for schema, t in objects:      # mart_platform is run metadata and differs by design
            ra, rb = table_rows(a, ma, schema, t), table_rows(b, mb, schema, t)
            sizes[f"{schema}.{t}"] = sum(ra.values())
            diffs[f"{schema}.{t}"] = sum(((ra - rb) + (rb - ra)).values())
    return diffs, sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nights", type=int, default=30)
    ap.add_argument("--d0", type=date.fromisoformat, default=date(2026, 10, 15))
    a = ap.parse_args()
    t_all = time.time()
    shutil.rmtree(E2E, ignore_errors=True)
    E2E.mkdir(parents=True)
    print(f"log: {LOG}")
    t = sh(ENV, "sim", "init", "--reset")
    t += sh(ENV, "sim", "backfill", "--to", str(a.d0))
    print(f"simulated 2022-07-01 → {a.d0} in {t:.0f}s")
    sh(ENV, "pipeline", "init", "--drop")
    t = sh(ENV, "pipeline", "run", "--asof", str(a.d0 + timedelta(1)), "--full-refresh")
    first = last_run(DW)
    print(f"initial full refresh as of {a.d0 + timedelta(1)}: {t:.0f}s")

    nights = []
    for i in range(1, a.nights + 1):
        d = a.d0 + timedelta(i)
        ts = sh(ENV, "sim", "backfill", "--to", str(d))
        tp = sh(ENV, "pipeline", "run", "--asof", str(d + timedelta(1)))
        sec, crm_rows, files, changed = last_run(DW)
        nights.append((d, ts, tp, sec, crm_rows, files, changed))
        print(f"night {i:2d} {d}: sim {ts:4.1f}s  pipeline {tp:5.1f}s (run {sec:5.1f}s)  CRM rows {crm_rows:5d}  files {files:3d}  "
              f"fact rows changed {changed:5d}", flush=True)
    final = a.d0 + timedelta(a.nights + 1)

    n_files = copy_files_to_ref_landing()
    sh(REF_ENV, "pipeline", "init", "--drop")
    t_ref = sh(REF_ENV, "pipeline", "run", "--asof", str(final), "--full-refresh")
    print(f"reference full refresh as of {final} ({n_files} files copied): {t_ref:.0f}s")
    diffs, sizes = compare()
    total = sum(diffs.values())
    for k, v in diffs.items():
        if v:
            print(f"  DIFFERENCE {k}: {v} row(s)")
    print(f"compared {len(diffs)} tables/views, {sum(sizes.values()):,} rows: {total} differences; whole script {time.time() - t_all:.0f}s")
    write_report(a, first, nights, t_ref, diffs, sizes, total, time.time() - t_all)
    return 1 if total else 0


def write_report(a, first, nights, t_ref, diffs, sizes, total, t_all):
    secs = sorted(n[3] for n in nights)
    lines = [
        "# End-to-end replay: incremental = full refresh",
        "",
        f"Generated by `scripts/e2e_replay.py` on {date.today()} (own databases `aisleco_crm_e2e`, `aisleco_dw_e2e`, "
        "`aisleco_dw_e2e_ref`; lakes under `var/e2e/`).",
        "",
        "Method: simulate 2022-07-01 → " f"{a.d0}, full refresh as of {a.d0 + timedelta(1)}; then {len(nights)} nights of "
        "`sim backfill --to D` + `pipeline run --asof D+1` (incremental). Reference: a full refresh of the same final CRM, "
        "with the same chat, tablet and external files dropped into a fresh lake. Compared: every Gold table "
        "(surrogate keys mapped to natural keys: ChannelID, PackageID, ContractID, outcome labels) and every row of every "
        "Sales, Operations and Executive mart view (multiset of rows). `mart_platform` is run metadata and is not compared.",
        "",
        f"**Result: {total} differences** over {len(diffs)} tables and views ({sum(sizes.values()):,} rows). "
        f"Initial full refresh: {first[0]:.0f} s. Reference full refresh: {t_ref:.0f} s. Whole script: {t_all / 60:.1f} min.",
        "",
        f"Incremental run time (etl.run started → finished): median {secs[len(secs) // 2]:.1f} s, max {secs[-1]:.1f} s.",
        "",
        "| Night | Sim day | Run (s) | CRM rows read (changes + master snapshots) | Files ingested | Gold fact rows changed |",
        "|---|---|---:|---:|---:|---:|",
    ]
    lines += [f"| {i} | {d} | {sec:.1f} | {crm:,} | {files} | {ch:,} |" for i, (d, _, _, sec, crm, files, ch) in enumerate(nights, 1)]
    lines += ["", "| Table / view | Rows | Differences |", "|---|---:|---:|"]
    lines += [f"| {k} | {sizes[k]:,} | {v} |" for k, v in diffs.items()]
    (ROOT / "docs" / "v2" / "e2e_replay.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
