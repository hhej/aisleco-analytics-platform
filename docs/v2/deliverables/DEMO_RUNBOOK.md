# Demo runbook: Aisle & Co. final presentation

Sunday 29 November 2026, 25-minute slot, talk 18:00 minutes, live demo 7:00 by Presenter B.
Deck: `docs/v2/deliverables/AisleCo_Final_Presentation.pptx` (19 slides; speaker notes name the presenter and timing).
⟦TBD⟧ Lead-model numbers, the Today-list example and the champion version are v4 placeholders until the v6 retrain; refresh them, plus forecast/capacity numbers if they changed, after "v6 ready".
Every click path below was checked against the running system on 17 Nov 2026. Fallback screenshots are in `docs/v2/img/`.

## 1. Talk timing

| # | Slide | Presenter | Length | Clock |
|---|---|---|---|---|
| 1 | Every couple answered. Every date staffed. | A | 0:30 | 0:00 |
| 2 | Two leaks put about ฿4.4M of fees at stake each year | A | 0:45 | 0:30 |
| 3 | Operations staff ran at 132 % of capacity in Oct–Nov 2025, 35 % in Lent | A | 0:50 | 1:15 |
| 4 | About 1,500 couples write each year and 7–9 % book | A | 0:50 | 2:05 |
| 5 | One platform on one MacBook (architecture) | B | 0:45 | 2:55 |
| 6 | Each source is ingested the way its real system allows | B | 0:35 | 3:40 |
| 7 | Raw stays raw … each role reads only its own mart | B | 0:35 | 4:15 |
| 8 | The nightly pipeline is proven | B | 0:35 | 4:50 |
| 9 | Sales and Operations get separate pages, linked | B | 0:30 | 5:25 |
| 10 | The lead model answers one decision | A | 0:35 | 5:55 |
| 11 | Top 15 holds ⟦TBD 73 %⟧ of bookings vs ⟦TBD 48 %⟧ | A | 0:40 | 6:30 |
| 12 | Gate history v1–v6 (v4 retired: data bug) ⟦TBD⟧ | A | 0:30 | 7:10 |
| 13 | Each data source earns its place; TF-IDF dropped | A | 0:25 | 7:40 |
| 14 | Staffing forecast: 17 over-capacity role-weeks, 644 h | A | 0:40 | 8:05 |
| 15 | The models run themselves (MLOps) | B | 0:35 | 8:45 |
| 16 | Four actions for the owner | A | 0:50 | 9:20 |
| 17 | **Live demo** | **B** | **7:00** | 10:10 |
| 18 | Limits and roadmap | A | 0:30 | 17:10 |
| 19 | Three decisions; questions | A | 0:20 | 17:40 |
| | **Total** | | **18:00** | 7 min left for questions |

Presenter A speaks 7:25, Presenter B 10:35. Hand-offs: after slide 4 (A → B), after slide 9 (B → A), slide 15 (A → B → A), after slide 16 (A → B for the demo), after the demo (B → A).

Numbers on slides 14 and 16 are the forecast of 17 Nov 2026. The forecast refreshes every night: on 29 Nov the Capacity planner will show different hours. Say "the forecast we ran on 17 November said …", or update the two slides from `mart_ops.v_forward_demand` the evening before (query in section 7).

## 2. Morning preparation (presentation day, at least 60 minutes before)

All commands from the repository root:

```bash
cd ~/Documents/wedding-organizer
caffeinate -dims &                      # keep the Mac awake until the talk is over (kill it afterwards)
brew services start postgresql@16
pg_isready                              # expect: accepting connections
scripts/services.sh start               # gateway :8001, MLflow :5050, Airflow :8080, web app :8000
scripts/services.sh status              # expect five lines "up"
.venv/bin/python -m pipeline status -n 3   # the newest run must be "as of <today>" and "success"
```

If the Mac was awake at 01:00 and 02:00, Airflow has already run the night (`aisleco_sim_day`, then `aisleco_daily`); the status shows a run as of today. Check the grid at http://localhost:8080/dags/aisleco_daily/grid: the last run is green. Then skip to section 3.

If the Mac slept (no run as of today), run the nightly cycle by hand. First check in Airflow that no `aisleco_sim_day` or `aisleco_daily` run is in progress (Airflow starts the most recent missed run when it wakes up; wait for it instead of running a second copy).

```bash
TODAY=$(date +%F)
.venv/bin/python -m sim backfill                       # simulates every missing day up to yesterday (≈ 1–2 s a day)
.venv/bin/python -m pipeline run --asof $TODAY         # sources → Bronze → Silver → Gold → marts (≈ 20 s)
.venv/bin/python -m ml daily --asof $TODAY             # scores today's open couples, then drift monitoring
.venv/bin/python -m ml forecast --asof $TODAY          # 26-week staffing forecast
.venv/bin/python -m pipeline status -n 1               # success, as of today
```

Then open http://127.0.0.1:8000 as `dev`: the header reads "Data as of <today>". Do not run `ml train` and do not rebuild anything on the day.

## 3. Browser set-up (15 minutes before)

