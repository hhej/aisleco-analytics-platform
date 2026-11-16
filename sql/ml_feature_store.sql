-- =====================================================================
-- Feature store and model registry for the conversion model and the demand forecast (Chapter 7).
-- Runs after the DW v7 DDL (it reads stg, the Silver zone, and etl.budget_mid).
--
--   feature.inquiry_features(ids, cutoffs)  the one feature definition, point in time
--   feature."InquiryFeatures"               offline store: what each open inquiry looked like each day
--   feature."InquiryLabel"                  booked within 120 days or not, and when that became final
--   ml."ModelVersion"                       model registry + metadata store
--   ml."InquiryScore"                       daily batch predictions
--   ml."DriftCheck"                         monitoring results
--   feature.demand_features(asof)           demand forecast features of the next 6 months; ml."DemandForecast"
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS ml;

-- Features of inquiry ids[k] as it looked just before cutoffs[k], using only rows dated
-- before the cutoff. Training (past cutoffs) and daily scoring (today) call the same code,
-- so the model is always fed features computed the same way.
-- Only inquiries still open at the cutoff are returned: received before it, not yet booked
-- and not closed (Lost/Converted) before it. Event timestamps (messages, consultation, quote, booking, closing)
-- are taken as of the cutoff; descriptive fields (channel, budget, package, wedding date)
-- are the latest values in Silver.
CREATE OR REPLACE FUNCTION feature.inquiry_features(p_ids int[], p_cutoffs timestamp[])
RETURNS TABLE ("InquiryID" int, "AsOf" timestamp, "AgeDays" int, "Channel" text, "BudgetRange" text,
               "PackageType" text, "HasRequestedDate" int, "LeadDays" int, "PeakDate" int,
               "ResponseMinutes" int, "HasChat" int, "CoupleMsgs" int, "CoupleMsgs24h" int,
               "HoursSilent" numeric, "Consulted" int, "Quoted" int, "QuoteShare" numeric)
LANGUAGE sql STABLE AS $$
SELECT i."InquiryID", s.cutoff,
       s.cutoff::date - i."InquiryDate"::date,
       coalesce(ch."ChannelName", 'Unknown')::text,
       coalesce(i."BudgetRange", 'Unknown')::text,
       coalesce(p."PackageType", 'None')::text,
       (i."RequestedEventDate" IS NOT NULL)::int,
       i."RequestedEventDate" - i."InquiryDate"::date,
       coalesce(extract(month FROM i."RequestedEventDate") IN (11, 12, 1, 2)
                OR EXISTS (SELECT 1 FROM stg."AuspiciousDate" a WHERE a."CalendarDate" = i."RequestedEventDate"), false)::int,
       -- first staff reply minus first couple message (as in FactInquiry), once the reply exists
       CASE WHEN m.t_in IS NOT NULL THEN floor(extract(epoch FROM r.t_staff - m.t_in) / 60)::int
            WHEN i."ContactedDate" < s.cutoff THEN i."ResponseTimeMinutes" END,
       (m.t_in IS NOT NULL)::int,
       coalesce(m.n, 0)::int,
       coalesce(m.n24, 0)::int,
       round((extract(epoch FROM s.cutoff - m.t_last) / 3600)::numeric, 1),
       coalesce(i."ConsultationDate" < s.cutoff, false)::int,
       coalesce(i."QuotedDate" < s.cutoff, false)::int,
       CASE WHEN i."QuotedDate" < s.cutoff
            THEN round(i."QuotedAmount" / nullif(etl.budget_mid(i."BudgetRange"), 0), 4) END
FROM unnest(p_ids, p_cutoffs) AS s(id, cutoff)
JOIN stg."Inquiry" i ON i."InquiryID" = s.id
LEFT JOIN stg."MarketingChannel" ch ON ch."ChannelID" = i."ChannelID"
LEFT JOIN stg."Package" p ON p."PackageID" = i."IndicativePackageID"
LEFT JOIN stg."Event" e ON e."InquiryID" = i."InquiryID"
LEFT JOIN LATERAL (
    SELECT min(c."SentAt") AS t_in, max(c."SentAt") AS t_last, count(*) AS n,
           count(*) FILTER (WHERE c."SentAt" < i."InquiryDate" + interval '1 day') AS n24
    FROM stg."ChatMessage" c
    WHERE c."InquiryID" = i."InquiryID" AND c."SenderType" = 'Customer' AND c."SentAt" < s.cutoff
    HAVING count(*) > 0) m ON true
LEFT JOIN LATERAL (
    SELECT min(c."SentAt") AS t_staff FROM stg."ChatMessage" c
    WHERE c."InquiryID" = i."InquiryID" AND c."SenderType" = 'Staff'
      AND c."SentAt" >= m.t_in AND c."SentAt" < s.cutoff) r ON true
