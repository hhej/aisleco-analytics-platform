"""Builds AisleCo_ML_Conversion_Demo.ipynb (execute it with nbconvert, see README).
With --colab it builds AisleCo_ML_Conversion_Demo_Colab.ipynb: the same cells plus setup for Google Colab.
Every cell calls the real code in ml/ and etl/; the notebook re-implements nothing."""
import sys
from pathlib import Path

import nbformat as nbf

ROOT = Path(__file__).resolve().parent.parent
COLAB = "--colab" in sys.argv
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md("""
# Aisle & Co. conversion model: feature store and MLOps
MADT 8102 Capstone, Chapter 7: MLOps and Feature Stores

**Business problem (Problem 2, high inquiry and low conversion).** About 14% of inquiries become a booking.
Three in four lost inquiries are closed as "No reply": the couple went silent and the 30-day rule closed
the inquiry. Sales cannot follow up every open inquiry with the same effort. This model scores every open
inquiry each morning with its chance to book, so Sales calls the most promising couples first and reaches
the quiet ones before they drop out.

**What this notebook shows**, in the order of the data science steps of Chapter 7:

| Step (Chapter 7) | Here | Section |
|---|---|---|
| Data extraction | The daily ETL loads the CRM into the warehouse (Chapter 6 pipeline) | 1 |
| Feature store | `feature.inquiry_features`: one feature definition in SQL, point in time; offline store `feature.InquiryFeatures` | 2 |
| Data analysis | Booking rate against the features | 3 |
| Data preparation, training | Time-based split, two candidate models and a baseline | 4 |
| Evaluation, validation | AUC by inquiry age, top-20% capture; the gate against the baseline and the production model | 4 |
| Model registry, metadata | `ml.ModelVersion`: every training run, its data window, metrics, decision and the model itself | 4, 7 |
| Serving | Batch prediction each morning into `ml.InquiryScore`, read by the dashboard's follow-up list | 5 |
| Monitoring | Data drift (PSI) and live accuracy into `ml.DriftCheck`; alerts in `etl.Alert`; retraining | 6 |
| Automation | Airflow: daily `ml_features → ml_score → ml_monitor → ml_forecast` after the ETL, weekly `aisleco_weekly_train` | 7 |
| A second model, for Problem 1 | Demand forecast of the next 6 months: weddings and staff-hours against capacity | 8 |

**The data is simulated** (`AisleCo_MultiZone_Architecture_v3.ipynb`): three years of inquiries, 2024 to 2026,
where each couple's hidden readiness to book drives what the CRM records. The assumptions are listed in
that notebook. The model's accuracy therefore reflects those assumptions, not real couples. What the notebook
demonstrates is the pipeline: with the real CRM, the same code learns the real drivers.
""")

code("""
import sys, time, logging
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
T0 = time.perf_counter()

ROOT = Path.cwd()
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from etl import db, pipeline
from etl.config import Config
from etl.__main__ import init_dw
from load_snapshot import load
from ml import features as F, train as T, score as S, monitor as M, forecast as FC

logging.basicConfig(level=logging.ERROR)
pd.set_option("display.width", 170); pd.set_option("display.max_columns", 30); pd.set_option("display.max_colwidth", 90)
INK, INK2, EDGE, BLUE, ORANGE, RED, GREEN = "#0b0b0b", "#52514e", "#9a9893", "#2a78d6", "#eb6834", "#b03a2e", "#1a7f37"

# separate databases, so the ETL demo's aisleco_crm / aisleco_dw are not touched
cfg = replace(Config(), crm_dsn="dbname=aisleco_crm_ml", dw_dsn="dbname=aisleco_dw_ml", landing_dir=ROOT / "data" / "landing_ml")

def dw(sql, params=None):
    with db.connect(cfg.dw_dsn, cfg, autocommit=True) as c:
        return pd.DataFrame(c.execute(sql, params).fetchall())

def with_dw(fn, *args, **kw):
    with db.connect(cfg.dw_dsn, cfg) as c:
        out = fn(c, *args, **kw); c.commit()
        return out
""")

md("""
## 1. Data extraction: the warehouse the model reads

The simulated CRM (three years, as of 15 Jan 2027) goes through the Chapter 6 pipeline, as every morning in
production. The model reads only the warehouse: Silver (`stg`, the cleaned CRM rows with their timestamps)
for features, and nothing from the CRM directly.
""")

