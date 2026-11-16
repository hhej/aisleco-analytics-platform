"""Bronze → Silver (Spec 02 §4–5). One DuckDB SQL file per Silver table (silver/sql/NN_<name>.sql), run in order.

Contract for a SQL file: a single SELECT (may start with WITH). It can read
  crm.<Table>                  current state of a CRM table from Bronze (latest version per key, deletes removed;
                               incremental tables also carry _run_id = the run whose extract delivered that version)
  bronze.chat                  every Bronze chat line: received_at, platform, body (raw string), body_sha256, filename, _run_id
  bronze.fair_files            every Bronze tablet file: filename, content (raw text), _run_id
  ext.holidays(date, name)     the latest holiday-calendar fetch, parsed
  ext.auspicious               rows of the latest file of each year (calendar_date, date_type, auspicious_level, description, filename)
  ref.intent_pattern           (intent, pattern) from silver/intents.yml (case-insensitive regular expressions)
  ref.run(run_id, asof)        the current run and its as-of date; ref.settings(key, value): pii_salt
  any Silver table built by an earlier file, by its name
and may output these columns, which the runner removes from the published table:
  _reject  VARCHAR    rule ID (rules.yml) that rejects the row → quarantine, not published
  _warn    VARCHAR[]  rule IDs that flag the row (counted, row kept)
  _key     VARCHAR    the record's key, for the quarantine index
  _run_id  INTEGER    the pipeline run that delivered the row (reject-rate rule S01)
Tables named restricted_<x> are written to silver_restricted/<x>.
"""
import json
import logging
import os
import re
import shutil
from pathlib import Path

import duckdb
import yaml

from pipeline import db, meta
from pipeline.config import SOURCES
from pipeline.ingest.crm_cdc import sha256
from pipeline.quality import dq
from sources.external import holidays

log = logging.getLogger("pipeline")
SQL = Path(__file__).parent / "sql"
INTENTS = Path(__file__).parent / "intents.yml"


def crm_views(con, cfg, run_id):
    manifests = sorted((cfg.bronze / "crm" / "_manifests").glob("run=*/manifest.json"))
    if not manifests:
        raise RuntimeError("no CRM extract in Bronze yet")
    runs = [json.loads(p.read_text()) for p in manifests]
    current = next((m for m in runs if m["run_id"] == run_id), None)
    if current:                                     # refuse files that do not match the manifest
        for t, m in current["tables"].items():
            for k in ("file", "key_file"):
                if k in m and sha256(cfg.lake_dir / m[k]) != m[k.replace("file", "sha256")]:
                    raise RuntimeError(f"checksum mismatch: {m[k]} (Bronze file changed after extraction)")
    con.execute("CREATE SCHEMA IF NOT EXISTS crm")
    for src in SOURCES:
        parts = [(m["run_id"], m["tables"][src.table]) for m in runs if src.table in m["tables"]]
        types = parts[-1][1]["types"]
        cols = ", ".join(f"'{c}': '{t}'" for c, t in types.items())
        read = lambda f: f"read_csv('{cfg.lake_dir / f}', header=true, columns={{{cols}}}, nullstr='')"
        if src.kind == "incremental":
            union = " UNION ALL ".join(f"SELECT *, {r} AS _run_id FROM {read(m['file'])}" for r, m in parts)
            keys = read(parts[-1][1]["key_file"]).replace(f"columns={{{cols}}}", f"columns={{'{src.key}': 'INTEGER', 'Pending': 'BOOLEAN'}}")
            con.execute(f'''CREATE VIEW crm."{src.table}" AS SELECT * EXCLUDE (_rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY "{src.key}" ORDER BY "UpdatedAt" DESC, _run_id DESC) AS _rn FROM ({union}))
                WHERE _rn = 1 AND "{src.key}" IN (SELECT "{src.key}" FROM {keys})''')
        else:
            con.execute(f'CREATE VIEW crm."{src.table}" AS SELECT * FROM {read(parts[-1][1]["file"])}')


