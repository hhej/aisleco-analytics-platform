-- fair_lead (Spec 02 §4.6): one wedding-fair sign-up (submission_id) from the booth tablets.
-- Explode submissions[]; keep the first sync of each submission_id (repeated batches counted, R31);
-- phone_hash = SHA-256(salt ‖ digits of phone); names, phone and LINE ID are not output (wishes go to restricted lead_text);
-- sync_delay_hours = synced_at − submitted_at; crm_inquiry_id checked against the CRM inquiries (R32).
-- A file that is not valid JSON is one rejected row (R30).
WITH files AS (
    SELECT filename, content, _run_id, json_valid(content) AS ok FROM bronze.fair_files
),
batches AS (
    SELECT filename, _run_id,
           content ->> '$.device_id' AS device_id,
           (content ->> '$.batch_seq')::INTEGER AS batch_seq,
           content ->> '$.fair.name' AS fair_name,
           (content ->> '$.fair.date')::DATE AS fair_date,
           timezone('Asia/Bangkok', (content ->> '$.synced_at')::TIMESTAMPTZ) AS synced_at,
           count(*) OVER (PARTITION BY content ->> '$.device_id', content ->> '$.batch_seq') AS files_of_batch,
           unnest(from_json(content -> '$.submissions', '["JSON"]')) AS s
    FROM files WHERE ok
),
subs AS (
    SELECT b.* EXCLUDE (s),
           s ->> '$.submission_id' AS submission_id,
           timezone('Asia/Bangkok', (s ->> '$.submitted_at')::TIMESTAMPTZ) AS submitted_at,
           (s ->> '$.crm_inquiry_id')::INTEGER AS inquiry_id,
           (s ->> '$.wedding.date')::DATE AS wedding_date,
           (s ->> '$.wedding.date_flexible')::BOOLEAN AS date_flexible,
           (s ->> '$.wedding.guests')::INTEGER AS guests,
           s ->> '$.wedding.venue_type' AS venue_type,
           s ->> '$.budget_band' AS budget_band,
           s ->> '$.package_interest' AS package_interest,
           (s ->> '$.consent_marketing')::BOOLEAN AS consent_marketing,
           regexp_replace(s ->> '$.couple.phone', '\D', '', 'g') AS phone_digits,
           row_number() OVER (PARTITION BY s ->> '$.submission_id' ORDER BY synced_at, filename) AS rn
    FROM batches b
)
SELECT submission_id, device_id, fair_name, fair_date, submitted_at,
       round(date_diff('second', submitted_at, synced_at) / 3600, 2) AS sync_delay_hours,
       inquiry_id, wedding_date, date_flexible, guests, venue_type, budget_band, package_interest, consent_marketing,
       sha256((SELECT value FROM ref.settings WHERE key = 'pii_salt') || phone_digits) AS phone_hash,
       NULL AS _reject,
       list_filter([
           CASE WHEN files_of_batch > 1 THEN 'R31' END,
           CASE WHEN inquiry_id IS NULL OR inquiry_id NOT IN (SELECT "InquiryID" FROM crm."Inquiry") THEN 'R32' END,
           CASE WHEN coalesce(length(phone_digits), 0) NOT BETWEEN 9 AND 10 THEN 'R33' END], lambda w: w IS NOT NULL) AS _warn,
       submission_id AS _key,
       _run_id
FROM subs WHERE rn = 1
UNION ALL BY NAME
SELECT 'R30' AS _reject, [] AS _warn, filename AS _key, _run_id FROM files WHERE NOT ok
