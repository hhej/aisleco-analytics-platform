# Spec 04. Machine learning and MLOps

Part of the [v2 plan](PLAN.md). Answers, for each model: which decision it supports, what exactly it predicts (the label),
from what (features, with leakage control), how it is trained and validated, how good is good enough, how it reaches the
user, and how it is watched after launch.

## 0. The two models

| | Model A: lead conversion (main model) | Model B: staffing demand (Problem 1) |
|---|---|---|
| Decision | Each morning: which open inquiries each salesperson contacts first, and what to say | Each week: per role group and week ahead, book freelancers, stop selling a date, or offer alternatives |
| User | Sales (2 salespeople, about 15 follow-ups a day each) | Operations manager, weekly meeting |
| Unit | open inquiry × score date | role group × target week, at a forecast date |
| Target | books within the next 60 days (0/1; 30 days by default, widened by the pre-registered EDA rule, §2) | staff-hours required in the target week |
| Method | gradient-boosted trees (LightGBM), tuned and calibrated; logistic regression as challenger | booked hours + Model A's expected hours from open leads + a count model for bookings not yet in sight, with Monte Carlo intervals |
| Main metric | PR-AUC; precision and recall in each day's top 15 | WAPE at 5–12 weeks ahead; interval coverage |
| Must beat | today's practice (newest message first), a stage rule, channel rates, a logistic regression | booked-only, seasonal naive, pickup ratio |
| Output | `gold.FactLeadScore` → Sales "Today" page | `gold.FactDemandForecast` → Operations page |

The two connect: Model A's probabilities for open inquiries are part of Model B's forecast, so a lead that is likely to book
already counts against next February's capacity, and the Sales list shows whether the couple's requested date still has room.

---

## Model A: lead conversion

### 1. Purpose and decision

- **Problem.** About 1,500 inquiries a year, 6–8 % book. At any moment about 120 inquiries are open; two salespeople can
  follow up about 30 a day. Today they work newest-first, so a couple who received a quote last week and went quiet is often
  never called again, and the 30-day rule closes them as "No reply".
- **Decision.** The order of each salesperson's follow-up list, a plain reason for each lead, and a suggested next action.
- **What success looks like.** More bookings from the same inquiries and the same sales hours. Measured offline by how many
  of the next 60 days' bookings the top 15 contain (§6), online by an A/B holdout (§8).

### 2. Prediction design

| Item | Definition |
|---|---|
| Score time *T* | 02:00 each day, after the pipeline; features use only data with timestamps before 00:00 of the score date |
| Population | inquiries open at *T*: received before *T*, not closed (`ClosedDate` null or ≥ *T*) |
| Label *y* | 1 if a booking (`Event.CreatedDate`) is made from this inquiry, or from another inquiry of the same `CustomerID`, in [*T*, *T* + 60 days); else 0. Later cancellation does not change the label. |
| Window | Rule fixed before looking: the smallest of 30, 45 and 60 days that holds at least 80 % of bookings (measured from every open day that precedes a booking). **Result (M5 EDA, `docs/v2/ml_eda.md`): 30 d = 55 %, 45 d = 72 %, 60 d = 82 % → 60 days.** Wedding decisions are slow (consultation, family, quote). The cost: labels mature after 60 days, so the test period and monitoring lag by 60 days. |
| Censoring | rows whose label window is not over at the training cut-off are excluded from training (their label is unknown) |
| Expected class balance | about 4–6 % positive rows (to be measured) |

### 3. Feature store

`feature` schema in `aisleco_dw`, built from Silver through `stg` (Chapter 7 feature-store design: one definition used for
training and serving).