code("""
ZONES = ROOT / "data" / "sim" / "zones_v3"
rows = load(ZONES, 1, cfg)
init_dw(cfg, drop=True)
run = pipeline.run(cfg, date(2027, 1, 15), "full")
print(f"CRM rows loaded: {sum(rows.values()):,}; ETL run {run} succeeded")
dw('''SELECT extract(year FROM "InquiryDate")::int AS inquiry_year, count(*) AS inquiries,
             count(*) FILTER (WHERE "Status" = 'Converted') AS booked,
             count(*) FILTER (WHERE "LostReason" = 'No reply') AS no_reply,
             count(*) FILTER (WHERE "Status" NOT IN ('Converted','Lost')) AS still_open
      FROM stg."Inquiry" GROUP BY 1 ORDER BY 1''')
""")

md("""
## 2. Feature store

A feature store sits between the raw data and the models, so that training and prediction use the same
features (Chapter 7). Here it is three objects in the warehouse, in plain PostgreSQL (`sql/ml_feature_store.sql`):

* `feature.inquiry_features(ids, cutoffs)`: the only definition of the features. For each inquiry and
  cutoff time it uses only rows dated before the cutoff. Training calls it with past cutoffs, the daily
  scoring with today, so both get features computed the same way.
* `feature."InquiryFeatures"`: the offline store, one row per open inquiry per scoring day.
* `feature."InquiryLabel"`: the label, booked within 120 days of the inquiry (on it, or on a later inquiry by the
  same customer) or not, and when that became final. A fixed window, not the CRM status: a couple marked Lost by
  the 30-day no-reply rule who books on day 90 is a booking, and a label never changes once it exists.

The design of the slides, entity + time index + features:

| Index | Time index | Features |
|---|---|---|
| `InquiryID` | `AsOf` (the cutoff) | channel, budget, package type, wedding date given, lead days, peak or auspicious date, our reply time, couple messages (total and in the first 24 h), hours since the couple last wrote, consulted, quoted, quote share of budget, age in days |

**Point in time.** One booked inquiry, as the store sees it at different ages. The features grow as the couple
writes, gets a consultation and a quote. After the booking it is no longer open, so it is not returned.
""")

code(r"""
lab = dw('SELECT * FROM feature."InquiryLabel" WHERE "Booked" = 1 AND "ClosedAt" - "InquiryDate" > interval \'25 days\' ORDER BY "InquiryID" LIMIT 1').iloc[0]
start = pd.Timestamp(lab.InquiryDate).normalize()
cuts = [start + pd.Timedelta(days=k) for k in (1, 7, 14, 21)] + [pd.Timestamp(lab.ClosedAt) + pd.Timedelta(days=1)]
with db.connect(cfg.dw_dsn, cfg) as c:
    pit = F.compute(c, [int(lab.InquiryID)] * len(cuts), [x.to_pydatetime() for x in cuts])
print(f"Inquiry {lab.InquiryID}: received {lab.InquiryDate:%d %b %Y %H:%M}, booked {lab.ClosedAt:%d %b %Y}. "
      f"Asked at {len(cuts)} cutoffs, returned at {len(pit)} (the last cutoff is after the booking).")
pit.set_index("AsOf").T
""")

md("""
**Training examples.** Each labelled inquiry is taken as it looked 1, 7, 14 and 30 days after it arrived, while it
was still open, and joined with its later outcome. This point-in-time join is what keeps the outcome out of the
features: a feature can only use what had happened by that age.
""")

code("""
ASOF0 = date(2025, 10, 1)       # the first model is trained with what was known on this date
with db.connect(cfg.dw_dsn, cfg) as c:
    ts = F.training_set(c, ASOF0)
print(f"{len(ts):,} training rows from {ts.InquiryID.nunique():,} labelled inquiries (known on {ASOF0}); booked share {ts.Booked.mean():.1%}")
ts.groupby("AgeDays").agg(rows=("Booked", "size"), booked_share=("Booked", "mean")).round(3)
""")

md("""
## 3. Data analysis

The booking rate against some features, on the training rows. Channel and the couple's own behaviour (how many
messages, how long they have been silent) separate bookers from the rest; that is what the model can use.
""")

code(r"""
fig, ax = plt.subplots(1, 3, figsize=(16, 3.8))
d1 = ts[ts.AgeDays == 1]
r = d1.groupby("Channel").Booked.mean().sort_values()
ax[0].barh(r.index, r.values * 100, color=BLUE); ax[0].set_title("Booking rate by channel (day 1, %)", loc="left")
b = d1.assign(m=d1.CoupleMsgs24h.clip(upper=4)).groupby("m").Booked.mean()
ax[1].bar([str(int(i)) if i < 4 else "4+" for i in b.index], b.values * 100, color=BLUE)
ax[1].set_title("Booking rate by couple messages in the first 24 h (%)", loc="left")
d7 = ts[(ts.AgeDays == 7) & ts.HoursSilent.notna()]
s = d7.groupby(pd.cut(d7.HoursSilent, [0, 24, 72, 120, 1000], labels=["< 1 day", "1-3 days", "3-5 days", "> 5 days"]), observed=True).Booked.mean()
ax[2].bar(s.index.astype(str), s.values * 100, color=ORANGE); ax[2].set_title("Day 7: booking rate by time since the couple wrote (%)", loc="left")
for a in ax:
    a.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.show()
""")

