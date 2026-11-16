-- Silver assignment (Spec 02 §4.7): one row per staff assignment. Wedding-day hours = min(12, hours), the rest is
-- planning (Spec 03 §4.3 spreads them over weeks). A missing assignment date counts as the booking date (Rev10).
-- An assignment whose event was rejected is rejected too (orphan), so hours never land without their wedding date;
-- orphans are re-extracted every run until the parent loads.
SELECT a."EventEmployeeAssignmentID" AS assignment_id, a."EventID" AS event_id, a."EmployeeID" AS employee_id,
       a."AssignmentRole" AS assignment_role, a."AssignedDate" AS assigned_at,
       coalesce(a."AssignedDate", e.booked_at) AS assigned_at_effective,
       a."WorkloadHours" AS workload_hours, least(12, a."WorkloadHours") AS event_day_hours,
       a."WorkloadHours" - least(12, a."WorkloadHours") AS planning_hours,
       a."IsLead" = 'Y' AS is_lead, a."Status" AS status, a."UpdatedAt" AS updated_at,
       CASE WHEN a."WorkloadHours" NOT BETWEEN 0 AND 300 THEN 'R10a'
            WHEN e.event_id IS NULL OR a."EmployeeID" NOT IN (SELECT "EmployeeID" FROM crm."Employee") THEN 'R10b' END AS _reject,
       list_filter([CASE WHEN a."AssignedDate"::DATE > e.event_date THEN 'R11' END], lambda w: w IS NOT NULL) AS _warn,
       a."EventEmployeeAssignmentID"::VARCHAR AS _key, a._run_id
FROM crm."EventEmployeeAssignment" a
LEFT JOIN event e ON e.event_id = a."EventID"
