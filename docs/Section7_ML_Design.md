# 7. Machine Learning and MLOps

This section adds two machine-learning models to the warehouse of Sections 5 and 6: a conversion model for
Problem 2 (high inquiry, low conversion), and a demand forecast for Problem 1 (seasonal capacity, 7.9). The code is in `ml/`, the feature store and registry in
`sql/ml_feature_store.sql`, and `AisleCo_ML_Conversion_Demo.ipynb` runs every step shown here.

## 7.1 The business question

About 14% of inquiries become a booking, and three in four lost inquiries end as "No reply": the
couple stops writing and the 30-day rule closes the inquiry. Sales has a few people for dozens of open
inquiries at any time. The model answers one question each morning: **which open inquiries are most
likely to book?** Sales calls the High band first and follows up the quiet couples before they drop out.
The no-reply rate is tracked as a KPI on the dashboard; the model is how Sales acts on it.

One model, one label: *did this inquiry turn into a booking?* An inquiry is scored every day while it is
open, so its score changes as the couple writes, gets a consultation and a quote.

## 7.2 Data

The model reads only the warehouse: the Silver zone (`stg`), which keeps every CRM row with its
timestamps, and the dimensions. No new source and no schema change in the CRM were needed. The CRM
already records what predicts a booking in practice: the channel, budget and package, the wedding date
and whether it is a peak or auspicious date, our reply time, and the couple's own messages.

