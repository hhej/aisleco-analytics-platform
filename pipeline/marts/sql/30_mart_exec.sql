-- =====================================================================
-- mart_exec: Executive "Business overview" (Spec 05 §3.4). Read by role aisleco_exec. One row per month; the page
-- compares the current month with the previous one and the same month last year.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS mart_exec;

CREATE OR REPLACE VIEW mart_exec.v_monthly_kpis AS
WITH m AS (SELECT DISTINCT "YearMonth" AS month FROM gold."DimDate" WHERE "FullDate" BETWEEN DATE '2023-01-01' AND gold.asof()),
bk AS (SELECT d."YearMonth" AS month, count(*) AS bookings, sum(b."AgreedFee") AS fee_revenue
       FROM gold."FactEventBooking" b JOIN gold."DimDate" d ON d."DateKey" = b."BookingDateKey" WHERE b."IsCancelled" = 0 GROUP BY 1),
held AS (SELECT d."YearMonth" AS month, count(*) AS weddings_held FROM gold."FactEventBooking" b JOIN gold."DimDate" d ON d."DateKey" = b."EventDateKey"
         WHERE b."IsCancelled" = 0 AND d."FullDate" <= gold.asof() GROUP BY 1),        -- held = already happened
inq AS (SELECT inquiry_month AS month, count(*) AS inquiries, sum("InquiryCount" - "IsOpen") AS decided, sum("IsBooked") AS booked,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY "ResponseMinutes") FILTER (WHERE channel_type = 'Online') AS median_first_reply_min
        FROM mart_sales.v_inquiry GROUP BY 1),
ut AS (SELECT to_char(week_start, 'YYYY-MM') AS month, sum(assigned_hours) AS assigned_hours, sum(capacity_hours) AS capacity_hours,
              sum(overtime_hours) AS overtime_hours, count(*) FILTER (WHERE status = 'Over') AS person_weeks_over
       FROM mart_ops.v_capacity_grid GROUP BY 1),
ta AS (SELECT closed_month AS month, sum(couples_turned_away) AS turned_away, sum(fee_at_stake_estimate) AS fee_at_stake
       FROM mart_ops.v_turned_away GROUP BY 1)
SELECT m.month, coalesce(bk.bookings, 0) AS bookings, coalesce(bk.fee_revenue, 0) AS fee_revenue, coalesce(held.weddings_held, 0) AS weddings_held,
       coalesce(inq.inquiries, 0) AS inquiries, inq.decided, inq.booked, round(inq.booked::numeric / nullif(inq.decided, 0), 4) AS conversion,
       inq.median_first_reply_min, ut.assigned_hours, ut.capacity_hours, round(ut.assigned_hours / nullif(ut.capacity_hours, 0), 4) AS ops_utilization,
       ut.overtime_hours, ut.person_weeks_over, coalesce(ta.turned_away, 0) AS turned_away, coalesce(ta.fee_at_stake, 0) AS fee_at_stake_estimate
FROM m LEFT JOIN bk USING (month) LEFT JOIN held USING (month) LEFT JOIN inq USING (month) LEFT JOIN ut USING (month) LEFT JOIN ta USING (month);

-- inquiries by month and channel (the 2026 TikTok shift)
CREATE OR REPLACE VIEW mart_exec.v_channel_mix AS
SELECT inquiry_month AS month, channel, count(*) AS inquiries, sum("IsBooked") AS booked, sum("InquiryCount" - "IsOpen") AS decided
FROM mart_sales.v_inquiry GROUP BY 1, 2;

-- capacity outlook by month and role group: hours already booked vs capacity (and the forecast once it exists)
CREATE OR REPLACE VIEW mart_exec.v_capacity_outlook AS
WITH w AS (SELECT r.week_start, r.role_group, r.capacity_hours, r.assigned_hours, f.p50_hours,
                  coalesce(f.p50_hours, r.assigned_hours) AS expected_hours      -- per week: the forecast where it exists, else booked
           FROM mart_ops.v_role_week r LEFT JOIN mart_ops.v_forward_demand f USING (week_start, role_group)
           WHERE r.week_start BETWEEN date_trunc('month', gold.asof()) AND gold.asof() + 183)
SELECT to_char(week_start, 'YYYY-MM') AS month, role_group, sum(capacity_hours) AS capacity_hours, sum(assigned_hours) AS booked_hours,
       sum(p50_hours) AS forecast_hours, sum(expected_hours) AS expected_hours,
       round(sum(expected_hours) / nullif(sum(capacity_hours), 0), 4) AS expected_utilization
FROM w GROUP BY 1, 2;

-- lost couples over the last 12 months by reason, with the fee at stake (estimate: median fee x booking rate of quoted leads,
-- applied to the couples who reached a quote; couples turned away for the date count in full, as they never got the chance)
CREATE OR REPLACE VIEW mart_exec.v_lost_value AS
WITH fee AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY "AgreedFee") AS median_fee FROM gold."FactEventBooking"
             WHERE "IsCancelled" = 0 AND "BookingDateKey" >= gold.dk(gold.asof() - 365)),        -- same basis as v_turned_away
     close AS (SELECT sum("IsBooked")::numeric / nullif(sum("IsQuoted"), 0) AS rate FROM gold."FactInquiry" WHERE "IsOpen" = 0)
SELECT lost_reason, closed_by, count(*) AS lost, round(count(*) FILTER (WHERE "IsQuoted" = 1 OR lost_reason = 'Date unavailable') * (SELECT median_fee FROM fee) * (SELECT rate FROM close)) AS fee_at_stake_estimate
FROM mart_sales.v_inquiry i JOIN gold."DimDate" cd ON cd."DateKey" = i."ClosedDateKey"
WHERE outcome = 'Lost' AND cd."FullDate" > gold.asof() - 365 GROUP BY 1, 2;

-- is the call list paying off (A/B result so far)
CREATE OR REPLACE VIEW mart_exec.v_model_value AS SELECT * FROM mart_sales.v_ab_result;
CREATE OR REPLACE VIEW mart_exec.v_model_effect AS SELECT * FROM mart_sales.v_ab_effect;
