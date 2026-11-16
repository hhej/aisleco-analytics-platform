-- =====================================================================
-- DW v7: KPI views (Gold -> Consumption). One view per question in Section 2.1.
-- Non-additive measures are recomputed from their parts, never summed. Ratios are not
-- stored in the facts at all: utilization and quote share of budget are computed here.
-- =====================================================================
SET client_min_messages = warning;
CREATE SCHEMA IF NOT EXISTS mart;

-- Base inquiry view: the fact with its outcome labels, the open / lost flags and the
-- quote share of budget (QuotedAmount / BudgetMidpoint; read it as a median or average).
CREATE OR REPLACE VIEW mart.v_inquiry AS
SELECT f.*, o."Outcome", o."FurthestStage", o."ClosedBy", o."LostReason",
       (o."Outcome" = 'Lost')::int AS "IsLost",
       (o."Outcome" = 'Open')::int AS "IsOpen",
       round(f."QuotedAmount" / nullif(f."BudgetMidpoint",0), 4) AS "QuoteShareOfBudget"
FROM fact."FactInquiry" f JOIN dim."DimInquiryOutcome" o USING ("OutcomeKey");

-- ---------- Problem 1: seasonal demand and capacity ----------

-- Weekly workload per person, readable (ops meeting)
CREATE OR REPLACE VIEW mart.v_weekly_workload AS
SELECT d."FullDate" AS week_start, e."FullName" AS employee, e."RoleName" AS role, e."EmploymentType" AS employment,
       w."PlanningHours", w."EventDayHours", w."TotalAssignedHours", w."StandardWeeklyHours",
       round(w."TotalAssignedHours" / nullif(w."StandardWeeklyHours",0), 4) AS "UtilizationRate",
       w."OvertimeHours", w."ActiveEvents", w."MaxConcurrentEvents", w."IsOverCapacity",
       (w."TotalAssignedHours" > w."StandardWeeklyHours") AS is_over_100pct
FROM fact."FactEmployeeWeeklyWorkload" w
JOIN dim."DimDate" d ON d."DateKey" = w."WeekStartDateKey"
JOIN dim."DimEmployee" e ON e."EmployeeKey" = w."EmployeeKey";

-- Peak-period acceptance: inquiries asking for a Nov-Feb or auspicious date
CREATE OR REPLACE VIEW mart.v_peak_acceptance AS
SELECT count(*) AS peak_inquiries,
       sum(f."IsConverted") AS booked_not_cancelled,
       round(sum(f."IsConverted")::numeric / nullif(count(*),0), 3) AS acceptance_rate,
       count(*) FILTER (WHERE f."LostReason" = 'Date unavailable') AS lost_date_unavailable
FROM mart.v_inquiry f JOIN dim."DimDate" d ON d."DateKey" = f."RequestedEventDateKey"
WHERE d."IsHighSeason" = 'Y' OR d."IsAuspicious" = 'Y';

-- Turned-away demand: lost because the date was unavailable, by requested month and auspicious level
CREATE OR REPLACE VIEW mart.v_turned_away_demand AS
SELECT d."YearMonth" AS requested_month, coalesce(d."AuspiciousLevel", 'Not auspicious') AS auspicious_level,
       count(*) AS inquiries_turned_away
FROM mart.v_inquiry f JOIN dim."DimDate" d ON d."DateKey" = f."RequestedEventDateKey"
WHERE f."LostReason" = 'Date unavailable'
GROUP BY 1, 2;

-- Staff hours per wedding by package, guest band and complexity
CREATE OR REPLACE VIEW mart.v_staff_hours_per_wedding AS
SELECT p."PackageFamily", b."ComplexityLevel",
       CASE WHEN b."GuestCount" < 250 THEN '<250' WHEN b."GuestCount" < 450 THEN '250-449' ELSE '450+' END AS guest_band,
       count(*) AS weddings,
       round(avg(b."TotalStaffHours"), 1) AS avg_total_hours,
       round(avg(b."PlanningHours"), 1) AS avg_planning_hours,
       round(avg(b."EventDayHours"), 1) AS avg_event_day_hours
FROM fact."FactEventBooking" b JOIN dim."DimPackage" p USING ("PackageKey")
WHERE b."IsCancelled" = 0
GROUP BY 1, 2, 3;

-- ---------- Problem 2: inquiry and conversion ----------