One login per browser window (the session is a cookie). Password for every demo user: `aisleco2026`.

| Window | URL | Log in as | Leave it on |
|---|---|---|---|
| Chrome, window 1 | http://127.0.0.1:8000 | `dev` (Data team) | Demo panel (sidebar) |
| Chrome, window 1, tab 2 | click "Data catalog" in the sidebar (opens /catalog in a new tab) | (same session) | KPIs (42) section |
| Chrome, window 1, tab 3 | http://localhost:8080/dags/aisleco_daily/grid | Airflow `admin` (password in `airflow/standalone_admin_password.txt`) | Grid view |
| Chrome, window 1, tab 4 | http://127.0.0.1:5050/#/models/lead_conversion | none | Versions list; close the "MLflow Assistant" side panel |
| Chrome Incognito window | http://127.0.0.1:8000 | `nok` (sales manager: sees both salespeople's lists) | Today |
| Safari window | http://127.0.0.1:8000 | `som` (Operations) | Capacity planner |
| Safari private window | http://127.0.0.1:8000 | `ann` (Owner) | Business overview |
| Terminal | repository root, font 18 pt | | the command of step 1b typed, not run |

Use `nok`, not `may` or `ken`, for the Sales steps: a new couple is assigned to one salesperson at random, and only the manager sees both lists.

Projector: 1440 px wide or more, browser zoom 100–110 %, Focus mode on (no notifications), Wi-Fi optional (the holiday calendar is cached).

Fallback deck: open `docs/v2/img/` in Finder (Quick Look with the space bar) before the talk, sorted by name.

## 4. The demo, minute by minute (Presenter B, 7:00)

### Step 1. A couple writes on LINE (1:30, clock 10:10–11:40)

| | |
|---|---|
| Click | Chrome window 1 (`dev`) → **Demo panel** → the message box already holds a Thai LINE message asking about Full Planning on 13 Feb 2027 for 300 guests → **Send**. |
| Audience sees | "Accepted by the gateway at hh:mm:ss" and "Landed in: `lake/landing/chat/platform=line/received_date=<today>/hour=<HH>/events.jsonl`". |
| 1b. Show the file | Terminal: `tail -1 "lake/landing/chat/platform=line/received_date=$(date +%F)/hour=$(date +%H)/events.jsonl" \| python3 -c "import json,sys; r=json.loads(sys.stdin.read()); print(r['received_at']); print(json.dumps(json.loads(r['body']), ensure_ascii=False, indent=1))"` → the raw LINE webhook body (destination, events, webhookEventId, message.text). |
| Say | "This is exactly what LINE sends us: a signed webhook. The gateway checked the signature, stored the raw body untouched, and the CRM's chat module created the couple and the inquiry." |
| Fallback | `docs/v2/img/app_demo_panel.jpg`; for the file, Spec 01 §3.1 shows the same payload format. |

Note for questions: the hourly file stays in landing until its hour is over, then the next run moves it to Bronze. The couple reaches the warehouse through the CRM change capture in this run.

### Step 2. Run the pipeline now (1:30, clock 11:40–13:10)

| | |
|---|---|
| Click | Demo panel → **Run now** (right card). The card says "Started via Airflow (manual__…)". Switch to the Airflow tab, refresh the grid: the new run fills task by task (≈ 20 s for the pipeline, ≈ 65 s with the model tasks). |
| Say while it runs | "Extract, then Silver with the data-quality rules, then the star schema, then the marts. Each square is a step with retries and a checkpoint; if one fails, the run resumes from there." |
| Then | Chrome Incognito (`nok`) → **Today** → refresh. Under **New since yesterday** the new couple appears: LINE OA, "No reply yet · wrote <today>", with the owner's name. |
| Say | "She is in the warehouse and on the sales page. She will get a chance-to-book score in tomorrow morning's run: features use only data from before midnight, so the model never sees the future." |
| Fallback | Airflow: `airflow_daily.jpg`. Sales page: `app_today.jpg` (shows the "New since yesterday" block). If the run is still going after 75 s, move on to step 3 and come back to New since yesterday at the end of step 3. |

### Step 3. Sales: whom to call first (1:15, clock 13:10–14:25)

| | |
|---|---|
| Click | Same `nok` window, scroll to **Call lists**. Point at #1: name, channel, package, budget, requested date with its badge (Room / Tight / Full), "High ⟦TBD⟧ %", "≈ ฿⟦TBD⟧k", **Why:** three reasons, **Next:** the action. Click **How is this ranked?** and read one sentence. Then **Mark contacted** on #1 → choose **Call**, outcome **Reached** → **Save**. The card shows "Contacted hh:mm · Call · Reached". |
| Say | "Ranked by expected fee: chance to book times the fee. No model jargon: reasons in plain words and a next action. The tick is written back to the CRM, the system of record, through a database role that can only insert follow-ups; tonight it flows into the warehouse." |
| Fallback | `app_today.jpg` (list) and `app_today_may_mark.jpg` (the Mark contacted form). |

(Exact names, percentages and amounts on the list change every night.)

### Step 4. Operations: who is overloaded, and can we take the date (1:15, clock 14:25–15:40)

