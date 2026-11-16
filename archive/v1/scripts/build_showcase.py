"""Builds AisleCo_Pipeline_Showcase.html: one self-contained page (works offline) that shows how the pipeline
works, its state, the Google Cloud service of each part, the real source code, the dashboard and the model.

  1. run the end-to-end notebook (AisleCo_End_to_End_Stress_Test.ipynb); it calls snapshot() at the state of
     15 Jan 2027 and saves docs/showcase/*.json / *.csv
  2. .venv/bin/python scripts/build_showcase.py        # also runs pytest for the test list

The page template is web/showcase.html; the data, the source files and the vendored libraries (web/vendor) are
inlined into it. Nothing here computes a business number: every number comes from the warehouse or the notebook."""
import ast
import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "showcase"
ASOF = date(2027, 1, 15)
OPS_ROLES = ("Lead Planner", "Planner", "Coordinator", "Event Staff")

GCP = {  # service: (link, one line on why it fits)
    "Cloud SQL for PostgreSQL": ("https://cloud.google.com/sql/docs/postgres",
                                 "The CRM stays on PostgreSQL, managed: backups, failover and the UpdatedAt trigger work as they do now."),
    "Cloud Storage": ("https://cloud.google.com/storage/docs",
                      "Each night's extract lands as files in a bucket, kept for replay and audit, cheap to store."),
    "Cloud Composer": ("https://cloud.google.com/composer/docs",
                       "Managed Airflow: the two DAGs in airflow/dags run as they are, with retries, logs and the run history."),
    "BigQuery": ("https://cloud.google.com/bigquery/docs",
                 "The star schema, staging and mart views become BigQuery datasets; the transform SQL runs inside it."),
    "Vertex AI Feature Store": ("https://cloud.google.com/vertex-ai/docs/featurestore/latest/overview",
                                "Serves the nightly feature rows; its offline store is a BigQuery table, like feature.InquiryFeatures."),
    "Vertex AI Training": ("https://cloud.google.com/vertex-ai/docs/training/overview",
                           "Runs ml/train.py as a custom job each Sunday, on a machine sized for the data."),
    "Vertex AI Model Registry": ("https://cloud.google.com/vertex-ai/docs/model-registry/introduction",
                                 "Keeps every model version with its metrics and which one is in production, like ml.ModelVersion."),
    "Vertex AI Batch Prediction": ("https://cloud.google.com/vertex-ai/docs/predictions/get-batch-predictions",
                                   "Scores all open inquiries once a night and writes the results to BigQuery."),
    "Vertex AI Model Monitoring": ("https://cloud.google.com/vertex-ai/docs/model-monitoring/overview",
                                   "Compares live feature distributions with the training data (drift), like ml/monitor.py."),
    "Looker Studio": ("https://cloud.google.com/looker-studio",
                      "The weekly meeting dashboard, reading the mart views in BigQuery."),
    "Cloud Monitoring": ("https://cloud.google.com/monitoring/docs",
                         "Turns failed runs and drift alerts into emails to the owner."),
    "Secret Manager": ("https://cloud.google.com/secret-manager/docs",
                       "Holds the database logins that etl/config.py reads from the environment today."),
}

