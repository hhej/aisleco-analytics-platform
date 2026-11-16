# Spec 05. Dashboards (custom web app)

Part of the [v2 plan](PLAN.md). Answers: should staff utilization and "who to contact" be one dashboard or two, who uses each
page to decide what, what every tile shows and from which mart view, and the design rules that keep it clear.

## 1. Combine or separate? Separate pages per role, deliberately linked

| Question | Staff utilization (Operations) | Who to contact (Sales) |
|---|---|---|
| Who looks | operations manager, lead planners | each salesperson; the sales manager |
| How often | weekly meeting, plus when a booking is confirmed | every morning and during the day |
| Decision | move assignments, book freelancers, stop selling a date | which couple to contact next and what to say |
| Unit on screen | person × week | one couple |
| Time frame | past 4 weeks to 26 weeks ahead | today |
| Personal data | staff nicknames only | the couple's name (restricted, Spec 03 §7) |
| Interaction | read and drill down | act: mark contacted, log the outcome |

Different people, rhythm, unit and permissions: one combined screen would serve neither. A worklist must stay focused (one
job, one ordered list); a capacity view needs the whole grid. They are therefore **separate pages behind one login**, each
role landing on its own.

They are **linked where the two problems meet** (Rev10: "a couple who asks for a fully booked peak date is a lost sale caused
by capacity"):
1. Each lead on the Sales list carries a **date badge** for the requested wedding date (Room / Tight / Full), from the
   Operations data. A "Full" date triggers the next best action "offer these alternative dates".
2. The Operations forecast includes the **expected hours of open leads** from the Sales pipeline (Model A × hours).
3. The **date checker** (one date in, availability and alternatives out) is shared by both roles.
4. The **Executive** page combines both problems at summary level for the owner.

## 2. Roles, logins, pages

| Role (DB role) | Demo user | Lands on | Can open |
|---|---|---|---|
| Sales (`aisleco_sales`) | `may`, `ken` (salespeople), `nok` (sales manager: sees the whole team) | Today | Today, Sales performance, Date checker |
| Operations (`aisleco_ops`) | `som` (operations manager) | Capacity planner | Capacity planner, Date checker |
| Executive (`aisleco_exec`) | `ann` (owner) | Business overview | Business overview, Sales performance (no names), Capacity planner (read-only) |
| Data (`aisleco_data`) | `dev` | Data & ML health | everything, plus the demo panel |

Passwords are bcrypt hashes in `app.user`; sessions are signed cookies (8 hours). Every API call checks the role, and the
database role behind the connection enforces the same limits (Spec 03 §7).

## 3. Pages

Wireframes are layout intent, not pixel specifications. Every number has a definition tooltip from `catalog/kpis.yml` and the
page header shows when the data was refreshed ("Data as of 02:14 today").

### 3.1 Sales: "Today" (daily worklist)

```
┌ Today · Mon 30 Nov · May ───────────────────────────────── Data as of 02:14 ┐
│ [Follow-ups done 6/15]  [Bookings this month 3 · target 8]  [My median first reply 22 min · target 60] │
├────────────────────────────────────────────────────────────────────────────┤
│ NEW SINCE YESTERDAY (reply first)                                           │
│  • Fah & Golf · LINE · 2 h ago · no reply yet                     [Reply now]│
├────────────────────────────────────────────────────────────────────────────┤
│ YOUR LIST (15)                        sort: [Expected fee ▾] [Chance]       │
│ #1  Ploy & Ton   Fair · Full Planning · 500k–1M · wants Sat 16 Jan [Room]  │
│     ●●● High 41 %   ≈ ฿38,000                                               │
│     Why: Quote sent 2 days ago · Asked whether the date is free · Replied fast │
│     Next: Follow up on the quote                         [Mark contacted ▾] │
│ #2  Mint & Jay   TikTok · Full · 1M–1.5M · wants Sat 13 Feb [Full]         │
│     ●●○ Medium 18 %  ≈ ฿29,000                                              │
│     Next: Offer Sat 20 Feb (auspicious, room) or Sun 7 Feb                  │
│ …                                                                           │
├────────────────────────────────────────────────────────────────────────────┤
│ GOING COLD: silent 10+ days, auto-close soon (3)  ·  STANDARD QUEUE (holdout) │
└────────────────────────────────────────────────────────────────────────────┘
```

| Tile | Question it answers | Source view | Visual |
|---|---|---|---|
| Header KPIs | Am I on track today and this month? | `mart_sales.v_my_week` | 3 stat tiles with target |
| New since yesterday | Who has not had a first reply? | `v_new_today` | list, red dot if waiting > 1 h |
| My list | Whom do I contact now, why, and what do I say? | `v_today_list` + `sales_pii` | ranked cards: name, channel, package, budget, requested date with date badge, band and probability, expected fee, 3 reasons, next action, "Mark contacted" (call / LINE / meeting + outcome) |
| Going cold | Which good leads will the 30-day rule close soon? | `v_going_cold` | compact list with the auto-close date |
| Standard queue | The holdout leads (today's practice, newest first) | `v_today_list` (holdout) | compact list, no score |

No AUC, no feature names, no 0/1 columns. "How is this ranked?" opens a short plain explanation.
Works at tablet width (sales use tablets at fairs).

### 3.2 Sales: "Sales performance" (weekly review, sales manager and owner)

| Tile | Question | Source view | Visual |
|---|---|---|---|
| Funnel this month vs last | Where do couples drop out? | `v_funnel` | horizontal funnel: inquiries → replied → consulted → quoted → booked, with step rates |
| Conversion by channel | Which channels bring couples who book? | `v_conversion_by_channel_month` | bars with 95 % interval whiskers (small samples are visibly uncertain), inquiry volume as a second series |
| Reply speed | Does a fast reply matter, and when do couples write? | `v_response_by_hour` | conversion by reply band; heat strip of arrivals by hour × weekday with median reply time (the evening-shift question) |
| Why we lose couples | Price, date, silence? | `v_lost_reasons` | stacked bar by month, sales-closed vs auto-closed |
| Price fit | Do quotes take too big a share of the budget? | `v_quote_share` | distribution of quote share, booked vs lost on price, 16 % reference line |
| Follow-up compliance | Is the list being worked? | `v_followup_compliance` | per salesperson: listed vs contacted, 4 weeks |

### 3.3 Operations: "Capacity planner" (weekly meeting)

```
┌ Capacity planner · week of 30 Nov ──────────────────────────── Data as of 02:14 ┐
│ [Peak utilization next 4 wks 128 % (Beam, wk 8 Feb)] [Person-weeks over 100 %: 9] │
│ [Freelance hours needed, next 12 wks: 86 h] [Couples turned away, 90 days: 7]   │
├──────────────────────────────────────────────────────────────────────────────┤
│ STAFF × WEEK   (past 4 | next 12)                  role: [All ▾]              │
│           Nov 2  Nov 9 … Nov 30 Dec 7 … Feb 8 Feb 15                          │
│ Som (LP)   62    71   …  88     94   …  101   76                             │
│ Beam (Co)  40    55   …  97    112   …  128   60      cell = utilization %    │
│ …                                                     click → weddings & hours│
├──────────────────────────────────────────────────────────────────────────────┤
│ NEXT 26 WEEKS BY ROLE GROUP: booked ▇ + expected from leads ▆ + expected new ▅ │
│ vs capacity ─── , band P10–P90; status per week: Over / Tight / OK / Quiet     │
│ table: week · status · gap in hours · suggested action (book N freelance h)    │
├──────────────────────────────────────────────────────────────────────────────┤
│ DATE CHECKER [Sat 13 Feb 2027 ▾] → 2 weddings booked; Wedding-day crew −4 h;   │
│ auspicious High; alternatives with room: Sat 20 Feb (High), Sun 7 Feb (Medium)│
├──────────────────────────────────────────────────────────────────────────────┤
│ Peak-date acceptance by requested month · Turned away (date unavailable)       │
│ Staff hours per wedding by package / guest band / complexity                  │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Tile | Question | Source view | Visual |
|---|---|---|---|
| Header KPIs | How bad is the next month, and what will it cost? | `v_capacity_grid`, `v_forward_demand`, `v_turned_away` | 4 stat tiles |
| Staff × week grid | Who is overloaded, when? | `v_capacity_grid` | heatmap: neutral up to 85 %, amber 85–100 %, red above 100 %; the number printed in each cell (colour is never the only cue); concurrency over the limit marked with a dot |
| Week detail (drawer) | Why is this person's week full? | `v_week_detail` | list of weddings with planning and wedding-day hours, and who else is on them |
| Next 26 weeks | Will we have enough people, and when to book freelancers? | `v_forward_demand` | stacked columns (booked, expected from leads, expected new) against a capacity line, P10–P90 band; table with status and the gap in hours |
| Date checker | Can we take this date? If not, which dates instead? | `v_date_check` | form + result card |
| Peak acceptance, turned away | How much peak demand do we lose to capacity? | `v_peak_acceptance`, `v_turned_away` | bars by requested month, auspicious level as colour |
| Hours per wedding | What does a wedding really cost in staff time? | `v_hours_per_wedding` | small table: package × guest band × complexity |

### 3.4 Executive: "Business overview" (monthly)

| Tile | Question | Source view | Visual |
|---|---|---|---|
| KPI strip with targets and vs last year | Are we growing, converting and coping? | `mart_exec.v_kpi_tiles` | 6 tiles: bookings, fee revenue, conversion, median first reply, ops utilization last quarter, couples turned away; each with a sparkline and the change |
| Bookings and revenue by month | Seasonality and growth | `v_bookings_month` | columns this year vs last year (line) |
| Conversion trend and channel mix | Is conversion falling, and why? | `v_conversion_trend`, `v_channel_mix` | line by month; stacked share of inquiries by channel (the 2026 TikTok shift is visible) |
| Capacity outlook, 6 months | Do we need permanent hires? | `v_capacity_outlook` | per month and role group: Over / Tight / OK / Quiet with the gap in hours |
| Lost reasons | Where does revenue leak? | `v_lost_reasons` | bars with fee at stake (estimate) |
| Model value | Is the call list paying off? | `v_model_value` | A/B effect with interval and the offline estimate, each labelled with its status |
| What changed this month | Summary | computed in the API from the tiles | 3–5 plain sentences generated by rules, e.g. "Conversion fell from 8.1 % to 6.2 %; TikTok rose to 33 % of inquiries and converts at 3 %." |

### 3.5 Data & ML health (data team)

Pipeline runs (status, duration per step, rows in/out/rejected), freshness of each source, DQ results and quarantine (with a
"show record" view), alerts; for each model: champion version, test metrics with intervals, matured-label performance over
time, calibration plot, drift (PSI) per feature, registry history with links to MLflow; A/B detail. This is where the data
science content lives, out of the business pages.

### 3.6 Demo panel (data role only)

For the live demo: "Send a LINE message as a new couple" (form → signed POST to the gateway → appears in Bronze within a second),
"Run the pipeline now" (triggers the Airflow DAG through its REST API, shows step progress), "Advance the simulation one day".

## 4. Design rules

Sources: Stephen Few (information dashboard design), Shneiderman's "overview first, zoom and filter, details on demand",
accessibility (WCAG 2.2 AA contrast), and the course rubric ("aligned, insightful, clear design").

1. **One page, one job.** Each page answers at most 5 questions, listed in this spec. Anything else goes to another page.
2. **Decision first.** The first screen shows what to do (list, red weeks, actions); detail is one click away.
3. **Every number defined.** Tooltip with the formula from the catalog; units always shown (฿, h, %, days).
4. **Comparison built in.** KPIs show a target or the previous period; rates from small samples show an interval or the count.
5. **Colour means status only.** Neutral greys and one accent (wine, from the v1 wedding theme) for data; red = over capacity or
   overdue; amber = tight; green is not used for "good" (colour-blind safety); numbers always printed, never colour alone.
6. **Freshness visible.** "Data as of" in every header; a stale source turns it amber.
7. **Fast.** Under 1 second per page on local data: marts are views over small facts; the API caches per run.
8. **Readable by staff.** Plain labels ("Chance to book", not "p_hat"), nicknames, Thai-friendly fonts (Inter + Noto Sans Thai).
9. **Responsive.** Sales pages work at tablet width; Operations and Executive are designed for a laptop or a projector.

## 5. Build

| Part | Choice |
|---|---|
| API | FastAPI, one router per page; psycopg pool per database role; JSON responses typed with pydantic |
| Front end | React 18, Vite, TypeScript, Tailwind CSS, Recharts (bars, lines, areas), CSS grid for the heatmap, lucide icons |
| Serving | `npm run build` → static files served by FastAPI at `http://localhost:8000` (one process, one URL) |
| Write-back | `POST /api/sales/activity` → CRM `SalesActivity` through the insert-only role `crm_app_writer` |
| Tests | API role tests (each role refused elsewhere), a contract test per view, screenshots of every page at 1440 px and 820 px |

## 6. Acceptance

1. Each page answers the questions in its table with real data from the marts.
2. A Sales login cannot reach Operations or Executive data through the UI, the API or the database.
3. "Mark contacted" writes to the CRM and shows at once; after the next run it appears in `FactSalesActivity`.
4. Every tile number matches the KPI catalog's formula (contract tests against the marts).
5. Screenshots reviewed in Chrome; no horizontal scroll at 820 px on Sales pages.