md("""
## 4. Training, evaluation and validation

`ml.train.train(as_of)` runs the whole automated step:

1. **Split by time**, never at random: test = inquiries received 300 to 120 days before the as-of date
   (old enough for their 120-day label to be final), train = everything before.
2. **Two candidates**: logistic regression and gradient boosting, the same preprocessing for both (one-hot for
   categories; missing values imputed with a "was missing" flag, since a missing reply time or wedding date is
   itself informative).
3. **Two baselines**: each channel's past booking rate, and a salesperson's rule of thumb written as code
   (quoted > consulted > the couple wrote in the last 3 days). The rule answers "couldn't an experienced
   salesperson just do this?": the model is only worth running if it clearly beats the stronger of the two.
4. **Evaluation** on the test window: AUC (overall and by inquiry age), PR-AUC, and the share of all bookings
   found in the top 20% of scores (what Sales gets if it calls one couple in five).
5. **Validation gate**: the best candidate is promoted only if its AUC is at least 0.05 above the stronger baseline, and
   at least as good as the production model on at least 50 test inquiries that model never saw (counted once
   each; an inquiry has up to 4 test rows, one per scoring age). The production model learnt from every inquiry
   up to 120 days before its own as-of date, so after a weekly retrain only a week of new inquiries is unseen:
   production is kept until 50 new inquiries have outcomes, and then replaced only by a model that is not worse.
6. **Registry**: the result, promoted or not, is written to `ml."ModelVersion"` with its data windows, metrics,
   decision, the training distributions (for drift checks) and the model itself. The promoted model is refit on
   train + test.
""")

code(r"""
r0 = with_dw(T.train, ASOF0)
print(f"model v{r0['version']} ({r0['algorithm']}): {r0['status']} - {r0['decision']}")
m = pd.DataFrame({**r0["candidates"], **{f"baseline: {k}": v for k, v in r0["baselines"].items()}}).T
m = m.join(pd.DataFrame(m.pop("auc_by_day").tolist(), index=m.index).add_prefix("AUC day "))
m
""")

code(r"""
fig, ax = plt.subplots(figsize=(9, 3.6))
days = list(r0["test"]["auc_by_day"])
curves = [*r0["candidates"].items(), *((f"baseline: {k}", v) for k, v in r0["baselines"].items())]
for (name, res), color in zip(curves, [BLUE, ORANGE, EDGE, GREEN]):
    ax.plot(days, [res["auc_by_day"][d] for d in days], marker="o", color=color, lw=2, label=name)
ax.axhline(0.5, color=INK2, ls=":", lw=1); ax.text(0, 0.51, "random", fontsize=8, color=INK2)
ax.set_xlabel("inquiry age when scored (days)"); ax.set_ylabel("AUC on the test window"); ax.set_ylim(0.45, 1)
ax.legend(frameon=False, loc="lower right"); ax.spines[["top", "right"]].set_visible(False)
ax.set_title("The longer an inquiry is open, the more the couple's behaviour tells", loc="left"); plt.tight_layout(); plt.show()
print(f"On day 1 the model has only the channel, budget, package, wedding date, our reply time and the first messages "
      f"(AUC {r0['test']['auc_by_day']['1']}). By day 14 the couple's replies and silences are visible "
      f"(AUC {r0['test']['auc_by_day']['14']}). The top 20% of scores contain {r0['test']['top20_capture']:.0%} of the bookings.")
rule = r0["baselines"]["rule"]
print(f"The salesperson's rule: AUC {rule['auc']} overall, {rule['auc_by_day']['1']} on day 1 and {rule['auc_by_day']['30']} on day 30. "
      "Once a couple has been quoted the rule knows what the model knows; the model's lead is in the first two weeks, "
      "before the couple's intent is obvious, which is when a follow-up can still change the outcome.")
""")

md("""
## 5. Serving: the morning follow-up list, and does it work?

Each morning after the ETL, `features.materialise` stores today's features of every open inquiry and
`score.score` adds the production model's chance to book, banded High (top 20% of training scores), Medium and
Low. The dashboard's *Follow-up today* tab reads `mart.v_followup_list`.

To see the model in use, the next cell replays the mornings from October 2025 to January 2027, once a week,
with the model trained on 1 Oct 2025: store features, score, monitor. When the monitor raises an alert, the
model is retrained with what is known that day (at most every 8 weeks), as the weekly training DAG would.
""")

