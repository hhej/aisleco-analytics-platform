-- =====================================================================
-- mart_platform: Data & ML health (Spec 05 §3.5). Read by role aisleco_data.
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS mart_platform;
CREATE OR REPLACE VIEW mart_platform.v_runs AS
SELECT r.run_id, r.asof_date, r.mode, r.triggered_by, r.status, r.message, r.started_at, r.finished_at,
       round(extract(epoch FROM r.finished_at - r.started_at)::numeric, 1) AS seconds
FROM etl.run r;
CREATE OR REPLACE VIEW mart_platform.v_step_times AS
SELECT run_id, step, attempt, status, duration_sec, rows_in, rows_out, error FROM etl.step_run;
CREATE OR REPLACE VIEW mart_platform.v_dq_results AS
SELECT d.run_id, d.rule_id, r.source, r.target, r.severity, r.description, d.rows_checked, d.rows_failed, d.passed
FROM etl.dq_result d JOIN etl.dq_rule r USING (rule_id);
CREATE OR REPLACE VIEW mart_platform.v_quarantine AS
SELECT source, target, record_key, rule_id, reason, first_run_id, last_run_id, resolved_run_id, quarantine_file, record FROM etl.rejected_row;
CREATE OR REPLACE VIEW mart_platform.v_freshness AS
SELECT source, max(ingested_at) AS last_ingested, count(*) AS files, max(run_id) AS last_run FROM etl.file_checkpoint GROUP BY 1
UNION ALL SELECT 'crm:' || source_table, changed_at, NULL, last_run_id FROM etl.watermark;
CREATE OR REPLACE VIEW mart_platform.v_alerts AS SELECT * FROM etl.alert;
-- models: every training run with its decision, monitoring results (created by `python -m ml init`)
DO $$ BEGIN
  IF to_regclass('ml.model_run') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW mart_platform.v_model_runs AS SELECT model, version, trained_at, asof_date, algorithm, train_window, test_window,
             train_rows, test_rows, status, decision, metrics, params FROM ml.model_run';
    EXECUTE 'CREATE OR REPLACE VIEW mart_platform.v_model_monitor AS SELECT * FROM ml.monitor';
  END IF;
END $$;
