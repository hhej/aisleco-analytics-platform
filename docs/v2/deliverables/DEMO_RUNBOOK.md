# Demo runbook: Aisle & Co. final presentation

- **When:** Sunday 29 November 2026, 25-minute slot: talk 18:00, then about 7 minutes of questions.
- **Presenters:** five; the live demo (5:00) is run by **you (pollot)**.
- **Deck:** `docs/v2/deliverables/AisleCo_Final_Presentation.pptx`, 19 main slides plus backup slides A1–A7. The speaker notes
  hold each person's Thai script.
- **Who says what:** `docs/v2/deliverables/PRESENTER_GUIDE.md` has the word-for-word scripts, the hand-off lines and the
  Q&A bank.
- **Fallbacks:** backup slides A1 show every demo step as a screenshot, and `docs/v2/img/` has the same images.

## 1. Talk timing

| Part | Slides | Presenter | Length | Clock |
|---|---|---|---|---|
| Business problem, stakeholders and success metrics | 1–5 | Member 2 | 3:30 | 0:00 |
| The whole system, sources, CRM database | 6–8 | Member 3 | 2:30 | 3:30 |
| Lake, warehouse, nightly pipeline and its tests | 9–11 | Member 4 | 2:30 | 6:00 |
| Dashboards, lead model, validation, forecast and MLOps | 12–15 | Member 5 | 3:10 | 8:30 |
| **Live demo** | 16 | **You** | **5:00** | 11:40 |
| Recommendations, limits, three decisions | 17–19 | Member 2 | 1:20 | 16:40 |
| **Total** | | | **18:00** | about 7 min left for questions |

The numbers on the slides are frozen "as of 25 Nov 2026" so that everyone learns one set. The demo shows that
morning's live data, so a few numbers will differ slightly. Say so if asked: "the slides are from 25 November; this is
today's data."

## 2. The night before and the morning

**Night of 28 Nov:**
- Leave the Mac plugged in and awake, so Airflow runs the simulated day at 01:00 and the nightly pipeline at 02:00:
  `caffeinate -dims &` (stop it after the talk).
- If the Mac sleeps, nothing breaks; follow the "Mac slept" steps below in the morning.
- Rehearsal couples stay in the data, like real ones. A messy rehearsal couple keeps rule R05 at "1 failed (warning)" on
  the Data health page, and runs still succeed. That is honest; if asked, say it is a test couple whose budget nobody has
  corrected in the CRM yet.

**Morning, at least 60 minutes before.** All commands from the repository root:

```bash
cd ~/Documents/wedding-organizer
caffeinate -dims &                      # keep the Mac awake until the talk is over (kill it afterwards)
brew services start postgresql@16
pg_isready                              # expect: accepting connections
scripts/services.sh start               # gateway :8001, MLflow :5050, Airflow :8080, web app :8000
scripts/services.sh status              # expect five lines "up"
.venv/bin/python -m pipeline status -n 3   # the newest run must be "as of <today>" and "success"
```

**If the Mac was awake at 01:00 and 02:00:** Airflow has already run the night (`aisleco_sim_day`, then
`aisleco_daily`), and the status shows a run as of today. Check the grid at
http://localhost:8080/dags/aisleco_daily/grid: the last run is green. Then skip to section 3.

**Only if** the Sales list or Data health looks short of couples (CRM and warehouse out of step), resync in about 15 s:
`.venv/bin/python -m pipeline run --asof $(date +%F) --full-refresh && .venv/bin/python -m ml daily --asof $(date +%F)`.

**If the Mac slept (no run as of today):**
- **Check first:** in Airflow, make sure no `aisleco_sim_day` or `aisleco_daily` run is already in progress. When the
  Mac wakes, Airflow starts the most recent missed run by itself; wait for it instead of starting a second copy.
- **Note:** a caught-up run for an older date can be stopped by the quality gate: rule R02 rejects inquiries dated after
  the run date, and S01 blocks the run. That is the gate working; the run for today is the one that matters.
