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
| Target | books within the next 30 days (0/1) | staff-hours required in the target week |
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
  of the next 30 days' bookings the top 15 contain (§6), online by an A/B holdout (§8).

### 2. Prediction design

| Item | Definition |
|---|---|
| Score time *T* | 02:00 each day, after the pipeline; features use only data with timestamps before 00:00 of the score date |
| Population | inquiries open at *T*: received before *T*, not closed (`ClosedDate` null or ≥ *T*) |
| Label *y* | 1 if a booking (`Event.CreatedDate`) is made from this inquiry, or from another inquiry of the same `CustomerID`, in [*T*, *T* + 30 days); else 0. Later cancellation does not change the label. |
| Why 30 days | it matches the action (a follow-up acts within weeks) and gives labels after 30 days, so monitoring and retraining stay fresh. **Data check in M5:** if fewer than 80 % of bookings happen within 30 days of a randomly chosen open day, the window becomes 45 days; the EDA report shows the curve. |
| Censoring | rows whose 30-day window is not over at the training cut-off are excluded from training (their label is unknown) |
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

- **Test**: score dates in [*C* − 120 d, *C* − 30 d). Never used for any choice; reported once.
- **Purge**: 30 days between train and test, so no training label window overlaps the test period; any inquiry that has rows in
  the test period is removed from training (no inquiry on both sides).
- **Rolling-origin cross-validation** inside the training period: 5 folds; each validates on a 2-month block of score dates and
  trains on everything before the block minus a 30-day purge; folds are grouped by inquiry.

**Candidates.** Logistic regression (one-hot, standardised, L2, `C` tuned) and LightGBM (Optuna TPE, 60 trials; `num_leaves`
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
| Bookings captured | share of the next 30 days' bookings that were in that day's top 15 |
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

**Ablation table** (report): PR-AUC as feature groups are added: profile → + engagement → + stage → + rule intents → + TF-IDF →
+ sales activity → + capacity. Shows what each data source contributes (also evidence for the Data Sources dimension).

**Gate** (in code, logged in MLflow; a candidate becomes `champion` only if all hold):
1. Paired bootstrap of PR-AUC (candidate − best baseline): lower 95 % bound > 0.
2. ECE ≤ 0.05 after calibration.
3. No slice with at least 100 test rows where the candidate's PR-AUC is more than 0.05 below B0's.
4. Against the current champion: compared on test inquiries the champion never trained on (v1 rule, at least 50 such
   inquiries); the candidate must not be worse. With fewer than 50, the champion stays.

### 7. Serving: the daily list

1. After the pipeline, `ml_score` loads `models:/lead_conversion@champion` from MLflow and scores `feature.v_lead_features_latest`.
2. **Expected fee**: Full Planning = fee rate × budget midpoint; Partial and Day-of = package base price; unknown package =
   median fee of the last 12 months. Expected fee of the lead = probability × fee.
3. **Rank** within each owner's list by expected fee (the page can switch to probability).
4. **Band**: High ≥ 25 %, Medium 10–25 %, Low < 10 % (checked in M5 so that High is roughly the top 15 %).
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
- **Effect** = 30-day booking rate of prioritised leads − that of holdout leads, with a 95 % bootstrap interval
  (`mart_sales.v_ab_result`, Executive "model value" tile).
- **Power analysis** (report): with a base 30-day booking rate of about 6 % and a 10 % holdout, the weeks of traffic needed
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
| Input drift: PSI per feature, today's scored rows vs the training distribution (10 bins, stored with the model) | daily | PSI > 0.2 (watch at 0.1) |
| Missing-as-a-feature: missing share per feature vs training | daily | +10 percentage points |
| Edge cases: values outside the training range, rare combinations (e.g. quoted with no consultation) | daily | count above the 99th percentile of training days |
| Score drift: distribution of probabilities and share of High | daily | PSI > 0.2 |
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

EDA report (`ml/reports/eda.html`, M5): time from inquiry to booking (supports the 30-day window), class balance, feature
distributions by outcome, missing values, the point-in-time check, the TikTok shift in 2026.

---

## Model B: staffing demand forecast

### 11. Purpose, target and method

- **Decision.** For each role group (Planning = lead planners + planners; Wedding day = coordinators + event staff) and each week
  in the next 26: will required hours exceed capacity? Then book freelancers (5–12 weeks ahead is when freelancers can still be
  booked), stop selling a date, or steer couples to alternative dates.
- **Target.** Total staff-hours worked in the target week by that role group, under the hours-spread rule (Spec 03 §4.3), as
  known once all bookings for that week exist.
- **Forecast at date *t* for week *w*** = three parts:

| Part | Meaning | How |
|---|---|---|
| Booked hours *B(w)* | hours of weddings already booked | the spread rule on known assignments; a booked wedding without assignments yet uses the standard hours of its package, guest band and complexity |
| Pipeline hours *P(w)* | hours of weddings expected from today's open inquiries | Σ over open inquiries of Model A's probability × the standard hours of a wedding on the requested date (inquiries without a date are spread by the historical lead-time distribution) |
| New-booking hours *N(w)* | hours of weddings booked later than 30 days from *t* (leads that have not arrived yet, or will decide late) | Poisson regression of the number of such weddings per target month on weeks ahead, the target month's season, Lent, the count of auspicious weekend days, inquiries in the last 28 days, and the same month last year; then spread to weeks by the historical weekday and auspicious mix, × standard hours. LightGBM with a Poisson objective is the challenger. |

- **Intervals.** 1,000 Monte Carlo draws: each open lead books with its probability; new weddings ~ Poisson; hours per wedding
  drawn from history by package. P10, P50 and P90 per role group and week.
- **Capacity.** Σ standard weekly hours of the contracts valid in week *w* (known at *t*). Status per Spec 03 §6.

### 12. Backtest and selection

- Rolling origins: every Monday of the last 12 months; horizons 1–26 weeks; actuals from the final data.
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