code(r"""
mondays = pd.date_range("2025-10-06", "2027-01-11", freq="7D").date
last_train, log = ASOF0, []
for d in mondays:
    with db.connect(cfg.dw_dsn, cfg) as c:
        n = F.materialise(c, d); S.score(c, d)
        checks = {name: (v, st) for name, v, st, _ in M.monitor(c, d)}
        retrain = any(st == "alert" for _, st in checks.values()) and (d - last_train).days >= 56
        if retrain:
            r = T.train(c, d); last_train = d
        c.commit()
    log.append({"week": d, "open": n, "PSI Channel": checks.get("PSI Channel", (None,))[0],
                "status": checks.get("PSI Channel", (None, "n/a"))[1], "live AUC": checks["live AUC"][0],
                "retrained": f"v{r['version']} {r['status']}" if retrain else ""})
log = pd.DataFrame(log)
print(f"{len(mondays)} mornings replayed; {(log.retrained != '').sum()} retrainings")
log[log.retrained != ""]
""")

code(r"""
band = dw('''SELECT s."Band", count(*) AS scored, round(avg(l."Booked"), 3) AS booked_later
             FROM ml."InquiryScore" s JOIN feature."InquiryLabel" l USING ("InquiryID")
             GROUP BY 1 ORDER BY 3 DESC''')
display(band)
b = band.set_index("Band").booked_later.astype(float)
print(f"Couples in the High band booked {b['High']:.0%} of the time, Medium {b['Medium']:.0%}, Low {b['Low']:.0%} "
      f"(every weekly score whose outcome is now known). Calling the High band first is where Sales' time pays off.")
""")

md("""
**Who to call: the model against the rule, as a gains chart.** Every weekly score whose outcome is now known,
sorted from most to least likely: how many of the later bookings does Sales reach by calling the first 10%, 20%,
... of the list? The salesperson's rule is computed on the same days from the same stored features.
""")

code(r"""
g = dw('SELECT s."Score"::float AS model, l."Booked" AS booked, f."Quoted", f."Consulted", '
       'f."HoursSilent"::float AS "HoursSilent", f."CoupleMsgs" '
       'FROM ml."InquiryScore" s JOIN feature."InquiryLabel" l USING ("InquiryID") '
       'JOIN feature."InquiryFeatures" f ON f."InquiryID" = s."InquiryID" AND f."AsOf"::date = s."AsOfDate"')
g["rule"] = T.rule_score(g)
x = np.arange(1, len(g) + 1) / len(g) * 100
fig, ax = plt.subplots(figsize=(7, 4.2))
curve = {}
for col, color in [("model", BLUE), ("rule", GREEN)]:
    curve[col] = g.sort_values(col, ascending=False, kind="stable").booked.cumsum().values / g.booked.sum() * 100
    ax.plot(x, curve[col], color=color, lw=2, label=col)
top20 = {k: v[int(len(g) * .2) - 1] for k, v in curve.items()}
ax.plot([0, 100], [0, 100], color=EDGE, ls=":", label="random"); ax.axvline(20, color=INK2, lw=0.8, ls="--")
ax.set_xlabel("% of open inquiries called, best first"); ax.set_ylabel("% of later bookings reached")
ax.legend(frameon=False, loc="lower right"); ax.spines[["top", "right"]].set_visible(False)
ax.set_title("Gains: calling the top of the list", loc="left"); plt.tight_layout(); plt.show()
print(f"Calling the top 20% of the list reaches {top20['model']:.0f}% of the later bookings with the model, "
      f"{top20['rule']:.0f}% with the rule and 20% at random. To reach the bookings the model finds in its top 20%, "
      f"Sales would have to call {x[np.argmax(curve['rule'] >= top20['model'])]:.0f}% of the list in the rule's order.")
""")

md("""
## 6. Monitoring: data drift and live accuracy

`monitor.monitor(as_of)` compares the inquiries of the last 120 days (as they looked on day 1) with the data the
production model was trained on (Chapter 7, data distribution shift). 120 days is longer than the longest gap
between two wedding fairs (99 days): with a shorter window, a stretch without a fair would look like drift
because fair leads would vanish from it.

* **PSI** (population stability index) for channel, budget, package type, and deciles of lead days, reply time
  and first-day messages. Below 0.1 is stable, 0.1 to 0.2 a warning, 0.2 or more an alert.
* **Missing rates**: a feature that suddenly goes missing more often would make the model predict from its
  imputed value (Chapter 7, "missing as a feature").
* **Live AUC**: the scores given 30 to 180 days ago, against the outcomes known now. A drop of 0.1 below the test
  AUC is an alert.

In 2026 a TikTok campaign changed who writes to us: TikTok went from about 6% of inquiries to about 19%, and those
couples book far less often. The channel PSI catches it.
""")