The data is simulated (`AisleCo_MultiZone_Architecture_v3.ipynb`, three years, 2024–2026, 1,905
inquiries). Each couple has a hidden readiness to book that drives what the CRM records; the
assumptions are listed in that notebook. A first version of the simulation decided bookings almost at
random, and no model could rank couples (AUC 0.56, the same as the simulation's own probabilities). The
simulation was rebuilt so that behaviour drives outcomes, as it does in real sales. The accuracy below is
therefore the accuracy of the simulation's assumptions; with the real CRM, the same pipeline learns the
real drivers.

## 7.3 Feature store

The feature store is three objects in PostgreSQL, following the entity / time index / features design
of Chapter 7. No separate feature-store product is used.

| Object | Role |
|---|---|
| `feature.inquiry_features(ids, cutoffs)` | The one definition of the features, in SQL. For each inquiry and cutoff time it uses only rows dated before the cutoff, and returns only inquiries still open then |
| `feature."InquiryFeatures"` | Offline store: the features of every open inquiry on every scoring day (key InquiryID + AsOf) |
| `feature."InquiryLabel"` | The label: did the couple book within 120 days of the inquiry, and when that became final (see below) |

| Feature | From |
|---|---|
| Channel, budget range, package type (or none) | Inquiry, MarketingChannel, Package |
| Wedding date given, lead days, peak or auspicious date | Inquiry, AuspiciousDate |
| Our first reply time (missing = not logged or in person) | ChatMessage |
| Couple messages so far and in the first 24 h; hours since the couple last wrote | ChatMessage |
| Consulted, quoted, quote share of budget (by the cutoff) | Inquiry |
| Age of the inquiry in days | Inquiry |

**Point in time.** Training calls the function with past cutoffs and the daily scoring calls it with
today, so both get the same features. A training example is an inquiry as it looked 1, 7, 14 and 30 days
after it arrived (while still open), joined with its later outcome. A test checks that adding rows dated
after a cutoff does not change the features at that cutoff. Limit: descriptive fields (channel, budget,
package, wedding date) are read as they are now, because the CRM keeps no history for them; event times
are exact. The offline store keeps each day's features from the first day in production.

**Label: a fixed 120-day window.** Booked = an event was created within 120 days of the inquiry, on this
inquiry or on a later one by the same customer (`CustomerID`); not booked = 120 days passed without one. The
CRM status is not used: three quarters of the Lost inquiries are closed by the 30-day no-reply AutoRule, and a
couple marked Lost who comes back on day 90 and books is a booking. A booking after day 120 is new business.
The label is final on the booking day or on day 120, so it never changes once it exists (a label read from the
status could flip from Lost to Booked between two trainings). 120 days is about three times the longest sales
cycle in the data (42 days). A reopened inquiry (status back to open in the CRM) is scored again.

## 7.4 Training, evaluation and validation

| Step | Design |
|---|---|
| Split | By time: test = inquiries received 300 to 120 days before the as-of date (every one has its final 120-day label), train = everything before |
| Candidates | Logistic regression and gradient boosting (scikit-learn), the same preprocessing: one-hot categories, median imputation with a "was missing" flag |
| Baselines | Each channel's past booking rate; and a salesperson's rule of thumb in code: quoted > consulted > the couple wrote in the last 3 days (more messages break ties) |
| Metrics | AUC overall and by inquiry age, PR-AUC, share of bookings in the top 20% of scores, Brier score |
| Gate | Promote only if AUC ≥ the stronger baseline + 0.05, and at least as good as the production model on at least 50 test inquiries that model never saw (counted once each, not once per scoring age); with fewer, production is kept |
| Production model | The winner refit on train + test; bands: High = top 20% of scores, Medium = next 30%, Low = the rest |

Results (test window 21 Mar – 16 Sep 2026, model trained on data to 15 Jan 2027):

| | AUC | Day 1 | Day 7 | Day 14 | Day 30 | Top 20% catches |
|---|---|---|---|---|---|---|
| Baseline: channel rate | 0.69 | 0.68 | 0.68 | 0.68 | 0.77 | 39% of bookings |
| Baseline: salesperson's rule | 0.79 | 0.71 | 0.79 | 0.86 | 0.96 | 49% of bookings |
| Gradient boosting (production) | **0.88** | 0.78 | 0.87 | 0.90 | 0.96 | 65% of bookings |

The first model (logistic regression), trained on data to 1 Oct 2025, scored 0.78 against 0.57 for the channel rate and 0.71 for the rule. On the first day
the model knows little beyond the channel, budget, package, wedding date, our reply time and the first
messages; by day 7–14 the couple's replies and silences show. Over 15 months of weekly scoring, couples in
the High band booked 39% of the time, Medium 13% and Low 1%.

## 7.5 Serving, monitoring and retraining

| Part | Design |
|---|---|
| Model registry and metadata | `ml."ModelVersion"`: every training run with its data windows, row counts, metrics of all candidates and the baseline, the gate's decision, the training distributions and the model itself. Status production / retired / rejected |
| Serving | Batch: each morning the features of every open inquiry are stored and scored into `ml."InquiryScore"`; the dashboard reads `mart.v_followup_list`, which also shows the **expected value** of each inquiry (chance to book × budget midpoint), so Sales can order the list by money as well as by chance |
| Data drift | PSI of channel, budget and package, and of deciles of lead days, reply time and first-day messages, for the inquiries of the last 120 days (on day 1) against the training data. < 0.1 ok, 0.1–0.2 warning, ≥ 0.2 alert. 120 days is longer than the longest gap between wedding fairs (99 days), so the fair calendar does not look like drift |
| Missing values | A feature's missing rate compared with training (≥ 0.05 change warns, ≥ 0.15 alerts) |
| Performance | Live AUC of the scores given 270–120 days ago (all their labels are final); 0.05 below the test AUC warns, 0.1 alerts |
| Alerts | `ml."DriftCheck"`, and alerts in `etl."Alert"` next to the ETL alerts |
| Retraining | Weekly (`aisleco_weekly_train`), and by hand after a drift alert. A retrained model replaces production only through the gate; production learnt from every inquiry up to 120 days before its as-of date, so after a weekly retrain it is usually kept until 50 new inquiries have outcomes, and then replaced only by a model that is at least as good on them |

In the simulation, a TikTok campaign in 2026 made TikTok about 19% of inquiries instead of 6%, and those
couples book far less often. The channel PSI was at most 0.12 in 2024–2025 and rose to 0.2–0.4 in 2026;
the first alert came on 16 Feb 2026. Retraining followed, and the registry shows each version and the one
the gate rejected. The live AUC stayed between about 0.85 and 0.9: the model already knew that TikTok
couples book less, so drift raised a flag without making the model worse.

## 7.6 Automation and the dashboard

| Airflow DAG | Tasks |
|---|---|
| `aisleco_daily_etl` (02:00) | The 11 ETL steps, then `ml_features → ml_score → ml_monitor → ml_forecast` |
| `aisleco_weekly_train` (Sunday 04:00) | `ml train`: both models; training set, candidates, gate, registry |

The same steps run from the command line (`python -m ml features|score|monitor|forecast|daily|train|status`).

The dashboard (`dashboard/app.py`, Streamlit) reads only `mart` views: Problem 1 capacity (weekly
utilization heatmap, the next 6 months against capacity with an action per month, peak-date acceptance, couples turned away), Problem 2 conversion (funnel by channel,
no-reply rate by month, reply speed, lost reasons), the follow-up list and model health.

## 7.7 MLOps level

| Level (Chapter 7) | This project |
|---|---|
| 0: manual | Passed: no manual step between data and prediction |
| 1: ML pipeline automation | Yes: automated training pipeline with continuous training, feature store, metadata store and registry, validation gate, monitoring with alerts |
| 2: CI/CD of the pipeline | Partly: 45 automated tests (11 for the two models) run with one command, but not yet on every push. The next step is a GitHub Actions workflow running `pytest` against a PostgreSQL service |

## 7.8 Tests

| Test | Result |
|---|---|
| Features at a past cutoff do not change when later rows (messages, consultations, quotes) are added | Pass |
| Training rows are dated before the inquiry closed; the label is not a feature; a booked inquiry is not returned after its booking | Pass |
| Same seed, same metrics; a retrain with no inquiries production never saw keeps production; an impossible gate gives "rejected" and production is unchanged | Pass |
| A model trained four months later is compared with production on at least 50 inquiries production never saw, and exactly one model is in production afterwards | Pass |
| One score per open inquiry per day, bands High / Medium / Low, a rerun does not duplicate | Pass |
| Channel PSI is ok in Oct 2025 and an alert in May 2026, written to `etl.Alert` | Pass |
| Both baselines are measured and the gate uses the stronger one | Pass |
| Expected value on the follow-up list = chance to book × budget midpoint | Pass |
| Demand features at a past date do not change when later bookings and inquiries are added | Pass |
| Demand forecast: 6 months, low ≤ forecast ≤ high, forecast ≥ booked, no duplicates on a rerun; one production version per model; when no ML candidate wins, a baseline is registered as production | Pass |

## 7.9 Demand forecast (Problem 1)

**Question.** How many weddings, and how many staff-hours, will each of the next 6 months need, against the
standard hours of the event staff? Ops needs this months ahead, while freelancers can still be booked.

**Method.** Weddings are booked 1 to 15 months ahead, so a near month is mostly on the books already (one month
ahead all of it, six months ahead about two thirds in the simulation). The forecast is the one hotels use:
*final = already booked + pickup still to come*. The forecast is **monthly**: with about 7 weddings a month and
under 2 a week, a weekly count would be noise; the weekly picture comes from the hours already assigned.

| Part | Design |
|---|---|
| Features | `feature.demand_features(as_of)`, point in time: weddings booked for the month, open inquiries asking for it, months ahead, season, auspicious days, the same month last year (booked a year earlier, final, and the pickup it still had then), open inquiries already quoted, bookings made in the last 28 days. Offline store `feature."DemandFeatures"` |
| Training rows | The features as they stood every Monday and every 1st of the month (about 800 rows to Jan 2027, five times the month starts alone); the backtest scores the 1sts |
| Candidates | Two baselines (same month last year; the average pickup at the same horizon and season) and two ML models (Poisson regression; LightGBM with a Poisson loss, small and subsampled), all predicting the pickup |
| Evaluation | Rolling-origin backtest: on the 1st of each of the last 12 months, fit on the months that had ended and forecast the next 6. WAPE (error as a share of weddings) by horizon, and the coverage of an 80% interval |
| Gate | An ML candidate is used only if its WAPE beats the best baseline; otherwise the baseline is the production forecast. One production version per model in `ml."ModelVersion"` (column `Model`) |
| Serving | `ml_forecast` daily into `ml."DemandForecast"`: booked, expected, 80% interval, staff-hours (hours already assigned + expected new weddings × hours per wedding) and capacity |
| Action | `mart.v_demand_forecast`: Over (85%+ of capacity: book freelancers, take no more auspicious-date weddings without them), Tight (65-85%: overtime, plan earlier), Quiet (under 35%: promote off-peak packages). In 2024-2026 months at 85%+ had 8-12 employee-weeks over 100%, months at 65-85% had 2-9 |
| Monitoring | WAPE of the last 6 months' forecasts against what happened, in `ml."DriftCheck"`; 0.2 above the backtest WAPE is an alert in `etl."Alert"` |

**Results** (backtest to 15 Jan 2027, WAPE):

| Candidate | All | 1 month | 3 months | 6 months | 80% interval holds |
|---|---|---|---|---|---|
| Seasonal naive (baseline) | 0.62 | 0.56 | 0.59 | 0.86 | 36% |
| Pickup average (baseline) | 0.124 | 0.00 | 0.12 | 0.40 | 87% |
| Poisson regression (**production**) | **0.116** | 0.05 | 0.12 | 0.21 | 89% |
| LightGBM | 0.122 | 0.02 | 0.14 | 0.28 | 89% |

The Poisson regression beats the pickup average overall, and by half 6 months ahead, where open and quoted
inquiries and last year's pickup say more than the bookings; one or two months ahead the month is almost all
booked and the average is hard to beat. The three added features and the weekly rows brought it from 0.142 to
0.116 (with the original features and monthly rows no ML candidate beat the baseline). Last year's pickup
matters most: without it LightGBM falls to 0.141. LightGBM is close behind; with about 50 test months, gaps of
0.01 are within noise, and the weekly retraining keeps whichever wins the backtest. Forecasting from the
calendar alone (seasonal naive) is five times worse: what is already booked is the most informative input.

**Would it have warned us?** On 1 Aug 2025, with only what was known that day, the forecast put October,
November and December 2025 at 104-129% of the event staff's hours (all of a wedding's hours counted in its
month): status Over. Those months then had 8, 6 and 12 employee-weeks over 100%. In the replay of March to
December 2026, the forecasts of the months that ended scored a WAPE of 0.14, with 87% of the actual counts in the
80% interval.