CREATE OR REPLACE VIEW mart.v_funnel_by_channel AS
SELECT "ChannelName", sum("InquiryCount") AS inquiries, sum("IsConsulted") AS consulted, sum("IsQuoted") AS quoted,
       sum("IsConverted") AS booked, sum("IsOpen") AS still_open,
       round(sum("IsConverted")::numeric / nullif(sum("InquiryCount") - sum("IsOpen"),0), 3) AS conversion_rate_decided
FROM mart.v_inquiry GROUP BY 1;

CREATE OR REPLACE VIEW mart.v_conversion_by_package AS
SELECT p."PackageFamily", sum(f."InquiryCount") AS inquiries, sum(f."IsConverted") AS booked,
       round(sum(f."IsConverted")::numeric / nullif(sum(f."InquiryCount"),0), 3) AS conversion_rate
FROM mart.v_inquiry f JOIN dim."DimPackage" p ON p."PackageKey" = f."IndicativePackageKey"
GROUP BY 1;

CREATE OR REPLACE VIEW mart.v_response_vs_conversion AS
SELECT "ResponseTimeBand", count(*) AS inquiries,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "ResponseTimeMinutes") AS median_minutes,
       round(avg("IsConsulted"), 3) AS consult_rate, round(avg("IsConverted"), 3) AS conversion_rate
FROM mart.v_inquiry GROUP BY 1;

CREATE OR REPLACE VIEW mart.v_lost_reasons AS
SELECT "ClosedBy", "LostReason", count(*) AS lost,
       round(count(*)::numeric / sum(count(*)) OVER (), 3) AS share_of_all_lost
FROM mart.v_inquiry WHERE "IsLost" = 1 GROUP BY 1, 2;

CREATE OR REPLACE VIEW mart.v_quote_share AS
SELECT CASE WHEN "IsConverted" = 1 THEN 'Booked' WHEN "LostReason" = 'Price' THEN 'Lost on price' ELSE 'Other' END AS outcome,
       count(*) AS quoted_inquiries,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "QuoteShareOfBudget") AS median_quote_share,
       round(avg("QuoteShareOfBudget"), 3) AS mean_quote_share
FROM mart.v_inquiry WHERE "IsQuoted" = 1 GROUP BY 1;

CREATE OR REPLACE VIEW mart.v_stage_dropoff AS
SELECT sum("InquiryCount") AS inquiries, sum("IsConsulted") AS consulted, sum("IsQuoted") AS quoted, sum("IsConverted") AS booked,
       round(avg("DaysToConsultation"), 1) AS avg_days_to_consultation,
       round(avg("DaysConsultationToQuote"), 1) AS avg_days_consultation_to_quote,
       round(avg("DaysQuoteToBooking"), 1) AS avg_days_quote_to_booking,
       round(avg("DaysToConvert"), 1) AS avg_days_to_convert
FROM mart.v_inquiry;

-- "No reply" (ghosting) rate: share of decided inquiries the 30-day silence rule closed
CREATE OR REPLACE VIEW mart.v_no_reply_rate AS
SELECT d."YearMonth" AS inquiry_month, f."ChannelName", count(*) FILTER (WHERE f."IsOpen" = 0) AS decided,
       count(*) FILTER (WHERE f."LostReason" = 'No reply') AS no_reply,
       round(count(*) FILTER (WHERE f."LostReason" = 'No reply')::numeric
             / nullif(count(*) FILTER (WHERE f."IsOpen" = 0), 0), 3) AS no_reply_rate
FROM mart.v_inquiry f JOIN dim."DimDate" d ON d."DateKey" = f."InquiryDateKey"
GROUP BY 1, 2;

-- ---------- Conversion model (feature store + registry, sql/ml_feature_store.sql) ----------

-- Sales follow-up list: open inquiries on the latest scoring day, most likely to book first.
-- ExpectedValue = chance to book x budget midpoint: a big wedding at 30% can be worth more than a small one at 40%.
CREATE OR REPLACE VIEW mart.v_followup_list AS
SELECT s."AsOfDate", s."InquiryID", s."Band", s."Score", f."Channel", f."BudgetRange", f."PackageType",
       f."AgeDays", f."HoursSilent", f."CoupleMsgs", f."Consulted", f."Quoted",
       s."Version", fi."BudgetMidpoint", round(s."Score" * fi."BudgetMidpoint") AS "ExpectedValue"
FROM ml."InquiryScore" s
JOIN feature."InquiryFeatures" f ON f."InquiryID" = s."InquiryID" AND f."AsOf"::date = s."AsOfDate"
LEFT JOIN fact."FactInquiry" fi ON fi."InquiryID" = s."InquiryID"
WHERE s."AsOfDate" = (SELECT max("AsOfDate") FROM ml."InquiryScore");

