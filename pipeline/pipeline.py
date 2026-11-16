"""The pipeline: single-purpose steps and the runner (Spec 01 §7). Each step runs in its own warehouse transaction
and is idempotent; etl.step_run records every attempt and is the checkpoint, so resume skips finished steps.
The step names are the Airflow tasks (airflow/dags/aisleco_daily.py)."""
import logging
from dataclasses import dataclass, field
from datetime import date

from pipeline import db, meta
from pipeline.config import Config
from pipeline.ingest import crm_cdc, files
from pipeline.silver import run as silver
from pipeline.warehouse import load as wh
from pipeline.marts import build as marts

log = logging.getLogger("pipeline")
LOCK_ID = 8102          # advisory lock: two runs never write at the same time


@dataclass
class Ctx:
    cfg: Config
    run_id: int
    asof: date
    mode: str           # incremental | full
    blocks: list = field(default_factory=list)


def _finish(ctx, conn):
    """Advance the watermarks (only now that every step succeeded) and close the run."""
    import json
    if meta.latest_run_id(conn) != ctx.run_id:
        raise RuntimeError(f"run {ctx.run_id} is not the latest run; it cannot move the watermarks")
    m = json.loads(crm_cdc.manifest_path(ctx.cfg, ctx.run_id).read_text())
    for table, t in m["tables"].items():
        if t["kind"] == "incremental" and t["max_updated_at"]:
            conn.execute("INSERT INTO etl.watermark AS w (source_table, last_updated_at, last_run_id, changed_at) "
                         "VALUES (%s, %s, %s, clock_timestamp()) ON CONFLICT (source_table) DO UPDATE SET "
                         "last_updated_at = greatest(w.last_updated_at, EXCLUDED.last_updated_at), last_run_id = EXCLUDED.last_run_id, "
                         "changed_at = EXCLUDED.changed_at", (table, t["max_updated_at"], ctx.run_id))
    conn.execute("UPDATE etl.watermark SET extracted_at = %s", (m["extracted_at"],))
    return {"watermarks": sum(1 for t in m["tables"].values() if t["kind"] == "incremental")}


STEPS = {
    "ingest_crm": crm_cdc.run,          # E  CRM → bronze/crm (incremental + snapshots)
    "ingest_files": files.run,          # E  landing → bronze (chat, tablets, external)
    "silver": silver.run,               # T  bronze → silver (DuckDB) + row rules, quarantine, block checks
    "load_stg": wh.load_stg,            # L  silver → stg (+ sales_pii)
    "gold_dims": wh.dims,               # T+L dimensions (upsert, SCD2 for staff)
    "fact_inquiry": wh.fact("20_fact_inquiry.sql", "FactInquiry", ["InquiryID"]),
    "fact_event_booking": wh.fact("21_fact_event_booking.sql", "FactEventBooking", ["EventID"]),
    "fact_staff_workload": wh.fact("22_fact_staff_workload.sql", "FactStaffWorkload", ["AssignmentID", "WeekStartDateKey"]),
    "fact_sales_activity": wh.fact("23_fact_sales_activity.sql", "FactSalesActivity", ["ActivityID"]),
    "marts": marts.marts,               #    gold → role marts (views) + grants
    "reconcile": marts.reconcile,       #    silver = gold = marts (C02, C03, C03b, C05)
    "finish": _finish,                  #    watermarks forward
}
DEPENDS = {"ingest_crm": [], "ingest_files": [], "silver": ["ingest_crm", "ingest_files"], "load_stg": ["silver"],
           "gold_dims": ["load_stg"], "fact_inquiry": ["gold_dims"], "fact_event_booking": ["gold_dims"],
           "fact_staff_workload": ["gold_dims"], "fact_sales_activity": ["gold_dims"],
           "marts": ["fact_inquiry", "fact_event_booking", "fact_staff_workload", "fact_sales_activity"],
           "reconcile": ["marts"], "finish": ["reconcile"]}


class AsOfError(ValueError):
    pass


def start(cfg, asof, mode="incremental", triggered_by="cli") -> int:
    with db.connect(cfg.dw_dsn, cfg, autocommit=True, purpose="warehouse") as c:
        last = c.execute("SELECT max(asof_date) AS d FROM etl.run WHERE status = 'success'").fetchone()["d"]
        if last and asof < last:
            raise AsOfError(f"as-of {asof} is earlier than the last successful run ({last})")
        run_id = meta.start_run(c, asof, mode, triggered_by)
    log.info("run %d started (as of %s, %s)", run_id, asof, mode)
    return run_id


def run_step(cfg, run_id, name):
    with db.connect(cfg.dw_dsn, cfg, autocommit=True, purpose="warehouse (log)") as logc:
        run = meta.get_run(logc, run_id)
        ctx = Ctx(cfg, run_id, run["asof_date"], run["mode"])
        if meta.step_done(logc, run_id, name):
            log.info("step %-14s already done in run %d (checkpoint), skipped", name, run_id)
            return None
        sr = meta.step_start(logc, run_id, name)
        try:
            with db.connect(cfg.dw_dsn, cfg, purpose="warehouse") as conn:
                with conn.transaction():
                    conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
                    metrics = STEPS[name](ctx, conn) or {}
                    if cfg.fail_at == name:
                        raise RuntimeError(f"injected failure in step {name} (ETL_FAIL_AT)")
                    if name == "finish":
                        meta.set_run_status(conn, run_id, "success")
        except Exception as e:
            meta.step_end(logc, sr, "failed", error=f"{type(e).__name__}: {e}")
            meta.set_run_status(logc, run_id, "failed", f"step {name}: {e}")
            meta.alert(logc, run_id, "error", f"step {name} failed: {e}")
            raise
        meta.step_end(logc, sr, "success", metrics)
        log.info("step %-14s ok  %s", name, {k: v for k, v in metrics.items() if not isinstance(v, dict)})
        return metrics


def run(cfg, asof, mode="incremental", triggered_by="cli") -> int:
    run_id = start(cfg, asof, mode, triggered_by)
    for name in STEPS:
        run_step(cfg, run_id, name)
    return run_id


def resume(cfg, run_id) -> int:
    with db.connect(cfg.dw_dsn, cfg, autocommit=True, purpose="warehouse") as c:
        run = meta.get_run(c, run_id)
        if run["status"] == "success":
            raise ValueError(f"run {run_id} already succeeded")
        if meta.latest_run_id(c) != run_id:
            raise ValueError(f"run {run_id} is not the latest run; start a new run instead")
        meta.set_run_status(c, run_id, "running", "resumed")
    for name in STEPS:
        run_step(cfg, run_id, name)
    return run_id
