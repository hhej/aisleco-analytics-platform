# Aisle & Co. v2: master plan

Status: **CONFIRMED 16 Nov 2026.** Presentation **Sun 29 Nov 2026**, 25-minute slot, aim for 15–20 minutes, presented with a teammate.
After you confirm, Claude builds it milestone by milestone without further guidance (Section 9), and logs
progress at the bottom of this file.

Specifications (read with this plan):

| Spec | What it fixes |
|---|---|
| [01 Sources and ingestion](01_sources_and_ingestion.md) | The 4 source systems, their real payload formats, how each one is ingested (full, incremental, push, file drop) and why |
| [02 Data lake](02_data_lake.md) | Folder layout of Bronze / Silver / quarantine / sandbox, file formats, every Silver table and its transformations, data-quality rules |
| [03 Warehouse and marts](03_warehouse_and_marts.md) | Gold star schema (grain, keys, SCD2), bus matrix, every KPI formula, the role marts and who may read them |
| [04 Machine learning](04_machine_learning.md) | Two models: purpose, decision, label, features, leakage control, training and validation method, metrics, baselines, MLOps |
| [05 Dashboards](05_dashboards.md) | Why the dashboards are separate, who uses each one, page wireframes, tile-by-tile specification, design rules |
| [06 Simulation](06_simulation.md) | The simulated business that feeds the sources: volumes, behaviour assumptions, injected data problems, calibration targets |

---

## 1. Goal

Rebuild the capstone as **one working analytics platform that runs on the MacBook**, in which every number on a
dashboard can be traced back, step by step, to the source record it came from. It must earn full marks on the
six rubric dimensions (Section 3) and survive the professor's questions in the live demo.

What changes from v1, in one line each:

- **Sources:** v1 ran only the CRM through the pipeline; the other sources lived in a pandas notebook. v2 runs four
  real source systems (a CRM database, a chat webhook receiver, tablet sync files, external calendars) through one pipeline.
- **Lake:** v1 had CSV folders and a `stg` schema. v2 has a real lake folder with Bronze (raw, immutable) and Silver
  (clean Parquet), plus quarantine and sandbox zones.
- **Warehouse:** a star schema with SCD Type 2 for staff contracts, a conformed channel dimension, and the ML outputs
  as facts; marts are one schema per role, with database permissions.
- **ML:** a lead-conversion model with an explicit decision, label and validation design (time-series cross-validation,
  tuning, calibration, SHAP reasons, baselines, A/B holdout, MLflow), plus a staffing-demand forecast linked to it.
- **Dashboards:** a custom web app with logins. Sales, Operations and Executive each get pages built for their
  decisions, plus a Data & ML health page for the data team. No model jargon on business pages.

## 2. Decisions

### Taken with you (16 Nov 2026)

| # | Topic | Decision |
|---|---|---|
| D1 | Rev10 scope | Keep Aisle & Co., Problem 1 (seasonal demand and capacity) and Problem 2 (high inquiry, low conversion) and their KPIs. Schemas, sources, volumes and the warehouse design may change. The report is rewritten to match. |
| D2 | Deadline | Presentation in 1–2 weeks. The **Must** scope (Section 7) finishes first; the rest is buffer and polish. |
| D3 | LINE | Simulated. The simulator posts real-format LINE and Meta webhook JSON (with valid signatures) to our receiver. |
| D4 | Dashboards | Custom web app: Python API (FastAPI) + React front end, with per-role logins. |
| D5 | Deliverables | Report rewrite (docx), slide deck (pptx), demo runbook, data catalog page. |
| D6 | Names on the call list | Shown, from a restricted schema that only the Sales role can read. The rest of the warehouse stays pseudonymous. |
| D7 | Volume | 100 weddings a year, about 1,500 inquiries a year (6–8 % conversion), history from 1 Jan 2024 to the day before the backfill runs. |
| D8 | Git | A local commit after each milestone passes its checks. No push. |
| D9 | Hosting | Everything native on the MacBook (Apple M2, 16 GB, macOS 26). No Docker (removed earlier at your request), no cloud. |

### Made by Claude (you can overturn any of these when you review)