-- Model health: each production model's test metrics and its latest monitoring checks
CREATE OR REPLACE VIEW mart.v_model_health AS
SELECT v."Version", v."Algorithm", v."AsOfDate" AS trained_on_data_to, v."TrainedAt",
       (v."Metrics" -> 'chosen' ->> 'auc')::numeric AS test_auc,
       (v."Metrics" -> 'baseline' ->> 'auc')::numeric AS baseline_auc,
       d."AsOfDate" AS checked_on, d."Check", d."Value", d."Status", d."Detail", v."Model",
       (v."Metrics" -> 'baselines' -> 'rule' ->> 'auc')::numeric AS rule_auc,
       (v."Metrics" -> 'chosen' ->> 'wape')::numeric AS test_wape
FROM ml."ModelVersion" v
LEFT JOIN ml."DriftCheck" d ON d."Version" = v."Version"
     AND d."AsOfDate" = (SELECT max("AsOfDate") FROM ml."DriftCheck" WHERE "Version" = v."Version")
WHERE v."Status" = 'production';

-- Registry history: every training run and the gate's decision (the model itself is left out)
CREATE OR REPLACE VIEW mart.v_model_versions AS
SELECT "Version", "TrainedAt", "AsOfDate" AS trained_on_data_to, "Algorithm", "Status", "TrainRows", "TestRows",
       ("Metrics" -> 'chosen' ->> 'auc')::numeric AS test_auc, ("Metrics" -> 'baseline' ->> 'auc')::numeric AS baseline_auc,
       ("Metrics" -> 'chosen' ->> 'top20_capture')::numeric AS top20_capture, "Decision", "Model",
       ("Metrics" -> 'baselines' -> 'rule' ->> 'auc')::numeric AS rule_auc,
       ("Metrics" -> 'chosen' ->> 'wape')::numeric AS test_wape
FROM ml."ModelVersion";

-- ---------- Demand forecast (Problem 1) ----------

-- Next 6 months on the latest forecasting day: weddings booked and expected, staff-hours against the
-- event staff's capacity, and what Ops should do. Thresholds from 2024-2026: months at 85%+ of
-- capacity had 8-12 employee-weeks over 100%, months at 65-85% had 2-9.
CREATE OR REPLACE VIEW mart.v_demand_forecast AS
SELECT f."AsOfDate", f."Month", f."Horizon", f."OnBooks" AS booked, f."Forecast" AS expected_weddings,
       f."Lo" AS low_80, f."Hi" AS high_80, f."BookedHours", f."ExpectedHours", f."CapacityHours",
       round(f."ExpectedHours" / nullif(f."CapacityHours", 0), 3) AS expected_utilization,
       CASE WHEN f."ExpectedHours" >= 0.85 * f."CapacityHours" THEN 'Over'
            WHEN f."ExpectedHours" >= 0.65 * f."CapacityHours" THEN 'Tight'
            WHEN f."ExpectedHours" < 0.35 * f."CapacityHours" THEN 'Quiet' ELSE 'OK' END AS status,
       CASE WHEN f."ExpectedHours" >= 0.85 * f."CapacityHours"
              THEN 'Book freelancers now; take no more auspicious-date weddings this month without them'
            WHEN f."ExpectedHours" >= 0.65 * f."CapacityHours"
              THEN 'Plan overtime and move planning work to earlier weeks'
            WHEN f."ExpectedHours" < 0.35 * f."CapacityHours"
              THEN 'Promote off-peak packages; schedule leave and training'
            ELSE 'No action' END AS action,
       f."Version"
FROM ml."DemandForecast" f
WHERE f."AsOfDate" = (SELECT max("AsOfDate") FROM ml."DemandForecast");

-- Forecast against what happened, for every forecast of a month that has ended
CREATE OR REPLACE VIEW mart.v_demand_accuracy AS
SELECT f."AsOfDate", f."Month", f."Horizon", f."Forecast", f."Lo", f."Hi", a.actual,
       (a.actual BETWEEN f."Lo" AND f."Hi") AS in_interval
FROM ml."DemandForecast" f
CROSS JOIN LATERAL (SELECT count(*) AS actual FROM stg."Event" e
                    WHERE e."EventDate" >= f."Month" AND e."EventDate" < f."Month" + interval '1 month') a
WHERE f."Month" + interval '1 month' <= (SELECT max("AsOfDate") FROM ml."DemandForecast");