- **Then run the nightly cycle by hand:**

```bash
TODAY=$(date +%F)
.venv/bin/python -m sim backfill                       # simulates every missing day up to yesterday (≈ 1–2 s a day)
.venv/bin/python -m pipeline run --asof $TODAY         # sources → raw → trusted → star schema → marts (≈ 25 s)
.venv/bin/python -m ml daily --asof $TODAY             # scores today's open couples, then drift monitoring
.venv/bin/python -m ml forecast --asof $TODAY          # 26-week staffing forecast
.venv/bin/python -m pipeline status -n 1               # success, as of today
```

Then open http://127.0.0.1:8000 as `dev`: the header reads "Data as of <today>". Do not run `ml train` and do not
rebuild anything on the day.

## 3. Browser set-up (15 minutes before)

- **One login per browser window,** because the session is a cookie.
- **Sign-ins expire after 8 hours:** sign in to every window on the morning itself, never the evening before. An
  expired window shows "not logged in" and must be signed in again.
- **Password** for every demo user: `aisleco2026`.

| Window | URL | Log in as | Leave it on |
|---|---|---|---|
| Chrome, window 1 | http://127.0.0.1:8000 | `dev` (Data team) | Demo panel (sidebar) |
| Chrome, window 1, tab 2 | click "Data catalog" in the sidebar (opens /catalog in a new tab) | (same session) | KPIs (42) section |
| Chrome, window 1, tab 3 | http://localhost:8080/dags/aisleco_daily/grid | Airflow `admin` (password in `airflow/standalone_admin_password.txt`) | Grid view (only if someone asks to see the scheduler; the demo follows the run on the Demo panel). **Run now** appears here as a *manual* run |
| Chrome, window 1, tab 4 | http://127.0.0.1:5050/#/models/lead_conversion | none | Versions list; close the "MLflow Assistant" side panel |
| Chrome Incognito window | http://127.0.0.1:8000 | `nok` (sales manager: sees both salespeople's lists) | Today |
| Safari window | http://127.0.0.1:8000 | `som` (Operations) | Capacity planner |
| Safari private window | http://127.0.0.1:8000 | `ann` (Owner) | Business overview |

- **Sales user:** use `nok`, not `may` or `ken`, for the Sales steps. A new couple is assigned to one salesperson at
  random, and only the manager sees both lists.
- **Projector:** 1440 px wide or more, browser zoom 100–110 %, Focus mode on (no notifications). Wi-Fi is optional,
  because the holiday calendar is cached.
- **Fallback:** keep the deck open in presenter view. Backup slides A1 (1 of 2, 2 of 2) are the demo as screenshots.

## 4. The demo, minute by minute (you, 5:00, clock 11:40–16:40)

Opening line (Thai script in the notes of slide 16): "We will follow one couple from their first LINE message to the
decision each role makes."

### Step 1. A couple writes on LINE (0:45, clock 11:40–12:25)

The Demo panel is now **Follow one couple**: a flow of nine stops (LINE chat → landing file → raw zone → trusted zone →
star schema → marts → lead score, with the CRM as the second lane). Each stop lights up when the couple is there, and a
click shows the couple's own record at that stop.

| | |
|---|---|
| Click | Chrome window 1 (`dev`) → **Demo panel**. The form already holds Full Planning, 13/02/2027, 300 guests, budget "6 แสน", and the Thai message built from them → **Send on LINE**. |
| Audience sees | **LINE chat**, **Landing file** and **CRM database** turn wine with a tick. Click **Landing file**: the raw webhook JSON exactly as LINE sends it, and its file path in the lake. |
| Say | "This is exactly what LINE sends: a signed webhook. The receiver checked the signature and stored the body untouched. At the same time the CRM recorded the inquiry: the package, the date, and the budget as she typed it." |
| Point at | **Raw zone: chat** is amber, "Waits for the hour to close (hh:00)". Say: "Chat files are hourly batches; this one moves at the top of the hour. The CRM copy of the inquiry does not wait." |
| Optional, if ahead of time | Budget → "ยังไม่แน่ใจ" → **Send on LINE** again: a second, messy couple appears as a chip "#… messy". Both ride the same run in step 2. |
| Fallback | Backup A1 (1 of 2), picture 1. |