| Object | What it is |
|---|---|
| `feature.registry` | one row per feature: name, group, type, description, formula, source columns, owner, version, added on |
| `feature.lead_features(ids, cutoffs)` | SQL function: features of each inquiry as of each cut-off, using only rows with timestamps before the cut-off (v1's point-in-time function, extended) |
| `feature.lead_features_daily` | offline store: one row per open inquiry per score date (what was served; training reads history from here and from the function) |
| `feature.v_lead_features_latest` | online view: today's rows, read by the scoring step |

Feature groups (all as of cut-off *T*; about 45 features):

| Group | Features (exact definitions go in `feature.registry`) |
|---|---|
| Lead profile | channel; platform; has tracking code; budget band (ordinal, −1 unknown); budget midpoint (THB); package of interest; has requested date; lead days (requested date − *T*); requested date in high season; its auspicious level (0–3); in Buddhist Lent; arrived outside office hours; day of week and month of arrival; fair lead; fair guest count; venue type |
| Capacity (cross-problem) | utilization of the requested wedding week as booked at *T* (booked hours ÷ capacity) |
| Engagement | age in days; customer messages (total, last 3 days, last 7 days); staff messages; hours since the couple's last message; hours since our last message; first-reply minutes (log); replied within 1 hour; median hours the couple takes to answer us |
| Stage | consulted; days since consultation; quoted; days since quote; quote share of budget; quoted amount |
| Text (rule intents, counts up to *T*) | the 11 intent flags of Spec 02 §4.4, summed over the couple's messages |
| Sales activity | follow-ups (total, last 7 days); days since the last follow-up; the last follow-up reached the couple |
| History | earlier inquiries by the same customer |

**Leakage controls.** Never used: `Status`, `ClosedDate`, `ClosedBy`, `LostReason`, anything from `Event`, the outcome
dimension, `UpdatedAt`, `IsHoldout`. A stage date counts only if it is before *T*; `QuotedAmount` only if `QuotedDate` < *T*.
Test (kept from v1, extended): features computed at *T* are identical before and after the data of the following 60 days is
added. Known limit: the CRM keeps only the latest quote and budget; if sales edits them later, history shows the new value
(the simulation never edits them; a `Quote` table is the fix, noted in the roadmap).

### 4. Text representation (Chapter 7 text features)

Two representations, compared by ablation (§6):
1. **Rule intents** (Spec 02 §4.4): transparent, used by the reasons shown to sales.
2. **TF-IDF** on character 2–4-grams of the couple's masked messages before *T* (Thai has no spaces between words, so character
   n-grams avoid a word tokenizer), reduced with truncated SVD to 16 components. The vectorizer and SVD are fitted on the training
   folds only and stored inside the model pipeline, so scoring applies the same transformation.

TF-IDF is kept only if it raises PR-AUC by at least 0.01 with a positive paired-bootstrap lower bound. Otherwise the model ships
with rule intents only, and the report says so.

### 5. Training set

1. One row per (inquiry, score date) for every day the inquiry was open, up to 60 days of age (older open leads are rare).
2. **Sample weight** 1 ÷ (rows of that inquiry), so a lead that stayed open 60 days does not count 60 times more than one that
   closed in 3.
3. Labels joined from the booking table by the rule of §2; censored rows dropped.

### 6. Validation, tuning, calibration, metrics

**Splits (all by time, never at random).** With training cut-off *C* (the run date):

```
 score dates:  ── train (rolling CV inside) ──┤ purge 30 d ├── test: 3 months ──┤ 30 d (labels maturing) ├─ C
```

- **Test**: score dates in [*C* − 240 d, *C* − 60 d): 6 months whose labels have matured (3 months in runs v1–v3 proved too few booked couples to decide, decision C24). Never used for any choice; reported once.
- **Purge**: 60 days (the label window) between train and test, so no training label window overlaps the test period; any inquiry that has rows in
  the test period is removed from training (no inquiry on both sides).
- **Rolling-origin cross-validation** inside the training period: 5 folds; each validates on a 2-month block of score dates and
  trains on everything before the block minus a 60-day purge; folds are grouped by inquiry.

**Candidates.** Logistic regression (one-hot, standardised, L2) and LightGBM (Optuna TPE, 30 trials; `num_leaves`
7–63, `min_child_samples` 20–200, `learning_rate` 0.01–0.1, `feature_fraction` 0.5–1.0, `bagging_fraction` 0.6–1.0,
`lambda_l2` 0–10, trees by early stopping). Objective: mean PR-AUC over the folds; ties go to the simpler model.

