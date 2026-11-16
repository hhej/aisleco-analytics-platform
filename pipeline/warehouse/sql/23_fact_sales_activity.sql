-- FactSalesActivity (Spec 03 §4.4): one row per logged follow-up. ListRank comes from the model's list of that day.
DROP TABLE IF EXISTS work."FactSalesActivity";
CREATE TABLE work."FactSalesActivity" AS
SELECT s.activity_id AS "ActivityID", gold.dk(s.activity_at::date) AS "ActivityDateKey",
       coalesce(e."EmployeeKey", -1) AS "SalespersonKey", coalesce(c."ChannelKey", -1) AS "ChannelKey", s.inquiry_id AS "InquiryID",
       s.activity_type AS "ActivityType", s.outcome AS "Outcome", s.on_call_list::int AS "FromCallList", ls."Rank" AS "ListRank",
       gold.is_holdout(s.inquiry_id) AS "IsHoldout", 1 AS "ActivityCount"
FROM stg.sales_activity s
LEFT JOIN stg.inquiry i ON i.inquiry_id = s.inquiry_id
LEFT JOIN gold."DimChannel" c ON c."ChannelID" = i.channel_id
LEFT JOIN gold."DimEmployee" e ON e."EmployeeID" = s.employee_id AND s.activity_at::date BETWEEN e."ValidFrom" AND e."ValidTo"
LEFT JOIN gold."FactLeadScore" ls ON ls."InquiryID" = s.inquiry_id AND ls."ScoreDateKey" = gold.dk(s.list_date);
SELECT count(*) AS rows_built FROM work."FactSalesActivity";
