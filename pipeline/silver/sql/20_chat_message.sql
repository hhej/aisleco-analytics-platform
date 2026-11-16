-- chat_message (Spec 02 §4.3): one real message, unique on (platform, platform_message_id).
-- Every Bronze chat line is parsed: LINE one row per events[] element of type message, Meta one row per entry[].messaging[]
-- element, outbound one row per record. Key: LINE message.id, Meta message.mid, outbound message_id. De-duplicated keeping
-- the earliest received_at (re-deliveries counted, R22). Platform epoch ms (UTC) and outbound sent_at (UTC ISO) become
-- Asia/Bangkok local time. Tracking code: a leading #[A-Z0-9]+ in the text, else Meta referral.ad_id. Sender: inbound →
-- customer; outbound → staff or bot; staff_code → employee_id. Linked to CRM ChatMessage on PlatformMessageID → inquiry_id,
-- customer_account_id; a Bronze message without a CRM row, or a CRM row without a Bronze message, is flagged (R24).
-- Text → features (text_len, lang, has_question, 11 intent flags from silver/intents.yml); the text itself is not written here.
-- _received_at and _copies (extra deliveries of the kept message) are internal: rule C01 reconciles Bronze events against them.
WITH raw AS (
    SELECT platform AS source, body, body_sha256, _run_id, json_valid(body) AS ok,
           timezone('Asia/Bangkok', received_at::TIMESTAMPTZ) AS received_at
    FROM bronze.chat
),
line_ev AS (
    SELECT * EXCLUDE (body), unnest(from_json(body -> '$.events', '["JSON"]')) AS ev
    FROM raw WHERE source = 'line' AND ok
),
meta_entry AS (
    SELECT * EXCLUDE (body), unnest(from_json(body -> '$.entry', '["JSON"]')) AS entry
    FROM raw WHERE source IN ('messenger', 'instagram') AND ok
),
meta_ev AS (
    SELECT * EXCLUDE (entry), unnest(from_json(entry -> '$.messaging', '["JSON"]')) AS ev FROM meta_entry
),
parsed AS (
    SELECT source AS platform, received_at, body_sha256, _run_id,
           ev ->> '$.message.id' AS msg_id,
           timezone('Asia/Bangkok', to_timestamp((ev ->> '$.timestamp')::BIGINT / 1000)) AS sent_at,
           ev ->> '$.message.text' AS text,
           'In' AS direction, 'customer' AS sender_type, NULL AS staff_code, NULL AS ad_id,
           len(list_filter(json_keys(ev -> '$.message'), lambda k: NOT list_contains(
               ['id', 'type', 'text', 'quoteToken', 'emojis', 'mention', 'stickerId', 'packageId', 'stickerResourceType',
                'keywords', 'contentProvider'], k))) > 0 AS unknown_field
    FROM line_ev WHERE ev ->> '$.type' = 'message'
    UNION ALL
    SELECT source, received_at, body_sha256, _run_id,
           ev ->> '$.message.mid',
           timezone('Asia/Bangkok', to_timestamp((ev ->> '$.timestamp')::BIGINT / 1000)),
           ev ->> '$.message.text',
           'In', 'customer', NULL, ev ->> '$.referral.ad_id', false
    FROM meta_ev WHERE (ev -> '$.message') IS NOT NULL         -- delivery and read receipts are not messages
    UNION ALL
    SELECT body ->> '$.platform', received_at, body_sha256, _run_id,
           body ->> '$.message_id',
           timezone('Asia/Bangkok', try_cast(body ->> '$.sent_at' AS TIMESTAMPTZ)),
           body ->> '$.text',
           'Out', body ->> '$.sender_type', body ->> '$.staff_code', NULL, false
    FROM raw WHERE source = 'outbound' AND ok
),
rejected AS (    -- R20: not JSON, or a message without its key, time or platform
    SELECT source AS platform, received_at, body_sha256, _run_id, CASE WHEN source = 'outbound' THEN 'Out' ELSE 'In' END AS direction
    FROM raw WHERE NOT ok
    UNION ALL
    SELECT platform, received_at, body_sha256, _run_id, direction FROM parsed
    WHERE msg_id IS NULL OR sent_at IS NULL OR platform IS NULL
),
ranked AS (
    SELECT *,
           row_number() OVER (PARTITION BY platform, msg_id ORDER BY received_at, body_sha256) AS rn,
           count(*) OVER (PARTITION BY platform, msg_id) - 1 AS copies
    FROM parsed WHERE msg_id IS NOT NULL AND sent_at IS NOT NULL AND platform IS NOT NULL
),
bronze_msg AS (
    SELECT * EXCLUDE (rn) FROM ranked WHERE rn = 1
),
intent_rx AS (
    SELECT intent, string_agg('(?:' || pattern || ')', '|') AS rx FROM ref.intent_pattern GROUP BY intent
),
-- a pattern read from a table is compiled once per row, so each distinct text is tagged once (chats repeat a lot)
tags AS (
    SELECT t.text, list(p.intent) AS intents
    FROM (SELECT DISTINCT text FROM bronze_msg WHERE direction = 'In') t
    JOIN intent_rx p ON regexp_matches(t.text, p.rx, 'i')
    GROUP BY ALL
),
features AS (
    SELECT b.*,
           length(regexp_replace(b.text, '[^\x{0E00}-\x{0E7F}]', '', 'g')) AS thai_chars,
           length(regexp_replace(b.text, '[^A-Za-z]', '', 'g')) AS latin_chars,
           CASE WHEN b.direction = 'In' THEN coalesce(t.intents, []) ELSE [] END AS intents
    FROM bronze_msg b LEFT JOIN tags t USING (text)
),
crm AS (
    SELECT c.*, a."Platform" AS account_platform
    FROM crm."ChatMessage" c LEFT JOIN crm."CustomerChannelAccount" a USING ("CustomerAccountID")
),
joined AS (
    SELECT coalesce(f.platform, c.account_platform) AS platform,
           coalesce(f.msg_id, c."PlatformMessageID") AS platform_message_id,
           coalesce(f.sent_at, c."SentAt") AS sent_at,
           coalesce(f.direction, c."Direction") AS direction,
           coalesce(f.sender_type, lower(c."SenderType")) AS sender_type,
           coalesce(c."EmployeeID", e."EmployeeID") AS employee_id,
           c."InquiryID" AS inquiry_id,
           c."CustomerAccountID" AS customer_account_id,
           CASE WHEN f.msg_id IS NULL THEN c."TrackingCode"
                ELSE coalesce(nullif(regexp_extract(f.text, '^\s*(#[A-Z0-9]+)', 1), ''), f.ad_id) END AS tracking_code,
           length(f.text) AS text_len,
           CASE WHEN f.text IS NULL THEN NULL
                WHEN f.thai_chars > 0.5 * (f.thai_chars + f.latin_chars) THEN 'th'
                WHEN f.thai_chars = 0 THEN 'en' ELSE 'mixed' END AS lang,
           regexp_matches(f.text, '\?|ไหม|มั้ย|มั๊ย|หรือเปล่า|ยัง|ไร') AS has_question,
           f.intents, f.staff_code, f.received_at, f.copies, f.unknown_field,
           f.msg_id IS NOT NULL AS in_bronze, c."ChatMessageID" IS NOT NULL AS in_crm,
           e."EmployeeID" IS NULL AND f.staff_code IS NOT NULL AS unmapped_code,
           coalesce(f._run_id, c._run_id) AS _run_id
    FROM features f
    FULL JOIN crm c ON c."PlatformMessageID" = f.msg_id
    LEFT JOIN crm."Employee" e ON e."EmployeeCode" = f.staff_code
)
SELECT platform, platform_message_id, sent_at, direction, sender_type, employee_id, inquiry_id, customer_account_id,
       tracking_code, text_len, lang, has_question,
       list_contains(intents, 'ask_price')::INTEGER          AS intent_ask_price,
       list_contains(intents, 'ask_availability')::INTEGER   AS intent_ask_availability,
       list_contains(intents, 'mention_date')::INTEGER       AS intent_mention_date,
       list_contains(intents, 'mention_guests')::INTEGER     AS intent_mention_guests,
       list_contains(intents, 'ask_package_detail')::INTEGER AS intent_ask_package_detail,
       list_contains(intents, 'ask_meeting')::INTEGER        AS intent_ask_meeting,
       list_contains(intents, 'price_objection')::INTEGER    AS intent_price_objection,
       list_contains(intents, 'family_decision')::INTEGER    AS intent_family_decision,
       list_contains(intents, 'competitor')::INTEGER         AS intent_competitor,
       list_contains(intents, 'positive')::INTEGER           AS intent_positive,
       list_contains(intents, 'commit')::INTEGER             AS intent_commit,
       CASE WHEN sender_type = 'staff' AND employee_id IS NULL THEN 'R07' END AS _reject,
       list_filter([
           CASE WHEN sent_at < received_at - INTERVAL 7 DAY OR sent_at > received_at + INTERVAL 5 MINUTE THEN 'R21' END,
           CASE WHEN copies > 0 THEN 'R22' END,
           CASE WHEN unmapped_code THEN 'R23' END,
           CASE WHEN in_bronze <> in_crm THEN 'R24' END,
           CASE WHEN unknown_field THEN 'S05' END], lambda w: w IS NOT NULL) AS _warn,
       platform || ':' || platform_message_id AS _key,
       _run_id,
       received_at AS _received_at,
       coalesce(copies, 0) AS _copies
FROM joined
UNION ALL BY NAME
-- R20 key: one index entry per received body (identical malformed bodies are separate deliveries)
SELECT platform, direction, 'R20' AS _reject, [] AS _warn, body_sha256 || '@' || received_at AS _key, _run_id, received_at AS _received_at, 0 AS _copies
FROM rejected