code(r"""
fig, ax = plt.subplots(1, 2, figsize=(16, 3.8))
ax[0].plot(log.week, log["PSI Channel"], color=BLUE, lw=2)
ax[0].axhline(0.1, color=ORANGE, ls="--", lw=1); ax[0].axhline(0.2, color=RED, ls="--", lw=1)
ax[0].text(log.week.iloc[0], 0.21, "alert", color=RED, fontsize=8); ax[0].text(log.week.iloc[0], 0.11, "warning", color=ORANGE, fontsize=8)
for w in log.week[log.retrained != ""]:
    ax[0].axvline(w, color=EDGE, lw=1, ls=":")
ax[0].set_title("Channel PSI, last 120 days vs the production model's training data (dotted: retrained)", loc="left", fontsize=10)
live = log.dropna(subset=["live AUC"])
ax[1].plot(live.week, live["live AUC"].astype(float), color=GREEN, lw=2, marker=".")
ax[1].set_ylim(0.5, 1); ax[1].set_title("Live AUC of past scores whose outcome is now known", loc="left", fontsize=10)
for a in ax:
    a.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.show()
alerts = dw('''SELECT "Message" FROM etl."Alert" WHERE "Message" LIKE 'model %%' ORDER BY "AlertID"''')
print(f"{len(alerts)} weekly drift alerts written to etl.Alert; the first three:"); alerts.head(3)
""")

md("""
Two things to read from this. The alert does not stop right after a retraining: a model can only learn from
inquiries whose outcome is known, so its training data trails the new channel mix by about three months, and the
PSI falls only as 2026 inquiries enter the training data. And drift is not the same as a worse model: the live AUC
stayed high, because the model already knew that TikTok couples book less. The alert's job is to make someone
look; the gate decides whether a retrained model is better.
""")

md("""
## 7. The registry, today's list, and the automation

Every training run is in the registry, including the ones the gate rejected, with the reason. On the as-of
date of the data (15 Jan 2027) the weekly training runs once more and the morning steps produce today's list.
""")

code(r"""
with db.connect(cfg.dw_dsn, cfg) as c:
    final = T.train(c, date(2027, 1, 15)); F.materialise(c, date(2027, 1, 15)); S.score(c, date(2027, 1, 15))
    today = M.monitor(c, date(2027, 1, 15)); c.commit()
print(f"weekly training on 15 Jan 2027: model v{final['version']} {final['status']} - {final['decision']}\n")
display(dw('SELECT * FROM mart.v_model_versions ORDER BY "Version"'))
dw('SELECT "InquiryID", "Band", "Score", "Channel", "AgeDays", "HoursSilent", "CoupleMsgs", "Consulted", "Quoted" '
   'FROM mart.v_followup_list ORDER BY "Score" DESC')
""")

md("""
**Automation (Airflow).** The same functions run in production as tasks, through the command line `python -m ml`:

* `aisleco_daily_etl`: the 11 ETL steps, then `ml_features → ml_score → ml_monitor → ml_forecast` (02:00 every day).
* `aisleco_weekly_train`: `ml train` every Sunday at 04:00 (both models); also triggered by hand after a drift alert.

**Dashboard.** `dashboard/app.py` (Streamlit) reads the `mart` views: Problem 1 capacity and the 6-month demand forecast, Problem 2 conversion and
no-reply rate, the follow-up list, and model health. Run it locally against this notebook's warehouse:

```bash
ETL_DW_DSN=dbname=aisleco_dw_ml .venv/bin/streamlit run dashboard/app.py
```
""")

code(r"""
from IPython.display import Image, display
for f in ["dashboard_followup.png", "dashboard_model_health.png"]:
    p = ROOT / "docs" / f
    if p.exists():
        display(Image(filename=str(p), width=1100))
""")

md("""
## 8. A second model, for Problem 1: the demand forecast

**Business problem (Problem 1, seasonal capacity).** Weddings bunch on auspicious dates and in the November to
February season. In those months the planners went over their standard hours (87 employee-weeks over 100% in
2024-2026), and couples were turned away because the date was full. Ops needs to know **months ahead** which
months will be over capacity, while there is still time to book freelancers or stop accepting more weddings.

**The question**: how many weddings, and how many staff-hours, will each of the next 6 months need, against the
standard hours of the event staff?

**Why "booked + pickup".** Weddings are booked 1 to 15 months ahead, so a month 2 months away is already mostly
on the books. A forecast that ignores the bookings and predicts the month from the calendar alone throws that away.
Hotels forecast this way: *final = already booked + the pickup still to come*. Every candidate here predicts the pickup.
The chart shows how much of a month's final number of weddings is already booked at each horizon.
""")

