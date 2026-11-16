-- auspicious_date (Spec 02 §4.1 step 4 input; Spec 01 §5b): the auspicious wedding dates compiled by marketing, one row
-- per (calendar_date, date_type), typed. Checks: level in Low/Medium/High; date inside the stated year (R41).
WITH src AS (
    SELECT try_cast(calendar_date AS DATE) AS calendar_date,
           trim(date_type) AS date_type,
           trim(auspicious_level) AS auspicious_level,
           description,
           calendar_date AS raw_date,
           regexp_extract(filename, 'year=(\d{4})', 1)::INTEGER AS file_year
    FROM ext.auspicious
)
SELECT calendar_date, date_type, auspicious_level, description,
       CASE WHEN auspicious_level NOT IN ('Low', 'Medium', 'High') OR auspicious_level IS NULL
                 OR calendar_date IS NULL OR year(calendar_date) <> file_year THEN 'R41' END AS _reject,
       raw_date || '/' || date_type AS _key
FROM src
