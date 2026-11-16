-- Silver inquiry (Spec 02 §4.5): one row per inquiry, current version (the view crm."Inquiry" already keeps the latest
-- version per key and drops deleted keys). Budget text → canonical band; first-reply time and message counts from
-- chat_message (bot messages never count as a reply). Stage timestamps are kept as they are; problems are flagged.
WITH m AS (
  SELECT inquiry_id,
         min(sent_at) FILTER (WHERE direction = 'In') AS first_customer_msg_at,
         max(sent_at) FILTER (WHERE direction = 'In') AS last_customer_msg_at,
         count(*) FILTER (WHERE direction = 'In') AS customer_msgs,
         count(*) FILTER (WHERE sender_type = 'staff') AS staff_msgs,
         arg_min(tracking_code, sent_at) FILTER (WHERE direction = 'In') AS first_tracking_code
  FROM chat_message WHERE inquiry_id IS NOT NULL GROUP BY 1),
r AS (
  SELECT m.inquiry_id, min(c.sent_at) AS first_staff_reply_at
  FROM m JOIN chat_message c ON c.inquiry_id = m.inquiry_id AND c.sender_type = 'staff' AND c.sent_at > m.first_customer_msg_at
  GROUP BY 1),
b AS (  -- budget: thousands of baht read from free text ("around 600k", "6 แสน", "1.2M", "ประมาณ 1 ล้าน", "800,000")
  SELECT "InquiryID", lower(trim("BudgetRange")) AS t,
         CASE WHEN lower(trim("BudgetRange")) IN ('300k-500k', '500k-1m', '1m-1.5m', '1.5m+') THEN NULL
              ELSE coalesce(
                try_cast(regexp_extract(replace(lower("BudgetRange"), 'ห้า', '5'), '([0-9.]+)\s*แสน', 1) AS DOUBLE) * 100,
                try_cast(regexp_extract(lower("BudgetRange"), '([0-9.]+)\s*(m|ล้าน)', 1) AS DOUBLE) * 1000,
                try_cast(regexp_extract(lower("BudgetRange"), '([0-9.]+)\s*k', 1) AS DOUBLE),
                try_cast(replace(regexp_extract("BudgetRange", '([0-9][0-9,]{4,})', 1), ',', '') AS DOUBLE) / 1000)
         END AS k
  FROM crm."Inquiry"),
band AS (
  SELECT "InquiryID", CASE
      WHEN t IN ('300k-500k', '500k-1m', '1m-1.5m', '1.5m+') THEN replace(replace(t, '1m', '1M'), '-1m', '-1M')
      WHEN k IS NULL THEN 'Unknown'
      WHEN k <= 500 THEN '300k-500k' WHEN k < 1000 THEN '500k-1M' WHEN k < 1500 THEN '1M-1.5M' ELSE '1.5M+' END AS budget_band
  FROM b),
x AS (
  SELECT i.*, band.budget_band, m.*, r.first_staff_reply_at, ch."ChannelName" AS channel_name, ch."ChannelType" AS channel_type,
         tc."ChannelName" AS code_channel, ch."ChannelID" IS NULL AS channel_missing,
         EXISTS (SELECT 1 FROM crm."Event" e WHERE e."InquiryID" = i."InquiryID") AS has_event
  FROM crm."Inquiry" i
  JOIN band USING ("InquiryID")
  LEFT JOIN m ON m.inquiry_id = i."InquiryID"
  LEFT JOIN r ON r.inquiry_id = i."InquiryID"
  LEFT JOIN crm."MarketingChannel" ch ON ch."ChannelID" = i."ChannelID"
  LEFT JOIN crm."MarketingChannel" tc ON tc."TrackingCode" = m.first_tracking_code)
SELECT "InquiryID" AS inquiry_id, "CustomerID" AS customer_id, CASE WHEN channel_missing THEN -1 ELSE "ChannelID" END AS channel_id,
       "IndicativePackageID" AS indicative_package_id, "AssignedSalesEmployeeID" AS sales_owner_id,
       "InquiryDate" AS inquiry_at, "RequestedEventDate" AS requested_event_date,
       "BudgetRange" AS budget_text, budget_band,
       CASE budget_band WHEN '300k-500k' THEN 300000 WHEN '500k-1M' THEN 500000 WHEN '1M-1.5M' THEN 1000000 WHEN '1.5M+' THEN 1500000 END AS budget_low_thb,
       CASE budget_band WHEN '300k-500k' THEN 500000 WHEN '500k-1M' THEN 1000000 WHEN '1M-1.5M' THEN 1500000 WHEN '1.5M+' THEN 2000000 END AS budget_high_thb,
       CASE budget_band WHEN '300k-500k' THEN 400000 WHEN '500k-1M' THEN 750000 WHEN '1M-1.5M' THEN 1250000 WHEN '1.5M+' THEN 1750000 END AS budget_midpoint_thb,
       "ContactedDate" AS contacted_at, "ConsultationDate" AS consultation_at, "QuotedDate" AS quoted_at, "QuotedAmount" AS quoted_amount,
       "ClosedDate" AS closed_at, "ClosedBy" AS closed_by, "Status" AS status, "LostReason" AS lost_reason,
       first_customer_msg_at, first_staff_reply_at, last_customer_msg_at,
       CASE WHEN channel_type = 'Offline' THEN NULL
            ELSE round(epoch(first_staff_reply_at - first_customer_msg_at) / 60, 1) END AS response_minutes,
       CASE WHEN channel_type = 'Offline' THEN 'in person'
            WHEN first_staff_reply_at IS NULL THEN 'no logged reply'
            WHEN first_staff_reply_at - first_customer_msg_at < INTERVAL 1 HOUR THEN '< 1 h'
            WHEN first_staff_reply_at - first_customer_msg_at <= INTERVAL 24 HOUR THEN '1–24 h'
            ELSE '> 24 h' END AS response_band,
       hour(coalesce(first_customer_msg_at, "InquiryDate")) AS first_msg_hour,
       hour(coalesce(first_customer_msg_at, "InquiryDate")) NOT BETWEEN 9 AND 19 AS arrived_outside_hours,
       coalesce(customer_msgs, 0) AS customer_msgs, coalesce(staff_msgs, 0) AS staff_msgs, first_tracking_code,
       "UpdatedAt" AS updated_at,
       CASE WHEN "InquiryID" IS NULL THEN 'R01'
            WHEN "InquiryDate" IS NULL OR "InquiryDate" < DATE '2022-01-01' OR "InquiryDate" >= (SELECT "asof" FROM ref.run) + 1 THEN 'R02' END AS _reject,
       list_filter([
         CASE WHEN channel_missing THEN 'R03' END,
         CASE WHEN code_channel IS NOT NULL AND code_channel <> channel_name THEN 'R03b' END,
         CASE WHEN least("ContactedDate", "ConsultationDate", "QuotedDate", "ClosedDate") < "InquiryDate"
                OR "QuotedDate" < "ConsultationDate" THEN 'R04' END,
         CASE WHEN budget_band = 'Unknown' THEN 'R05' END,
         CASE WHEN ("Status" = 'Converted' AND NOT has_event) OR ("Status" = 'Lost' AND "LostReason" IS NULL) THEN 'R06' END
       ], lambda w: w IS NOT NULL) AS _warn,
       "InquiryID"::VARCHAR AS _key, _run_id
FROM x
