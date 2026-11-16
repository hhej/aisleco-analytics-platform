-- FactStaffWorkload (Spec 03 §4.3): one row per employee x wedding x week. The hours-spread rule:
--   wedding-day hours (<= 12) in the wedding week; planning hours from the assignment week to the week before the
--   wedding: n <= 10 weeks evenly; n > 10: 20 % in the first 2 weeks, 50 % in the last 8, 30 % evenly in between;
--   assigned in the wedding week or later: everything in the wedding week;
--   cancelled wedding: weeks from the cancellation week on are dropped (earlier work was done).
DROP TABLE IF EXISTS work.spread, work."FactStaffWorkload";
CREATE TABLE work.spread AS
WITH a AS (
  SELECT a.assignment_id, a.employee_id, a.event_id, a.assignment_role, a.planning_hours, a.event_day_hours, e.event_date,
         date_trunc('week', e.event_date)::date AS ew,
         least(date_trunc('week', a.assigned_at_effective), date_trunc('week', e.event_date))::date AS aw,
         date_trunc('week', e.cancelled_at)::date AS cw
  FROM stg.assignment a JOIN stg.event e USING (event_id) WHERE a.status = 'Active'),
n AS (SELECT *, (ew - aw) / 7 AS n FROM a),
weeks AS (
  SELECT n.*, k, (aw + 7 * (k - 1)) AS wk,
         CASE WHEN n.n <= 10 THEN planning_hours / n.n
              WHEN k <= 2 THEN planning_hours * 0.2 / 2
              WHEN k > n.n - 8 THEN planning_hours * 0.5 / 8
              ELSE planning_hours * 0.3 / (n.n - 10) END AS plan_h
  FROM n, generate_series(1, n.n) k WHERE n.n > 0)
SELECT assignment_id, employee_id, event_id, assignment_role, event_date, wk, plan_h, 0::numeric AS day_h, cw FROM weeks
UNION ALL   -- the wedding week: wedding-day hours, plus all planning hours when there is no planning period
SELECT assignment_id, employee_id, event_id, assignment_role, event_date, ew, CASE WHEN n = 0 THEN planning_hours ELSE 0 END, event_day_hours, cw FROM n;
DELETE FROM work.spread WHERE cw IS NOT NULL AND wk >= cw;
DELETE FROM work.spread WHERE plan_h = 0 AND day_h = 0;

CREATE TABLE work."FactStaffWorkload" AS
SELECT s.assignment_id AS "AssignmentID", gold.dk(s.wk) AS "WeekStartDateKey",
       coalesce(de."EmployeeKey", dx."EmployeeKey", -1) AS "EmployeeKey", s.event_id AS "EventID", gold.dk(s.event_date) AS "EventDateKey",
       s.assignment_role AS "AssignmentRole", s.plan_h AS "PlanningHours", s.day_h AS "EventDayHours",
       s.plan_h + s.day_h AS "TotalHours"
FROM work.spread s
LEFT JOIN gold."DimEmployee" de ON de."EmployeeID" = s.employee_id AND s.wk BETWEEN de."ValidFrom" AND de."ValidTo"
-- a contract that starts mid-week: use the version valid at the wedding
LEFT JOIN gold."DimEmployee" dx ON dx."EmployeeID" = s.employee_id AND s.event_date BETWEEN dx."ValidFrom" AND dx."ValidTo" AND de."EmployeeKey" IS NULL;
SELECT count(*) AS rows_built FROM work."FactStaffWorkload";