# The pipeline map: id, title, zone, GCP services, source files, ETL steps of the node, plain description
NODES = [
    ("crm", "CRM (source)", "source", ["Cloud SQL for PostgreSQL"],
     ["sql/oltp_v5_cdc_support.sql", "sql/WeddingOrganizer_OLTP_v5_dbdiagram.sql"], [],
     "The sales and planning application's database, OLTP v5: customers, inquiries, chat messages, events and staff "
     "assignments. A trigger stamps UpdatedAt on every change, so the ETL can ask for only the rows changed since "
     "the last night. The pipeline only reads from it."),
    ("extract", "Extract", "etl", ["Cloud Composer", "Secret Manager"],
     ["etl/extract.py", "etl/db.py", "etl/config.py"], ["extract"],
     "At 02:00 one worker per table reads the rows changed since the watermark, all from one consistent snapshot of "
     "the CRM. It writes them as CSV files to the landing zone. A batch cap (100,000 rows) keeps a night inside the "
     "2-minute target; the rest waits for the next run. Rows rejected earlier for a missing parent are read again."),
    ("landing", "Landing + staging", "etl", ["Cloud Storage", "BigQuery"],
     ["etl/stage.py", "etl/meta.py"], ["stage"],
     "Landed files (Bronze) are copied into staging tables (Silver). Row rules run here: a row that breaks one, "
     "for example an unknown status, goes to the dead-letter table etl.RejectedRow with the reason, and its "
     "children go with it. When the row is fixed in the CRM, the next run loads it and closes the dead-letter entry."),
    ("gate", "Quality gate", "etl", ["Cloud Composer", "Cloud Monitoring"],
     ["etl/quality.py"], ["dq_gate"],
     "Set rules check the staging tables as a whole: no chat message or staff assignment may point to a missing "
     "inquiry, event or employee, and the share of rejected rows per table must stay under the limit. A blocking "
     "failure stops the run before any dimension or fact is touched. Warnings are logged and the run continues."),
    ("transform", "Transform + load", "etl", ["BigQuery"],
     ["etl/pipeline.py", "etl/load.py", "sql/transform/10_dim_date.sql", "sql/transform/11_dim_package.sql",
      "sql/transform/12_dim_employee.sql", "sql/transform/20_fact_inquiry.sql", "sql/transform/21_fact_event_booking.sql",
      "sql/transform/22_fact_weekly_workload.sql", "sql/dw_v7_functions.sql"],
     ["dim_date", "dim_package", "dim_employee", "fact_inquiry", "fact_event_booking", "fact_weekly_workload"],
     "SQL inside the warehouse builds the dimensions and the three facts: inquiries (response time, funnel), "
     "bookings, and each person's weekly hours. A merge writes only the rows whose values changed. Weeks that "
     "have ended are frozen, so a later cancellation does not rewrite past workload; it raises a late-change alert."),
    ("reconcile", "Reconcile", "etl", ["Cloud Composer"],
     ["etl/quality.py"], ["reconcile", "finish"],
     "After the load, source-to-target checks compare the warehouse with staging: row counts, staff hours per "
     "event and per week, and unknown keys. Only then do the "
     "watermarks move forward. A crash before this point is resumed from the last good step; a rerun changes nothing."),
    ("warehouse", "Warehouse + marts", "store", ["BigQuery"],
     ["sql/WeddingOrganizer_DW_v7_dbdiagram.sql", "sql/dw_v7_mart_views.sql"], [],
     "DW v7: a star schema of 4 dimensions and 3 facts. The mart views compute the KPIs of both business problems "
     "(utilization, turned-away couples, funnel, no-reply rate) and are what the dashboard reads."),
    ("features", "Feature store", "ml", ["Vertex AI Feature Store", "BigQuery"],
     ["ml/features.py", "sql/ml_feature_store.sql"], [],
     "One SQL function computes each open inquiry's features at a point in time: channel, budget, reply time, the "
     "couple's messages, silence, consultation, quote. Every night's rows are stored, so training uses exactly what "
     "was known on that night. The step refuses to run if that night's ETL has not succeeded."),
    ("train", "Training + registry", "ml", ["Vertex AI Training", "Vertex AI Model Registry"],
     ["ml/train.py"], [],
     "Every Sunday: split by time, two candidate models and two baselines (each channel's booking rate, and a "
     "salesperson's rule of thumb). A candidate "
     "replaces production only if it beats the baseline by 0.05 AUC and is at least as good as production on at "
     "least 50 inquiries production never saw. Every version, kept or not, is in the registry with the reason."),
    ("score", "Nightly scoring", "ml", ["Vertex AI Batch Prediction"],
     ["ml/score.py", "ml/__main__.py"], [],
     "After the ETL, the production model gives every open inquiry its chance to book, banded High (in the top "
     "20% of the training scores), Medium (above their median) and Low. Sales starts the day with this list."),
    ("monitor", "Monitoring", "ml", ["Vertex AI Model Monitoring", "Cloud Monitoring"],
     ["ml/monitor.py"], [],
     "Each night: drift of the inputs against the training data (PSI over the last 120 days) and the live accuracy "
     "on inquiries whose outcome is now known. An alert is written to etl.Alert."),
    ("forecast", "Demand forecast", "ml", ["Vertex AI Training", "Vertex AI Batch Prediction"],
     ["ml/forecast.py", "sql/ml_feature_store.sql"], [],
     "Each night: how many weddings each of the next 6 months will have, as weddings already booked plus the bookings "
     "still to come, with an 80% range, and the staff-hours this needs against the operations staff's capacity. "
     "Every Sunday a backtest (one forecast per month over the last 12 months, each made only with what was known then) "
     "compares two baselines and two ML models; an ML model is used only if it beats the best baseline."),
    ("dashboard", "Dashboard", "use", ["Looker Studio"],
     ["dashboard/app.py"], [],
     "The weekly meeting view: staff capacity and the demand forecast (Problem 1), conversion and no-reply "
     "(Problem 2), today's follow-up list and the model's health. It reads only the mart views."),
    ("airflow", "Airflow (schedule)", "ops", ["Cloud Composer"],
     ["airflow/dags/aisleco_daily_etl.py", "airflow/dags/aisleco_weekly_train.py", "etl/__main__.py"], [],
     "aisleco_daily_etl runs the 11 ETL steps at 02:00, then features, scoring, monitoring and the demand forecast. "
     "aisleco_weekly_train trains both models on Sundays at 04:00. Each step is one task with retries; a failed task stops the ones after it."),
]

