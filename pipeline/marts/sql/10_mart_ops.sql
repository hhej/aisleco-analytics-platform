-- =====================================================================
-- mart_ops: Operations "Capacity planner" (Spec 05 §3.3). Read by role aisleco_ops. Every ratio is computed here,
-- from parts stored in Gold (Spec 03 §2 rule 5), as a ratio of sums.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS mart_ops;

-- one row per employee (ops roles) per week the employee has a contract, including weeks with no work
CREATE OR REPLACE VIEW mart_ops.v_capacity_grid AS
WITH weeks AS (SELECT DISTINCT "WeekStart" AS week_start FROM gold."DimDate" WHERE "DateKey" <> -1),
grid AS (
  SELECT w.week_start, e."EmployeeID" AS employee_id, e."EmployeeKey" AS employee_key, e."Nickname" AS nickname, e."RoleName" AS role_name,
         e."RoleGroup" AS role_group, e."EmploymentType" AS employment_type, e."StandardWeeklyHours" AS capacity_hours,
         e."MaxConcurrentEvents" AS max_concurrent
  FROM weeks w JOIN gold."DimEmployee" e ON w.week_start BETWEEN e."ValidFrom" AND e."ValidTo"
  WHERE e."RoleGroup" IN ('Planning', 'Wedding day')),
hrs AS (
  SELECT d."WeekStart" AS week_start, e."EmployeeID" AS employee_id, sum(f."TotalHours") AS assigned_hours,
         sum(f."PlanningHours") AS planning_hours, sum(f."EventDayHours") AS event_day_hours, count(DISTINCT f."EventID") AS weddings
  FROM gold."FactStaffWorkload" f JOIN gold."DimDate" d ON d."DateKey" = f."WeekStartDateKey"
  JOIN gold."DimEmployee" e ON e."EmployeeKey" = f."EmployeeKey" GROUP BY 1, 2)
SELECT g.*, coalesce(h.assigned_hours, 0) AS assigned_hours, coalesce(h.planning_hours, 0) AS planning_hours,
       coalesce(h.event_day_hours, 0) AS event_day_hours, coalesce(h.weddings, 0) AS weddings,
       round(coalesce(h.assigned_hours, 0) / nullif(g.capacity_hours, 0), 4) AS utilization,
       greatest(0, coalesce(h.assigned_hours, 0) - g.capacity_hours) AS overtime_hours,
       coalesce(h.weddings, 0) > g.max_concurrent AS over_concurrency,
       CASE WHEN coalesce(h.assigned_hours, 0) >= g.capacity_hours THEN 'Over'
            WHEN coalesce(h.assigned_hours, 0) >= 0.85 * g.capacity_hours THEN 'Tight'
            WHEN coalesce(h.assigned_hours, 0) >= 0.5 * g.capacity_hours THEN 'OK' ELSE 'Quiet' END AS status
FROM grid g LEFT JOIN hrs h USING (week_start, employee_id);

-- which weddings make up one person's week (the drawer behind a heatmap cell)
CREATE OR REPLACE VIEW mart_ops.v_week_detail AS
SELECT d."WeekStart" AS week_start, e."EmployeeID" AS employee_id, e."Nickname" AS nickname, f."EventID" AS event_id,
       ed."FullDate" AS event_date, p."PackageFamily" AS package, b."GuestCount" AS guests, b."ComplexityLevel" AS complexity,
       f."AssignmentRole" AS role, f."PlanningHours" AS planning_hours, f."EventDayHours" AS event_day_hours, f."TotalHours" AS total_hours,
       (SELECT string_agg(DISTINCT e2."Nickname", ', ') FROM gold."FactStaffWorkload" f2 JOIN gold."DimEmployee" e2 ON e2."EmployeeKey" = f2."EmployeeKey"
         WHERE f2."EventID" = f."EventID" AND e2."EmployeeID" <> e."EmployeeID") AS also_on_it
FROM gold."FactStaffWorkload" f
JOIN gold."DimDate" d ON d."DateKey" = f."WeekStartDateKey"
JOIN gold."DimDate" ed ON ed."DateKey" = f."EventDateKey"
JOIN gold."DimEmployee" e ON e."EmployeeKey" = f."EmployeeKey"
JOIN gold."FactEventBooking" b ON b."EventID" = f."EventID"
JOIN gold."DimPackage" p ON p."PackageKey" = b."PackageKey";

-- role group x week: ratio of sums for utilization; overtime summed per person first (spare hours never cancel overtime)
CREATE OR REPLACE VIEW mart_ops.v_role_week AS
SELECT week_start, role_group, sum(capacity_hours) AS capacity_hours, sum(assigned_hours) AS assigned_hours,
       round(sum(assigned_hours) / nullif(sum(capacity_hours), 0), 4) AS utilization,
       sum(overtime_hours) AS overtime_hours, count(*) FILTER (WHERE status = 'Over') AS person_weeks_over,
       sum(weddings) AS wedding_assignments
FROM mart_ops.v_capacity_grid GROUP BY 1, 2;