WHERE i."InquiryDate" < s.cutoff
  AND (e."CreatedDate" IS NULL OR e."CreatedDate" >= s.cutoff)
  -- closed = Lost/Converted with a past ClosedDate; a reopened inquiry (status back to open) is scored again
  AND NOT (i."Status" IN ('Lost', 'Converted') AND i."ClosedDate" < s.cutoff)
$$;

-- Offline store: one row per open inquiry per scoring day (filled by `python -m ml features`)
CREATE TABLE IF NOT EXISTS feature."InquiryFeatures" AS
SELECT * FROM feature.inquiry_features('{}'::int[], '{}'::timestamp[]) WITH NO DATA;
DO $$ BEGIN
  ALTER TABLE feature."InquiryFeatures" ADD PRIMARY KEY ("InquiryID", "AsOf");
EXCEPTION WHEN invalid_table_definition THEN NULL; END $$;

-- Label: did the couple book within 120 days of this inquiry (a fixed outcome window, whatever the
-- CRM status says)? Booked = an event was created in the window, on this inquiry or on a later one by
-- the same customer (a later cancellation does not change the sales outcome); not booked = 120 days
-- passed without one. So a couple marked Lost (often by the 30-day no-reply AutoRule) who comes back
-- and books on day 90 is Booked; a booking after day 120 is new business. ClosedAt = when the outcome
-- became final: the booking, or the end of the window. A label never changes once it exists.
-- 120 = ml.train.LABEL_DAYS; keep the two equal.
CREATE OR REPLACE VIEW feature."InquiryLabel" AS
SELECT i."InquiryID", i."InquiryDate", (b.at < w.end_at)::int AS "Booked", least(b.at, w.end_at) AS "ClosedAt"
FROM stg."Inquiry" i
CROSS JOIN LATERAL (SELECT i."InquiryDate" + interval '120 days' AS end_at) w
CROSS JOIN LATERAL (
    SELECT coalesce(min(e."CreatedDate"), 'infinity') AS at FROM stg."Event" e
    WHERE e."InquiryID" = i."InquiryID"
       OR (e."CustomerID" = i."CustomerID" AND e."CreatedDate" >= i."InquiryDate")) b;

-- Registry and metadata: every training run, promoted or not, with what it was trained on
-- and how it scored. The model itself is stored here too (pickled scikit-learn pipeline).
CREATE TABLE IF NOT EXISTS ml."ModelVersion" (
  "Version" int GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  "TrainedAt" timestamp NOT NULL DEFAULT now(),
  "AsOfDate" date NOT NULL,                 -- data known up to this date
  "Algorithm" text NOT NULL,
  "TrainWindow" text NOT NULL,
  "TestWindow" text NOT NULL,
  "TrainRows" int NOT NULL,
  "TestRows" int NOT NULL,
  "Metrics" jsonb NOT NULL,                 -- test metrics of every candidate and the baseline
  "Features" jsonb NOT NULL,
  "TrainingStats" jsonb NOT NULL,           -- feature distributions at training, for drift checks
  "Thresholds" jsonb NOT NULL,              -- score cut-offs for the High / Medium / Low bands
  "Status" text NOT NULL CHECK ("Status" IN ('production', 'retired', 'rejected')),
  "Decision" text NOT NULL,
  "Artifact" bytea NOT NULL
);

CREATE TABLE IF NOT EXISTS ml."InquiryScore" (
  "InquiryID" int NOT NULL,
  "AsOfDate" date NOT NULL,
  "Version" int NOT NULL REFERENCES ml."ModelVersion" ("Version"),
  "Score" numeric(6,4) NOT NULL,
  "Band" text NOT NULL,
  PRIMARY KEY ("InquiryID", "AsOfDate")
);

CREATE TABLE IF NOT EXISTS ml."DriftCheck" (
  "AsOfDate" date NOT NULL,
  "Version" int NOT NULL REFERENCES ml."ModelVersion" ("Version"),
  "Check" text NOT NULL,                    -- e.g. 'PSI Channel', 'missing ResponseMinutes', 'live AUC'
  "Value" numeric,
  "Status" text NOT NULL CHECK ("Status" IN ('ok', 'warning', 'alert', 'n/a')),
  "Detail" text,
  PRIMARY KEY ("AsOfDate", "Version", "Check")
);

-- ---------------------------------------------------------------------
-- Demand forecast (Problem 1): how many weddings will each of the next 6 months hold?
-- ---------------------------------------------------------------------
-- One registry for both models: Model = 'conversion' or 'demand', one production version each.
ALTER TABLE ml."ModelVersion" ADD COLUMN IF NOT EXISTS "Model" text NOT NULL DEFAULT 'conversion'
  CHECK ("Model" IN ('conversion', 'demand'));
