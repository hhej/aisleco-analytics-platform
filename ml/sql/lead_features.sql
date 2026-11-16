-- Feature store definition for the lead-conversion model (Spec 04 §3). DuckDB SQL over the Silver tables.
-- Input: spine(inquiry_id, cutoff) = which inquiry, as of which moment. Every feature uses only rows with timestamps
-- BEFORE the cutoff (point in time), so the same definition serves training (many past cutoffs) and scoring (today 00:00).
-- Never used: status, closed_at, closed_by, lost_reason, anything from event about this inquiry, updated_at, holdout.
WITH s AS (SELECT inquiry_id, cutoff FROM spine),
i AS (
  SELECT s.*, q.customer_id, q.inquiry_at, q.channel_id, q.indicative_package_id, q.requested_event_date, q.budget_band,
         q.budget_midpoint_thb, q.arrived_outside_hours, q.first_tracking_code,
         CASE WHEN q.first_staff_reply_at < s.cutoff THEN q.response_minutes END AS response_minutes,
         CASE WHEN q.consultation_at < s.cutoff THEN q.consultation_at END AS consultation_at,
         CASE WHEN q.quoted_at < s.cutoff THEN q.quoted_at END AS quoted_at,
         CASE WHEN q.quoted_at < s.cutoff THEN q.quoted_amount END AS quoted_amount
  FROM s JOIN inquiry q USING (inquiry_id)),
m AS (
  SELECT s.inquiry_id, s.cutoff,
         count(*) FILTER (WHERE c.direction = 'In') AS customer_msgs,
         count(*) FILTER (WHERE c.direction = 'In' AND c.sent_at >= s.cutoff - INTERVAL 3 DAY) AS customer_msgs_3d,
         count(*) FILTER (WHERE c.direction = 'In' AND c.sent_at >= s.cutoff - INTERVAL 7 DAY) AS customer_msgs_7d,
         count(*) FILTER (WHERE c.sender_type = 'staff') AS staff_msgs,
         max(c.sent_at) FILTER (WHERE c.direction = 'In') AS last_customer_msg_at,
         max(c.sent_at) FILTER (WHERE c.sender_type = 'staff') AS last_staff_msg_at,
         sum(c.intent_ask_price) AS n_ask_price, sum(c.intent_ask_availability) AS n_ask_availability,
         sum(c.intent_mention_date) AS n_mention_date, sum(c.intent_mention_guests) AS n_mention_guests,
         sum(c.intent_ask_package_detail) AS n_ask_package_detail, sum(c.intent_ask_meeting) AS n_ask_meeting,
         sum(c.intent_price_objection) AS n_price_objection, sum(c.intent_family_decision) AS n_family_decision,
         sum(c.intent_competitor) AS n_competitor, sum(c.intent_positive) AS n_positive, sum(c.intent_commit) AS n_commit
  FROM s JOIN chat_message c ON c.inquiry_id = s.inquiry_id AND c.sent_at < s.cutoff GROUP BY 1, 2),
f AS (
  SELECT s.inquiry_id, s.cutoff, count(*) AS followups, count(*) FILTER (WHERE a.activity_at >= s.cutoff - INTERVAL 7 DAY) AS followups_7d,
         max(a.activity_at) AS last_followup_at, arg_max(a.outcome = 'Reached', a.activity_at) AS last_followup_reached
  FROM s JOIN sales_activity a ON a.inquiry_id = s.inquiry_id AND a.activity_at < s.cutoff GROUP BY 1, 2),
prev AS (
  SELECT i.inquiry_id, i.cutoff, count(o.inquiry_id) AS previous_inquiries
  FROM i LEFT JOIN inquiry o ON o.customer_id = i.customer_id AND o.inquiry_at < i.inquiry_at GROUP BY 1, 2),
cap AS (  -- utilization of the requested wedding week, as booked at the cutoff (Planning + Wedding-day roles)
  SELECT i.inquiry_id, i.cutoff,
         (SELECT sum(w.hours) FROM week_hours w WHERE w.week_start = date_trunc('week', i.requested_event_date)
            AND w.assigned_at < i.cutoff AND w.booked_at < i.cutoff) /
         nullif((SELECT sum(k.standard_weekly_hours) FROM employee_contract k WHERE k.role_group IN ('Planning', 'Wedding day')
            AND date_trunc('week', i.requested_event_date) BETWEEN k.effective_from AND coalesce(k.effective_to, DATE '9999-12-31')), 0)
           AS requested_week_utilization
  FROM i WHERE i.requested_event_date IS NOT NULL)
