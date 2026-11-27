"""Data & ML health (Spec 05 §3.5) and the demo panel (§3.6). Data team only."""
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import duckdb
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import require
from app.api.db import CRM, as_role, connect
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
    # what the CRM's chat module records from the message, so the CRM row matches what the couple wrote
    package: Literal["Full", "Partial", "DayOf", "Custom"] | None = None
    requested_date: date | None = None
    guests: int | None = Field(None, ge=20, le=2000)
    budget: Literal["300k-500k", "500k-1M", "1M-1.5M", "1.5M+"] | None = None
    budget_text: str | None = Field(None, min_length=1, max_length=40)


@router.post("/api/demo/line")
def demo_line(body: LineDemo, user: dict = Depends(require("data"))):
    """Send one LINE message as a new couple, now: the CRM's chat module creates the couple and the inquiry, and a signed
    webhook goes to the chat gateway exactly as LINE would send it (sim.live_message)."""
    from sim.__main__ import live_message
    now = datetime.now(BKK)
    if body.requested_date and body.requested_date <= now.date():
        raise HTTPException(422, "the requested wedding date must be in the future")
    try:
        created = live_message(body.text, pkg=body.package, req=body.requested_date, guests=body.guests,
                               budget=body.budget, budget_text=body.budget_text)
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


# ---- Follow one couple (the demo page): where is this inquiry now, layer by layer? ----

def _duck_one(sql, params):
    with duckdb.connect() as con:
        cur = con.execute(sql, params)
        r = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], r)) if r else None


def _chat_file(platform, msg_id, sent_at):
    """The hourly file holding this message: still in landing (its hour is not over) or already moved to Bronze."""
    for zone in ("landing", "bronze"):
        for t in (sent_at, sent_at + timedelta(hours=1)):  # received just after the hour turned
            f = settings.LAKE_DIR / zone / "chat" / f"platform={platform}" / f"received_date={t:%Y-%m-%d}" / f"hour={t:%H}" / "events.jsonl"
            for line in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
                if msg_id in line:
                    rec = json.loads(line)
                    hour = datetime.fromisoformat(rec["received_at"]).replace(minute=0, second=0, microsecond=0)
                    return {"found": True, "zone": zone, "file": str(f.relative_to(ROOT)), "received_at": rec["received_at"],
                            "body": json.loads(rec["body"]), "body_sha256": rec["body_sha256"], "sealed_at": hour + timedelta(hours=1)}
    return {"found": False}


@router.get("/api/demo/latest")
def demo_latest(user: dict = Depends(require("data"))):
    """The two quick picks: the newest couple in the CRM, and the newest LINE couple the lead model has scored."""
    with connect(CRM) as c:
        newest = c.execute('SELECT max("InquiryID") AS id FROM transaction."Inquiry"').fetchone()["id"]
    with as_role("data") as q:
        scored = q.one("""SELECT s."InquiryID" AS id FROM gold."FactLeadScore" s JOIN gold."DimChannel" c USING ("ChannelKey")
                          WHERE s."ScoreDateKey" = (SELECT max("ScoreDateKey") FROM gold."FactLeadScore") AND c."Platform" = 'line'
                          ORDER BY s."InquiryID" DESC LIMIT 1""")
    return {"newest": newest, "scored": scored["id"] if scored else None}


