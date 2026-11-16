"""CRM (OLTP v6) → Bronze (Spec 01 §2.3). Reused from v1's extractor, adapted to v6 and the lake layout.

* Transaction tables: incremental. Rows with UpdatedAt after the watermark; the read starts up to 5 minutes
  before the previous snapshot (late commits), never later than the watermark. A full refresh reads everything.
* Master tables: full snapshot every run (small). Personal-data tables: full snapshot of allowed columns only,
  into the restricted folder.
* Every incremental table also writes its full primary-key list (deletes are invisible to a timestamp).
* One shared database snapshot (pg_export_snapshot) for all parallel readers: a child row never arrives
  without the parent version it points to.
* Batch cap: at most max_rows_per_run changed transaction rows, oldest first; a taken row pulls its parents;
  the rest waits in the CRM (the queue); held-back keys are marked Pending.
* Orphans in quarantine (rejected because their parent was) are read again every run.
* Files go to bronze/_tmp first and move into place only when every table succeeded; manifest.json holds
  row counts, highest UpdatedAt, SHA-256 and column types (Silver reads with those types and checks the sums).
"""
import hashlib
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from psycopg import IsolationLevel, sql

from pipeline import db, meta
from pipeline.config import SOURCE_BY_TABLE, SOURCES

log = logging.getLogger("pipeline")
PG_TO_DUCK = {"integer": "INTEGER", "bigint": "BIGINT", "numeric": "DECIMAL(12,2)", "date": "DATE", "boolean": "BOOLEAN",
              "timestamp without time zone": "TIMESTAMP", "character varying": "VARCHAR", "character": "VARCHAR", "text": "VARCHAR"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_path(cfg, run_id):
    return cfg.bronze / "crm" / "_manifests" / f"run={run_id:05d}" / "manifest.json"


def read_from(wm, extracted_at, overlap):
    """Lower bound of the incremental read: the overlap reaches back from the previous snapshot, never from the
    watermark, so a run draining a backlog re-reads nothing."""
    if wm is None:
        return None
    return wm - overlap if extracted_at is None else min(wm, extracted_at - overlap)


def _changed(alias, since):
    return sql.SQL('{}."UpdatedAt" > {}').format(sql.Identifier(alias), sql.Literal(since)) if since is not None else sql.SQL("true")


def _taken(src, alias, cutoff, sinces, wms, depth=0):
    """This changed row is in the batch: changed up to the cutoff, a re-read at or below its own watermark (not counted
    in the cap), or a taken child of a capped table points to it (the parent is pulled forward)."""
    a = sql.Identifier(alias)
    pulls = [sql.SQL('{}."UpdatedAt" <= {}').format(a, sql.Literal(max(cutoff, wms.get(src.table) or cutoff)))]
    for child in SOURCES:
        for col, parent in child.parents:
            if parent == src.table and child.kind == "incremental":
                c = f"c{depth}_{child.table}"
                pulls.append(sql.SQL("{a}.{k} IN (SELECT {c}.{col} FROM {t} {c} WHERE {c}.{col} IS NOT NULL AND {d})").format(
                    t=sql.Identifier(child.schema, child.table), c=sql.Identifier(c), col=sql.Identifier(col),
                    a=a, k=sql.Identifier(src.key), d=_taken(child, c, cutoff, sinces, wms, depth + 1)))
    return sql.SQL("({} AND ({}))").format(_changed(alias, sinces[src.table]), sql.SQL(" OR ").join(pulls))


def _cutoff(cur, wms, cap):
    """UpdatedAt of the cap-th oldest change across the transaction tables, or None if within the cap."""
    parts = [sql.SQL('SELECT "UpdatedAt" AS u FROM {} {}').format(
                 sql.Identifier(s.schema, s.table),
                 sql.SQL('WHERE "UpdatedAt" > {}').format(sql.Literal(wms[s.table])) if wms.get(s.table) else sql.SQL(""))
             for s in SOURCES if s.kind == "incremental"]
    rows = cur.execute(sql.SQL("SELECT u FROM ({}) x ORDER BY u LIMIT 2 OFFSET {}").format(
        sql.SQL(" UNION ALL ").join(parts), sql.Literal(cap - 1))).fetchall()
    return rows[0]["u"] if len(rows) == 2 else None


def _columns(cur, src):
    rows = cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
                       "ORDER BY ordinal_position", (src.schema, src.table)).fetchall()
    types = {r["column_name"]: PG_TO_DUCK[r["data_type"]] for r in rows}
    cols = list(src.columns) or list(types)
    return {c: types[c] for c in cols}