code(r"""
D = date(2027, 1, 15)
with db.connect(cfg.dw_dsn, cfg) as c:
    hist = FC.history(c, D)
share = (hist.groupby("Horizon").OnBooks.sum() / hist.groupby("Horizon").Final.sum() * 100)
monthly = hist.drop_duplicates("Month").set_index("Month").Final
fig, ax = plt.subplots(1, 2, figsize=(16, 3.6), gridspec_kw={"width_ratios": [1, 2]})
ax[0].bar(share.index.astype(str), share.values, color=BLUE)
ax[0].set_xlabel("months ahead"); ax[0].set_title("% of a month's weddings already booked", loc="left")
ax[1].bar(monthly.index, monthly.values, width=20, color=[ORANGE if m.month in (11, 12, 1, 2) else EDGE for m in monthly.index])
ax[1].set_title("Weddings per month (orange: Nov-Feb season)", loc="left")
for a in ax:
    a.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.show()
print(f"{len(hist)} rows of history: the next 6 months as they stood on the 1st of each month since {hist.AsOf.min():%b %Y}, "
      f"with the final count of each month that has ended. One month ahead, {share[1]:.0f}% of the weddings are already "
      f"booked; six months ahead, {share[6]:.0f}%.")
""")

md("""
**Features** (`feature.demand_features(as_of)`, one SQL definition, point in time like the conversion model's):
weddings already booked for the month, open inquiries asking for a date in it, months ahead, season, number of
auspicious days, the same month last year (as booked a year earlier at the same horizon, final, and the pickup
it still had then), open inquiries already quoted, and bookings made in the last 28 days. The models learn from
the features as they stood every Monday and every 1st of the month (about 800 rows instead of 150).

**Candidates and the backtest.** Four candidates, two of them simple baselines:

| Candidate | Pickup still to come |
|---|---|
| Seasonal naive (baseline) | Same month last year, final, minus what is booked |
| Pickup average (baseline) | The average pickup seen at the same horizon and season |
| Poisson regression | A count model on the features |
| LightGBM | Small, subsampled trees with a Poisson loss on the features |

With about 7 weddings a month, a random split is meaningless and a single test window is too small, so the
evaluation is a **rolling-origin backtest**: on the 1st of each of the last 12 months, each candidate is fitted
on the months that had ended by then and forecasts the next 6; it is scored on the months that have ended since.
The metric is **WAPE** (absolute error as a share of the weddings), with an 80% interval whose coverage is checked.

**Gate.** An ML candidate is used only if its WAPE beats the best baseline. Otherwise the baseline is registered as
the production forecast: with this little data a simple method can win, and the registry should say so.
""")

code(r"""
rd = with_dw(FC.train, D)
print(f"demand model v{rd['version']}: {rd['algorithm']} - {rd['decision']}\n")
res = pd.DataFrame(rd["candidates"]).T
res.join(pd.DataFrame(res.pop("wape_by_horizon").tolist(), index=res.index).add_prefix("WAPE h")).sort_values("wape")
""")

code(r"""
bt = rd["backtest"]
fig, ax = plt.subplots(1, 2, figsize=(16, 3.6))
for name, color in [(rd["algorithm"], BLUE), ("Poisson regression", ORANGE), ("seasonal naive", EDGE)]:
    w = bt[bt.Candidate == name].groupby("Horizon").apply(lambda g: (g.Forecast - g.Final).abs().sum() / g.Final.sum() * 100, include_groups=False)
    ax[0].plot(w.index, w.values, marker="o", lw=2, color=color, label=name)
ax[0].set_xlabel("months ahead"); ax[0].set_ylabel("WAPE %"); ax[0].legend(frameon=False)
ax[0].set_title("Backtest error by horizon", loc="left")
h3 = bt[(bt.Candidate == rd["algorithm"]) & (bt.Horizon == 3)].sort_values("Month")
ax[1].fill_between(h3.Month, h3.Lo, h3.Hi, color=BLUE, alpha=.15, label="80% interval")
ax[1].plot(h3.Month, h3.Forecast, color=BLUE, lw=2, marker="o", label="forecast made 3 months ahead")
ax[1].plot(h3.Month, h3.Final, color=INK, lw=0, marker="s", label="actual")
ax[1].legend(frameon=False); ax[1].set_title("Forecast 3 months ahead against what happened", loc="left")
for a in ax:
    a.spines[["top", "right"]].set_visible(False)
plt.tight_layout(); plt.show()
""")

