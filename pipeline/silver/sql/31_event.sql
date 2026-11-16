-- Silver event (Spec 02 §4.7): one row per booked wedding, current version.
SELECT "EventID" AS event_id, "CustomerID" AS customer_id, "InquiryID" AS inquiry_id, "PackageID" AS package_id,
       "EventDate" AS event_date, "GuestCount" AS guest_count, "ComplexityLevel" AS complexity_level,
       "AgreedPackagePrice" AS agreed_fee, "Status" AS status, "Status" = 'Cancelled' AS is_cancelled,
       "CancelledDate" AS cancelled_at, "CreatedDate" AS booked_at,
       date_diff('day', "CreatedDate"::DATE, "EventDate") AS booking_lead_days, "UpdatedAt" AS updated_at,
       CASE WHEN "EventDate" IS NULL OR "GuestCount" NOT BETWEEN 20 AND 2000 THEN 'R08a' END AS _reject,
       list_filter([
         CASE WHEN "ComplexityLevel" NOT IN ('Low', 'Medium', 'High') OR "ComplexityLevel" IS NULL THEN 'R08b' END,
         CASE WHEN "InquiryID" IS NOT NULL AND "InquiryID" NOT IN (SELECT "InquiryID" FROM crm."Inquiry") THEN 'R09' END
       ], lambda w: w IS NOT NULL) AS _warn,
       "EventID"::VARCHAR AS _key, _run_id
FROM crm."Event"
