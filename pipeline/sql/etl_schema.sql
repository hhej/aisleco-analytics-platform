-- Pipeline control tables (the govern zone, Spec 02 §1): run log, checkpoints, watermarks, file checkpoints,
-- data-quality rules and results, quarantine index, alerts.
CREATE SCHEMA IF NOT EXISTS etl;
CREATE TABLE etl.run (
  run_id serial PRIMARY KEY, asof_date date NOT NULL, mode text NOT NULL, triggered_by text, status text NOT NULL,
  message text, started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz);
CREATE TABLE etl.step_run (
  step_run_id serial PRIMARY KEY, run_id int NOT NULL REFERENCES etl.run, step text NOT NULL, attempt int NOT NULL,
  status text NOT NULL, started_at timestamptz NOT NULL DEFAULT clock_timestamp(), finished_at timestamptz,
  duration_sec numeric(10,3), rows_in bigint, rows_out bigint, metrics jsonb, error text);
CREATE TABLE etl.watermark (
  source_table text PRIMARY KEY, last_updated_at timestamp, extracted_at timestamp, last_run_id int, changed_at timestamptz);
CREATE TABLE etl.file_checkpoint (
  source text NOT NULL, bronze_path text PRIMARY KEY, landing_name text NOT NULL, bytes bigint NOT NULL, sha256 text NOT NULL,
  rows int, run_id int NOT NULL, ingested_at timestamptz NOT NULL DEFAULT clock_timestamp(), note text);
CREATE TABLE etl.dq_rule (
  rule_id text PRIMARY KEY, source text NOT NULL, target text NOT NULL, severity text NOT NULL, description text NOT NULL);
CREATE TABLE etl.dq_result (
  run_id int NOT NULL REFERENCES etl.run, rule_id text NOT NULL REFERENCES etl.dq_rule, rows_checked bigint, rows_failed bigint NOT NULL,
  passed boolean NOT NULL, checked_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY (run_id, rule_id));
CREATE TABLE etl.rejected_row (
  source text NOT NULL, target text NOT NULL, record_key text NOT NULL, rule_id text NOT NULL REFERENCES etl.dq_rule,
  reason text, record jsonb, quarantine_file text, first_run_id int NOT NULL, last_run_id int NOT NULL, resolved_run_id int,
  PRIMARY KEY (target, record_key, rule_id));
CREATE TABLE etl.alert (
  alert_id serial PRIMARY KEY, run_id int, severity text NOT NULL, message text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT clock_timestamp());
