-- Silver sales_activity (Spec 02 §4.7): one row per logged follow-up (call, LINE, meeting).
SELECT s."ActivityID" AS activity_id, s."InquiryID" AS inquiry_id, s."EmployeeID" AS employee_id, s."ActivityAt" AS activity_at,
       s."ActivityType" AS activity_type, s."Outcome" AS outcome, s."Source" AS source, s."ListDate" AS list_date,
       s."Source" = 'CallList' AS on_call_list, s."UpdatedAt" AS updated_at,
       CASE WHEN i.inquiry_id IS NULL THEN 'R12a' END AS _reject,
       list_filter([CASE WHEN s."ActivityAt" < i.inquiry_at THEN 'R12b' END], lambda w: w IS NOT NULL) AS _warn,
       s."ActivityID"::VARCHAR AS _key, s._run_id
FROM crm."SalesActivity" s
LEFT JOIN inquiry i ON i.inquiry_id = s."InquiryID"
