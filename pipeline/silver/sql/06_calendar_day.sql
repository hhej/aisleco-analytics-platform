-- calendar_day (Spec 02 §4.1): one calendar day, 2022-01-01 to 2028-12-31.
-- Holidays from the latest ICS fetch (several on one date joined with " / "); Buddhist Lent = Asalha Bucha + 1 (Khao Phansa)
-- through + 89 (Ok Phansa) — 2025: 11 Jul – 7 Oct, 2026: 30 Jul – 26 Oct; a year without Asalha Bucha in the feed is
-- approximated as 20 Jul – 17 Oct (lent_source = 'approximated'). Auspicious level = the highest over all date types that day
-- (High > Medium > Low), types joined. Flags: high season (Nov–Feb), Valentine's Day (from the ICS), weekend, public holiday.
-- Week start = the Monday of the ISO week.
WITH spine AS (
    SELECT d::DATE AS date FROM generate_series(DATE '2022-01-01', DATE '2028-12-31', INTERVAL 1 DAY) t(d)
),
hol AS (
    SELECT date,
           string_agg(name, ' / ' ORDER BY name) AS holiday_name,
           -- the Google feed also lists observances that are not days off
           bool_or(name NOT IN ('Valentine''s Day', 'Christmas Eve', 'Christmas Day', 'Chinese New Year''s Day',
                                'Second Day of Chinese New Year', 'Third Day of Chinese New Year', 'Mahidol Day')) AS is_public_holiday,
           bool_or(name = 'Valentine''s Day') AS is_valentine
    FROM ext.holidays GROUP BY date
),
years AS (
    SELECT y, min(h.date) AS asalha
    FROM (SELECT DISTINCT year(date) AS y FROM spine) s
    LEFT JOIN ext.holidays h ON h.name = 'Asalha Bucha' AND year(h.date) = s.y
    GROUP BY y
),
lent AS (
    SELECT y,
           coalesce(asalha + 1, make_date(y, 7, 20)) AS lent_start,
           coalesce(asalha + 89, make_date(y, 10, 17)) AS lent_end,
           CASE WHEN asalha IS NULL THEN 'approximated' ELSE 'ics' END AS lent_source
    FROM years
),
ausp AS (
    SELECT calendar_date,
           CASE max(CASE auspicious_level WHEN 'High' THEN 3 WHEN 'Medium' THEN 2 ELSE 1 END)
                WHEN 3 THEN 'High' WHEN 2 THEN 'Medium' ELSE 'Low' END AS auspicious_level,
           string_agg(DISTINCT date_type, ', ' ORDER BY date_type) AS auspicious_types
    FROM auspicious_date GROUP BY calendar_date
)
SELECT s.date,
       date_trunc('week', s.date)::DATE AS week_start,
       isoyear(s.date) AS iso_year,
       weekofyear(s.date) AS iso_week,
       month(s.date) AS month,
       year(s.date) AS year,
       isodow(s.date) >= 6 AS is_weekend,
       h.holiday_name,
       coalesce(h.is_public_holiday, false) AS is_public_holiday,
       s.date BETWEEN l.lent_start AND l.lent_end AS is_buddhist_lent,
       l.lent_source,
       month(s.date) IN (11, 12, 1, 2) AS is_high_season,
       coalesce(h.is_valentine, false) AS is_valentine,
       coalesce(a.auspicious_level, 'None') AS auspicious_level,
       a.auspicious_types
FROM spine s
JOIN lent l ON l.y = year(s.date)
LEFT JOIN hol h ON h.date = s.date
LEFT JOIN ausp a ON a.calendar_date = s.date
ORDER BY s.date
