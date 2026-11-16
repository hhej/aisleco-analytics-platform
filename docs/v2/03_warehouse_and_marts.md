# Spec 03. Warehouse (Gold) and data marts

Part of the [v2 plan](PLAN.md). Answers: what the star schema is (grain, keys, history), how every KPI is calculated
(add, subtract or divide, and at which level), where the data marts live, and who may read them.

## 1. Database `aisleco_dw` (PostgreSQL 16)

| Schema | Holds | Written by | Read by |
|---|---|---|---|
| `stg` | Copy of Silver for the Gold build (truncated and reloaded each run) | `load_stg` (DuckDB → PostgreSQL `COPY`) | Gold SQL |
| `gold` | Star schema: 5 dimensions, 6 facts | Gold SQL (`pipeline/warehouse/sql/`) | marts, ML, data team |
| `mart_sales`, `mart_ops`, `mart_exec`, `mart_platform` | Role marts: views over Gold (§7) | `pipeline/marts/sql/` | the matching database role only |
| `sales_pii` | Couples' display names for the call list | warehouse load from `silver_restricted.customer_contact` | Sales role only |
| `feature` | Feature store (Spec 04 §3) | ML steps | ML |
| `ml` | Model runs, monitoring results | ML steps | `mart_platform`, data team |
| `etl` | Run log, steps, watermarks, file checkpoints, DQ rules and results, quarantine index, alerts | pipeline | `mart_platform` |
| `app` | Web-app users (bcrypt password hashes) and their roles | admin script | web API |

Why marts are schemas in the warehouse: in Kimball's dimensional warehouse a data mart is a subject area **inside** the
warehouse (Chapter 5, "the data mart becomes a logical distinction"). Separate stand-alone marts would copy data and drift.

## 2. Design rules

1. **Built from the KPIs.** Every table and column traces to a KPI in §6, a dashboard tile (Spec 05) or a model (Spec 04).
   Nothing is loaded "in case" (kept from Rev10).