-- next 26 weeks: the latest forecast (Spec 04 §11), with status and the gap in hours
CREATE OR REPLACE VIEW mart_ops.v_forward_demand AS
SELECT fd."FullDate" AS forecast_date, tw."FullDate" AS week_start, f."RoleGroup" AS role_group,
       f."BookedHours" AS booked_hours, f."PipelineHours" AS pipeline_hours, f."NewBookingHours" AS new_booking_hours,
       f."P10Hours" AS p10_hours, f."P50Hours" AS p50_hours, f."P90Hours" AS p90_hours, f."CapacityHours" AS capacity_hours,
       round(f."P50Hours" / nullif(f."CapacityHours", 0), 4) AS expected_utilization,
       greatest(0, f."P50Hours" - f."CapacityHours") AS gap_hours,
       CASE WHEN f."P50Hours" >= f."CapacityHours" THEN 'Over' WHEN f."P50Hours" >= 0.85 * f."CapacityHours" THEN 'Tight'
            WHEN f."P50Hours" >= 0.5 * f."CapacityHours" THEN 'OK' ELSE 'Quiet' END AS status, f."ModelVersion" AS model_version
FROM gold."FactDemandForecast" f
JOIN gold."DimDate" fd ON fd."DateKey" = f."ForecastDateKey" JOIN gold."DimDate" tw ON tw."DateKey" = f."TargetWeekDateKey"
WHERE f."ForecastDateKey" = (SELECT max("ForecastDateKey") FROM gold."FactDemandForecast");

-- one row per future date: weddings booked that day and week, headroom per role group, auspicious level
CREATE OR REPLACE VIEW mart_ops.v_date_check AS
WITH ev AS (SELECT d."FullDate" AS day, count(*) AS weddings FROM gold."FactEventBooking" b JOIN gold."DimDate" d ON d."DateKey" = b."EventDateKey"
            WHERE b."IsCancelled" = 0 GROUP BY 1)
SELECT d."FullDate" AS date, d."DayName" AS day_name, d."WeekStart" AS week_start, d."AuspiciousLevel" AS auspicious_level,
       d."IsBuddhistLent" AS in_lent, d."IsHighSeason" AS high_season, d."HolidayName" AS holiday,
       coalesce(ev.weddings, 0) AS weddings_that_day,
       (SELECT coalesce(sum(e2.weddings), 0) FROM ev e2 WHERE e2.day BETWEEN d."WeekStart" AND d."WeekStart" + 6) AS weddings_that_week,
       (SELECT capacity_hours - assigned_hours FROM mart_ops.v_role_week r WHERE r.week_start = d."WeekStart" AND r.role_group = 'Planning') AS planning_headroom_hours,
       (SELECT capacity_hours - assigned_hours FROM mart_ops.v_role_week r WHERE r.week_start = d."WeekStart" AND r.role_group = 'Wedding day') AS wedding_day_headroom_hours
FROM gold."DimDate" d LEFT JOIN ev ON ev.day = d."FullDate"
WHERE d."FullDate" >= gold.asof();

-- peak-date acceptance by requested month: decided inquiries for Nov-Feb or auspicious dates that booked
CREATE OR REPLACE VIEW mart_ops.v_peak_acceptance AS
SELECT d."YearMonth" AS requested_month, count(*) AS peak_inquiries, sum(1 - f."IsOpen") AS decided, sum(f."IsBooked") AS booked,
       round(sum(f."IsBooked")::numeric / nullif(sum(1 - f."IsOpen"), 0), 4) AS acceptance_rate,
       count(*) FILTER (WHERE o."LostReason" = 'Date unavailable') AS lost_date_unavailable
FROM gold."FactInquiry" f JOIN gold."DimDate" d ON d."DateKey" = f."RequestedEventDateKey"
JOIN gold."DimInquiryOutcome" o USING ("OutcomeKey")
WHERE d."IsHighSeason" OR d."AuspiciousLevel" <> 'None' GROUP BY 1;

-- couples turned away (lost: date unavailable) by requested month and auspicious level, with the fee at stake (estimate)
CREATE OR REPLACE VIEW mart_ops.v_turned_away AS
WITH fee AS (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY "AgreedFee") AS median_fee FROM gold."FactEventBooking"
             WHERE "IsCancelled" = 0 AND "BookingDateKey" >= gold.dk(gold.asof() - 365)),
     close AS (SELECT sum("IsBooked")::numeric / nullif(sum("IsQuoted"), 0) AS rate FROM gold."FactInquiry" WHERE "IsOpen" = 0)
SELECT d."YearMonth" AS requested_month, d."AuspiciousLevel" AS auspicious_level, cd."YearMonth" AS closed_month,
       count(*) AS couples_turned_away,
       round(count(*) * (SELECT median_fee FROM fee) * (SELECT rate FROM close)) AS fee_at_stake_estimate
FROM gold."FactInquiry" f JOIN gold."DimInquiryOutcome" o USING ("OutcomeKey")
JOIN gold."DimDate" d ON d."DateKey" = f."RequestedEventDateKey" JOIN gold."DimDate" cd ON cd."DateKey" = f."ClosedDateKey"
WHERE o."LostReason" = 'Date unavailable' GROUP BY 1, 2, 3;

-- staff hours per wedding by package, guest band and complexity (input for pricing and staffing rules)
CREATE OR REPLACE VIEW mart_ops.v_hours_per_wedding AS
SELECT p."PackageFamily" AS package, b."GuestBand" AS guest_band, b."ComplexityLevel" AS complexity, count(*) AS weddings,
       round(sum(b."PlannedHoursTotal") / count(*), 1) AS avg_total_hours, round(sum(b."PlanningHours") / count(*), 1) AS avg_planning_hours,
       round(sum(b."EventDayHours") / count(*), 1) AS avg_event_day_hours, round(avg(b."StaffCount"), 1) AS avg_staff
FROM gold."FactEventBooking" b JOIN gold."DimPackage" p USING ("PackageKey") WHERE b."IsCancelled" = 0 GROUP BY 1, 2, 3;