**Limits.** A wedding not yet booked has all its hours counted in its event month (its planning hours really
spread over the weeks before). Cancelled bookings count as booked (about 4%; the CRM keeps no cancellation date).
Six months ahead the 80% interval is too narrow (real counts vary more than Poisson noise): read the 6-month
figure as a direction. The simulated CRM ends in December 2026, so the forecast for 2027 lacks the bookings that
would still come in real life.

## 7.10 Why a model, and not a salesperson's judgement?

An experienced salesperson can read a couple they have talked to. The question is whether a model adds anything
beyond that, so the judgement is written down as a rule (7.4) and the model has to beat it by 0.05 AUC to be used.

* **Once a couple is quoted, the rule is as good as the model** (AUC 0.96 at day 30 for both).
* **The model's lead is in the first two weeks** (day 1: 0.76 against 0.71; day 7: 0.86 against 0.81), before a couple's
  intent is obvious, which is when a follow-up can still change the outcome; and on fair days, when dozens of
  inquiries arrive at once and nobody can keep track of who went quiet.
* **Calling the top 20% of the list** reached 71% of the later bookings with the model and 51% with the rule
  (weekly scores October 2025 to January 2027); to reach as many bookings in the rule's order, Sales would call 30%.

The model does not decide: it hands Sales an ordered list each morning, including the quiet couples a busy person
misses, and Sales still makes the call. A fair test with real staff, proposed before go-live: a salesperson ranks 30
past inquiries as they looked on day 1, and the ranking's AUC is compared with the model's.