**Calibration.** Isotonic regression fitted on the out-of-fold predictions of the last two folds (time order kept). The list
ranks by expected fee = probability × fee, so the probabilities must be honest. Reported: Brier score, expected calibration
error (ECE, 10 bins), reliability plot.

**Metrics on the test period**, each with a 95 % interval from a cluster bootstrap by inquiry (1,000 resamples):

| Metric | Why |
|---|---|
| **PR-AUC** (primary) | positives are rare; it measures ranking quality where it matters |
| **Precision@15 and recall@15** per salesperson-day, averaged over test days | the actual decision: 15 follow-ups a day |
| Bookings captured | share of the bookings within the label window that were in that day's top 15 |
| ROC-AUC | comparability with v1 and the literature |
| Brier, ECE | calibration |
| Slices | PR-AUC and precision@15 by channel, stage, age (0–2, 3–14, 15+ days), budget band |

**Baselines** (the model must beat the best one):

| ID | Baseline | What it represents |
|---|---|---|
| B0 | newest customer message first | what sales do today |
| B1 | quoted > consulted > wrote in the last 72 h, then message count (v1 rule) | an experienced salesperson's rule |
| B2 | historical booking rate of the lead's channel × stage | simple statistics |
| B3 | logistic regression | the simplest real model |


**Ablation and feature choice.** The ablation runs on the rolling-origin CV, never on the test: feature groups are added in the
order of §3 and the model uses the prefix with the best CV PR-AUC (ties → fewer features). The table is reported (it also shows
what each data source contributes).

**Gate** (in code, logged in MLflow; a candidate becomes `champion` only if all hold). Revised after the first full run (decision
C23): the original rule "significantly better than the best simple rule" asked the wrong question and had no power on three months
of test data (≈ 70 booked couples). The business question is whether the list beats what sales do today without being worse than a
good rule:
1. **Superiority over today's practice (B0):** paired-bootstrap 95 % lower bound of the PR-AUC gain > 0.
2. **Non-inferiority against the best simple rule (B1 or B2):** lower bound of the PR-AUC difference > −0.05.
3. ECE ≤ 0.05 after calibration (the list ranks by probability × fee, so probabilities must be honest).
4. No slice with at least 100 test rows where the candidate's PR-AUC is more than 0.05 below B0's.
5. Against the current champion: compared on test inquiries the champion never trained on (at least 50 such inquiries); the
   candidate must not be worse. With fewer than 50, the champion stays.
B3 (logistic regression) is a model-selection candidate, not a gate baseline: LightGBM is chosen only if its CV PR-AUC beats
logistic regression by more than 0.005 (ties go to the simpler model); the B3 test result is reported.

### 7. Serving: the daily list

1. After the pipeline, `ml_score` loads `models:/lead_conversion@champion` from MLflow and scores `feature.v_lead_features_latest`.
2. **Expected fee**: Full Planning = fee rate × budget midpoint; Partial and Day-of = package base price; unknown package =
   median fee of the last 12 months. Expected fee of the lead = probability × fee.
