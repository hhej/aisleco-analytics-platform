-- =====================================================================
-- mart_sales: Sales "Today" and "Sales performance" (Spec 05 §3.1-3.2). Read by role aisleco_sales.
-- No names here: the API joins sales_pii (readable by the Sales role only) when it shows the list.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS mart_sales;

-- base view: one inquiry with its labels (used by the other views)
CREATE OR REPLACE VIEW mart_sales.v_inquiry AS
SELECT f.*, d."FullDate" AS inquiry_date, d."YearMonth" AS inquiry_month, c."ChannelName" AS channel, c."ChannelType" AS channel_type,
       p."PackageFamily" AS package_interest, o."Outcome" AS outcome, o."FurthestStage" AS furthest_stage, o."ClosedBy" AS closed_by,
       o."LostReason" AS lost_reason, rq."FullDate" AS requested_date, la."FullDate" AS last_activity_date,
       so."Nickname" AS sales_owner, so."EmployeeID" AS sales_owner_id
FROM gold."FactInquiry" f
JOIN gold."DimDate" d ON d."DateKey" = f."InquiryDateKey"
JOIN gold."DimDate" rq ON rq."DateKey" = f."RequestedEventDateKey"
JOIN gold."DimDate" la ON la."DateKey" = f."LastActivityDateKey"
JOIN gold."DimChannel" c USING ("ChannelKey")
JOIN gold."DimPackage" p ON p."PackageKey" = f."IndicativePackageKey"
JOIN gold."DimInquiryOutcome" o USING ("OutcomeKey")
JOIN gold."DimEmployee" so ON so."EmployeeKey" = f."SalesOwnerKey";

-- today's scored open leads, with the requested date's capacity badge (drill-across: each fact summarised first)
CREATE OR REPLACE VIEW mart_sales.v_today_list AS
SELECT s."InquiryID" AS inquiry_id, i."CustomerID" AS customer_id, i.sales_owner_id, i.sales_owner, i.channel, i.package_interest,
       i."BudgetBand" AS budget_band, i.requested_date, i.furthest_stage, i.inquiry_date, i.last_activity_date,
       gold.asof() - i.last_activity_date AS days_silent, i.last_activity_date + 30 AS auto_close_date,
       s."Probability" AS probability, s."ExpectedFee" AS expected_fee, s."Rank" AS rank, s."Band" AS band, s."IsHoldout" = 1 AS holdout,
       s."Reason1" AS reason1, s."Reason2" AS reason2, s."Reason3" AS reason3, s."NextBestAction" AS next_best_action,
       CASE WHEN i.requested_date IS NULL THEN NULL
            WHEN dc.weddings_that_day >= 2 OR least(dc.planning_headroom_hours, dc.wedding_day_headroom_hours) < 0 THEN 'Full'
            WHEN dc.weddings_that_day = 1 OR least(dc.planning_headroom_hours, dc.wedding_day_headroom_hours) < 20 THEN 'Tight'
            ELSE 'Room' END AS date_badge,
       s."ModelVersion" AS model_version, sd."FullDate" AS score_date
FROM gold."FactLeadScore" s
JOIN gold."DimDate" sd ON sd."DateKey" = s."ScoreDateKey"
JOIN mart_sales.v_inquiry i USING ("InquiryID")
LEFT JOIN mart_ops.v_date_check dc ON dc.date = i.requested_date
WHERE s."ScoreDateKey" = (SELECT max("ScoreDateKey") FROM gold."FactLeadScore");

-- arrived since yesterday and still without a first reply
CREATE OR REPLACE VIEW mart_sales.v_new_today AS
SELECT "InquiryID" AS inquiry_id, "CustomerID" AS customer_id, sales_owner_id, sales_owner, channel, inquiry_date, package_interest, "BudgetBand" AS budget_band
FROM mart_sales.v_inquiry WHERE "IsOpen" = 1 AND "IsReplied" = 0 AND inquiry_date >= gold.asof() - 1;

-- open leads silent for 10+ days: the 30-day rule will close them soon
CREATE OR REPLACE VIEW mart_sales.v_going_cold AS
SELECT "InquiryID" AS inquiry_id, "CustomerID" AS customer_id, sales_owner_id, sales_owner, channel, furthest_stage, last_activity_date,
       gold.asof() - last_activity_date AS days_silent, last_activity_date + 30 AS auto_close_date
FROM mart_sales.v_inquiry WHERE "IsOpen" = 1 AND gold.asof() - last_activity_date >= 10;

-- per salesperson: bookings this month and median first reply over the last 7 days
CREATE OR REPLACE VIEW mart_sales.v_my_week AS
SELECT e."EmployeeID" AS employee_id, e."Nickname" AS nickname,
       (SELECT count(*) FROM mart_sales.v_inquiry i JOIN gold."DimDate" b ON b."DateKey" = i."BookingDateKey"
         WHERE i.sales_owner_id = e."EmployeeID" AND b."YearMonth" = to_char(gold.asof(), 'YYYY-MM')) AS bookings_this_month,
       (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY i."ResponseMinutes") FROM mart_sales.v_inquiry i
         WHERE i.sales_owner_id = e."EmployeeID" AND i.inquiry_date > gold.asof() - 7) AS median_first_reply_min_7d
FROM gold."DimEmployee" e WHERE e."RoleName" = 'Sales Executive' AND gold.asof() BETWEEN e."ValidFrom" AND e."ValidTo";

-- funnel by inquiry month and channel (counts are additive; rates computed by the page as ratios of sums)
CREATE OR REPLACE VIEW mart_sales.v_funnel AS
SELECT inquiry_month, channel, sum("InquiryCount") AS inquiries, sum("IsReplied") AS replied, sum("IsConsulted") AS consulted,
       sum("IsQuoted") AS quoted, sum("IsBooked") AS booked, sum("IsOpen") AS still_open
