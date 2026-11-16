"""Continuous training (Spec 04 §9): every Sunday 03:00 the lead-conversion model is rebuilt from the feature store,
tuned, calibrated, evaluated against the baselines and the current champion, and registered in MLflow. Only a candidate
that passes the gate moves the `champion` alias; the old champion keeps `previous` for rollback."""
import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

ROOT = os.environ.get("AISLECO_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
ASOF = "{{ dag_run.conf.get('asof') if dag_run and dag_run.conf and dag_run.conf.get('asof') else data_interval_end.in_timezone('Asia/Bangkok').strftime('%Y-%m-%d') }}"

with DAG(dag_id="aisleco_weekly_train", schedule="0 3 * * 0", start_date=datetime(2026, 11, 1), catchup=False, max_active_runs=1,
         default_args={"retries": 1, "retry_delay": timedelta(minutes=5)}, tags=["aisleco", "ml"], doc_md=__doc__) as dag:
    BashOperator(task_id="train_lead_conversion", cwd=ROOT, bash_command=f"{ROOT}/.venv/bin/python -m ml train --asof {ASOF}")
