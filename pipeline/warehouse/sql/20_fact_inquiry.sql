-- FactInquiry (Spec 03 §4.1): one row per inquiry, an accumulating snapshot. Builds work."FactInquiry"; the
-- generic merge loads it. Outcome labels go to the junk dimension DimInquiryOutcome (new combinations only).
DROP TABLE IF EXISTS work.inq, work."FactInquiry";

-- an inquiry counts as booked if an event came from it, or from a later inquiry of the same couple within 120 days
-- (a couple who wrote on LINE and on Facebook appears twice until sales merges them)
CREATE TABLE work.inq AS
SELECT i.*,
       own.event_id AS own_event, own.booked_at AS own_booked_at,
       (SELECT min(e.booked_at) FROM stg.event e JOIN stg.inquiry j ON j.inquiry_id = e.inquiry_id
        WHERE j.customer_id = i.customer_id AND j.inquiry_id <> i.inquiry_id AND j.inquiry_at >= i.inquiry_at
          AND e.booked_at < i.inquiry_at + interval '120 days') AS sibling_booked_at,
       (SELECT employee_id FROM stg.chat_message m WHERE m.inquiry_id = i.inquiry_id AND m.sender_type = 'staff'
          AND m.sent_at = i.first_staff_reply_at LIMIT 1) AS responder_id,
       (SELECT count(*) FROM stg.sales_activity s WHERE s.inquiry_id = i.inquiry_id) AS follow_ups
FROM stg.inquiry i
LEFT JOIN stg.event own ON own.inquiry_id = i.inquiry_id;

ALTER TABLE work.inq ADD COLUMN booked_at timestamp, ADD COLUMN outcome text, ADD COLUMN furthest text;
UPDATE work.inq SET booked_at = coalesce(own_booked_at, sibling_booked_at);
UPDATE work.inq SET
  outcome = CASE WHEN booked_at IS NOT NULL THEN 'Booked' WHEN status = 'Lost' THEN 'Lost' WHEN closed_at IS NULL THEN 'Open' ELSE 'Closed' END,
  furthest = CASE WHEN booked_at IS NOT NULL THEN 'Booked' WHEN quoted_at IS NOT NULL THEN 'Quoted' WHEN consultation_at IS NOT NULL THEN 'Consulted'
                  WHEN contacted_at IS NOT NULL OR first_staff_reply_at IS NOT NULL THEN 'Contacted' ELSE 'New' END;

INSERT INTO gold."DimInquiryOutcome" ("Outcome", "FurthestStage", "ClosedBy", "LostReason")
SELECT DISTINCT outcome, furthest, CASE WHEN outcome = 'Booked' THEN 'Sales' ELSE coalesce(closed_by, 'N/A') END,
       CASE WHEN outcome = 'Lost' THEN coalesce(lost_reason, 'N/A') ELSE 'N/A' END FROM work.inq
ON CONFLICT DO NOTHING;

CREATE TABLE work."FactInquiry" AS
SELECT i.inquiry_id AS "InquiryID", i.customer_id AS "CustomerID",
       gold.dk(i.inquiry_at::date) AS "InquiryDateKey", gold.dk(i.first_staff_reply_at::date) AS "FirstReplyDateKey",
       gold.dk(i.consultation_at::date) AS "ConsultationDateKey", gold.dk(i.quoted_at::date) AS "QuoteDateKey",
       gold.dk(i.closed_at::date) AS "ClosedDateKey", gold.dk(i.booked_at::date) AS "BookingDateKey",
       gold.dk(i.requested_event_date) AS "RequestedEventDateKey",
       gold.dk(greatest(i.last_customer_msg_at, i.consultation_at, i.inquiry_at)::date) AS "LastActivityDateKey",
       coalesce(c."ChannelKey", -1) AS "ChannelKey", coalesce(p."PackageKey", -1) AS "IndicativePackageKey",
       coalesce(r."EmployeeKey", -1) AS "FirstResponderKey", coalesce(o2."EmployeeKey", -1) AS "SalesOwnerKey",
       oc."OutcomeKey",
       i.budget_band AS "BudgetBand", i.response_band AS "ResponseBand", i.first_msg_hour AS "FirstMessageHour",
       1 AS "InquiryCount",
       (i.first_staff_reply_at IS NOT NULL OR i.response_band = 'in person')::int AS "IsReplied",
       (i.consultation_at IS NOT NULL)::int AS "IsConsulted", (i.quoted_at IS NOT NULL)::int AS "IsQuoted",
       (i.booked_at IS NOT NULL)::int AS "IsBooked", (i.outcome = 'Open')::int AS "IsOpen",
       (i.outcome = 'Lost' AND i.closed_by = 'AutoRule')::int AS "IsAutoClosed", gold.is_holdout(i.inquiry_id) AS "IsHoldout",
       i.response_minutes AS "ResponseMinutes",
       i.consultation_at::date - i.inquiry_at::date AS "DaysToConsult", i.quoted_at::date - i.inquiry_at::date AS "DaysToQuote",
       i.closed_at::date - i.inquiry_at::date AS "DaysToClose", i.booked_at::date - i.inquiry_at::date AS "DaysToBook",
       i.requested_event_date - i.inquiry_at::date AS "LeadDays",
       i.quoted_amount AS "QuotedAmount", i.budget_midpoint_thb AS "BudgetMidpoint",
       i.customer_msgs AS "CustomerMessages", i.staff_msgs AS "StaffMessages", i.follow_ups AS "FollowUps"
FROM work.inq i
LEFT JOIN gold."DimChannel" c ON c."ChannelID" = i.channel_id
LEFT JOIN gold."DimPackage" p ON p."PackageID" = i.indicative_package_id
LEFT JOIN gold."DimEmployee" r ON r."EmployeeID" = i.responder_id AND i.first_staff_reply_at::date BETWEEN r."ValidFrom" AND r."ValidTo"
LEFT JOIN gold."DimEmployee" o2 ON o2."EmployeeID" = i.sales_owner_id AND i.inquiry_at::date BETWEEN o2."ValidFrom" AND o2."ValidTo"
JOIN gold."DimInquiryOutcome" oc ON (oc."Outcome", oc."FurthestStage", oc."ClosedBy", oc."LostReason") =
     (i.outcome, i.furthest, CASE WHEN i.outcome = 'Booked' THEN 'Sales' ELSE coalesce(i.closed_by, 'N/A') END,
      CASE WHEN i.outcome = 'Lost' THEN coalesce(i.lost_reason, 'N/A') ELSE 'N/A' END);

SELECT count(*) AS rows_built FROM work."FactInquiry";