def _extract_table(cfg, src, sinces, wms, snapshot, out_dir, cutoff=None, retry=()):
    conn = db.connect(cfg.crm_dsn, cfg, purpose=f"CRM ({src.table})")
    conn.isolation_level = IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET TRANSACTION SNAPSHOT {}").format(sql.Literal(snapshot)))
            types = _columns(cur, src)
            table = sql.SQL("{} r").format(sql.Identifier(src.schema, src.table))
            inc = src.kind == "incremental"
            since = sinces.get(src.table)
            capped = inc and cutoff is not None
            again = (sql.SQL("r.{} = ANY({})").format(sql.Identifier(src.key), sql.Literal(list(retry))) if retry else sql.SQL("false"))
            changed = sql.SQL("({} OR {})").format(_changed("r", since), again) if inc else sql.SQL("true")
            batch = sql.SQL("({} OR {})").format(_taken(src, "r", cutoff, sinces, wms), again) if capped else changed
            held = sql.SQL("{} AND NOT {}").format(changed, batch) if capped else sql.SQL("false")      # the queue
            where = sql.SQL("WHERE {}").format(batch)
            cols = sql.SQL(", ").join(sql.SQL("r.{}").format(sql.Identifier(c)) for c in types)
            stats = cur.execute(sql.SQL('SELECT count(*) AS n, max(r."UpdatedAt") AS hi FROM {} {}').format(table, where)).fetchone()
            backlog = cur.execute(sql.SQL("SELECT count(*) AS n FROM {} WHERE {}").format(table, held)).fetchone()["n"] if capped else 0
            hi = cutoff if capped else stats["hi"]
            files = {"file": out_dir / f"{src.table}.csv"}
            queries = [(files["file"], sql.SQL("COPY (SELECT {} FROM {} {} ORDER BY r.{}) TO STDOUT WITH (FORMAT csv, HEADER)")
                        .format(cols, table, where, sql.Identifier(src.key)))]
            if inc:
                files["key_file"] = out_dir / f"{src.table}_keys.csv"
                queries.append((files["key_file"], sql.SQL('COPY (SELECT r.{}, ({}) AS "Pending" FROM {} ORDER BY 1) TO STDOUT WITH (FORMAT csv, HEADER)')
                                .format(sql.Identifier(src.key), held, table)))
            for target, query in queries:
                with open(target, "wb") as f, cur.copy(query) as cp:
                    for chunk in cp:
                        f.write(chunk)
        conn.rollback()
    finally:
        conn.close()
    out = {"kind": src.kind, "since": since.isoformat() if since else None, "rows": stats["n"], "backlog": backlog,
           "max_updated_at": hi.isoformat() if hi else None, "types": types}
    for k, p in files.items():
        out[k], out[k.replace("file", "sha256")] = p.name, sha256(p)
    return src.table, out


def run(ctx, dw_conn) -> dict:
    cfg = ctx.cfg
    tmp = cfg.bronze / "_tmp" / f"run={ctx.run_id:05d}"
    shutil.rmtree(tmp, ignore_errors=True)             # a rerun starts clean
    tmp.mkdir(parents=True)
    marks = {} if ctx.mode == "full" else meta.watermarks(dw_conn)
    wms = {t: m[0] for t, m in marks.items()}
    overlap = timedelta(seconds=cfg.overlap_seconds)
    retry = {} if ctx.mode == "full" else {r["target"]: r["keys"] for r in dw_conn.execute(
        "SELECT target, array_agg(DISTINCT record_key::int) AS keys FROM etl.rejected_row "
        "WHERE resolved_run_id IS NULL AND rule_id IN ('R10b', 'R12a') GROUP BY 1").fetchall()}
    retry = {{"assignment": "EventEmployeeAssignment", "sales_activity": "SalesActivity"}.get(k, k): v for k, v in retry.items()}

    coord = db.connect(cfg.crm_dsn, cfg, purpose="CRM")
    coord.isolation_level = IsolationLevel.REPEATABLE_READ
    coord.read_only = True
    try:
        snapshot = coord.execute("SELECT pg_export_snapshot() AS s").fetchone()["s"]
        extracted_at = coord.execute("SELECT localtimestamp AS t").fetchone()["t"]
        sinces = {s.table: read_from(*marks.get(s.table, (None, None)), overlap) for s in SOURCES if s.kind == "incremental"}
        cutoff = (_cutoff(coord.cursor(), wms, cfg.max_rows_per_run) if ctx.mode != "full" and cfg.max_rows_per_run > 0 else None)
        with ThreadPoolExecutor(max_workers=cfg.extract_workers) as pool:
            futures = [pool.submit(_extract_table, cfg, s, sinces, wms, snapshot, tmp, cutoff, retry.get(s.table, ())) for s in SOURCES]
            tables = dict(f.result() for f in futures)
    finally:
        coord.close()

    # all tables succeeded: move them into their Bronze partitions, manifest last
    for t, m in tables.items():
        dest = cfg.bronze / SOURCE_BY_TABLE[t].folder / t / f"load_date={ctx.asof}" / f"run={ctx.run_id:05d}"
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        for k in ("file", "key_file"):
            if k in m:
                shutil.move(tmp / m[k], dest / m[k])
                m[k] = str((dest / m[k]).relative_to(cfg.lake_dir))
    manifest = {"run_id": ctx.run_id, "mode": ctx.mode, "asof": str(ctx.asof), "extracted_at": extracted_at.isoformat(),
                "cutoff": cutoff.isoformat() if cutoff else None, "tables": tables}
    mp = manifest_path(cfg, ctx.run_id)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(manifest, indent=2))
    shutil.rmtree(tmp, ignore_errors=True)
    total = sum(t["rows"] for t in tables.values())
    backlog = sum(t["backlog"] for t in tables.values())
    if backlog:
        meta.alert(dw_conn, ctx.run_id, "warning", f"batch cap {cfg.max_rows_per_run:,}: {backlog:,} changed row(s) after {cutoff} "
                   "wait in the CRM backlog; the next run continues from there")
    return {"rows_in": total, "rows_out": total, "cutoff": manifest["cutoff"], "backlog": backlog,
            "per_table": {k: v["rows"] for k, v in tables.items()}}