def file_views(con, cfg, conn):
    con.execute("CREATE SCHEMA IF NOT EXISTS bronze; CREATE SCHEMA IF NOT EXISTS ext; CREATE SCHEMA IF NOT EXISTS ref")
    cp = conn.execute("SELECT source, bronze_path, run_id FROM etl.file_checkpoint").fetchall()
    con.execute("CREATE TABLE ref.file_run(path VARCHAR, run_id INTEGER, source VARCHAR)")
    if cp:     # one bulk insert: executemany is row by row in DuckDB (seconds for a few years of hourly chat files)
        con.execute("INSERT INTO ref.file_run SELECT unnest(?), unnest(?), unnest(?)",
                    [[str(cfg.lake_dir / r["bronze_path"]) for r in cp], [r["run_id"] for r in cp], [r["source"] for r in cp]])
    chat = cfg.bronze / "chat"
    if any(chat.glob("platform=*")):
        # a table, not a view: two Silver files read it, and reading thousands of small files is the slow part
        con.execute(f'''CREATE TABLE bronze.chat AS SELECT c.*, f.run_id AS _run_id FROM read_json('{chat}/platform=*/*/*/events.jsonl',
            format='newline_delimited', columns={{received_at: 'VARCHAR', platform: 'VARCHAR', body: 'VARCHAR', body_sha256: 'VARCHAR'}},
            filename=true, hive_partitioning=false) c LEFT JOIN ref.file_run f ON f.path = c.filename''')
    else:
        con.execute("CREATE VIEW bronze.chat AS SELECT NULL::VARCHAR received_at, NULL::VARCHAR platform, NULL::VARCHAR body, "
                    "NULL::VARCHAR body_sha256, NULL::VARCHAR filename, NULL::INTEGER _run_id WHERE false")
    fair = cfg.bronze / "fair_tablet"
    con.execute(f'''CREATE VIEW bronze.fair_files AS SELECT t.filename, t.content, f.run_id AS _run_id
        FROM read_text('{fair}/*/*.json') t LEFT JOIN ref.file_run f ON f.path = t.filename''' if any(fair.glob("*/*.json")) else
                "CREATE VIEW bronze.fair_files AS SELECT NULL::VARCHAR filename, NULL::VARCHAR content, NULL::INTEGER _run_id WHERE false")
    last = holidays.latest(cfg.lake_dir)
    con.execute("CREATE TABLE ext.holidays(date DATE, name VARCHAR)")
    if last:
        con.executemany("INSERT INTO ext.holidays VALUES (?, ?)", holidays.parse(last))
    files = sorted((cfg.bronze / "external" / "auspicious").glob("year=*/*.csv"), key=lambda p: (p.parent.name, p.stat().st_mtime))
    latest = {p.parent.name: p for p in files}
    if latest:
        union = " UNION ALL ".join(f"SELECT *, '{p}' AS filename FROM read_csv('{p}', header=true, all_varchar=true)" for p in latest.values())
        con.execute(f"CREATE VIEW ext.auspicious AS {union}")
    else:
        con.execute("CREATE VIEW ext.auspicious AS SELECT NULL::VARCHAR calendar_date, NULL::VARCHAR date_type, "
                    "NULL::VARCHAR auspicious_level, NULL::VARCHAR description, NULL::VARCHAR filename WHERE false")
    con.execute("CREATE TABLE ref.intent_pattern(intent VARCHAR, pattern VARCHAR)")
    if INTENTS.exists():
        con.executemany("INSERT INTO ref.intent_pattern VALUES (?, ?)", [(k, p) for k, ps in yaml.safe_load(INTENTS.read_text()).items() for p in ps])


def preview(cfg, conn, run_id, asof):
    """A DuckDB connection with every input registered, for developing and testing Silver SQL."""
    con = duckdb.connect()
    crm_views(con, cfg, run_id)
    file_views(con, cfg, conn)
    con.execute("CREATE TABLE ref.run AS SELECT ?::INTEGER AS run_id, ?::DATE AS asof", [run_id, asof])
    con.execute("CREATE TABLE ref.settings AS SELECT 'pii_salt' AS key, ? AS value", [os.environ.get("PII_SALT", "dev-salt")])
    return con


def run(ctx, conn):
    cfg = ctx.cfg
    build = cfg.lake_dir / "silver" / "_build" / f"run={ctx.run_id:05d}"
    shutil.rmtree(build, ignore_errors=True)
    con = preview(cfg, conn, ctx.run_id, ctx.asof)
    # DQ results and the quarantine index are written outside the step's transaction, so a blocked run keeps its evidence
    dqc = db.connect(cfg.dw_dsn, cfg, autocommit=True, purpose="warehouse (dq log)")
    only = os.environ.get("SILVER_ONLY")          # development: run only files with these number prefixes
    stats, rejected = {}, {}
    for f in sorted(SQL.glob("*.sql")):
        if only and f.name.split("_")[0] not in only.split(","):
            continue
        name = re.sub(r"^\d+_", "", f.stem)
        con.execute(f"CREATE TABLE _all_{name} AS {f.read_text()}")
        cols = [c[0] for c in con.execute(f"DESCRIBE _all_{name}").fetchall()]
        keep = [c for c in cols if not c.startswith("_")]
        has = lambda c: c in cols
        where = "WHERE _reject IS NULL" if has("_reject") else ""
        con.execute(f'CREATE TABLE "{name}" AS SELECT {", ".join(chr(34) + c + chr(34) for c in keep)} FROM _all_{name} {where}')
        n_all = con.execute(f"SELECT count(*) FROM _all_{name}").fetchone()[0]
        n_new = con.execute(f"SELECT count(*) FROM _all_{name} WHERE _run_id = {ctx.run_id}").fetchone()[0] if has("_run_id") else None
        rej = con.execute(f"SELECT * FROM _all_{name} WHERE _reject IS NOT NULL").fetchdf() if has("_reject") else None
        warns = dict(con.execute(f"SELECT w, count(*) FROM (SELECT unnest(_warn) w FROM _all_{name}) GROUP BY 1").fetchall()) if has("_warn") else {}
        dq.record_rows(dqc, ctx, name, n_all, n_new, rej, warns, cfg)
        rejected[name] = 0 if rej is None else len(rej)
        out = cfg.lake_dir / "silver_restricted" / "_build" / name.removeprefix("restricted_") if name.startswith("restricted_") else build / name
        out.mkdir(parents=True, exist_ok=True)
        con.execute(f"COPY \"{name}\" TO '{out / 'data.parquet'}' (FORMAT parquet, COMPRESSION zstd)")
        stats[name] = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
    try:
        dq.set_checks(dqc, ctx, con)       # block / warn rules over the whole build; raises on a block
    finally:
        dqc.close()
    # publish: every table swapped in only now (a failed build leaves the published Silver untouched)
    for root, b in ((cfg.lake_dir / "silver", build), (cfg.lake_dir / "silver_restricted", cfg.lake_dir / "silver_restricted" / "_build")):
        if not b.exists():
            continue
        for t in b.iterdir():
            cur, prev = root / t.name, root / "_prev" / t.name
            prev.parent.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(prev, ignore_errors=True)
            if cur.exists():
                cur.rename(prev)
            t.rename(cur)
        shutil.rmtree(b, ignore_errors=True)
    shutil.rmtree(cfg.lake_dir / "silver" / "_build", ignore_errors=True)
    con.close()
    return {"rows_out": sum(stats.values()), "tables": stats, "rejected": {k: v for k, v in rejected.items() if v}}