2. **Grain first.** Every fact states its grain; all its measures are at that grain.
3. **Surrogate keys** (integer, meaningless) on every dimension; the source ID is kept for tracing; date keys are `YYYYMMDD`.
4. **No missing links.** Each dimension has an Unknown member (key −1); a stage not reached uses date key −1.
5. **Ratios are never stored.** Facts keep the parts (hours and capacity; quote and budget); marts divide (professor's review of v1).
6. **History.** SCD Type 2 where history changes a KPI (staff contracts); Type 1 elsewhere. Rev10's "frozen weeks" workaround is
   retired, because the contract history now comes from the CRM (Spec 01 §2.1).
7. **No personal data** in Gold. `CustomerID` is a pseudonymous key; names exist only in `sales_pii`.

## 3. Dimensions

| Dimension | Grain | Source (Silver) | Type | Key attributes | Role-playing uses |
|---|---|---|---|---|---|
| `DimDate` | calendar day 2023–2028 | `calendar_day` | static, refreshed from the calendar | FullDate, WeekStart, Month, Quarter, Year, IsWeekend, IsHighSeason, IsBuddhistLent, IsPublicHoliday, HolidayName, IsValentine, AuspiciousLevel ('None' if not auspicious) | inquiry, first reply, consultation, quote, closed, booking, requested wedding, wedding, cancellation, week start, score date, forecast date, target week |
| `DimEmployee` | employee **contract version** | `employee_contract` | **SCD Type 2** | EmployeeKey, EmployeeID, Nickname, RoleName, RoleGroup (Planning / Wedding-day / Sales / Office), EmploymentType, StandardWeeklyHours, MaxConcurrentEvents, ValidFrom, ValidTo (9999-12-31 if open), IsCurrent | staff on an assignment, first responder, sales owner, salesperson on an activity |
| `DimPackage` | package price version | `package` | Type 1 (each price version is already its own row in the CRM) | PackageID, PackageName, PackageFamily, PackageType, FeeType, BasePrice, FeeRate, EffectiveFrom, EffectiveTo | package of interest, package booked |
| `DimChannel` | marketing channel | `channel` | Type 1 | ChannelID, ChannelName, ChannelType (Online/Offline), Platform, TrackingCode | inquiry channel, booking channel |
| `DimInquiryOutcome` | combination of outcome labels (junk dimension, ~25 rows) | `inquiry` | Type 1, rows added when a new combination appears | Outcome (Open / Booked / Lost), FurthestStage (New / Contacted / Consulted / Quoted / Booked), ClosedBy (Sales / AutoRule / —), LostReason | — |

**SCD2 rule for `DimEmployee`.** One row per `EmployeeContract` row. A fact row that happens on day *d* gets the key of the
version with `ValidFrom ≤ d ≤ ValidTo`. Example (Appendix B.5 of Rev10): Nina moves from 44 to 36 hours on 1 Feb 2027.
`DimEmployee` holds two rows for Nina; workload in the week of 11 Jan joins the 44-hour row, the week of 8 Feb the 36-hour row.
No week is frozen: a late correction to an assignment flows into history, and rule C03b counts such late changes.

**Surrogate key management.** Keys are assigned by `MERGE` on the natural key (`EmployeeID + ValidFrom` for DimEmployee,
the source ID otherwise). A key never changes once issued; reloading the same data issues no new keys.

## 4. Facts

### 4.1 `FactInquiry`: accumulating snapshot, one row per inquiry
The row is created when the inquiry arrives and updated as it moves through the stages (Chapter 5: an accumulating snapshot
for a process with milestones).

| Column | Meaning | Rule |
|---|---|---|
| InquiryDateKey, FirstReplyDateKey, ConsultationDateKey, QuoteDateKey, ClosedDateKey, BookingDateKey | milestone dates | −1 until reached |
| RequestedEventDateKey | the wedding date the couple asked for | −1 if not given |
| ChannelKey, IndicativePackageKey, FirstResponderKey, SalesOwnerKey, OutcomeKey | context | role-playing DimEmployee for responder and owner |
| InquiryID, CustomerID | degenerate dimensions | |
| InquiryCount | 1 | additive |
| IsReplied, IsConsulted, IsQuoted, IsBooked, IsOpen, IsAutoClosed | 0/1 | additive (sums are counts) |
| ResponseMinutes | first staff reply − first customer message | non-additive: median |
| FirstMessageHour | 0–23 | dimension attribute stored on the fact (degenerate) |
| DaysToConsult, DaysToQuote, DaysToClose, DaysToBook | days between milestones | non-additive: median |
| LeadDays | requested wedding date − inquiry date | non-additive |
| QuotedAmount, BudgetMidpoint | THB | additive in sums; their ratio is computed in marts only |
| CustomerMessages, StaffMessages, FollowUps | counts | additive |
| IsHoldout | 1 if the lead is in the model's random holdout group (Spec 04 §8) | additive |

`IsBooked = 1` when an event was created from this inquiry, or from a later inquiry of the same `CustomerID` within 120 days
(a couple writing on LINE and on Facebook appears twice until sales merges them). Later cancellation does not change it; it is
counted by `FactEventBooking.IsCancelled`.

### 4.2 `FactEventBooking`: transaction, one row per booked wedding

| Column | Rule |
|---|---|
| BookingDateKey, EventDateKey, CancelledDateKey (−1) | dates |
| PackageKey, ChannelKey (the inquiry's channel; Walk-in if none) | context |
| EventID, InquiryID, CustomerID, ComplexityLevel, GuestBand (`< 250`, `250–449`, `450+`) | degenerate |
| GuestCount, AgreedFee (THB) | additive (guest count additive across weddings) |
| StaffCount, PlannedHoursTotal, PlanningHours, EventDayHours | from assignments, additive |
| BookingLeadDays | non-additive |
| IsCancelled | additive count |

### 4.3 `FactStaffWorkload`: one row per assignment per week (the hours spread over time)
Grain: **employee × wedding × week**. This is where staff hours are placed in the weeks they are worked.

**Hours-spread rule** (replaces Rev10's even spread; agreed operations profile, in one SQL file with its constants):
1. Wedding-day hours = min(12, assignment hours), all in the wedding week.
2. Planning hours = the rest, spread from the week the person was assigned (`assigned_at_effective`) to the week before
   the wedding week. Let *n* be the number of those weeks.
   - *n* ≤ 10: evenly, planning hours ÷ *n* per week.
   - *n* > 10: 20 % in the first 2 weeks (kick-off), 50 % in the last 8 weeks (final preparations), 30 % spread evenly over
     the *n* − 10 weeks in between.
3. Assigned in the wedding week or later: everything in the wedding week.
4. Cancelled wedding: weeks before the cancellation week keep their hours (the work was done); later weeks are dropped.

Worked example: Som, 80 h on Ploy and Ton's wedding (Sat 16 Jan 2027), assigned Thu 2 Apr 2026.
Wedding-day 12 h in the week of 11 Jan 2027. Planning 68 h over the weeks of 30 Mar 2026 … 4 Jan 2027: *n* = 41.
Kick-off weeks (30 Mar, 6 Apr): 68 × 20 % ÷ 2 = 6.80 h each. Last 8 weeks (16 Nov … 4 Jan): 68 × 50 % ÷ 8 = 4.25 h each.
The 31 weeks between: 68 × 30 % ÷ 31 = 0.658 h each. Check: 13.6 + 34.0 + 20.4 = 68.0 h.

| Column | Rule |
|---|---|
| WeekStartDateKey, EmployeeKey (the contract version valid on that Monday), EventDateKey | keys |
| EventID, AssignmentID, AssignmentRole | degenerate |
| PlanningHours, EventDayHours, TotalHours | additive |

### 4.4 `FactSalesActivity`: transaction, one row per logged follow-up
ActivityDateKey, SalespersonKey (DimEmployee), ChannelKey (of the inquiry), InquiryID, ActivityType, Outcome, FromCallList (0/1),
ListRank (the lead's rank on that day's list, null if not listed), IsHoldout. Measures: ActivityCount (1), additive.

### 4.5 `FactLeadScore`: periodic snapshot, one row per open inquiry per score date (written by the model, Spec 04 §7)
ScoreDateKey, InquiryID, ChannelKey, SalesOwnerKey, ModelVersion, Probability (non-additive; but the sum of probabilities is the
expected number of bookings, which is additive), ExpectedFee (THB, = Probability × expected fee, additive as an expectation),
Rank (within the owner's list), Band (High / Medium / Low), IsHoldout, Reason1–3 (plain-language text), NextBestAction (text).

### 4.6 `FactDemandForecast`: periodic snapshot, one row per forecast date × target week × role group (Spec 04 §11)
ForecastDateKey, TargetWeekDateKey, RoleGroup, BookedHours, PipelineHours, NewBookingHours, P10Hours, P50Hours, P90Hours,
CapacityHours, ModelVersion. All hours additive across role groups; never across forecast dates.

## 5. Bus matrix (conformance, Chapter 5)

| Fact (process) | Date | Employee | Package | Channel | Inquiry outcome | Degenerate |
|---|---|---|---|---|---|---|
| FactInquiry (inquiry pipeline) | ✓ (7 roles) | ✓ (responder, owner) | ✓ (interest) | ✓ | ✓ | InquiryID, CustomerID |
| FactEventBooking (bookings) | ✓ (4 roles) | | ✓ (booked) | ✓ | | EventID, InquiryID, CustomerID |
| FactStaffWorkload (staff hours) | ✓ (week, wedding) | ✓ (staff) | | | | EventID, AssignmentID |
| FactSalesActivity (follow-ups) | ✓ | ✓ (salesperson) | | ✓ | | InquiryID |
| FactLeadScore (model scores) | ✓ | ✓ (owner) | | ✓ | | InquiryID |
| FactDemandForecast (forecast) | ✓ (forecast, target week) | role group | | | | |

Questions that span two processes are answered by **drilling across** (Chapter 5): each fact is summarised to the shared grain
first (e.g. by week), then the summaries are joined. Two facts are never joined row to row (no double counting).
Example: the date checker (Spec 05) sums `FactStaffWorkload` by week and, separately, counts `FactInquiry` requests by week,
then joins the two weekly results.

## 6. KPI catalog: every formula

Read: **level** = where the division happens. "Ratio of sums" means: sum numerator and denominator over the whole group,
then divide once. Averages of ratios are never used. Each KPI is a mart view column (§7) and an entry in `catalog/kpis.yml`.

### Problem 1: seasonal demand and capacity

| KPI | Formula | Level | Notes |
|---|---|---|---|
| Assigned hours | Σ `FactStaffWorkload.TotalHours` | employee × week | additive |
| Capacity hours | `DimEmployee.StandardWeeklyHours` of the version valid on the week's Monday; 0 if no contract | employee × week | from SCD2 |
| Utilization | Assigned hours ÷ Capacity hours | employee × week; team or role = Σ assigned ÷ Σ capacity | ratio of sums |
| Overtime hours | Σ over employee-weeks of max(0, assigned − capacity) | computed per employee-week, then summed | a team's spare hours never cancel one person's overtime |
| Over-capacity person-weeks | count of employee-weeks with utilization > 100 % | | |
| Concurrent weddings | count of distinct EventID with hours in the week | employee × week | compared with MaxConcurrentEvents |
| Peak-date acceptance rate | inquiries with a requested date in Nov–Feb or on an auspicious date that booked ÷ those of them that are decided (not open) | by requested month | Rev10 definition, decided only |
| Turned-away demand | count of inquiries lost with reason "Date unavailable" | by requested month and auspicious level | |
| Fee at stake (turned away) | turned-away count × median fee of booked weddings of the same package family in the last 12 months × booking rate of quoted leads | by requested month | labelled "estimate" |
| Staff hours per wedding | Σ PlannedHoursTotal ÷ count of weddings | by package family, guest band, complexity | input for pricing and staffing |
| Forecast hours, capacity status | Spec 04 §11; status Over ≥ 100 %, Tight 85–100 %, OK 50–85 %, Quiet < 50 % of capacity | role group × week | thresholds in `catalog/kpis.yml` |

### Problem 2: inquiry and conversion

| KPI | Formula | Level | Notes |
|---|---|---|---|
| Inquiries | Σ InquiryCount | any | additive |
| Conversion rate (cohort) | Σ IsBooked ÷ (Σ InquiryCount − Σ IsOpen) for inquiries **received** in the period | by inquiry month, channel, package of interest | open leads excluded until decided |
| Stage reach | consult rate = Σ IsConsulted ÷ Σ InquiryCount; quote rate = Σ IsQuoted ÷ Σ IsConsulted; close rate = Σ IsBooked ÷ Σ IsQuoted | funnel | each step on the previous step |
| First-reply time | median of ResponseMinutes (bot excluded); share in each band | by channel, hour of arrival, responder | median, never mean |
| Reply-speed effect | conversion rate by response band | | the evening-shift question |
| Sales-cycle days | median DaysToBook | booked only | |
| Lost-reason share | lost with reason *r* ÷ all lost | sales-closed and auto-closed shown separately | |
| No-reply (ghosting) rate | Σ IsAutoClosed ÷ decided | by inquiry month | |
| Quote share of budget | QuotedAmount ÷ BudgetMidpoint per inquiry, then the median per group | by outcome (booked vs lost on price) | per-row ratio is valid here: the question is about each couple's share |
| Follow-up compliance | follow-ups done on listed leads ÷ leads listed | salesperson × day | from FactSalesActivity and FactLeadScore |
| Pipeline value | Σ ExpectedFee of today's open leads | today | expectation, labelled as such |
| Model lift (A/B) | conversion of prioritised leads − conversion of holdout leads, 95 % bootstrap interval | since model launch | Spec 04 §8 |

### Executive

| KPI | Formula |
|---|---|
| Bookings, fee revenue | count and Σ AgreedFee of non-cancelled bookings by booking month; vs the same month last year |
| Wedding mix | weddings by wedding month (seasonality) |
| Utilization (ops roles) | ratio of sums over Planning + Wedding-day role groups, by quarter |

## 7. Marts and access control

Each mart is a set of views named after the page tile they feed (`v_<page>_<tile>`). Dashboards read marts only.

| Mart | Views (main) | Database role | Also granted |
|---|---|---|---|
| `mart_sales` | `v_today_list` (today's scored open leads with reasons, next best action, requested-date capacity badge), `v_new_today`, `v_going_cold`, `v_my_week`, `v_funnel`, `v_conversion_by_channel_month`, `v_response_by_hour`, `v_lost_reasons`, `v_quote_share`, `v_followup_compliance`, `v_ab_result` | `aisleco_sales` | `sales_pii` (SELECT) |
| `mart_ops` | `v_capacity_grid` (employee × week: assigned, capacity, utilization, overtime, concurrent), `v_week_detail` (which weddings make up a person's week), `v_forward_demand` (forecast by role group × week), `v_date_check` (per date: weddings booked, role headroom, auspicious level, alternatives), `v_peak_acceptance`, `v_turned_away`, `v_hours_per_wedding` | `aisleco_ops` | — |
| `mart_exec` | `v_kpi_tiles` (with targets and previous period), `v_bookings_month`, `v_conversion_trend`, `v_channel_mix`, `v_capacity_outlook`, `v_lost_reasons`, `v_model_value` | `aisleco_exec` | `mart_ops`, and the non-personal views of `mart_sales` |
| `mart_platform` | `v_runs`, `v_step_times`, `v_dq_results`, `v_quarantine`, `v_freshness`, `v_model_performance`, `v_drift`, `v_registry` | `aisleco_data` | everything (data team) |

Rules enforced by PostgreSQL, not only by the web app:
- each role has `USAGE` on its schemas and `SELECT` on their views, nothing else; no role can read `gold`, `stg` or `etl` directly
  except `aisleco_data`;
- `sales_pii` is readable only by `aisleco_sales` and `aisleco_data`;
- the web API connects with the logged-in user's role, so a Sales login cannot read `mart_ops` even through a bug in the API
  (a test proves the refusal);
- a salesperson's list is filtered to their own leads by the API; the sales manager sees all.

## 8. Reconciliation and source-to-target checks

| ID | Check | Severity |
|---|---|---|
| C02 | every Silver inquiry and event has exactly one Gold fact row, and no extra rows | block |
| C03 | Σ FactStaffWorkload.TotalHours = Σ assignment hours − hours dropped after cancellations (spreading neither creates nor loses hours) | block |
| C03b | count of past-week hours that changed since the previous run (late corrections) | warn |
| C04 | Silver holds exactly the CRM's latest key list | block |
| C05 | mart totals equal Gold totals (bookings by month, inquiries by channel, hours by week) | block |
| Source-to-target (test) | every KPI in §6 recomputed in Python from the simulator's ground truth equals the mart value | test, 0 differences |

## 9. Worked example: Mint and Jay (Rev10 B.3), source → dashboard

IDs and headroom figures below are illustrative; M3 replaces them with the values the simulated data produces.

| Step | Where | What |
|---|---|---|
| 1 | CRM `Inquiry` 18877 | created 13:02 from the LINE webhook; channel TikTok; requested 13 Feb 2027; budget "1M-1.5M"; owner May |
| 2 | Bronze, Silver | Spec 02 §6: one message, response 7 minutes, intents ask_availability + mention_date + mention_guests |
| 3 | CRM, next day | May closes it: Lost, "Date unavailable", ClosedBy Sales |
| 4 | Gold `FactInquiry` | RequestedEventDateKey 20270213 (high season, auspicious High); OutcomeKey → (Lost, Contacted, Sales, Date unavailable); ResponseMinutes 7 |
| 5 | `mart_ops.v_turned_away` | +1 in February 2027, auspicious High |
| 6 | `mart_ops.v_date_check` for 13 Feb 2027 | 2 weddings booked; coordinator headroom −4 h; suggests Sat 20 Feb (High, 1 wedding, headroom +18 h) |
| 7 | Operations page | the turned-away bar for Feb 2027 shows her; the date checker shows the same suggestion May gave |