SELECT i.inquiry_id, i.cutoff,
       -- lead profile
       ch.channel_name AS channel, coalesce(ch.platform, 'in person') AS platform, (i.first_tracking_code IS NOT NULL)::INT AS has_tracking_code,
       CASE i.budget_band WHEN '300k-500k' THEN 0 WHEN '500k-1M' THEN 1 WHEN '1M-1.5M' THEN 2 WHEN '1.5M+' THEN 3 ELSE -1 END AS budget_band_ord,
       i.budget_midpoint_thb, coalesce(p.package_type, 'None') AS package_interest,
       (i.requested_event_date IS NOT NULL)::INT AS has_requested_date,
       date_diff('day', i.cutoff::DATE, i.requested_event_date) AS lead_days,
       cd.is_high_season::INT AS requested_high_season,
       CASE cd.auspicious_level WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 1 ELSE 0 END AS requested_auspicious,
       cd.is_buddhist_lent::INT AS requested_in_lent,
       i.arrived_outside_hours::INT AS arrived_outside_hours, dayofweek(i.inquiry_at) AS inquiry_dow, month(i.inquiry_at) AS inquiry_month,
       (fl.submission_id IS NOT NULL)::INT AS fair_lead, fl.guests AS fair_guests, coalesce(fl.venue_type, 'n/a') AS fair_venue,
       -- capacity (cross-problem)
       cap.requested_week_utilization,
       -- engagement
       date_diff('day', i.inquiry_at::DATE, i.cutoff::DATE) AS age_days,
       coalesce(m.customer_msgs, 0) AS customer_msgs, coalesce(m.customer_msgs_3d, 0) AS customer_msgs_3d,
       coalesce(m.customer_msgs_7d, 0) AS customer_msgs_7d, coalesce(m.staff_msgs, 0) AS staff_msgs,
       date_diff('hour', coalesce(m.last_customer_msg_at, i.inquiry_at), i.cutoff) AS hours_since_customer_msg,
       date_diff('hour', m.last_staff_msg_at, i.cutoff) AS hours_since_staff_msg,
       ln(1 + i.response_minutes) AS log_response_minutes, (i.response_minutes < 60)::INT AS replied_within_1h,
       -- stage
       (i.consultation_at IS NOT NULL)::INT AS consulted, date_diff('day', i.consultation_at::DATE, i.cutoff::DATE) AS days_since_consult,
       (i.quoted_at IS NOT NULL)::INT AS quoted, date_diff('day', i.quoted_at::DATE, i.cutoff::DATE) AS days_since_quote,
       i.quoted_amount / nullif(i.budget_midpoint_thb, 0) AS quote_share, i.quoted_amount,
       -- text (rule intents, counts up to the cutoff)
       coalesce(m.n_ask_price, 0) AS n_ask_price, coalesce(m.n_ask_availability, 0) AS n_ask_availability,
       coalesce(m.n_mention_date, 0) AS n_mention_date, coalesce(m.n_mention_guests, 0) AS n_mention_guests,
       coalesce(m.n_ask_package_detail, 0) AS n_ask_package_detail, coalesce(m.n_ask_meeting, 0) AS n_ask_meeting,
       coalesce(m.n_price_objection, 0) AS n_price_objection, coalesce(m.n_family_decision, 0) AS n_family_decision,
       coalesce(m.n_competitor, 0) AS n_competitor, coalesce(m.n_positive, 0) AS n_positive, coalesce(m.n_commit, 0) AS n_commit,
       -- sales activity
       coalesce(f.followups, 0) AS followups, coalesce(f.followups_7d, 0) AS followups_7d,
       date_diff('day', f.last_followup_at::DATE, i.cutoff::DATE) AS days_since_followup, coalesce(f.last_followup_reached, false)::INT AS last_followup_reached,
       -- history
       prev.previous_inquiries
FROM i
LEFT JOIN m USING (inquiry_id, cutoff) LEFT JOIN f USING (inquiry_id, cutoff) LEFT JOIN prev USING (inquiry_id, cutoff)
LEFT JOIN cap USING (inquiry_id, cutoff)
LEFT JOIN channel ch ON ch.channel_id = i.channel_id
LEFT JOIN package p ON p.package_id = i.indicative_package_id
LEFT JOIN calendar_day cd ON cd.date = i.requested_event_date
LEFT JOIN fair_lead fl ON fl.inquiry_id = i.inquiry_id AND fl.submitted_at < i.cutoff