3. **Rank** within each owner's list by expected fee (the page can switch to probability).
4. **Band**: High = above the 85th percentile of the calibrated test-period scores (roughly the top 15 %), Medium = 60th–85th, Low below; the cut-offs are stored with the model.
5. **Reasons**: SHAP values (tree explainer) per lead; the three largest positive contributions are turned into sentences
   by `ml/reasons.yml` (e.g. `days_since_quote` → "Quote sent 2 days ago"; `intent_ask_availability` → "Asked whether a
   date is free"; `replied_within_1h` → "We answered within an hour"). Negative drivers are shown as risks ("Silent for 9 days").
6. **Next best action**, rules in `ml/actions.yml`, first match wins:

| Situation | Suggested action |
|---|---|
| no staff reply yet | Reply now |
| asked about a date whose week is Over capacity | Offer the nearest auspicious dates with room: … (from the date checker) |
| quoted ≥ 3 days ago, no answer, quote share of budget > 16 % | Follow up on the quote; offer Partial Planning or a payment plan |
| quoted ≥ 3 days ago, no answer | Follow up on the quote |
| consulted, not quoted after 5 days | Send the quote |
| silent ≥ 10 days, High or Medium band | Last check-in before the auto-close on {date} |
| otherwise | Check in |

7. Rows go to `gold.FactLeadScore` with the model version.

### 8. Holdout and A/B evaluation

- 10 % of inquiries are in a **holdout** group: `hash(InquiryID) mod 10 = 0`, fixed at arrival. They appear in a separate
  "Standard queue" on the Sales page, ordered newest-first with no score: today's practice. The other 90 % are worked from the
  ranked list. This is a randomised controlled comparison of "ranked list" against "today's practice".
- **Effect** = 60-day booking rate of prioritised leads − that of holdout leads, with a 95 % bootstrap interval
  (`mart_sales.v_ab_result`, Executive "model value" tile).
- **Power analysis** (report): with the base 60-day booking rate measured on the holdout and a 10 % holdout, the weeks of traffic needed
  to detect +1.5 percentage points at 80 % power and 5 % significance. If that is longer than the project, the report says
  the test is running and shows the interval so far, nothing more.
- **Offline value estimate** (before the A/B result is in): on the test period, bookings captured by the model's top 15 minus
  bookings captured by B0's top 15, × an assumed effect of a follow-up on a lead's booking chance (the simulation's parameter,
  Spec 06 §5, stated as an assumption), × median fee = THB per month. Shown with its assumption, never as a measured result.
- In the simulation the launch date is the first champion; from then on the simulated salespeople work the list for treated
  leads and the standard queue for holdout leads (Spec 06 §5).

### 9. Monitoring and retraining (MLOps level 1, Chapter 7)

| Check | When | Alert when |
|---|---|---|
| Input drift: PSI per feature, today's scored rows vs the champion's test-period population (every open lead on every test day, the same kind of population as a scoring day), bins from the training deciles; calendar fields (month, weekday) excluded as seasonal | daily | PSI > 0.2 (watch at 0.1) |
| Missing-as-a-feature: missing share per feature vs training | daily | +10 percentage points |
| Edge cases: values outside the training range, rare combinations (e.g. quoted with no consultation) | daily | count above the 99th percentile of training days |
| Score drift: PSI of today's calibrated scores against the champion's scores on the reference population (deciles) | daily | PSI > 0.2 |
| Label drift: base rate of matured labels | weekly | ± 50 % relative |
| Performance on matured labels (8-week rolling PR-AUC, precision@15, ECE), measured on **holdout** leads too, which the model did not influence | weekly | PR-AUC −20 % relative to its test value, or ECE > 0.08 |

**Feedback loop** (Chapter 7): the list changes who gets called, which changes outcomes and the follow-up features. The holdout
group gives unbiased data for monitoring; the report discusses the effect on retraining.

**Retraining (continuous training).** `aisleco_weekly_train`, Sunday 03:00, and on any performance alert: build the training
set → tune → calibrate → evaluate → gate → register in MLflow. Promotion moves the `champion` alias; the previous champion keeps
the alias `previous` for one-step rollback (`python -m ml rollback`). Every run logs data version (pipeline run ID), code
version (git commit), seed, parameters, metrics, plots and the decision.

### 10. Model card and EDA report

Generated on each promotion into `docs/v2/model_card_lead_conversion.md`: intended use and users, not-intended uses (not a
reason to ignore any couple; the holdout and the standard queue keep everyone reachable), data and period, label, features,
metrics with intervals and slices, calibration, baselines, ablation, known limits, monitoring plan, PDPA notes (text masked,
names only in the Sales role).

EDA report (`docs/v2/ml_eda.md`, `python -m ml eda`): time to booking (decides the label window), class balance, feature
distributions by outcome, missing values, the point-in-time check, the TikTok shift in 2026.

---

## Model B: staffing demand forecast

### 11. Purpose, target and method

- **Decision.** For each role group (Planning = lead planners + planners; Wedding day = coordinators + event staff) and each week
  in the next 26: will required hours exceed capacity? Then book freelancers (5–12 weeks ahead is when freelancers can still be
  booked), stop selling a date, or steer couples to alternative dates.
- **Target.** Total staff-hours worked in the target week by that role group, under the hours-spread rule (Spec 03 §4.3), as
  known once all bookings for that week exist.
- **Forecast at date *t* for week *w*** = three parts (as built in M6, `ml/forecast.py`, decision C22):

| Part | Meaning | How |
|---|---|---|
| Booked hours *B(w)* | hours of weddings booked before *t* | the spread rule on their assignments (Gold `FactStaffWorkload`) |
| Pipeline hours *P(w)* | hours expected from today's open inquiries that name a date | Σ over open inquiries of the booking rate of their stage × channel, measured on inquiries decided before *t*, × the average hours profile of a wedding of that package by role group and weeks-before-wedding. Model A's probabilities are not used here: in a backtest they would leak the future (the model was trained on later data), and retraining Model A for every past origin is too costly; the shared link between the two problems is the date badge on the Sales list and the requested-week utilization feature. |
| Pickup *R(w)* | hours not yet in sight (late bookings, couples who have not written yet) | LightGBM quantile regression (10 / 50 / 90 %) of actual − B − P on weeks ahead, the target week's month, high season, Lent, auspicious weekend days, B, P, inquiries in the last 28 days and the same week last year |

- **Intervals.** The quantile models' 10–90 % band covered only 48 % of actuals in the backtest, so the published band is
  split-conformal: the P50 forecast times the 10th and 90th percentiles of the backtest's relative errors per horizon bucket
  (leave-one-origin-out coverage 79 %).
- **Capacity.** Σ standard weekly hours of the contracts valid in week *w* (known at *t*). Status per Spec 03 §6.

### 12. Backtest and selection

- Rolling origins: the first Monday of each month from 12 months back to 13 weeks back (9 origins, so each has at least a quarter of actuals); horizons 1–26 weeks; models of each origin trained only on target weeks over before it; actuals from the final data.
- Metrics by horizon bucket (1–4, 5–12, 13–26 weeks): WAPE (Σ|actual − forecast| ÷ Σ actual), bias (Σ(forecast − actual) ÷ Σ actual),
  coverage of the P10–P90 interval (target about 80 %), pinball loss.
- Baselines: (1) booked hours only; (2) seasonal naive: the same week last year × this year's bookings to date ÷ last year's at
  the same point; (3) pickup ratio: booked hours × the historical ratio of final to booked hours at that lead time.
- The method with the lowest WAPE at 5–12 weeks becomes production. If a baseline wins, it is used and the report says why (the
  v1 forecast had this honest outcome once).

---

## 13. Honest framing (for the report and the Q&A)

| Likely question | Answer |
|---|---|
| "The data is simulated. Does the model only learn your simulator?" | Yes, the drivers are assumptions (Spec 06), stated openly. The hidden readiness that drives behaviour is never stored or used; the model sees only what a CRM would record, with noise. What the project proves is the method: leakage-safe features, time-based validation, baselines, calibration, monitoring. On the real CRM the same pipeline learns the real drivers. |
| "Why not accuracy?" | 94 % accuracy is reached by predicting "no booking" for everyone. PR-AUC and precision in the top 15 measure what sales actually do. |
| "Why 30 days?" | It matches the action and gives fresh labels; the EDA curve shows the share of bookings it captures (§2). |
| "How do you know it adds value?" | Offline: bookings captured in the top 15 versus today's practice, with intervals. Online: the 10 % holdout. The THB estimate states its assumption. |
| "Why LightGBM?" | Tabular data, mixed types, missing values, non-linear effects (days since quote); tuned with time-series CV and compared with logistic regression, which wins ties. |
| "What if the data drifts?" | PSI, missing-share and performance checks, weekly retraining with a gate, rollback alias (§9). The 2026 TikTok shift in the simulation is the test case. |