md("""
**Reading the result.** The calendar alone (seasonal naive) is far off: the number of weddings in a month swings
from year to year, while what is already booked is known. Building on the bookings, the Poisson regression beats
the pickup average overall and most clearly 4 to 6 months ahead, where open and quoted inquiries and last year's
pickup tell more; one or two months ahead the month is almost all booked and the average is hard to beat. The
gap is small (about 50 test months), so the weekly retraining keeps whichever candidate wins the backtest, the
baseline included. This also answers *"why ML?"* with evidence rather than opinion.

**Serving and action.** Each morning after the ETL, `ml_forecast` writes the next 6 months into
`ml."DemandForecast"`. Weddings become staff-hours: the hours already assigned in each month's weeks (from
`fact.FactEmployeeWeeklyWorkload`), plus the expected new weddings times the average hours per wedding, against
the standard hours of the event staff (planners, coordinators, event staff). `mart.v_demand_forecast` turns that
into a status: in 2024-2026, months at 85% of capacity or more had 8 to 12 employee-weeks over 100%, months
at 65-85% had 2 to 9.

| Status | Expected hours | Action |
|---|---|---|
| Over | 85% of capacity or more | Book freelancers now; take no more auspicious-date weddings without them |
| Tight | 65-85% | Plan overtime; move planning work to earlier weeks |
| OK | 35-65% | No action |
| Quiet | under 35% | Promote off-peak packages; schedule leave and training |
""")

code(r"""
fc = with_dw(FC.forecast, D)
view = dw('SELECT * FROM mart.v_demand_forecast ORDER BY "Month"')
fig, ax = plt.subplots(figsize=(11, 3.6))
xs = np.arange(len(view)); lab = pd.to_datetime(view.Month).dt.strftime("%b %Y")
ax.bar(xs - .2, view.BookedHours.astype(float), .4, color=EDGE, label="hours of booked weddings")
ax.bar(xs + .2, view.ExpectedHours.astype(float), .4, color=BLUE, label="booked + expected")
ax.scatter(xs, view.CapacityHours.astype(float), marker="_", s=900, color=RED, label="capacity")
ax.plot(xs, view.CapacityHours.astype(float) * .85, lw=0, marker="_", ms=25, color=ORANGE, label="85% (Over)")
ax.set_ylim(0, view.CapacityHours.astype(float).max() * 1.35)
ax.set_xticks(xs, lab); ax.legend(frameon=False, ncols=4, loc="upper left"); ax.spines[["top", "right"]].set_visible(False)
ax.set_title(f"Next 6 months as of {D:%d %b %Y}: staff-hours against capacity", loc="left"); plt.tight_layout(); plt.show()
view[["Month", "booked", "expected_weddings", "low_80", "high_80", "ExpectedHours", "CapacityHours", "expected_utilization", "status", "action"]]
""")

md("""
February to July is the quiet half of the year, so today's forecast asks for no action. **Would it have warned
Ops before the last peak?** The next cell goes back to 1 Aug 2025 and uses only what was known that day: the
pickup average fitted on the months that had ended by then, and the average hours per wedding held so far
(all of a wedding's hours counted in its month, so the share runs higher than the weekly utilization).
""")

code(r"""
A = date(2025, 8, 1)
with db.connect(cfg.dw_dsn, cfg) as c:
    past, then = FC.history(c, A), FC.compute(c, A)
    per = float(c.execute('SELECT avg(b."TotalStaffHours") AS h FROM fact."FactEventBooking" b JOIN dim."DimDate" d '
                          'ON d."DateKey" = b."EventDateKey" WHERE b."IsCancelled" = 0 AND d."FullDate" < %s', (A,)).fetchone()["h"])
then["expected"] = then.OnBooks + FC.PickupAverage().fit(past).predict(then)
after = dw('''SELECT date_trunc('month', week_start)::date AS "Month", sum("StandardWeeklyHours")::float AS capacity,
                     round(sum("TotalAssignedHours") / sum("StandardWeeklyHours"), 2)::float AS actual_utilization,
                     count(*) FILTER (WHERE is_over_100pct) AS staff_weeks_over_100
              FROM mart.v_weekly_workload WHERE role = ANY(%s) GROUP BY 1''', (list(FC.OPS_ROLES),))
then = then.merge(after, on="Month")
then["expected_share"] = (then.expected * per / then.capacity).round(2)
then["status then"] = pd.cut(then.expected_share, [-1, .35, .65, .85, 99], labels=["Quiet", "OK", "Tight", "Over"])
print(f"Forecast made on {A:%d %b %Y} ({len(past)} rows of history, {per:.0f} staff-hours per wedding), against what happened:")
then[["Month", "OnBooks", "expected", "expected_share", "status then", "actual_utilization", "staff_weeks_over_100"]].round(2)
""")

md("""
**Monitoring the forecast.** Once a month has passed, `forecast.monitor` compares every forecast made for it with
what happened (`mart.v_demand_accuracy`) and writes the WAPE of the last 6 months to `ml."DriftCheck"`; 0.2 above the
backtest WAPE is an alert in `etl."Alert"`. To show it, the next cell replays March to December 2026 as production would have run it
(the first month with a full year of backtest history): on the 1st of each month the weekly training retrains, and the forecast is made with what was known that day.
""")