### Step 2. The nightly run, now, followed on screen (1:00, clock 12:25–13:25)

| | |
|---|---|
| Click | **Run now** (right card). Stay on this page; there is no need to switch to Airflow. The run number and seconds count up, each stop pulses while its step runs, the arrow into it moves, and the page selects each stop as the couple arrives (about 20–30 s). It starts the same Airflow DAG (`aisleco_daily`) as the nightly run; its model steps (score, monitor, forecast) finish in the background about 40 s later. |
| Say while it runs | "Raw zone: only the rows that changed, copied as they are. Trusted zone: cleaned and checked by the quality rules. Star schema: keys and facts. Marts: one view per role. These are the same steps that run every night at two." |
| Then point at | The card **What each layer did to the data**: "6 แสน" typed → band 500k–1M → midpoint ฿750,000; ChannelID 1 → ChannelKey 1 · LINE OA; 2027-02-13 → DateKey 20270213; name and phone stay in the restricted zone. **Lead score** is amber: "Scored in tomorrow's 02:00 run". |
| Say | "She is in the warehouse and on the sales page. She gets a score in tomorrow morning's run: the model only uses data from before midnight, so it never sees the future." |
| If you sent the messy couple | Click its chip. **Trusted zone** is amber, "Kept, flagged R05". Say: "The budget text can't be read as a band, so it becomes Unknown. The rule keeps the row, flags it, and counts it on the Data health page." |
| Then | Chrome Incognito (`nok`) → **Today** → refresh. Under **New since yesterday** the couple appears: LINE OA, "No reply yet · wrote <today>". |
| Fallback | If the run fails or is still going after 60 s, click **Yesterday's, scored #…**. It is the same page for yesterday's couple, and every stop is green, including the chat file and the lead score. Say: "This is yesterday's couple, who already went through the nightly run." Then use backup A1 (1 of 2), pictures 1–2. |

### Step 3. Sales: whom to contact first (1:00, clock 13:25–14:25)

| | |
|---|---|
| Click | Same `nok` window, scroll to **Call lists**. Point at #1: channel, package, budget, requested date with its badge (Room / Tight / Full), band and percentage, "≈ ฿…k", **Why:** three reasons, **Next:** the action. Then **Mark contacted** on #1 → **Call**, outcome **Reached** → **Save**. The card shows "Contacted hh:mm · Call · Reached". |
| Say | "Ranked by expected fee: chance to book times the fee. Reasons in plain words and a next action. The tick is written back to the CRM through a database role that can only add follow-ups." |
| Fallback | Backup A1 (1 of 2), pictures 3–4. |

(Names, percentages and amounts on the list change every night.)

### Step 4. Operations: who is overloaded, and can we take the date (1:00, clock 14:25–15:25)

| | |
|---|---|
| Click | Safari (`som`) → **Capacity planner**. Point at the four tiles. In **Staff × week**, click a red cell (a week above 150 %): the drawer lists that week's weddings and hours. Close it (×). Scroll to **Date checker**: 13/02/2027 → **Check date**. |
| Audience sees | Sat 13 Feb 2027: auspicious High, high season, weddings that day and week, headroom in hours, a status chip, and **Alternatives with room, within 4 weeks**. |
| Say | "This is the couple who just wrote. Before sales answer, they check the date: here are auspicious dates we can still staff. A full date becomes an offer, not a lost couple." |
| Fallback | Backup A1 (2 of 2), pictures 1–2. |

### Step 5. Owner: what changed (0:30, clock 15:25–15:55)

