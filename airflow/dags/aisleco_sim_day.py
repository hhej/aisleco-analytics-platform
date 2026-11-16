"""The simulated business (Spec 06): at 01:00 it plays the previous day (couples write, staff reply, bookings), writing
the CRM, posting webhooks to the gateway and dropping tablet files, so aisleco_daily at 02:00 has a new day to load.
Only exists because the company is simulated; a real company would not have this DAG."""
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

ROOT = os.environ.get("AISLECO_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

with DAG(dag_id="aisleco_sim_day", schedule="0 1 * * *", start_date=datetime(2026, 11, 1), catchup=False, max_active_runs=1,
         default_args={"retries": 1, "retry_delay": timedelta(minutes=1)}, tags=["aisleco", "simulation"], doc_md=__doc__) as dag:
    # catch up to yesterday (Bangkok, the Mac's clock): idempotent, and also correct for manual runs and missed nights
    BashOperator(task_id="simulate_yesterday", cwd=ROOT, bash_command=f"{ROOT}/.venv/bin/python -m sim backfill")
