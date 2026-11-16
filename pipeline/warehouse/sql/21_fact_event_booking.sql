-- FactEventBooking (Spec 03 §4.2): one row per booked wedding. Hours per wedding are the sums of its active assignments.
DROP TABLE IF EXISTS work."FactEventBooking";
CREATE TABLE work."FactEventBooking" AS
SELECT e.event_id AS "EventID", e.inquiry_id AS "InquiryID", e.customer_id AS "CustomerID",
       gold.dk(e.booked_at::date) AS "BookingDateKey", gold.dk(e.event_date) AS "EventDateKey", gold.dk(e.cancelled_at::date) AS "CancelledDateKey",
       coalesce(p."PackageKey", -1) AS "PackageKey",
       coalesce(c."ChannelKey", (SELECT "ChannelKey" FROM gold."DimChannel" WHERE "ChannelName" = 'Walk-in'), -1) AS "ChannelKey",
       e.complexity_level AS "ComplexityLevel",
       CASE WHEN e.guest_count < 250 THEN '< 250' WHEN e.guest_count < 450 THEN '250–449' ELSE '450+' END AS "GuestBand",
       e.guest_count AS "GuestCount", e.agreed_fee AS "AgreedFee",
       coalesce(a.staff, 0) AS "StaffCount", coalesce(a.total, 0) AS "PlannedHoursTotal",
       coalesce(a.planning, 0) AS "PlanningHours", coalesce(a.event_day, 0) AS "EventDayHours",
       e.booking_lead_days AS "BookingLeadDays", e.is_cancelled::int AS "IsCancelled"
FROM stg.event e
LEFT JOIN stg.inquiry i ON i.inquiry_id = e.inquiry_id
LEFT JOIN gold."DimChannel" c ON c."ChannelID" = i.channel_id
LEFT JOIN gold."DimPackage" p ON p."PackageID" = e.package_id
LEFT JOIN (SELECT event_id, count(DISTINCT employee_id) AS staff, sum(workload_hours) AS total, sum(planning_hours) AS planning,
                  sum(event_day_hours) AS event_day FROM stg.assignment WHERE status = 'Active' GROUP BY 1) a ON a.event_id = e.event_id;
SELECT count(*) AS rows_built FROM work."FactEventBooking";