| | |
|---|---|
| Click | Safari (`som`) → **Capacity planner**. Point at the four tiles (peak utilization, person-weeks over 100 %, freelance hours needed next 12 weeks, couples turned away). In **Staff × week**, click a red cell (e.g. Som, a week above 150 %) → the drawer lists the weddings in that week, planning and wedding-day hours, and who else is on them. Close the drawer (×). Scroll to **Date checker**: the date field holds 13/02/2027 → **Check date**. |
| Audience sees | Sat 13 Feb 2027: auspicious High, high season, weddings that day and that week, planning and wedding-day headroom in hours, status chip, and **Alternatives with room, within 4 weeks**. |
| Say | "This is the couple who just wrote. Before sales answer, they check the date: here is the headroom, and here are auspicious dates we can still staff. A full date becomes an offer, not a lost couple." |
| Fallback | `app_capacity.jpg`, `app_capacity_drawer.jpg`, `app_date_checker.jpg`. |

### Step 5. Executive: what changed (0:30, clock 15:40–16:10)

| | |
|---|---|
| Click | Safari private (`ann`) → **Business overview**. Read the **What changed** box (five plain sentences), point at one tile's "vs last month / vs last year". |
| Say | "The owner reads sentences, not charts: bookings, conversion, the TikTok shift, staff load and couples turned away, each with the fee at stake labelled as an estimate." |
| Fallback | `app_overview.jpg`. |

### Step 6. Data: proof behind the numbers (1:00, clock 16:10–17:10)

| | |
|---|---|
| Click 1 | Catalog tab → **KPIs (42)** → card **Conversion** → **Show lineage to the source**. The drawer walks the number: dashboard tile → `mart_exec.v_monthly_kpis.conversion` → `mart_sales.v_inquiry.IsBooked` → `gold.FactInquiry.IsBooked` → Silver → Bronze → CRM column. |
| Click 2 | MLflow tab → `lead_conversion` versions: ⟦TBD: v6 after the retrain (v4 retired)⟧ carries the alias **@champion**; the others are kept with their gate decision. |
| Click 3 | Airflow tab → the run started in step 2 is green. |
| Say | "Every number on every page traces back to a source column. Every model version is kept with the reason it was promoted or rejected. And the pipeline you watched is the one that runs every night at two." |
| Fallback | `catalog_lineage.jpg`, `mlflow_registry.jpg`, `airflow_daily.jpg`, `app_health.jpg`. |

Hand back to Presenter A: "Back to the limits and what comes next."

## 5. If something breaks

| Symptom | Do this (keep talking, never debug on stage) |
|---|---|
| Send shows "the chat gateway is not running" | Show `app_demo_panel.jpg`; after the talk: `scripts/services.sh start gateway`. |
| Run now shows "Started via the command line" | Airflow was down; the pipeline still runs (log `var/log/demo_run.log`). Skip the Airflow view, use `airflow_daily.jpg`. |
| A page shows an error or spins | Refresh once. If it persists: `app_*.jpg`. After the talk: `scripts/services.sh stop web && scripts/services.sh start web`. |
| Logged out | Log in again (`aisleco2026`). |
| New couple not under New since yesterday after the run | The run is not finished (Data & ML health shows the latest run) or it failed: show `app_today.jpg` and say the couple appears after the run; check Airflow after the talk. |
| Projector too narrow (horizontal scroll) | Browser zoom 90 %. |

## 6. Pre-flight checklist

- [ ] Mac on power, `caffeinate` running, Focus mode on, other apps closed.
- [ ] `scripts/services.sh status`: postgres, gateway, mlflow, airflow, web all "up".
- [ ] `pipeline status`: newest run as of today, success; app header "Data as of <today>".
- [ ] `nok` Today shows call lists with scores (scores exist for today).
- [ ] Capacity planner loads; date checker returns a card for 13/02/2027.
- [ ] Airflow grid open and logged in; MLflow registry open; catalog tab open on KPIs.
- [ ] Terminal at the repository root with the step-1b command typed.
- [ ] Deck opened in Keynote or PowerPoint, presenter view on, speaker notes visible; slides 14 and 16 numbers either updated or announced as "forecast of 17 November".
- [ ] Fallback folder `docs/v2/img/` open in Finder.
- [ ] Both presenters have rehearsed the hand-offs with a stopwatch (target 18:00).

## 7. Refreshing the forecast numbers on slides 14 and 16 (optional, evening before)

```bash
psql -d aisleco_dw -c "SELECT role_group, count(*) FILTER (WHERE status='Over') AS over_weeks, round(sum(gap_hours)) AS gap_hours,
  min(week_start) FILTER (WHERE status='Over') AS first_over, max(week_start) FILTER (WHERE status='Over') AS last_over
  FROM mart_ops.v_forward_demand WHERE forecast_date = (SELECT max(forecast_date) FROM mart_ops.v_forward_demand) GROUP BY 1"
```

On 17 Nov this returned Planning 8 weeks / 230 h and Wedding day 9 weeks / 414 h (644 h in total, 23 Nov – 25 Jan).
