-- Feature store and ML bookkeeping (Spec 04 §3, §9). Created by `python -m ml init` in aisleco_dw.
CREATE SCHEMA IF NOT EXISTS feature;
CREATE SCHEMA IF NOT EXISTS ml;
-- one row per feature: what it means and how it is computed (the definition itself is ml/sql/lead_features.sql)
CREATE TABLE IF NOT EXISTS feature.registry (
  feature text PRIMARY KEY, feature_group text NOT NULL, dtype text NOT NULL, description text NOT NULL,
  source text NOT NULL, owner text NOT NULL DEFAULT 'data team', version int NOT NULL DEFAULT 1, added_on date NOT NULL DEFAULT current_date);
-- offline store: the features served each day (one row per open inquiry per score date)
CREATE TABLE IF NOT EXISTS feature.lead_features_daily (
  score_date date NOT NULL, inquiry_id int NOT NULL, features jsonb NOT NULL, PRIMARY KEY (score_date, inquiry_id));
-- every training run, promoted or not (the MLflow registry holds the artifacts; this is the warehouse-side record)
CREATE TABLE IF NOT EXISTS ml.model_run (
  model text NOT NULL, version text NOT NULL, mlflow_run_id text, trained_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  asof_date date NOT NULL, algorithm text, train_window text, test_window text, train_rows int, test_rows int,
  params jsonb, metrics jsonb, decision text, status text NOT NULL, PRIMARY KEY (model, version));
-- monitoring results (drift, calibration, matured-label performance)
CREATE TABLE IF NOT EXISTS ml.monitor (
  check_date date NOT NULL, model text NOT NULL, model_version text, check_name text NOT NULL, subject text NOT NULL,
  value numeric, threshold numeric, status text NOT NULL, detail text, PRIMARY KEY (check_date, model, check_name, subject));
