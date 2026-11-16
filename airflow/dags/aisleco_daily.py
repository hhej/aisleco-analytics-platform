"""Aisle & Co. daily pipeline (v2): sources → Bronze → Silver → Gold → marts, then the models' daily steps.

Each task is one pipeline step (pipeline/pipeline.py) run with the project's own Python, so Airflow only orchestrates:
schedule, dependencies, retries, logs, alerts. The steps, checkpoints and run log are the same as on the command line.
The holiday calendar is refreshed inside ingest_files on the 1st of each month.

Trigger with a config to replay a date or rebuild:  {"asof": "2026-11-16", "full_refresh": false}
"""
import logging
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

ROOT = os.environ.get("AISLECO_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
PY = os.path.join(ROOT, ".venv", "bin", "python")
ENV = {k: v for k, v in os.environ.items() if k.startswith(("ETL_", "CRM_", "DW_", "LAKE_", "PII_"))}

# the same graph as pipeline.pipeline.DEPENDS (copied so the DAG file imports nothing from the project)
DEPENDS = {"ingest_crm": [], "ingest_files": [], "silver": ["ingest_crm", "ingest_files"], "load_stg": ["silver"],
           "gold_dims": ["load_stg"], "fact_inquiry": ["gold_dims"], "fact_event_booking": ["gold_dims"],
           "fact_staff_workload": ["gold_dims"], "fact_sales_activity": ["gold_dims"],
           "marts": ["fact_inquiry", "fact_event_booking", "fact_staff_workload", "fact_sales_activity"],
           "reconcile": ["marts"], "finish": ["reconcile"]}
DOCS = {
    "ingest_crm": "E: CRM changes since the watermark (one shared snapshot, batch cap) + master snapshots → bronze/crm",
    "ingest_files": "E: closed chat hour files, tablet drops, auspicious file, monthly holiday pull → bronze",
    "silver": "T: Bronze → Silver Parquet (DuckDB), row rules → quarantine, block checks, atomic swap",
    "load_stg": "L: Silver → stg (DuckDB → PostgreSQL); restricted contacts → sales_pii",
    "gold_dims": "T+L: dimensions (upsert, stable keys, SCD2 staff contracts)",
    "fact_inquiry": "T+L: FactInquiry accumulating snapshot (merge with change detection)",
    "fact_event_booking": "T+L: FactEventBooking", "fact_staff_workload": "T+L: hours spread over weeks",
    "fact_sales_activity": "T+L: FactSalesActivity", "marts": "role marts (views) and grants",
    "reconcile": "Silver = Gold = marts (C02, C03, C03b, C05)", "finish": "watermarks forward, run closed",
}
ASOF = ("{{ dag_run.conf['asof'] if dag_run and dag_run.conf and dag_run.conf.get('asof') "
        "else data_interval_end.in_timezone('Asia/Bangkok').strftime('%Y-%m-%d') }}")


def on_failure(context):
    """The failed step has already written etl.alert; this surfaces it in the Airflow log (add e-mail/LINE Notify here)."""
    ti = context["task_instance"]
    logging.getLogger("airflow.task").error("ALERT aisleco_daily: task %s failed (try %s), pipeline run %s. See etl.alert.",
                                            ti.task_id, ti.try_number, ti.xcom_pull(task_ids="start_run"))


with DAG(
    dag_id="aisleco_daily",
    description="Aisle & Co. sources → lake → warehouse → marts → models",
    schedule="0 2 * * *",                 # 02:00 Bangkok, after the simulated business day (aisleco_sim_day, 01:00)
    start_date=datetime(2026, 11, 1),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(seconds=30), "retry_exponential_backoff": True,
                  "on_failure_callback": on_failure},
    tags=["aisleco", "pipeline", "madt8102"],
    doc_md=__doc__,
) as dag:
    start = BashOperator(
        task_id="start_run", retries=0, cwd=ROOT, env=ENV, append_env=True,
        bash_command=f"{PY} -m pipeline start --triggered-by airflow --asof {ASOF}"
                     "{{ ' --full-refresh' if dag_run and dag_run.conf and dag_run.conf.get('full_refresh') else '' }}",
        doc_md="Creates the run in etl.run and passes its id to every step (XCom)")
    tasks = {name: BashOperator(task_id=name, cwd=ROOT, env=ENV, append_env=True, doc_md=DOCS[name],
                                bash_command=f"{PY} -m pipeline step {name} --run-id {{{{ ti.xcom_pull(task_ids='start_run') }}}}")
             for name in DEPENDS}
    for name, ups in DEPENDS.items():
        if not ups:
            start >> tasks[name]
        for up in ups:
            tasks[up] >> tasks[name]
    # the models' daily steps (Spec 04 §7, §9): score today's open inquiries with the champion, then monitor
    ml_score = BashOperator(task_id="ml_score", cwd=ROOT, env=ENV, append_env=True, bash_command=f"{PY} -m ml score --asof {ASOF}",
                            doc_md="Lead scores, reasons and next best action → gold.FactLeadScore (the Sales list)")
    ml_monitor = BashOperator(task_id="ml_monitor", cwd=ROOT, env=ENV, append_env=True, bash_command=f"{PY} -m ml monitor --asof {ASOF}",
                              doc_md="Drift (PSI), missing shares, matured-label performance → ml.monitor; alerts → etl.alert")
    ml_forecast = BashOperator(task_id="ml_forecast", cwd=ROOT, env=ENV, append_env=True, bash_command=f"{PY} -m ml forecast --asof {ASOF}",
                               doc_md="Staff-hours by role group for 26 weeks (booked + pipeline + pickup, backtested) → gold.FactDemandForecast")
    tasks["finish"] >> ml_score >> [ml_monitor, ml_forecast]