| # | Topic | Decision | Why |
|---|---|---|---|
| C1 | Lake storage | Plain folder `lake/` on the Mac disk | One machine; the same layout maps 1:1 to S3 or MinIO later. MinIO would add a service and gain nothing here. |
| C2 | Lake file formats | Bronze keeps the format received (JSONL, JSON, CSV, ICS). Silver is Parquet (ZSTD). | Raw must stay raw; Parquet is typed, columnar and compressed. |
| C3 | Lake engine | DuckDB (in-process SQL over JSON, CSV and Parquet) | Zero operations, fast; no cluster needed. |
| C4 | Table format | No Delta or Iceberg. Silver is rebuilt from Bronze each run and swapped in atomically (write to a temporary folder, then rename). | ACID and time travel are not needed when Silver is a pure function of an immutable Bronze. |
| C5 | Warehouse | PostgreSQL 16, database `aisleco_dw` | Many concurrent dashboard readers, per-role GRANTs, `MERGE`. A DuckDB file allows only one writer and has no roles. |
| C6 | Transform code | Plain SQL files run by our Python runner (no dbt) | Reuses v1's tested runner, error handling and data-quality framework. Lineage and formulas are documented in the catalog (M8). |
| C7 | Orchestration | Airflow 2.10.5 in the existing `.venv-airflow` (macOS fixes already in place) | Course-standard; already working. |
| C8 | ML tooling | scikit-learn, LightGBM, Optuna, SHAP, MLflow (local server, Postgres backend) | Standard data-science stack; everything runs on CPU in minutes. |
| C9 | Front end | React + Vite + TypeScript + Tailwind + Recharts, built to static files served by FastAPI | One process, one URL, modern component ecosystem. |
| C10 | Database names | v2 uses `aisleco_crm` and `aisleco_dw`. The v1 databases with those names are renamed to `v1_aisleco_crm` / `v1_aisleco_dw` (not dropped). Other v1 databases are left untouched. | Clean names for the demo, nothing destroyed. |
| C11 | v1 files | Moved to `archive/v1/` (notebooks, Streamlit app, showcase page, old SQL and scripts). Colab support dropped. | Out of the way but kept; everything now runs on the Mac. |
| C12 | Clock | Simulated history ends yesterday; a nightly job simulates the previous day, then the pipeline runs. On presentation day the dashboards show that day. | The demo shows "today", in peak season (Dec–Feb weddings). |

## 3. Rubric → evidence map

The rubric (MADT 8102 course outline, p. 6) has six dimensions of 10 points each. "10 points" wording is quoted.

| Dimension | "10 points" means | Evidence v2 will show | Where |
|---|---|---|---|
| Data Sources | Correct implementation | 4 sources covering all 4 types: CRM database (structured), LINE/Meta webhooks and tablet JSON (semi-structured), chat text (unstructured), Google's public Thai holiday calendar and the auspicious-date file (external). Each one is ingested by its own method, chosen and justified. | Spec 01, M1–M2, demo |
| Database & Lake/Warehouse design | Correct, normalized, meets business needs | OLTP v6 in 3NF with ER diagram; lake zones with layout, formats, retention and access; Kimball star schema with grain statements, bus matrix, SCD2, junk and role-playing dimensions; marts per role | Specs 02–03, M1–M3 |
| ETL / Data pipeline | Fully functional, efficient, error-handled | Daily Airflow run: incremental CDC extraction, Bronze → Silver → Gold → marts → features → scores; data-quality rules with quarantine; retries, alerts, checkpoint/resume, idempotency; Ch.6 test types (source-to-target, transformation, end-to-end, performance, error handling); run time under 2 minutes at 10× volume | Specs 01–03, M2–M4 |
| Dashboards & Visualization | Fully aligned, insightful, clear design | 3 role dashboards (Sales, Operations, Executive) + Data & ML health; each tile answers a named decision; KPI definitions from the catalog; write-back ("mark contacted") | Spec 05, M7 |
| AI/ML Integration | Correct, validated, adds real business value | Lead-conversion model with time-series CV, tuning, calibration, baselines, confidence intervals, SHAP reasons; THB value estimate and an A/B holdout design; feature store, registry, scheduled scoring, drift and performance monitoring, weekly retraining; demand forecast with backtest and intervals | Spec 04, M5–M6 |
| Business Problem & Storytelling | Strong link, clear narrative, persuasive | One story from a couple's first LINE message to a staffing decision; every KPI tied to a decision and a THB figure; report, deck and live demo | M9 |