code(r"""
for d in pd.date_range("2026-03-01", "2026-12-01", freq="MS").date:
    with db.connect(cfg.dw_dsn, cfg) as c:
        FC.train(c, d); FC.forecast(c, d); c.commit()
chk = with_dw(FC.monitor, date(2026, 12, 1))
with_dw(FC.train, D); with_dw(FC.forecast, D)        # back to today: the dashboard shows the forecast of 15 Jan 2027
acc = dw('SELECT * FROM mart.v_demand_accuracy WHERE "AsOfDate" < %s', (date(2026, 12, 1),))
acc["err"] = (acc.Forecast.astype(float) - acc.actual).abs()
print(f"{chk[0]} on 1 Dec 2026: {chk[1]} ({chk[2]}) - {chk[3]}")
print(f"All {len(acc)} forecasts of months that ended by 1 Dec 2026: WAPE {acc.err.sum() / acc.actual.sum():.2f}, "
      f"{acc.in_interval.mean():.0%} of the actual counts inside the 80% interval.")
acc.groupby("Horizon").apply(lambda g: pd.Series({"forecasts": len(g), "WAPE": round(g.err.sum() / g.actual.sum(), 3),
                                                  "in 80% interval": round(g.in_interval.mean(), 2)}), include_groups=False)
""")

md("""
Known limits of the demand forecast:

* **Monthly, not weekly**: about 7 weddings a month, under 2 a week. A weekly count would be mostly noise; the
  weekly picture comes from the hours already assigned (`fact.FactEmployeeWeeklyWorkload`), which the forecast adds to.
* The hours of a wedding that is not yet booked are all counted in its event month, though its planning hours
  really spread over the weeks before. Cancelled bookings count as booked (about 4%): the CRM keeps no cancellation date.
* In the 2026 replay above, the hours already assigned come from today's warehouse; the wedding counts are point in time.
* The 80% interval assumes Poisson noise around the pickup. It holds 1 to 5 months ahead, but 6 months ahead the
  real counts vary more than that and fall outside it more often: read the 6-month figure as a direction, not a range.
* In the simulation every wedding is booked at least 40 days ahead, so one month ahead the forecast is just the
  bookings. A real CRM will have some short-notice weddings.
* The simulated CRM ends in December 2026, so the bookings for 2027 that would still come in real life never
  arrive in this data. The forecast for 2027 is what production would show on 15 Jan 2027.
""")

md("""
## 9. Where this sits on the MLOps levels of Chapter 7

| Level | What it needs | This project |
|---|---|---|
| 0: manual | Scripts and notebooks, manual hand-over | Passed: nothing here is a manual step |
| 1: ML pipeline automation | Automated training pipeline, continuous training on new data, feature store, metadata store and model registry, validation before deployment, monitoring that triggers retraining | **Yes**: weekly and drift-triggered training, `feature` schema, `ml.ModelVersion`, the gate, `ml.DriftCheck` + `etl.Alert` |
| 2: CI/CD of the pipeline | Each code change to the pipeline built, tested and deployed automatically | Partly: 45 automated tests (11 for the two models) run with one command, but nothing runs them on each push yet. A GitHub Actions workflow running `pytest` against a PostgreSQL service would be the next step |

Known limits: descriptive fields (channel, budget, package, wedding date) are read as they are now, not as they
were on past dates, because the CRM keeps no history for them; event times (messages, consultation, quote,
booking) are exact. From the first day in production the offline store keeps every day's features, so this
limit fades with time. The data is simulated, so the accuracy shown is that of the simulation's assumptions.
""")

code("""
print(f"Notebook run time: {time.perf_counter() - T0:.0f} s")
""")

# ---------------------------------------------------------------------------------------------
# Colab variant: the same cells plus the shared setup cells (fetch the project, install PostgreSQL 16)
# ---------------------------------------------------------------------------------------------
if COLAB:
    from colab_setup import setup_cells
    cells[1:1] = setup_cells("A full Run all takes about 4 minutes on a standard Colab machine, about 2 of them for the setup.")
    i = next(i for i, c in enumerate(cells) if "ETL_DW_DSN=dbname=aisleco_dw_ml" in c.source)
    cells[i].source = cells[i].source.replace(
        "Run it locally against this notebook's warehouse:",
        "It runs on a local machine with the project (README); in Colab, the screenshots below show it with this notebook's data:")

out = ROOT / ("AisleCo_ML_Conversion_Demo_Colab.ipynb" if COLAB else "AisleCo_ML_Conversion_Demo.ipynb")
nbf.write(nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3"}}), out)
print("written", out)
