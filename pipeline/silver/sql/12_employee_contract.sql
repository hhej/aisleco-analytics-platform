-- Silver employee_contract (Spec 02 §4.2): one row per contract version, joined to the person and the role.
-- The basis of the SCD Type 2 staff dimension (Spec 03 §3). Overlaps are caught by rule R13 (block).
SELECT c."ContractID" AS contract_id, e."EmployeeID" AS employee_id, e."EmployeeCode" AS employee_code, e."Nickname" AS nickname,
       r."RoleName" AS role_name, r."RoleGroup" AS role_group, c."EmploymentType" AS employment_type,
       c."StandardWeeklyHours" AS standard_weekly_hours, c."MaxConcurrentEvents" AS max_concurrent_events,
       c."EffectiveFrom" AS effective_from, c."EffectiveTo" AS effective_to, e."HireDate" AS hire_date, e."Status" AS status
FROM crm."EmployeeContract" c
JOIN crm."Employee" e USING ("EmployeeID")
JOIN crm."EmployeeRole" r USING ("RoleID")