| | |
|---|---|
| Click | Safari private (`ann`) → **Business overview**. Read one sentence of **What changed**, point at one tile's "vs last month / vs last year". |
| Say | "The owner reads sentences, not charts; every baht figure is labelled as an estimate." |
| Fallback | Backup A1 (2 of 2), picture 3. |

### Step 6. Proof behind the numbers (0:45, clock 15:55–16:40)

| | |
|---|---|
| Click 1 | Catalog tab → **KPIs (42)** → **Conversion** → **Show lineage to the source**: dashboard tile → mart view → star schema → trusted zone → raw zone → CRM column. |
| Click 2 | MLflow tab → `lead_conversion` versions: v6 carries **@champion**, v4 **@retired** with its `retired_reason` tag, v7 is kept with its rejection. |
| Say | "Every number traces back to a source column. Every model version is kept with the reason it was used or rejected. And the run you just watched is the one that runs every night at two." |
| Fallback | Backup A1 (2 of 2), pictures 4–6. |

Hand back to Member 2: "Back to what we recommend."

## 5. If something breaks

| Symptom | Do this (keep talking, never debug on stage) |
|---|---|
| Send shows "the chat gateway is not running" | Show backup A1; after the talk: `scripts/services.sh start gateway`. |
| Run now and Airflow is down | The page falls back to the command line by itself (log `var/log/demo_run.log`); the stops still light up. |
| A stop says "Updating…" during the run | The trusted zone swaps its files at the end of its step; it clears on the next refresh (2 s). |
| A page shows an error or spins | Refresh once. If it persists, show backup A1. After the talk: `scripts/services.sh stop web && scripts/services.sh start web`. |
| A window says "not logged in" | The 8-hour sign-in expired: Sign out → sign in again (`aisleco2026`). |
| The run fails or the couple does not reach Marts | Click **Yesterday's, scored #…** on the Demo panel and keep talking (step 2 fallback); check Airflow after the talk. |
| Projector too narrow (horizontal scroll) | Browser zoom 90 %. |

**Q&A, error handling: "Has it ever failed for real?"** Yes:
- On 17 Nov a typo in one SQL file broke a count.
- Reconciliation rule C01 stopped runs 9–11 before anything was published, so the dashboards kept the previous correct
  data and alerts were recorded.
- After the fix, run 12 succeeded.

**And on 24 Nov:** a run that Airflow caught up for 21 Nov after the Mac slept was stopped by the quality gate (R02 + S01),
as designed; the next run loaded those rows.

## 6. Pre-flight checklist

- [ ] Mac on power, `caffeinate` running, Focus mode on, other apps closed.
- [ ] `scripts/services.sh status`: postgres, gateway, mlflow, airflow, web all "up".
- [ ] `pipeline status`: newest run as of today, success; app header "Data as of <today>".
- [ ] `nok` Today shows call lists with scores.
- [ ] Capacity planner loads; date checker returns a card for 13/02/2027.
- [ ] Airflow grid open and logged in; MLflow registry open; catalog tab open on KPIs.
- [ ] All windows signed in this morning (8-hour limit).
- [ ] Deck open in Keynote or PowerPoint, presenter view on, notes visible; backup A1 one click away.
- [ ] A screen recording of a rehearsal demo saved on the desktop (second fallback).
- [ ] All five presenters have rehearsed the hand-offs with a stopwatch (target 18:00).

## 7. Changing the numbers or names on the slides

- **Freezing the numbers:** the slide numbers are frozen in `docs/v2/deliverables/deck/numbers.json`. To take a new set
  (this changes what presenters say, so do it only before rehearsals):

```bash
.venv/bin/python scripts/deck_numbers.py                     # reads the marts, redraws the charts
cd docs/v2/deliverables/deck && npm install && node build.js # rebuilds the deck and PRESENTER_GUIDE.md
```

- **Names:** change them in `docs/v2/deliverables/deck/content.js` (`members`), then run `node build.js`.