FROM mart_sales.v_inquiry GROUP BY 1, 2;

-- conversion by channel and month, decided inquiries only, with a 95 % Wilson interval (small samples look uncertain)
CREATE OR REPLACE VIEW mart_sales.v_conversion_by_channel_month AS
WITH x AS (SELECT channel, inquiry_month, sum("InquiryCount") AS inquiries, sum("InquiryCount" - "IsOpen") AS decided, sum("IsBooked") AS booked
           FROM mart_sales.v_inquiry GROUP BY 1, 2)
SELECT x.*, round(booked::numeric / nullif(decided, 0), 4) AS conversion,
       round(((booked + 1.92) / nullif(decided + 3.84, 0) - 1.96 * sqrt(booked::numeric * (decided - booked) / nullif(decided, 0) + 0.96) / nullif(decided + 3.84, 0))::numeric, 4) AS ci_low,
       round(((booked + 1.92) / nullif(decided + 3.84, 0) + 1.96 * sqrt(booked::numeric * (decided - booked) / nullif(decided, 0) + 0.96) / nullif(decided + 3.84, 0))::numeric, 4) AS ci_high
FROM x;

-- when couples write and how fast we answer; conversion by reply band (the evening-shift question)
CREATE OR REPLACE VIEW mart_sales.v_response_by_hour AS
SELECT "FirstMessageHour" AS hour, extract(isodow FROM inquiry_date)::int AS weekday, count(*) AS inquiries,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "ResponseMinutes") AS median_reply_min
FROM mart_sales.v_inquiry WHERE channel_type = 'Online' GROUP BY 1, 2;
CREATE OR REPLACE VIEW mart_sales.v_response_band AS
SELECT "ResponseBand" AS response_band, count(*) AS inquiries, sum("InquiryCount" - "IsOpen") AS decided, sum("IsBooked") AS booked,
       round(sum("IsConsulted")::numeric / count(*), 4) AS consult_rate,
       round(sum("IsBooked")::numeric / nullif(sum("InquiryCount" - "IsOpen"), 0), 4) AS conversion,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY "ResponseMinutes") AS median_minutes
FROM mart_sales.v_inquiry GROUP BY 1;

-- why we lose couples: by closing month, closed by sales (a real reason) vs the 30-day rule
CREATE OR REPLACE VIEW mart_sales.v_lost_reasons AS
SELECT cd."YearMonth" AS closed_month, i.closed_by, i.lost_reason, count(*) AS lost
FROM mart_sales.v_inquiry i JOIN gold."DimDate" cd ON cd."DateKey" = i."ClosedDateKey" WHERE i.outcome = 'Lost' GROUP BY 1, 2, 3;

-- quote share of budget per quoted inquiry (a per-couple ratio is valid here), by outcome group
CREATE OR REPLACE VIEW mart_sales.v_quote_share AS
SELECT "InquiryID" AS inquiry_id, inquiry_month, round("QuotedAmount" / nullif("BudgetMidpoint", 0), 4) AS quote_share,
       CASE WHEN outcome = 'Booked' THEN 'Booked' WHEN lost_reason = 'Price' THEN 'Lost on price' WHEN outcome = 'Lost' THEN 'Lost, other' ELSE 'Open' END AS group_name
FROM mart_sales.v_inquiry WHERE "IsQuoted" = 1 AND "BudgetMidpoint" IS NOT NULL;

-- is the list worked? per salesperson and day: leads listed (rank <= 15, not holdout) vs listed leads contacted
CREATE OR REPLACE VIEW mart_sales.v_followup_compliance AS
SELECT d."FullDate" AS day, e."Nickname" AS salesperson, count(*) AS listed,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM gold."FactSalesActivity" a WHERE a."InquiryID" = s."InquiryID"
                                      AND a."ActivityDateKey" = s."ScoreDateKey" AND a."FromCallList" = 1)) AS contacted
FROM gold."FactLeadScore" s JOIN gold."DimDate" d ON d."DateKey" = s."ScoreDateKey" JOIN gold."DimEmployee" e ON e."EmployeeKey" = s."SalesOwnerKey"
WHERE s."IsHoldout" = 0 AND s."Rank" <= 15 GROUP BY 1, 2;

-- A/B: 30-day booking rate of prioritised vs holdout leads, from each lead's first score (Spec 04 §8)
CREATE OR REPLACE VIEW mart_sales.v_ab_result AS
WITH first AS (SELECT DISTINCT ON ("InquiryID") "InquiryID", "ScoreDateKey", "IsHoldout" FROM gold."FactLeadScore" ORDER BY "InquiryID", "ScoreDateKey"),
x AS (SELECT f."IsHoldout", (b."FullDate" IS NOT NULL AND b."FullDate" < sd."FullDate" + 30)::int AS booked30, sd."FullDate" AS first_score
      FROM first f JOIN gold."DimDate" sd ON sd."DateKey" = f."ScoreDateKey" JOIN gold."FactInquiry" i USING ("InquiryID")
      JOIN gold."DimDate" b ON b."DateKey" = i."BookingDateKey"
      WHERE sd."FullDate" <= gold.asof() - 30)
SELECT CASE WHEN "IsHoldout" = 1 THEN 'Standard queue (holdout)' ELSE 'Ranked list' END AS arm, count(*) AS leads, sum(booked30) AS booked_30d,
       round(avg(booked30), 4) AS booking_rate_30d, min(first_score) AS from_date, max(first_score) AS to_date
FROM x GROUP BY 1;