@router.get("/api/demo/trace")
def demo_trace(inquiry_id: int, user: dict = Depends(require("data"))):
    """One couple at every stop: CRM, the chat message's hourly file, raw zone (Bronze), trusted zone (Silver), star schema
    (Gold), marts, lead score, and the latest pipeline run. Each stop is looked up on its own, so a layer being rebuilt
    mid-run shows an error for that stop only."""
    sid = str(inquiry_id)
    out = {"inquiry_id": inquiry_id}
    with connect(CRM) as crm, as_role("data") as q:   # CRM: read-only, as the OS user, like the pipeline's CRM extract

        def stop(name, fn):
            try:
                out[name] = fn()
            except Exception as e:  # e.g. Silver swapping its files mid-run
                out[name] = {"found": False, "error": f"{type(e).__name__}: {e}"}

        def crm_stop():
            row = crm.execute('SELECT * FROM transaction."Inquiry" WHERE "InquiryID" = %s', (inquiry_id,)).fetchone()
            msg = crm.execute("""SELECT m.*, a."Platform" AS platform FROM transaction."ChatMessage" m
                                 LEFT JOIN master."CustomerChannelAccount" a USING ("CustomerAccountID")
                                 WHERE m."InquiryID" = %s AND m."Direction" = 'In' ORDER BY m."SentAt" LIMIT 1""", (inquiry_id,)).fetchone()
            return {"found": row is not None, "row": row, "message": msg}

        def raw_crm():
            g = settings.LAKE_DIR / "bronze/crm/Inquiry/*/*/Inquiry.csv"
            r = _duck_one(f"""SELECT * FROM read_csv('{g}', header=true, all_varchar=true, filename=true, union_by_name=true)
                              WHERE "InquiryID" = ? ORDER BY filename DESC LIMIT 1""", [sid])
            return {"found": True, "file": str(Path(r.pop("filename")).relative_to(ROOT)), "row": r} if r else {"found": False}

        def trusted():
            silver = settings.LAKE_DIR / "silver"
            row = _duck_one(f"SELECT * FROM read_parquet('{silver / 'inquiry/data.parquet'}') WHERE inquiry_id = ?", [inquiry_id])
            chat = _duck_one(f"SELECT * FROM read_parquet('{silver / 'chat_message/data.parquet'}') WHERE platform_message_id = ?",
                             [msg["PlatformMessageID"]]) if msg else None
            quarantined = q.one("""SELECT rule_id, reason, last_run_id FROM mart_platform.v_quarantine
                                   WHERE target = 'inquiry' AND record_key = %s AND resolved_run_id IS NULL LIMIT 1""", (sid,))
            dq = q.all("""SELECT rule_id, severity, description, rows_checked, rows_failed, passed FROM mart_platform.v_dq_results
                          WHERE target = 'inquiry' AND run_id = (SELECT max(run_id) FROM mart_platform.v_dq_results) ORDER BY rule_id""")
            # shortcut: per-row warnings are not stored, so R05 is re-derived from the published row (the condition in
            # 30_inquiry.sql); store per-row warnings if more rules need this
            flags = ["R05"] if row and row["budget_band"] == "Unknown" else []
            return {"found": row is not None, "row": row, "chat": chat, "quarantined": quarantined, "dq": dq, "flags": flags}

        def star():
            r = q.one("""SELECT f.*, c."ChannelName", p."PackageName", e."Nickname" AS "SalesOwner" FROM gold."FactInquiry" f
                         LEFT JOIN gold."DimChannel" c USING ("ChannelKey")
                         LEFT JOIN gold."DimPackage" p ON p."PackageKey" = f."IndicativePackageKey"
                         LEFT JOIN gold."DimEmployee" e ON e."EmployeeKey" = f."SalesOwnerKey"
                         WHERE f."InquiryID" = %s""", (inquiry_id,))
            return {"found": r is not None, "row": r}

        def mart():
            new = q.one("SELECT * FROM mart_sales.v_new_today WHERE inquiry_id = %s", (inquiry_id,))
            listed = q.one("SELECT * FROM mart_sales.v_today_list WHERE inquiry_id = %s", (inquiry_id,))
            views = [v for v, r in (("v_new_today", new), ("v_today_list", listed)) if r]
            return {"found": bool(views), "views": views, "row": listed or new}

        def score():
            r = q.one('SELECT * FROM gold."FactLeadScore" WHERE "InquiryID" = %s ORDER BY "ScoreDateKey" DESC LIMIT 1', (inquiry_id,))
            return {"found": r is not None, "row": r}

        def run():
            r = q.one("SELECT * FROM etl.run ORDER BY run_id DESC LIMIT 1")
            steps = q.all("""SELECT step, attempt, status, started_at, finished_at, duration_sec, metrics, error FROM etl.step_run
                             WHERE run_id = %s ORDER BY step_run_id""", (r["run_id"],)) if r else []
            return {**(r or {}), "steps": steps}

        stop("crm", crm_stop)
        msg = out["crm"].get("message")
        stop("chat", lambda: _chat_file(msg["platform"], msg["PlatformMessageID"], msg["SentAt"]) if msg else {"found": False})
        for name, fn in (("raw_crm", raw_crm), ("trusted", trusted), ("star", star), ("mart", mart), ("score", score), ("run", run)):
            stop(name, fn)
    out["demo_running"] = _demo_proc is not None and _demo_proc.poll() is None
    return out
