-- restricted_lead_text (Spec 02 §4.3 step 9, §4.8): masked free text for the ML job only — (source chat|fair, message or
-- submission id, inquiry_id, sent_at, direction, sender_type, masked text). Phone numbers, e-mail addresses and LINE IDs are
-- replaced by <PHONE>, <EMAIL>, <LINEID>. Chat rows are the published chat_message rows (already de-duplicated, rejects
-- removed) with their text re-read from Bronze; fair rows are the tablet sign-ups' non-empty wishes.
WITH raw AS MATERIALIZED (
    SELECT platform AS source, body, _run_id FROM bronze.chat WHERE json_valid(body)
),
chat_text AS (
    SELECT source AS platform, ev ->> '$.message.id' AS msg_id, ev ->> '$.message.text' AS text, _run_id
    FROM (SELECT source, _run_id, unnest(from_json(body -> '$.events', '["JSON"]')) AS ev FROM raw WHERE source = 'line')
    UNION ALL
    SELECT source, ev ->> '$.message.mid', ev ->> '$.message.text', _run_id
    FROM (SELECT source, _run_id, unnest(from_json(entry -> '$.messaging', '["JSON"]')) AS ev
          FROM (SELECT source, _run_id, unnest(from_json(body -> '$.entry', '["JSON"]')) AS entry
                FROM raw WHERE source IN ('messenger', 'instagram')))
    UNION ALL
    SELECT body ->> '$.platform', body ->> '$.message_id', body ->> '$.text', _run_id FROM raw WHERE source = 'outbound'
),
chat AS (
    SELECT 'chat' AS source, m.platform_message_id AS message_id, m.inquiry_id, m.sent_at, m.direction, m.sender_type,
           min(t.text) AS text, min(t._run_id) AS _run_id       -- re-deliveries carry the same text
    FROM chat_message m JOIN chat_text t ON t.platform = m.platform AND t.msg_id = m.platform_message_id
    WHERE t.text IS NOT NULL
    GROUP BY ALL
),
fair AS (
    SELECT 'fair' AS source, s ->> '$.submission_id' AS message_id, (s ->> '$.crm_inquiry_id')::INTEGER AS inquiry_id,
           timezone('Asia/Bangkok', (s ->> '$.submitted_at')::TIMESTAMPTZ) AS sent_at, 'In' AS direction, 'customer' AS sender_type,
           s ->> '$.wishes' AS text, _run_id,
           row_number() OVER (PARTITION BY s ->> '$.submission_id' ORDER BY filename) AS rn
    FROM (SELECT filename, _run_id, unnest(from_json(content -> '$.submissions', '["JSON"]')) AS s
          FROM bronze.fair_files WHERE json_valid(content))
),
texts AS (
    SELECT * FROM chat
    UNION ALL BY NAME
    SELECT * EXCLUDE (rn) FROM fair WHERE rn = 1 AND trim(text) <> ''
)
SELECT source, message_id, inquiry_id, sent_at, direction, sender_type,
       regexp_replace(regexp_replace(regexp_replace(regexp_replace(text,
           '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', '<EMAIL>', 'g'),
           '(?:\+66[-\s]?|0)\d{1,2}[-\s.]?\d{3}[-\s.]?\d{3,4}', '<PHONE>', 'g'),
           '(?i)line\s*id\s*[:：]?\s*@?[A-Za-z0-9._-]+', 'line id: <LINEID>', 'g'),
           '@[A-Za-z0-9._-]{3,}', '<LINEID>', 'g') AS text_masked,
       source || ':' || message_id AS _key,
       _run_id
FROM texts