# The medallion zones and every table in them, in data-flow order. build() checks that this covers every
# table and view in the warehouse snapshot, so a new table cannot be left off the page.
CRM_TABLES = {
    "Inquiry": "one inquiry from a couple: channel, dates asked for, budget, status, quote",
    "ChatMessage": "every message with the couple, in or out, by the couple, a staff member or the bot",
    "Event": "a booked wedding: date, package, price, guests, status",
    "EventEmployeeAssignment": "who works on which wedding, from when, and their hours",
    "Employee": "staff: role, standard weekly hours, how many weddings at once",
    "EmployeeRole": "Lead Planner, Planner, Coordinator, Event Staff, Sales",
    "Package": "wedding packages and their price versions",
    "MarketingChannel": "LINE OA, Facebook, Instagram, TikTok, wedding fair, website, referral, walk-in",
    "AuspiciousDate": "auspicious wedding dates and how strongly they are sought",
}
ZONES = [
    {"key": "source", "name": "Source: the CRM", "where": "PostgreSQL schemas master and transaction", "gcp": "Cloud SQL for PostgreSQL",
     "text": "The sales and planning application's own database. The pipeline reads it and never writes to it. The three customer "
             "tables (names, contacts, chat accounts) are never extracted; only the CustomerID key is.",
     "tables": [(f"{'transaction' if t in ('Inquiry', 'ChatMessage', 'Event', 'EventEmployeeAssignment') else 'master'}.{t}", d)
                for t, d in CRM_TABLES.items()]
               + [("master.Customer", "the couple. Not extracted: personal data"),
                  ("master.CustomerContact", "phone numbers and e-mail addresses. Not extracted: personal data"),
                  ("master.CustomerChannelAccount", "the couple's chat accounts. Not extracted: personal data")]},
    {"key": "bronze", "name": "Bronze: raw extracts", "where": "data/landing/run_NNNNN/ (files), schema land (transient)", "gcp": "Cloud Storage",
     "text": "Each night's changed rows exactly as the CRM had them, one folder per run, kept for replay and audit. The stage step "
             "bulk-loads them into land tables, checks them against the manifest and applies the row rules.",
     "tables": [("<table>.csv", "the rows changed since the last run, one file per CRM table (9 tables)"),
                ("<table>_keys.csv", "every key the CRM holds now, used to find deleted rows"),
                ("manifest.json", "row counts and checksums: a damaged or partial file stops the run"),
                ("land.<table>", "the file loaded into the warehouse, emptied each run"),
                ("land.<table>_Keys", "the key list loaded; Pending marks rows held back by the batch cap")]},
    {"key": "silver", "name": "Silver: cleaned CRM", "where": "schema stg", "gcp": "BigQuery dataset",
     "text": "A cleaned copy of the 9 CRM tables the warehouse needs, merged night by night: rows that broke a row rule are in the "
             "dead letter instead, deleted rows are removed, and personal data is never here. The model's features are computed from it.",
     "tables": [(f"stg.{t}", d) for t, d in CRM_TABLES.items()]},
    {"key": "gold", "name": "Gold: star schema", "where": "schemas dim and fact", "gcp": "BigQuery dataset",
     "text": "The business model of DW v7: 4 dimensions and 3 facts, built by SQL from Silver. A merge writes only changed rows; "
             "weeks that have ended are frozen.",
     "tables": [("dim.DimDate", "calendar, with auspicious dates; used as inquiry, event, booking and week dates"),
                ("dim.DimPackage", "each package price version, rolled up to its package family"),
                ("dim.DimEmployee", "staff and their role"),
                ("dim.DimInquiryOutcome", "every combination of outcome, furthest stage, lost reason and who closed it"),
                ("fact.FactInquiry", "one row per inquiry: channel, response time, stage dates, quote, outcome (Problem 2)"),
                ("fact.FactEventBooking", "one row per wedding: date, package, price, staff count, planning and event-day hours (Problem 1)"),
                ("fact.FactEmployeeWeeklyWorkload", "one row per person per week: hours assigned against standard hours, events at once (Problem 1)")]},
    {"key": "consumption", "name": "Consumption: KPI views", "where": "schema mart (views)", "gcp": "BigQuery views, read by Looker Studio",
     "text": "One view per business question. Ratios are computed here from their parts, so a dashboard can never sum or average them.",
     "tables": [("mart.v_inquiry", "the inquiry fact with its outcome labels; the base of the Problem 2 views"),
                ("mart.v_weekly_workload", "weekly hours and utilization per person"),
                ("mart.v_peak_acceptance", "how many inquiries for Nov-Feb or auspicious dates became a booking"),
                ("mart.v_turned_away_demand", "couples lost because the date was full, by month asked for"),
                ("mart.v_staff_hours_per_wedding", "staff hours per wedding by package and size"),
                ("mart.v_funnel_by_channel", "inquiries, consultations, quotes and bookings per channel"),
                ("mart.v_conversion_by_package", "booking rate per package"),
                ("mart.v_response_vs_conversion", "how reply speed relates to the booking rate"),
                ("mart.v_lost_reasons", "why couples were lost"),
                ("mart.v_quote_share", "the quote as a share of the couple's budget"),
                ("mart.v_stage_dropoff", "where couples drop out, and the days between stages"),
                ("mart.v_no_reply_rate", "share of decided inquiries closed by the 30-day silence rule"),
                ("mart.v_followup_list", "today's open inquiries ranked by chance to book"),
                ("mart.v_model_health", "the production models' metrics and latest checks"),
                ("mart.v_model_versions", "every training run and the gate's decision"),
                ("mart.v_demand_forecast", "the next 6 months: weddings and staff-hours against capacity, and what to do"),
                ("mart.v_demand_accuracy", "each forecast of an ended month against what happened")]},
    {"key": "ml", "name": "Feature store and models", "where": "schemas feature and ml", "gcp": "Vertex AI Feature Store and Model Registry, BigQuery",
     "text": "Built from Silver and Gold for the two models. Every night's features are kept, so a model is always trained on what "
             "was known at the time.",
     "tables": [("feature.InquiryFeatures", "one row per open inquiry per night: the inputs of the conversion model"),
                ("feature.InquiryLabel", "view: whether each closed inquiry was booked, and when it closed"),
                ("feature.DemandFeatures", "per night and month ahead: weddings on the books, open inquiries, season"),
                ("ml.ModelVersion", "the registry of both models: every version, its metrics, decision and the model file"),
                ("ml.InquiryScore", "each night's chance to book per open inquiry, with its band"),
                ("ml.DemandForecast", "each night's forecast of the next 6 months"),
                ("ml.DriftCheck", "each night's monitoring results")]},
    {"key": "control", "name": "Pipeline control", "where": "schemas etl and work", "gcp": "BigQuery dataset",
     "text": "How the pipeline keeps track of itself: what ran, where to resume, what was rejected and why.",
     "tables": [("etl.Run", "one row per nightly run: date, mode, status"),
                ("etl.StepRun", "each step of each run: seconds, rows, result; the checkpoint for resume"),
                ("etl.Watermark", "per table, the last UpdatedAt loaded: where the next extract starts"),
                ("etl.DqRule", "the data-quality rules, stored as data"),
                ("etl.DqResult", "each rule's result in each run"),
                ("etl.RejectedRow", "the dead letter: rows that broke a rule, until they are fixed in the CRM"),
                ("etl.Alert", "late changes to frozen weeks, drift and failed checks"),
                ("work.*", "scratch tables the transform step builds and drops each run")]},
]