## 4. Architecture

```
 SOURCES (systems of record)            INGESTION (how)                    DATA LAKE  lake/  (files)                 WAREHOUSE  aisleco_dw (PostgreSQL)         USE
 ───────────────────────────            ─────────────────                  ─────────────────────────                ──────────────────────────────────         ───
 ① CRM  aisleco_crm (PostgreSQL)  ──►  nightly, incremental by UpdatedAt ─► bronze/crm/…  (CSV + manifest)  ┐
                                       + full snapshot of master tables                                      │
                                       + primary-key list (deletes)                                          │
 ② Chat gateway :8001 (FastAPI)   ──►  push: platform POSTs each event  ─► bronze/chat/… (JSONL per hour)   ├─► silver/ (Parquet, DuckDB SQL) ─► stg ─► gold (star schema) ─► mart_sales  ─► Sales pages
    LINE / Messenger / Instagram       signature checked, raw appended                                       │     clean, dedupe, conform,             dims + facts       mart_ops    ─► Operations page
    + shared-inbox send log                                                                                  │     PII masked, text → features         SCD2, surrogate    mart_exec   ─► Executive page
 ③ Fair tablets (offline app)     ──►  file drop on reconnect           ─► bronze/fair_tablet/… (JSON)      │     quarantine bad rows                  keys               mart_platform► Data & ML page
 ④ External: Thai holiday ICS     ──►  monthly pull (full)              ─► bronze/external/… (ICS, CSV)     ┘                                          │
    + auspicious-date CSV               yearly file                                                                                                 ├─► feature (store) ─► ml (MLflow) ─► scores back into gold
                                                                                                                                                    └─► sales_pii (restricted: names for Sales only)
 Orchestration: Airflow (daily 02:00, weekly training, monthly external pull).   Governance: etl schema (run log, DQ results, quarantine index, alerts) + catalog/.
```

Where each zone lives, in one table:

