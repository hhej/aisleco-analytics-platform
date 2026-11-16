-- Web app schema in aisleco_dw (Spec 05 §2): logins and the one fact every role may read, the data's freshness.
-- The user table is readable by the database owner only (the API's login lookup); the four mart roles never see it.
CREATE SCHEMA IF NOT EXISTS app;
REVOKE ALL ON SCHEMA app FROM PUBLIC;

CREATE TABLE IF NOT EXISTS app."user" (
  username      text PRIMARY KEY,
  display_name  text NOT NULL,
  role          text NOT NULL CHECK (role IN ('sales', 'ops', 'exec', 'data')),
  employee_code text,                -- CRM master."Employee"."EmployeeCode" (sales only)
  employee_id   int,                 -- resolved from the CRM at seed time: the write-back role may not read master."Employee"
  is_manager    boolean NOT NULL DEFAULT false,
  password_hash text NOT NULL,       -- bcrypt
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- "Data as of" for every page header: the latest successful pipeline run
CREATE OR REPLACE VIEW app.v_data_as_of AS
SELECT run_id, asof_date, finished_at FROM etl.run WHERE status = 'success' ORDER BY run_id DESC LIMIT 1;

GRANT USAGE ON SCHEMA app TO aisleco_sales, aisleco_ops, aisleco_exec, aisleco_data;
GRANT SELECT ON app.v_data_as_of TO aisleco_sales, aisleco_ops, aisleco_exec, aisleco_data;