BANNED = ["seamless", "robust", "leverage", "empower", "unlock", "delve", "cutting-edge", "journey", "elevate",
          "harness", "streamline", "game-changer", "revolutioniz", "effortless", "state-of-the-art"]


def _plain(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _rows(c, sql, params=None):
    return [{k: _plain(v) for k, v in r.items()} for r in c.execute(sql, params).fetchall()]


def snapshot(cfg, asof=ASOF):
    """Everything the page shows from the warehouse, as of the last replayed night. Called by the e2e notebook
    before its failure tests add test rows; written to docs/showcase/snapshot.json."""
    sys.path.insert(0, str(ROOT))
    from etl import db
    d = asof.isoformat()
    with db.connect(cfg.dw_dsn, cfg, autocommit=True) as c:
        runs = _rows(c, 'SELECT "RunID", "AsOfDate", "Mode", "Status" FROM etl."Run" WHERE "AsOfDate" <= %s ORDER BY 1', (asof,))
        steps = _rows(c, '''SELECT s."RunID", s."Step", s."DurationSec", s."RowsIn", s."RowsOut", s."Metrics"
                             FROM etl."StepRun" s JOIN etl."Run" r USING ("RunID")
                             WHERE r."AsOfDate" <= %s AND s."Status" = 'success' ORDER BY s."StepRunID"''', (asof,))
        for s in steps:      # keep only the plain numbers of each step's metrics
            s["Metrics"] = {k: v for k, v in (s["Metrics"] or {}).items() if isinstance(v, (int, float))}
        snap = {
            "asof": d,
            "runs": runs,
            "steps": steps,
            "registry": _rows(c, '''SELECT "Version", "AsOfDate", "Algorithm", "Status", "TrainRows", "TestRows", "Decision",
                                        "Metrics" -> 'chosen' AS chosen, "Metrics" -> 'baseline' AS baseline,
                                        "Metrics" -> 'vs_production' AS vs_production
                                    FROM ml."ModelVersion" WHERE "AsOfDate" <= %s AND "Model" = 'conversion' ORDER BY 1''', (asof,)),
            "demand_registry": _rows(c, '''SELECT "Version", "AsOfDate", "Algorithm", "Status", "Decision",
                                               "Metrics" -> 'chosen' AS chosen, "Metrics" -> 'candidates' AS candidates
                                           FROM ml."ModelVersion" WHERE "AsOfDate" <= %s AND "Model" = 'demand' ORDER BY 1''', (asof,)),
            # the view shows the latest forecast night; the snapshot is taken on the last replayed night
            "demand_forecast": _rows(c, 'SELECT * FROM mart.v_demand_forecast WHERE "AsOfDate" = %s ORDER BY "Month"', (asof,)),
            # every forecast of a month that has ended, averaged per month and horizon
            "demand_accuracy": _rows(c, '''SELECT "Month", "Horizon", round(avg("Forecast"), 2) AS forecast, min("Lo") AS lo,
                                               max("Hi") AS hi, max(actual) AS actual, round(avg(in_interval::int), 3) AS in_interval,
                                               count(*) AS forecasts
                                           FROM mart.v_demand_accuracy WHERE "AsOfDate" <= %s GROUP BY 1, 2 ORDER BY 1, 2''', (asof,)),
            "drift": _rows(c, 'SELECT "AsOfDate", "Version", "Check", "Value", "Status" FROM ml."DriftCheck" WHERE "AsOfDate" <= %s ORDER BY 1, 3', (asof,)),
            "alerts": _rows(c, '''SELECT a."CreatedAt", r."AsOfDate", a."Severity", a."Message" FROM etl."Alert" a
                                  LEFT JOIN etl."Run" r USING ("RunID") ORDER BY a."AlertID"'''),
            "dq": {"rules": c.execute('SELECT count(*) AS n FROM etl."DqRule" WHERE "IsActive" = %s', ("Y",)).fetchone()["n"],
                   "rejected": c.execute('SELECT count(*) AS n FROM etl."RejectedRow"').fetchone()["n"]},
            "tables": [{**t, "rows": c.execute(f'SELECT count(*) AS n FROM {t["schema"]}."{t["table"]}"').fetchone()["n"]}
                       for t in _rows(c, '''SELECT table_schema AS schema, table_name AS "table" FROM information_schema.tables
                                            WHERE table_type = 'BASE TABLE' AND table_schema IN ('dim', 'fact', 'stg', 'feature', 'ml')
                                            ORDER BY 1, 2''')],
            "dashboard": {
                "workload": _rows(c, '''SELECT week_start, employee, role, "UtilizationRate", "ActiveEvents" FROM mart.v_weekly_workload
                                        WHERE role = ANY(%s) AND week_start <= %s ORDER BY 1, 2''', (list(OPS_ROLES), asof)),
                "peak": _rows(c, "SELECT * FROM mart.v_peak_acceptance")[0],
                "turned_away": _rows(c, '''SELECT requested_month, sum(inquiries_turned_away)::int AS n FROM mart.v_turned_away_demand
                                           GROUP BY 1 ORDER BY 1'''),
                "funnel": _rows(c, "SELECT * FROM mart.v_funnel_by_channel ORDER BY inquiries DESC"),
                "stages": _rows(c, "SELECT * FROM mart.v_stage_dropoff")[0],
                "no_reply": _rows(c, '''SELECT inquiry_month, sum(decided)::int AS decided, sum(no_reply)::int AS no_reply
                                        FROM mart.v_no_reply_rate GROUP BY 1 ORDER BY 1'''),
                "response": _rows(c, "SELECT * FROM mart.v_response_vs_conversion"),
                "lost": _rows(c, "SELECT * FROM mart.v_lost_reasons ORDER BY lost DESC"),
                # the view picks the latest scoring day; the page shows the last replayed night
                "followup": _rows(c, '''SELECT s."InquiryID", s."Band", s."Score", f."Channel", f."BudgetRange", f."PackageType",
                                           f."AgeDays", f."HoursSilent", f."CoupleMsgs", f."Consulted", f."Quoted", s."Version"
                                        FROM ml."InquiryScore" s JOIN feature."InquiryFeatures" f
                                          ON f."InquiryID" = s."InquiryID" AND f."AsOf"::date = s."AsOfDate"
                                        WHERE s."AsOfDate" = %s ORDER BY s."Score" DESC''', (asof,)),
            },
        }
    return snap


def tests():
    """Run pytest; each test with its file, its Scenario/Expected docstring and its result."""
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "r.xml"
        subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", "tests"],
                       cwd=ROOT, capture_output=True, text=True)
        result = {(tc.get("classname").split(".")[-1], tc.get("name")): "FAIL" if len(tc) else "PASS"
                  for tc in ET.parse(xml).getroot().iter("testcase")}
    out = []
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        for fn in ast.parse(f.read_text()).body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_"):
                doc = ast.get_docstring(fn) or ""
                out.append({"file": f.name, "name": fn.name, "doc": doc, "result": result.get((f.stem, fn.name), "NOT RUN")})
    return out