CREATE UNIQUE INDEX IF NOT EXISTS one_production_per_model ON ml."ModelVersion" ("Model") WHERE "Status" = 'production';

-- Features of each of the 6 calendar months after the as-of date, as known at the start of that day.
-- Weddings are booked 1 to 15 months ahead, so most of a near month is already on the books; the
-- model predicts the pickup still to come (final - OnBooks).
--   OnBooks          events booked before the as-of date for that month
--   OpenInquiries    inquiries still open that ask for a date in that month
--   OnBooksLastYear  the same month a year earlier, as it stood a year earlier (same horizon)
--   LastYearFinal    the same month a year earlier, final
--   LastYearPickup   LastYearFinal - OnBooksLastYear: how much was still to come a year ago
--   OpenQuoted       of the OpenInquiries, those already quoted (the likeliest to book)
--   Bookings28d      bookings made in the 28 days before the as-of date, any month (sales momentum)
DROP FUNCTION IF EXISTS feature.demand_features(date) CASCADE;
CREATE OR REPLACE FUNCTION feature.demand_features(p_asof date)
RETURNS TABLE ("AsOf" date, "Month" date, "Horizon" int, "OnBooks" int, "OpenInquiries" int, "HighSeason" int,
               "AuspiciousDays" int, "OnBooksLastYear" int, "LastYearFinal" int, "LastYearPickup" int,
               "OpenQuoted" int, "Bookings28d" int)
LANGUAGE sql STABLE AS $$
SELECT p_asof, m.m::date, h,
       (SELECT count(*) FROM stg."Event" e
        WHERE e."CreatedDate" < p_asof AND e."EventDate" >= m.m AND e."EventDate" < m.m + interval '1 month')::int,
       o.n::int,
       (extract(month FROM m.m) IN (11, 12, 1, 2))::int,
       (SELECT count(DISTINCT a."CalendarDate") FROM stg."AuspiciousDate" a
        WHERE a."CalendarDate" >= m.m AND a."CalendarDate" < m.m + interval '1 month')::int,
       ly.booked::int, ly.final::int, (ly.final - ly.booked)::int,
       o.quoted::int,
       (SELECT count(*) FROM stg."Event" e WHERE e."CreatedDate" >= p_asof - 28 AND e."CreatedDate" < p_asof)::int
FROM generate_series(1, 6) h, LATERAL (SELECT date_trunc('month', p_asof) + h * interval '1 month' AS m) m,
LATERAL (SELECT count(*) AS n, count(*) FILTER (WHERE i."QuotedDate" < p_asof) AS quoted FROM stg."Inquiry" i
         WHERE i."InquiryDate" < p_asof AND i."RequestedEventDate" >= m.m AND i."RequestedEventDate" < m.m + interval '1 month'
           AND (i."ClosedDate" IS NULL OR i."ClosedDate" >= p_asof)
           AND NOT EXISTS (SELECT 1 FROM stg."Event" e WHERE e."InquiryID" = i."InquiryID" AND e."CreatedDate" < p_asof)) o,
LATERAL (SELECT count(*) FILTER (WHERE e."CreatedDate" < p_asof - interval '1 year') AS booked, count(*) AS final
         FROM stg."Event" e WHERE e."CreatedDate" < p_asof
           AND e."EventDate" >= m.m - interval '1 year' AND e."EventDate" < m.m - interval '11 months') ly
$$;

-- Offline store: the features of the next 6 months, kept for every forecasting day
CREATE TABLE IF NOT EXISTS feature."DemandFeatures" AS
SELECT * FROM feature.demand_features('2000-01-01') WITH NO DATA;
DO $$ BEGIN
  ALTER TABLE feature."DemandFeatures" ADD PRIMARY KEY ("AsOf", "Month");
EXCEPTION WHEN invalid_table_definition THEN NULL; END $$;

-- Daily forecast: weddings and staff-hours per month against the event staff's capacity
CREATE TABLE IF NOT EXISTS ml."DemandForecast" (
  "AsOfDate" date NOT NULL,
  "Month" date NOT NULL,
  "Horizon" int NOT NULL,
  "Version" int NOT NULL REFERENCES ml."ModelVersion" ("Version"),
  "OnBooks" int NOT NULL,                   -- weddings already booked
  "Forecast" numeric(6,2) NOT NULL,         -- expected weddings in all (booked + still to come)
  "Lo" int NOT NULL,                        -- 80% interval
  "Hi" int NOT NULL,
  "BookedHours" numeric(8,1) NOT NULL,      -- hours already assigned in the month's weeks
  "ExpectedHours" numeric(8,1) NOT NULL,    -- booked hours + expected new weddings x hours per wedding
  "CapacityHours" numeric(8,1) NOT NULL,    -- standard hours of the event staff in the month's weeks
  PRIMARY KEY ("AsOfDate", "Month")
);
