"""Data & ML health (Spec 05 §3.5) and the demo panel (§3.6). Data team only."""
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require
from app.api.db import as_role
from sources import settings

router = APIRouter()
ROOT = Path(__file__).resolve().parents[2]
GATEWAY = os.environ.get("APP_GATEWAY_URL", "http://127.0.0.1:8001")
AIRFLOW = os.environ.get("APP_AIRFLOW_URL", "http://localhost:8080")
BKK = timezone(timedelta(hours=7))
CORE_VIEWS = {"v_runs", "v_step_times", "v_dq_results", "v_quarantine", "v_freshness", "v_alerts"}
_demo_proc: subprocess.Popen | None = None


@router.get("/api/data/health")
def health(user: dict = Depends(require("data"))):
    with as_role("data") as q:
        runs = q.all("SELECT * FROM mart_platform.v_runs ORDER BY run_id DESC LIMIT 20")
        last = runs[0]["run_id"] if runs else None
        steps = q.all("SELECT * FROM mart_platform.v_step_times WHERE run_id = %s ORDER BY run_id, attempt", (last,))
        last_ok = next((r["run_id"] for r in runs if r["status"] == "success"), last)
        dq = q.all("SELECT * FROM mart_platform.v_dq_results WHERE run_id = %s ORDER BY passed, rule_id", (last_ok,))
        quarantine = q.all("SELECT * FROM mart_platform.v_quarantine ORDER BY resolved_run_id NULLS FIRST, last_run_id DESC LIMIT 200")
        fresh = q.all("SELECT * FROM mart_platform.v_freshness ORDER BY source")
        alerts = q.all("SELECT * FROM mart_platform.v_alerts ORDER BY created_at DESC LIMIT 20")
        scores = q.one('SELECT count(*) AS rows, max("ScoreDateKey") AS last_score_key FROM gold."FactLeadScore"')
        forecast = q.one('SELECT count(*) AS rows, max("ForecastDateKey") AS last_forecast_key FROM gold."FactDemandForecast"')
        extra = [r["table_name"] for r in q.all("""SELECT table_name FROM information_schema.tables WHERE table_schema = 'mart_platform'
                                                   ORDER BY 1""") if r["table_name"] not in CORE_VIEWS]
        model_views = {v: q.all(f'SELECT * FROM mart_platform."{v}" LIMIT 50') for v in extra}  # names from the catalog, not the user
    return {"runs": runs, "steps": steps, "dq_run_id": last_ok, "dq": dq, "quarantine": quarantine, "freshness": fresh, "alerts": alerts,
            "models": {"lead_scores": scores, "demand_forecast": forecast, "views": model_views},
            "demo_running": _demo_proc is not None and _demo_proc.poll() is None}


class LineDemo(BaseModel):
    text: str = Field(min_length=1, max_length=500)


@router.post("/api/demo/line")
def demo_line(body: LineDemo, user: dict = Depends(require("data"))):
    """Send one LINE message as a new couple, now: the CRM's chat module creates the couple and the inquiry, and a signed
    webhook goes to the chat gateway exactly as LINE would send it (sim.live_message)."""
    from sim.__main__ import live_message
    now = datetime.now(BKK)
    try:
        created = live_message(body.text)
    except httpx.HTTPError:
        raise HTTPException(503, "the chat gateway is not running (scripts/services.sh start gateway)")
    path = settings.LAKE_DIR / "landing/chat/platform=line" / f"received_date={now:%Y-%m-%d}" / f"hour={now:%H}" / "events.jsonl"
    return {"ok": True, **created, "landed_file": str(path.relative_to(ROOT)) if path.exists() else None,
            "received_at": now.isoformat(timespec="seconds")}


@router.post("/api/demo/run")
def demo_run(user: dict = Depends(require("data"))):
    """Run the pipeline now: through Airflow's REST API when it is up, otherwise as a background CLI process."""
    global _demo_proc
    pw_file = ROOT / "airflow/standalone_admin_password.txt"
    try:
        if pw_file.exists():
            r = httpx.post(f"{AIRFLOW}/api/v1/dags/aisleco_daily/dagRuns", json={"conf": {"asof": date.today().isoformat()}},
                           auth=("admin", pw_file.read_text().strip()), timeout=5)
            if r.status_code in (200, 201):
                return {"via": "airflow", "dag_run_id": r.json().get("dag_run_id")}
    except httpx.HTTPError:
        pass  # Airflow is down: fall back to the CLI
    if _demo_proc is not None and _demo_proc.poll() is None:
        raise HTTPException(409, "a pipeline run started from here is still running")
    log = ROOT / "var/log/demo_run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "a") as f:
        f.write(f"\n=== demo run {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        f.flush()
        _demo_proc = subprocess.Popen([sys.executable, "-m", "pipeline", "run", "--asof", date.today().isoformat()],
                                      cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
    return {"via": "cli", "pid": _demo_proc.pid, "log": "var/log/demo_run.log"}