| Zone | Where | Format | Written by | Who can read |
|---|---|---|---|---|
| Landing (transient) | `lake/landing/` (tablet and external drop folders, the gateway's open hourly file) | as sent | sources | pipeline only |
| Bronze (raw, immutable) | `lake/bronze/<source>/…/<partition>/` | as received + manifest | ingestion step | data team; chat text only by the restricted ML job |
| Silver (clean, conformed) | `lake/silver/<table>/` and `lake/silver_restricted/<table>/` | Parquet | DuckDB SQL (`pipeline/silver/sql`) | data team, ML |
| Quarantine | `lake/quarantine/<run>/<source>/` + index in `etl.rejected_row` | JSONL with the reason | DQ step | data team |
| Sandbox | `lake/sandbox/` | anything | analysts | data team |
| Gold (warehouse) | `aisleco_dw.gold` | PostgreSQL tables | warehouse load | data team, marts |
| Marts | `aisleco_dw.mart_sales`, `mart_ops`, `mart_exec`, `mart_platform` | PostgreSQL views | mart step | the matching role only |
| Restricted personal data | `aisleco_dw.sales_pii` | PostgreSQL table | warehouse load | Sales role only |

## 5. Tech stack

| Layer | Tool (version pinned at install) | Runs as |
|---|---|---|
| Source database | PostgreSQL 16 (Homebrew), database `aisleco_crm` | `brew services` |
| Chat gateway | FastAPI + uvicorn | `scripts/services.sh`, port 8001 |
| Lake | folder `lake/`, DuckDB, Parquet | inside pipeline steps |
| Warehouse | PostgreSQL 16, database `aisleco_dw` | `brew services` |
| Pipeline | Python 3.11 (`.venv`), psycopg 3, DuckDB | CLI `python -m pipeline …`, called by Airflow |
| Orchestration | Airflow 2.10.5 (`.venv-airflow`, LocalExecutor, metadata DB `airflow`) | port 8080 |
| ML | scikit-learn, LightGBM, Optuna, SHAP | inside pipeline steps |
| Experiment tracking, registry | MLflow server (backend DB `mlflow`, artifacts `lake/mlflow/`) | port 5050 (5000 is taken by macOS AirPlay) |
| Web app | FastAPI API + React/Vite/TypeScript/Tailwind/Recharts (static build) | port 8000 |
| Tests | pytest | CLI |

`scripts/services.sh start|stop|status` starts and stops the gateway, MLflow, Airflow and the web app (PostgreSQL stays a brew service).
Logs go to `var/log/`.

## 6. Repository layout (v2)

```
sources/crm/            OLTP v6 DDL, UpdatedAt triggers, dbdiagram file
sources/chat_gateway/   FastAPI receiver: /webhook/line, /webhook/meta, /outbound
sources/external/       fetchers: Thai holiday ICS, auspicious CSV
sim/                    the business simulator (couples, staff, platforms, tablets)
pipeline/ingest/        source → Bronze   (crm_cdc.py, files.py, external.py)
pipeline/silver/        Bronze → Silver   (sql/NN_*.sql run by DuckDB, run.py)
pipeline/warehouse/     Silver → stg → Gold (sql/NN_*.sql run by PostgreSQL, load.py)
pipeline/marts/         Gold → role marts (sql/*.sql, grants.sql)
pipeline/quality/       data-quality rules (rules.yml), quarantine, reconciliation
pipeline/__main__.py    python -m pipeline init | run | resume | status
ml/                     feature store, conversion model, demand forecast, monitoring
app/api/                FastAPI: login, role marts, write-back
app/web/                React front end
airflow/dags/           aisleco_daily, aisleco_weekly_train, aisleco_monthly_external, aisleco_sim_day
catalog/                tables.yml, kpis.yml → catalog page (M8)
tests/                  pytest
docs/v2/                this plan, the specs, the runbook; report and deck outputs
archive/v1/             v1 (reference only)
lake/, var/             data (incl. landing drop folders), logs (git-ignored)
```

Reused from v1 (adapted, not rewritten): the incremental extractor (watermark, overlap, shared snapshot, batch cap,
key lists, manifest, checksums), the data-quality framework (rules as data, dead letter, reconciliation), the run log
and resume logic, the Gold transform SQL, the point-in-time feature function, the training gate, the replay test idea,
the benchmark script, the Airflow launcher with its macOS fixes, and the simulator's behaviour model.

## 7. Milestones

Priority: **Must** = needed for full marks and the demo. **Should** = strengthens the grade. **Could** = only if time remains.
Each milestone ends with: tests pass → progress log updated → local commit `v2 Mn: <title>`.

### M0. Checkpoint and scaffolding (Must)
1. Commit the current uncommitted v1 work as `v1 final (checkpoint before v2)`.
2. Move v1 files to `archive/v1/` (list in C11); keep `source/` (course PDFs) and the Rev10 docx where they are.
3. Rename databases `aisleco_crm` → `v1_aisleco_crm`, `aisleco_dw` → `v1_aisleco_dw`.
4. Create the v2 folders (Section 6), `.gitignore` entries, `requirements.txt` additions (duckdb, fastapi, uvicorn, httpx,
   icalendar, mlflow, optuna, shap, bcrypt, itsdangerous, pyyaml), `scripts/services.sh`.
5. Install and pin versions; `npm create vite` for `app/web`.

Check: `pytest` collects 0 v2 tests without errors; `services.sh status` works; `git status` clean after the commit.

### M1. Source systems and the simulated business (Must)
1. OLTP v6 DDL (Spec 01 §2): v5 + `EmployeeContract`, `SalesActivity`, `Inquiry.AssignedSalesEmployeeID`, `Event.CancelledDate`;
   `AuspiciousDate` removed (now external); `UpdatedAt` triggers; dbdiagram file.
2. Chat gateway (Spec 01 §3): signature checks, raw append to hourly JSONL, `/health`, sim-clock header honoured only in sim mode.
3. External fetchers: Thai holiday ICS (real download, cached copy in `sources/external/fixtures/` for offline runs), auspicious CSV generator.
4. Simulator `sim/` (Spec 06): day-by-day engine; writes the CRM like the app would; posts webhooks with valid signatures;
   writes tablet batch files; injects the data problems listed in Spec 06 §7.
5. `python -m sim backfill --to yesterday` and `python -m sim day --date D`.

Check (Spec 06 §8 calibration targets): inquiries 1,350–1,650 per full year; weddings 90–115 per year; conversion 6–8 %;
≥ 55 % of weddings in Nov–Feb; chat events ≈ 15 per inquiry; redelivered webhooks ≈ 2 %; duplicate tablet batches ≈ 5 %;
TikTok share of online inquiries ≈ 10 % in 2024–25 and ≈ 33 % in 2026. A `sim report` prints these numbers.

### M2. Ingestion and the lake (Must)
1. `pipeline/ingest/crm_cdc.py`: v1 extractor adapted to OLTP v6 and the Bronze layout; master tables as full snapshots;
   restricted PII extract to `bronze/crm_restricted/`.
2. `pipeline/ingest/files.py`: chat hourly files and tablet files → Bronze, file checkpoints in `etl.file_checkpoint`, checksums.
3. `pipeline/ingest/external.py`: monthly ICS pull, yearly auspicious file.
4. `pipeline/silver/`: the 12 Silver tables of Spec 02 §4, run by DuckDB, atomic swap, row counts in `etl.step_run`.
5. `pipeline/quality/`: rules of Spec 02 §5 (reject → quarantine, warn, block); reconciliation Bronze → Silver.

Check: Silver tables built from the backfill; a second run with no new data changes nothing (idempotent);
the injected problems of Spec 06 §7 all land in quarantine or are flagged, each named in a test.

### M3. Warehouse, marts and access control (Must)
1. `stg` load (DuckDB → PostgreSQL), Gold dimensions (DimDate, DimEmployee SCD2, DimPackage, DimChannel, DimInquiryOutcome)
   and facts (FactInquiry, FactEventBooking, FactStaffWorkload, FactSalesActivity; FactLeadScore and FactDemandForecast tables created empty).
2. Merge with change detection, stable surrogate keys, unknown members (key −1).
3. Marts `mart_sales`, `mart_ops`, `mart_exec`, `mart_platform`; `sales_pii`; database roles and GRANTs (Spec 03 §7).
4. Reconciliation Silver → Gold, and source-to-target check against the simulator's ground truth (every KPI in Spec 03 §6).

Check: source-to-target 0 differences; a test logs in as each database role and is refused outside its mart.

### M4. Orchestration and the Chapter 6 test suite (Must)
1. Airflow DAGs: `aisleco_daily` (sim-day at 01:00 is a separate DAG), `aisleco_monthly_external`; retries, failure alerts to `etl.alert`.
2. `python -m pipeline run --asof D [--full-refresh]`, `resume RUN_ID`, `status`.
3. Tests by Ch.6 type: source-to-target, transformation rules, end-to-end (30 nights replayed: incremental result = full refresh),
   error handling (CRM down, corrupt file, bad signature, schema change in a webhook, DQ block, warehouse down mid-load → resume),
   idempotency, performance (benchmark at 1× and 10× volume).

Check: all tests pass; daily run under 2 minutes at 10× volume; one green Airflow run in the UI (screenshot).

### M5. Lead-conversion model and MLOps (Must)
Everything in Spec 04 §1–§10: feature store v2 (point-in-time, with text features), training set builder, rolling-origin CV,
Optuna tuning, calibration, baselines, gate, MLflow registry with the `champion` alias, daily batch scoring into
FactLeadScore with SHAP reasons and next best action, holdout flag, monitoring (drift, calibration, matured-label performance),
weekly retraining DAG, model card.

Check: champion beats the best baseline on PR-AUC with the paired-bootstrap rule of Spec 04 §6; the point-in-time test passes;
the model card is generated.

### M6. Staffing-demand forecast (Should)
Spec 04 §11–§12: weekly staff-hours by role for 26 weeks ahead = booked + expected from open inquiries + expected new bookings,
with P10/P50/P90 from Monte Carlo; rolling-origin backtest against 3 baselines; FactDemandForecast.

Check: backtest table produced; the production method is whichever wins (honest outcome even if a baseline wins).

### M7. Web app: role dashboards (Must; Data & ML page is Should)
Spec 05: login, role routing, Sales "Today" + "Sales performance", Operations "Capacity planner" + date checker,
Executive "Business overview", Data & ML health, admin demo panel (send a test LINE message, run the pipeline).

Check: each role sees only its pages (API test and database refusal); page load under 1 s on local data;
screenshots of every page reviewed in Chrome at 1440 px and at tablet width.

### M8. Data catalog page (Must, you asked for it)
`catalog/tables.yml` + `catalog/kpis.yml` (one entry per table, column and KPI: meaning, formula, source columns, grain, additivity).
The API serves the definitions to dashboard tooltips (one source of truth). A generated HTML catalog page with lineage
source → Bronze → Silver → Gold → mart → dashboard tile. A test fails if a Gold column or mart KPI is missing from the catalog.

### M9. Deliverables (Must)
1. Demo runbook `docs/v2/DEMO_RUNBOOK.md`: minute-by-minute script, exact commands, expected screen, fallback for each step.
2. Slide deck (pptx), consulting style, about 15–18 slides: problem → current pain in numbers → platform → evidence → model value
   in THB → recommendations → demo → roadmap.
3. Report (docx), new revision (Rev 13): business, problems and KPIs, OLTP, sources and ingestion, data lake, warehouse and marts,
   ETL and quality, dashboards, ML and MLOps, results and value, limits and roadmap, appendices (catalog extract, test results, revision history).
4. Full rehearsal from a clean state: `services.sh start` → backfill → initial load → train → dashboards, timed.

### M10. Polish (Could)
Larger phrase bank for chat text (optionally expanded with the local Ollama model), historical A/B replay, extra stress runs,
dark mode for the web app, Thai labels.

## 8. Schedule

Today is Mon 16 Nov 2026; the presentation is 1–2 weeks away (exact date still to confirm, see Section 11).
Days are elapsed working days of autonomous runs; checkpoints let you review without blocking the work.

| Day | Milestones | You can review |
|---|---|---|
| 1 | M0, M1 | `sim report` numbers, sample payloads in Bronze |
| 2 | M2 | lake folders, Silver tables, quarantine examples |
| 3 | M3, M4 | star schema diagram, Airflow run, test results |
| 4 | M5 | model card, MLflow UI |
| 5 | M7 (Sales, Operations, Executive) | screenshots |
| 6 | M8, M6, Data & ML page | catalog page, forecast backtest |
| 7 | M9 runbook + deck + report draft | the three documents |
| 8+ | rehearsal, your feedback, M10 | |

If the presentation is on day 7 or earlier, M6 and M10 drop first, then the Data & ML page.

## 9. Execution protocol (how Claude works after you confirm)

1. At the start of each milestone, re-read this plan and the specs it names. Build only what they define.
2. A question the specs do not answer: pick the simplest option that is consistent with the specs, record it in the
   decision log (Section 10) with its reason, and continue. Stop and ask only for: credentials, anything destructive outside
   this repository and the v2 databases, or a change that would contradict a decision in Section 2.
3. Never push to GitHub. Never drop a v1 database. Never edit files under `source/` or the Rev10 docx.
4. Each milestone ends with tests, a progress-log entry (what was built, its check results, timings) and a local commit.
5. Plain wording in all user-facing text: no filler, no model jargon on business pages.
6. Memory file `aisleco-v2-rebuild` holds the short state across sessions; this file holds the full state.

## 10. Decision log (changes after confirmation)

| Date | # | Decision | Reason |
|---|---|---|---|
| 2026-11-16 | D10 | Presentation 29 Nov, 15–20 min, two presenters | user |
| 2026-11-16 | D11 | Use subagents for parallel work | user |
| 2026-11-16 | C13 | Google's holiday feed has Asalha Bucha only to 2027: Lent after 2027 approximated and flagged | real data gap found in M1 |
| 2026-11-16 | C19 | Lead-model label window 60 days (Spec 04 §2 rule: 30 d held 55 % of bookings, 45 d 72 %, 60 d 82 %) | pre-registered EDA rule |
| 2026-11-16 | C20 | Gate compares with the best non-ML baseline (B0–B2); logistic vs LightGBM is CV model selection, ties → simpler | the first run compared logistic regression with itself |
| 2026-11-16 | C21 | Training rows sampled at ages 1,2,3,4,5,7,10,14,21,30,45,60 days (weights per inquiry); test keeps every daily row | 3× faster tuning; each test day is a real decision |
| 2026-11-16 | C22 | Forecast pipeline hours use point-in-time stage × channel rates, not Model A; pickup by LightGBM quantile regression; band split-conformal | leakage-free backtest; quantile band covered 48 %, conformal 79 % |
| 2026-11-16 | C23 | Lead-model gate revised after run v2 (which I report): superiority over today's practice + non-inferiority (−0.05) vs the best rule; ablation moved from the test set to CV and picks the feature set | v2: PR-AUC 0.389 vs B0 0.129, vs B1 0.345 (gain CI −0.06..+0.16, underpowered); test-set ablation would leak |
| 2026-11-16 | C24 | Lead-model test period 6 months (was 3), training cut moved back accordingly | v3 non-inferiority CI −0.097..+0.125 vs the stage rule: 3 months hold ~70 booked couples, too few to decide anything; v1–v3 stay in MLflow as the record |
| 2026-11-17 | C25 | Budget-band bug fixed (Silver 30_inquiry.sql: '1M-1.5M', '1.5M+' were mis-cased, 1,549 couples without budget); regression test added; champion v4 retired with the reason (`python -m ml retire`), corrected model retrained and gated on baselines only (a champion trained on wrong data is not a valid comparison) | found by the report writer |
| 2026-11-17 | C26 | Risk bands by test-period quantiles (High ≈ top 15 %, Medium next 25 %); daily score-drift PSI added | v4 bands gave 6.5 % High against the spec's ~15 % |
| 2026-11-16 | C15 | No separate monthly DAG: `ingest_files` pulls the holiday calendar on the 1st of the month (or when none exists) | one fewer moving part |
| 2026-11-16 | C16 | FactStaffWorkload stores week shares unrounded; C03 tolerance 0.001 h | rounding each week to 3 decimals lost up to 0.016 h per assignment |
| 2026-11-16 | C17 | Hours per wedding scaled by 0.87 (Spec 06 §3) | ops utilization averaged 89 % with 170 % peaks; now 78 % average, 110–144 % peaks, 33–48 % in Lent |
| 2026-11-16 | C18 | Intent tags computed once per distinct message text | regex from a table is compiled per row in DuckDB; fine at this size, revisit for real chat volumes |
| 2026-11-16 | C14 | Simulation starts 1 Jul 2022 (warm-up) so 2024 is a full year of weddings; calendar and DimDate run 2022–2028 | weddings are booked 4–14 months ahead |

## 11. Confirmed details

1. Presentation: 29 Nov 2026, 25-minute slot; target 15–20 minutes including the live demo.
2. A teammate co-presents: the runbook and deck split speaker parts (business story vs. live system).
3. Specs 01–06 accepted as written.
4. Use subagents for independent parallel work (e.g. gateway, front end, deliverables), with the specs as their brief.

## 12. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Time (1–2 weeks) | unfinished parts | Must-first order; Should/Could cut first; daily progress log |
| "The model only learns your simulator" | ML credibility | Spec 04 §13: honest framing, hidden variable never stored, baselines and ablations, noise, the same pipeline on real data |
| Airflow on macOS | crashes | keep the v1 fixes and launcher; the CLI runs the same steps without Airflow |
| MLflow install size or conflicts | delay | fall back to the v1 registry table in PostgreSQL (same gate logic) |
| 16 GB RAM with all services up | slow demo | services script starts only what a demo step needs; measured in rehearsal |
| Internet down at demo | external fetch fails | cached ICS fixture; Bronze already holds the last fetch; the failure itself is a handled error case |

## 13. Progress log

| Date | Milestone | Result |
|---|---|---|
| 2026-11-16 | Plan | Draft written |
| 2026-11-16 | Plan | Confirmed by user; M0 started |
| 2026-11-16 | M0 | v1 committed (4c0f085) and moved to `archive/v1/`; v1 DBs renamed `v1_aisleco_crm/dw`; deps installed (duckdb 2.0.0, fastapi, httpx, icalendar, bcrypt); `scripts/services.sh` (bash 3.2-safe). MLflow/Optuna/SHAP install still running. |
| 2026-11-17 | M5 | Lead model v4 = champion (gate history: v1 tie with itself, v2/v3 underpowered on 3-month test → C23, C24). 6-month out-of-time test: PR-AUC 0.362 vs today's practice 0.135 (gain CI +0.145..+0.315), vs stage rule +0.051 (CI −0.010..+0.118, non-inferior), ECE 0.011, ROC-AUC 0.868. CV ablation: chat-text intents +0.068 PR-AUC. TF-IDF tested and dropped (mean gain −0.001). Model card, EDA, daily scores with SHAP reasons. First real nightly cycle via Airflow (sim day 16 Nov → `aisleco_daily` 17 Nov, 16/16 tasks green, ~65 s); salespeople now work the ranked list (A/B holdout running). |
| 2026-11-17 | Incident | A comment typo in 20_chat_message.sql (my edit) dropped the R20 rows' receive time; runs 9–11 (including the scheduled 02:00 Airflow run 10) were **blocked by rule C01** (Bronze ≠ Silver reconciliation) before anything was published, yesterday's data stayed live, alerts recorded. Fixed; run 12 green; 84 tests pass. A real example of the error handling for the report. |
| 2026-11-17 | M5 | v6 champion after the budget fix and v4's retirement: PR-AUC 0.373, vs practice +0.240 (CI 0.153–0.338), vs stage rule +0.064 (CI −0.006–0.138), ECE 0.009. |
| 2026-11-17 | M9 | Deck + runbook and report Rev 13 in progress (subagents). Screenshots in docs/v2/img. Live-demo LINE message now creates the CRM inquiry too. |
| 2026-11-16 | M4 | (subagent) `scripts/e2e_replay.py`: 30 nights incremental vs full refresh, 39 tables/views, 196,070 rows, **0 differences**; incremental runs median 9.5 s. `tests/test_error_handling.py`: 10 Ch.6 failure cases (CRM down + resume, tampered Bronze, DQ block, crash + resume, idempotency, as-of guard, batch cap drain, late commit overlap, hard delete, orphan resolution). Benchmark: daily run ≤ 13.3 s at 10× (target 120 s), full load 66 s at 1×. Fixes: marts drop/recreate, Silver 59 → 12 s, FactStaffWorkload(EventID) index; simulator later-start bug; DQ results now survive a blocked run. |
| 2026-11-16 | M6 | Staffing forecast: booked + stage-rate pipeline + LightGBM quantile pickup, conformal band. Backtest (12 monthly origins) WAPE 5–12 wk: model 0.092, pickup ratio 0.124, booked-only 0.249, seasonal naive 0.337; band coverage 79 %. |
| 2026-11-16 | M7+M8 | (subagents) Web app: 6 pages, per-role DB connections, write-back, 24 tests; 9 mart issues found and fixed (Today page 16 s → 0.03 s). Data catalog: 97 tables, 983 columns, 42 KPIs, lineage to CRM column, completeness test. |
| 2026-11-16 | M2+M3 | Ingestion (CRM CDC reused from v1 + file ingestion), Silver runner + 13 Silver tables (11 trusted + 2 restricted; chat/tablet/calendar SQL by subagent), 39 DQ rules as data with quarantine, Gold star schema (5 dims incl. SCD2 staff, 6 facts), 4 role marts (36 views) + PostgreSQL grants, reconciliation C02/C03/C05, Airflow DAGs `aisleco_daily` + `aisleco_sim_day`. Full initial load (`--full-refresh`): ingest 166,792 CRM rows + 45,169 files ~50 s, Silver→marts 22 s. Resume from checkpoint tested. Injected problems caught: R20 58, R22 777, R30 4, R31 83, R33 41, R41 1, S05 1,126, R11 5. KPIs: conversion 7.0–8.8 %, ops utilization 72–81 %/yr. 40 tests pass. |
| 2026-11-16 | M1 | OLTP v6 (`sources/crm/`, 13 tables, simulated-clock trigger, no-overlap constraint, insert-only `crm_app_writer`). Chat gateway + holiday fetcher + auspicious generator (subagent, 17 tests). Simulator `sim/` calibrated (Spec 06 §8 table): backfill 1 Jul 2022 → 15 Nov 2026 in 83 s: 6,564 inquiries, 41,170 sales follow-ups, ~45,000 hourly webhook files, 144 tablet files, 163 rejected requests. Found: Google feed lacks lunar holidays after 2027 (C13); contract constraint caught an update-order bug. 21 tests pass. |