def build():
    snap = json.loads((OUT_DIR / "snapshot.json").read_text())
    replay = pd.read_csv(OUT_DIR / "replay_log.csv")
    checks = pd.read_csv(OUT_DIR / "checks.csv").fillna("")
    load_test = pd.read_csv(OUT_DIR / "load_test.csv")
    files = sorted({f for n in NODES for f in n[4]})
    data = {
        "snapshot": snap,
        "replay": json.loads(replay.to_json(orient="records")),
        "checks": checks.to_dict("records"),
        "load_test": json.loads(load_test.to_json(orient="records")),
        "tests": tests(),
        "nodes": [dict(zip(["id", "title", "zone", "gcp", "files", "steps", "text"], n)) for n in NODES],
        "gcp": {k: {"url": u, "why": w} for k, (u, w) in GCP.items()},
        "zones": ZONES,
        "code": {f: (ROOT / f).read_text() for f in files},
        "built": datetime.now().strftime("%d %b %Y %H:%M"),
    }
    page = (ROOT / "web" / "showcase.html").read_text()
    v = ROOT / "web" / "vendor"
    words = re.sub(r"<[^>]+>", " ", page) + " ".join(n[6] for n in NODES) + " ".join(w for _, w in GCP.values())
    bad = [b for b in BANNED if re.search(rf"\b{b}", words, re.I)]
    assert not bad, f"banned words in the page text: {bad}"
    listed = {t for z in ZONES for t, _ in z["tables"]}
    missing = [f'{t["schema"]}.{t["table"]}' for t in snap["tables"] if f'{t["schema"]}.{t["table"]}' not in listed]
    assert not missing, f"tables in the warehouse but not on the page (add them to ZONES): {missing}"
    words += " ".join(z["text"] + " ".join(d for _, d in z["tables"]) for z in ZONES)
    bad = [b for b in BANNED if re.search(rf"\b{b}", words, re.I)]
    assert not bad, f"banned words in the zone text: {bad}"
    for n in data["nodes"]:
        assert n["files"] and n["gcp"] and all(g in GCP for g in n["gcp"]), n["id"]
    assert all(t["result"] == "PASS" for t in data["tests"]), [t["name"] for t in data["tests"] if t["result"] != "PASS"]
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    page = (page.replace("/*__VENDOR_CSS__*/", (v / "hljs-github.min.css").read_text())
                .replace("/*__VENDOR_JS__*/", "\n".join((v / f).read_text() for f in
                                                        ["chart.umd.min.js", "highlight.min.js"]).replace("</script", "<\\/script"))
                .replace("/*__DATA__*/", f"const DATA = {blob};"))
    assert not any(f"/*__{k}__*/" in page for k in ("VENDOR_CSS", "VENDOR_JS", "DATA"))
    out = ROOT / "AisleCo_Pipeline_Showcase.html"
    out.write_text(page)
    print(f"written {out.name}: {out.stat().st_size / 1e6:.1f} MB, {len(data['tests'])} tests, "
          f"{len(data['checks'])} checks, {len(data['replay'])} nights, {len(files)} source files")


if __name__ == "__main__":
    build()
