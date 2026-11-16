"""Silver → stg → Gold (Spec 03). load_stg copies the Silver Parquet tables into the warehouse's transient staging
schema with DuckDB (and the restricted contact table into sales_pii); the Gold steps build each table in schema work
and merge it with change detection, so surrogate keys stay stable and each step reports what it changed."""
from pathlib import Path

import duckdb
from psycopg import sql

from pipeline import db

SQL = Path(__file__).parent / "sql"


def load_stg(ctx, conn):
    cfg = ctx.cfg
    tables = sorted(p.parent.name for p in (cfg.lake_dir / "silver").glob("*/data.parquet") if not p.parent.name.startswith("_"))
    con = duckdb.connect()
    con.execute("INSTALL postgres; LOAD postgres")
    con.execute(f"ATTACH '{cfg.dw_dsn}' AS dw (TYPE postgres)")
    counts = {}
    for t in tables:
        con.execute(f"CREATE OR REPLACE TABLE dw.stg.{t} AS SELECT * FROM read_parquet('{cfg.lake_dir / 'silver' / t / 'data.parquet'}')")
        counts[t] = con.execute(f"SELECT count(*) FROM read_parquet('{cfg.lake_dir / 'silver' / t / 'data.parquet'}')").fetchone()[0]
    pii = cfg.lake_dir / "silver_restricted" / "customer_contact" / "data.parquet"
    con.execute("DETACH dw")
    con.close()
    # personal data goes straight to the restricted schema, inside the step's transaction
    conn.execute("TRUNCATE sales_pii.customer_contact")
    if pii.exists():
        rows = duckdb.sql(f"SELECT customer_id, display_name, contact_first_name, phone_masked FROM '{pii}'").fetchall()
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO sales_pii.customer_contact VALUES (%s,%s,%s,%s)", rows)
        counts["sales_pii.customer_contact"] = len(rows)
    for idx in ("CREATE INDEX IF NOT EXISTS ix_stg_chat_inq ON stg.chat_message (inquiry_id, sent_at)",
                "CREATE INDEX IF NOT EXISTS ix_stg_event_inq ON stg.event (inquiry_id)",
                "CREATE INDEX IF NOT EXISTS ix_stg_inq_cust ON stg.inquiry (customer_id)",
                "CREATE INDEX IF NOT EXISTS ix_stg_sa_inq ON stg.sales_activity (inquiry_id)"):
        conn.execute(idx)
    return {"rows_out": sum(counts.values()), "tables": counts}


def _columns(conn, schema, table):
    rows = conn.execute("""
        SELECT a.attname AS col, format_type(a.atttypid, a.atttypmod) AS typ
        FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped AND a.attidentity = ''
        ORDER BY a.attnum""", (schema, table)).fetchall()
    return [(r["col"], r["typ"]) for r in rows]


def merge(conn, table, keys, cascade=()) -> dict:
    """work.<table> (what the fact should hold now) → gold.<table>: insert new rows, update only rows whose values
    changed (compared after casting to the target type), delete rows whose source is gone. Reused from v1."""
    t, s = sql.Identifier("gold", table), sql.Identifier("work", table)
    cols = _columns(conn, "gold", table)
    data = [(c, ty) for c, ty in cols if c not in keys]
    on = sql.SQL(" AND ").join(sql.SQL("t.{c} = s.{c}").format(c=sql.Identifier(k)) for k in keys)
    changed = sql.SQL("({}) IS DISTINCT FROM ({})").format(
        sql.SQL(", ").join(sql.SQL("t.{}").format(sql.Identifier(c)) for c, _ in data),
        sql.SQL(", ").join(sql.SQL("s.{}::{}").format(sql.Identifier(c), sql.SQL(ty)) for c, ty in data))
    counts = conn.execute(sql.SQL("""
        SELECT count(*) FILTER (WHERE tp AND sp AND ch) AS updated, count(*) FILTER (WHERE tp AND sp AND NOT ch) AS unchanged,
               count(*) FILTER (WHERE sp AND NOT tp) AS inserted, count(*) FILTER (WHERE tp AND NOT sp) AS deleted
        FROM (SELECT t.{k} IS NOT NULL AS tp, s.{k} IS NOT NULL AS sp, coalesce({ch}, false) AS ch FROM {t} t FULL JOIN {s} s ON {on}) x
        """).format(k=sql.Identifier(keys[0]), ch=changed, t=t, s=s, on=on)).fetchone()
    gone = sql.SQL("NOT EXISTS (SELECT 1 FROM {s} s WHERE {on})").format(s=s, on=on)
    for child, col in cascade:
        conn.execute(sql.SQL("DELETE FROM {c} WHERE {col} IN (SELECT t.{k} FROM {t} t WHERE {gone})").format(
            c=sql.Identifier("gold", child), col=sql.Identifier(col), k=sql.Identifier(keys[0]), t=t, gone=gone))
    conn.execute(sql.SQL("DELETE FROM {t} t WHERE {gone}").format(t=t, gone=gone))
    conn.execute(sql.SQL("MERGE INTO {t} t USING {s} s ON {on} WHEN MATCHED AND {ch} THEN UPDATE SET {sets} "
                         "WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})").format(
        t=t, s=s, on=on, ch=changed, cols=sql.SQL(", ").join(sql.Identifier(c) for c, _ in cols),
        sets=sql.SQL(", ").join(sql.SQL("{c} = s.{c}").format(c=sql.Identifier(c)) for c, _ in data),
        vals=sql.SQL(", ").join(sql.SQL("s.{}").format(sql.Identifier(c)) for c, _ in cols)))
    return dict(counts)


def _run_ctx(ctx, conn):
    conn.execute("DELETE FROM work.run_ctx")
    conn.execute("INSERT INTO work.run_ctx VALUES (%s, %s)", (ctx.run_id, ctx.asof))


def dims(ctx, conn):
    _run_ctx(ctx, conn)
    return db.run_sql_file(conn, SQL / "10_dims.sql")


def fact(filename, table, keys, cascade=()):
    def step(ctx, conn):
        _run_ctx(ctx, conn)
        built = db.run_sql_file(conn, SQL / filename)["rows_built"]
        r = merge(conn, table, keys, cascade)
        return {"rows_in": built, "rows_out": r["inserted"] + r["updated"] + r["deleted"], **r}
    return step
