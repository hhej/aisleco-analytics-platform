# Spec 06. The simulated business

Part of the [v2 plan](PLAN.md). Answers: where the data comes from when there is no real company, how the simulation
behaves like one, which assumptions drive it, and which data problems it injects on purpose to test the pipeline.

## 1. Purpose and honesty

Aisle & Co. is fictional, so a simulator plays every outside actor: couples, salespeople, planners, LINE and Meta servers,
fair tablets and marketing. It writes only through the same doors a real company would use: rows into the CRM database,
signed HTTP webhooks to the gateway, JSON files into the tablet drop folder, a CSV into the external drop folder.
The pipeline cannot tell it from a real company, and it never reads the simulator's internals.

The behaviour rules below are **assumptions** chosen to resemble how wedding sales and operations work in Bangkok. They are not
measurements. The report says so in the same words. What the project proves is that the platform and the methods work;
with a real CRM the same pipeline learns the real behaviour.

Ground truth (each couple's hidden readiness, the intended outcome of every rule) is written to `var/sim_truth/` only, which
the pipeline has no path to. Tests use it for source-to-target checks and to prove the model never sees it.

## 2. Clock and modes

| Mode | Command | What it does |
|---|---|---|
| Backfill | `python -m sim backfill --from 2024-01-01 --to yesterday --seed 11` | simulates every day in order; posts webhooks with `X-Sim-Clock` so they land in the right Bronze partitions; about 3 years in a few minutes |
| Day | `python -m sim day --date D` (DAG `aisleco_sim_day`, 01:00) | simulates one more day; from model launch on, salespeople work the call list from `mart_sales` |
| Live message | demo panel or `python -m sim message --new-couple` | one LINE message now, real clock |
| Report | `python -m sim report` | the calibration numbers of §8 |

`--scale N` multiplies couples and staff by N (stress tests). The same seed gives the same data.

## 3. The company

| Item | Setting |
|---|---|
| Staff (ops roles) | 2 lead planners (Som, Nina), 3 planners, 2 coordinators (Beam, Ploy), 3 event staff (Arm, Golf, Mook): 10 people, 36–44 standard hours each, about 420 hours a week |
| Sales | 2 salespeople (May, Ken) and a sales manager (Nok); about 15 follow-ups a day each |
| Office | marketing and finance (not assigned to weddings) |
| Contract history | Nina 44 → 36 h from 1 Feb 2026; Ploy hired 1 Jun 2024; freelance coordinator Fah 20 h/week for Dec–Feb peaks each season; one planner leaves in Aug 2025 and is replaced in Oct 2025 |
| Packages | Full Planning (12.5 % of budget, 13.5 % from 1 Jul 2026), Partial Planning ฿90,000, Day-of Coordination ฿45,000 (฿52,000 from 1 Jul 2026), Custom |
| Hours per wedding (by role, before noise) | Full: lead planner 80, planner 40, coordinator 24, 2 event staff × 12. Partial: planner 45, coordinator 20, event staff 12. Day-of: coordinator 20, 2 event staff × 12. +25 % above 400 guests, +30 % for high complexity, ±15 % noise |
| Fairs | 5 a year (late Jan, mid Mar, early Jun, late Aug, mid Oct), Friday–Sunday |

Capacity is about 420 h a week against an average demand of about 330 h, so average utilization is near 78 %, with peaks above
110 % in December–February and lows near 40 % in Buddhist Lent: the shape Problem 1 describes.

## 4. Demand

- **Inquiries:** 1,500 a year on average, Poisson per day. Weekly seasonality (more on weekends and evenings); yearly seasonality
  peaking January–April (after proposal season, booking the next high season); fair days × 4.
- **Channels:** 2024–25: LINE 30 %, Facebook 20 %, Instagram 15 %, TikTok 10 %, Website 8 %, Fair 10 %, Referral 5 %, Walk-in 2 %.
  From January 2026 a TikTok campaign makes TikTok a third of online inquiries. TikTok couples are less ready to book, so 2026
  conversion falls: the drift the monitoring must catch (kept from v1).
- **Requested wedding date:** given by ready couples more often; 4–14 months ahead; 60 % in November–February; strongly
  preferring auspicious dates and weekends; almost never in Buddhist Lent.
- **Budget band and package of interest:** drawn per channel (referrals and walk-ins skew to higher budgets and Full Planning).

## 5. Behaviour (daily, per open inquiry)

Each couple has a hidden readiness *z* ~ Normal(μ_channel, 1) (μ: Referral +0.6, Walk-in +0.3, LINE +0.2, Facebook/Instagram 0,
Website −0.2, Fair −0.3, TikTok −0.8). It is never stored outside `var/sim_truth/`. Probabilities are logistic, σ(x) = 1/(1 + e^(−x)).
Each simulated day, each open inquiry may move one step:

| Event (per day) | Daily probability (logit *x*) | Effect on the data |
|---|---|---|
| Couple writes (messages, intents) | −1.6 + 0.8z + 0.9·(we wrote to them yesterday) − 0.08·(days since their last message) | `ChatMessage` + webhook with text; intents drawn by *z* and stage (ready couples ask about dates and meetings; price shoppers ask price and go quiet; `price_objection` before a price loss) |
| First staff reply | inbox staffed 09:00–20:00; reply delay log-normal (median 25 min in hours; next morning otherwise); 6 % typed on a phone and not logged | outbound log and `ChatMessage` (or nothing, if on the phone) |
| Consultation booked | −3.4 + 1.1z + 0.5·(first reply < 1 h) − 0.7·(first reply > 24 h) + 0.4·(date given) + 0.5·(reached by a follow-up in the last 3 days) | `Inquiry.ConsultationDate` |
| Quote sent (after consultation) | −1.2 + 0.6z, from 1 day after the consultation | `QuotedDate`, `QuotedAmount` (Full: budget × 0.9–1.35 × fee rate) |
| Booking (after a quote) | −3.0 + 1.2z + 0.5·Referral − 0.6·(peak or auspicious date) − 0.8·(quote > 16 % of budget mid) − 0.5·(wedding < 150 days away) + **0.6·(reached by a follow-up in the last 3 days)** | `Event`, assignments within 0–14 days, inquiry Converted |
| Sales closes as lost | reason drawn from the couple's state: "Date unavailable" when the requested week is above 100 % capacity and the couple is not flexible (60 % are not); "Price" after `price_objection`; "Chose competitor", "Scope mismatch", "Budget changed" otherwise | `Status = Lost`, `ClosedBy = Sales` |
| Auto-close | 30 days after the couple's last message, if not booked | `Lost`, "No reply", `ClosedBy = AutoRule` |

The **follow-up effect** (bold) is the lever the model can pull: a reached follow-up helps a couple who is undecided, and helps
little a couple who would book anyway or never will (the logistic curve is flat at both ends). It is the parameter the offline
value estimate of Spec 04 §8 names as its assumption.

**Salespeople.** Each contacts up to 15 of their open leads a day; 55 % of attempts reach the couple. Before the model launches:
newest customer message first, with some randomness (today's practice). After launch: leads on the ranked list in order, then the
holdout's standard queue newest first, within the same 15. Every attempt is a `SalesActivity` row with `Source` and `ListDate`.

**Operations.** On booking, operations assigns staff of the right roles; for each role it picks the person with the fewest hours
in the wedding week (sometimes nobody has room, and a week goes over 100 %). 4 % of bookings are cancelled at a random time before
the wedding. Contract changes and freelancers follow §3.

**Couples writing on two platforms.** 3 % of couples also write from a second platform, creating a second customer and inquiry;
sales merges them at the consultation (the second inquiry is closed, its customer re-pointed).

## 6. Chat text

Phrase bank `sim/phrases/*.yml`: 15–25 Thai and English templates per intent, with slots (dates in Thai and English formats,
guest numbers, budgets, venues, package names), joined as greeting + 1–3 intents + closing. Language mix: 75 % Thai, 15 % mixed,
10 % English. Typos, emoji and stickers in a share of messages; a few messages contain a phone number or LINE ID (to test masking).
Staff replies use their own templates. Optional (M10): enlarge the bank with the local Ollama model, reviewed before use.

## 7. Injected data problems (each must be caught, and each has a test)

| Problem | Rate | Expected handling |
|---|---|---|
| Re-delivered webhooks | 2 % of events | R22: dropped in Silver, counted |
| Bad signature | 3 a month | gateway 401, `_rejected/` |
| Malformed JSON body | 1 a month | R20: quarantine |
| LINE adds a new field to message events (from 1 Sep 2026) | all events after | S05: warning, Bronze keeps it, Silver unaffected |
| Staff reply typed on the phone (not logged) | 6 % of first replies | response time measured to the next logged reply (known limit, Rev10) |
| Budget typed as free text ("around 600k", "6 แสน") | 8 % of inquiries | R05: mapped by regex; unreadable → `Unknown` |
| Staff message without employee | 0.5 % | R07: quarantine |
| Assignment hours typo (999) corrected a week later | 1 a year | R10: quarantine, then resolved on correction |
| Event with 0 guests corrected next day; its assignments become orphans | 1 a year | R08 and R10 (children follow parent), resolved together |
| Assignment dated after the wedding | 1 % | R11: warning; all hours in the wedding week |
| Hard-deleted spam inquiry | 0.5 % | key list removes it from Silver and Gold |
| Late commit (row written with a timestamp minutes before the previous snapshot) | a few per month | the 5-minute overlap picks it up |
| Duplicate tablet batch | 5 % of batches | R31: dropped |
| Truncated tablet file | 1 a year | R30: quarantine |
| Tablet sign-up without phone | 3 % | R33: warning |
| Hours added to an assignment after its weeks are over | 2 % of assignments | C03b: late-change warning, history updated |
| ICS download fails | 1 time (simulated network error) | R40: warning, last good fetch used |
| Auspicious file row with level "VeryHigh" | 1 row | R41: row quarantined |

## 8. Calibration targets (`python -m sim report`)

| Measure | Target | Source in the data |
|---|---|---|
| Inquiries per full year | 1,350–1,650 | CRM |
| Weddings held per year | 90–115 | CRM |
| Conversion (decided inquiries) | 6–8 % (2026 lower than 2024–25) | CRM |
| Weddings in Nov–Feb | ≥ 55 % | CRM |
| Weddings in Buddhist Lent | ≤ 5 % | CRM + calendar |
| Median first-reply time (chat leads) | 20–60 min | messages |
| No-reply share of lost inquiries | 50–65 % | CRM |
| Lost "Date unavailable" | 3–8 % of lost | CRM |
| Ops utilization: yearly / peak month / Lent month | ~75–80 % / > 105 % / < 50 % | Gold (checked in M3) |
| Chat events per inquiry | 12–18 | Bronze |
| TikTok share of online inquiries 2024–25 / 2026 | ~10 % / ~33 % | CRM |

M1 adjusts the intercepts of §5 until every measure is inside its range, and records the final values in this spec.
