"""Run, step (checkpoint), watermark and alert bookkeeping in schema etl. Writes go through an autocommit
connection, so a failure is recorded even when the step's own transaction rolls back."""
import json
import logging

from psycopg.types.json import Jsonb

log = logging.getLogger("pipeline")


def start_run(conn, asof, mode, triggered_by) -> int:
    return conn.execute("INSERT INTO etl.run(asof_date, mode, triggered_by, status) VALUES (%s,%s,%s,'running') RETURNING run_id",
                        (asof, mode, triggered_by)).fetchone()["run_id"]


def get_run(conn, run_id) -> dict:
    row = conn.execute("SELECT * FROM etl.run WHERE run_id = %s", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"run {run_id} does not exist")
    return row


def latest_run_id(conn):
    return conn.execute("SELECT max(run_id) AS r FROM etl.run").fetchone()["r"]


def set_run_status(conn, run_id, status, message=None):
    conn.execute("UPDATE etl.run SET status = %s, message = %s, "
                 "finished_at = CASE WHEN %s::text IN ('success','failed') THEN clock_timestamp() END WHERE run_id = %s",
                 (status, message, status, run_id))


def step_done(conn, run_id, step) -> bool:
    return conn.execute("SELECT 1 FROM etl.step_run WHERE run_id = %s AND step = %s AND status = 'success'",
                        (run_id, step)).fetchone() is not None


def step_start(conn, run_id, step) -> int:
    return conn.execute(
        "INSERT INTO etl.step_run(run_id, step, attempt, status) SELECT %s, %s, coalesce(max(attempt), 0) + 1, 'running' "
        "FROM etl.step_run WHERE run_id = %s AND step = %s RETURNING step_run_id", (run_id, step, run_id, step)).fetchone()["step_run_id"]


def step_end(conn, step_run_id, status, metrics=None, error=None):
    metrics = metrics or {}
    conn.execute(
        "UPDATE etl.step_run SET status = %s, finished_at = clock_timestamp(), "
        "duration_sec = round(extract(epoch FROM clock_timestamp() - started_at)::numeric, 3), "
        "rows_in = %s, rows_out = %s, metrics = %s, error = %s WHERE step_run_id = %s",
        (status, metrics.get("rows_in"), metrics.get("rows_out"), Jsonb(json.loads(json.dumps(metrics, default=str))), error, step_run_id))


def alert(conn, run_id, severity, message):
    """Stored in etl.alert (Data & ML page) and logged; Airflow's failure callback surfaces it in the UI too."""
    conn.execute("INSERT INTO etl.alert(run_id, severity, message) VALUES (%s,%s,%s)", (run_id, severity, message))
    (log.error if severity in ("error", "critical") else log.warning)("ALERT [%s] run %s: %s", severity, run_id, message)


def watermarks(conn) -> dict:
    """{table: (last_updated_at, extracted_at)}"""
    return {r["source_table"]: (r["last_updated_at"], r["extracted_at"])
            for r in conn.execute("SELECT * FROM etl.watermark").fetchall()}