## 7.11 Proving the impact

The model predicts who will book; it does not prove that calling them makes them book. Before go-live:

1. **Log follow-up calls in the CRM** (who was contacted, when, by whom). Without it, nobody can tell whether a booking
   followed a call.
2. **A/B test the follow-up list** for 3 months: half the open inquiries are followed up in the model's order, half in
   the usual order; compare the booking rate and the no-reply rate.
3. With the follow-up log, an **uplift model** can come next: who books *because* they were called, so Sales does not
   spend calls on couples who would book anyway.

## 7.12 Roadmap: further uses of ML at Aisle & Co.

Not built, because the data they need is not recorded yet. Each line says what the CRM would have to start keeping.

| Use | Question | Data needed |
|---|---|---|
| Cancellation risk | Will this booked couple cancel? | The cancellation date and reason (the CRM keeps only the status) |
| Chat text (NLP) | What is the couple asking, and are they hesitant on price? | The message text (only timestamps reach the warehouse now) and consent to analyse it |
| Upsell | Which couples add services or upgrade the package? | The final contract value and add-ons per wedding |
| Quote pricing | Which quote level books without leaving money on the table? | Every quote revision, discounts given, competitors' prices |
| Vendor matching | Which venue and photographer suit this couple? | Vendor assignments and post-event ratings |
| Reviews and referrals | Which couples will refer friends? | Post-event reviews and the referrer of each inquiry |
| Marketing spend | Which channel brings bookings at the lowest cost? | Spend per channel and month (only inquiry counts now) |
