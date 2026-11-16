# Aisle & Co. analytics platform (MADT 8102 capstone, v2)

A wedding organizer's end-to-end analytics system, running entirely on one MacBook: four source systems, a data lake
(Bronze/Silver), a Kimball warehouse with role-based marts, a lead-conversion model and a staffing-demand forecast with
MLOps, and a web app with a dashboard per role. All data is simulated by a business simulator (`sim/`) that writes
only through the doors a real company would use.

**Business problems.** (1) Seasonal demand and capacity: staff overloaded in Nov–Feb and idle in Buddhist Lent.
(2) High inquiry, low conversion: about 1,500 inquiries a year, 6–8 % book.

## Where things are

| Layer | Where | Spec |
|---|---|---|
| Sources | CRM `aisleco_crm` (PostgreSQL), chat gateway (LINE/Meta webhooks, :8001), fair-tablet drops, Google Thai holiday calendar + auspicious dates | [01](docs/v2/01_sources_and_ingestion.md) |
| Lake | `lake/landing`, `lake/bronze` (raw, immutable), `lake/silver` (Parquet), `lake/silver_restricted`, `lake/quarantine` | [02](docs/v2/02_data_lake.md) |
| Warehouse | `aisleco_dw`: `gold` star schema, `mart_sales` / `mart_ops` / `mart_exec` / `mart_platform`, `sales_pii` | [03](docs/v2/03_warehouse_and_marts.md) |
| Models | `ml/` (features, training, scoring, monitoring, forecast), MLflow (:5050) | [04](docs/v2/04_machine_learning.md) |
| Dashboards | `app/` (FastAPI + React), http://127.0.0.1:8000 | [05](docs/v2/05_dashboards.md) |
| Simulation | `sim/` | [06](docs/v2/06_simulation.md) |
| Plan and progress | [docs/v2/PLAN.md](docs/v2/PLAN.md) | |
| Data catalog | [docs/v2/catalog.html](docs/v2/catalog.html) (also at /catalog in the app) | |

## Run it

```bash
brew services start postgresql@16
scripts/rebuild.sh                      # everything from nothing (≈ 45 min)
scripts/services.sh start               # gateway :8001, MLflow :5050, Airflow :8080, web app :8000
```

Daily operation (what Airflow does): `aisleco_sim_day` 01:00 simulates yesterday; `aisleco_daily` 02:00 runs
`python -m pipeline run`, then `python -m ml score`, `monitor`, `forecast`; `aisleco_weekly_train` retrains on Sundays.

Command lines: `python -m sim …`, `python -m pipeline init|run|resume|status`, `python -m ml train|score|monitor|forecast|eda|text|rollback`.
Tests: `pytest tests -q` (the slow Chapter 6 failure tests use their own `*_e2e*` databases).
Demo logins: may, ken, nok (Sales), som (Operations), ann (Executive), dev (Data); password `aisleco2026`.

v1 (Streamlit, notebooks, Colab) is kept in `archive/v1/`.
