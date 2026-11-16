-- Role-based access (Spec 03 §7), enforced by PostgreSQL: each role reads its marts only.
DO $$ DECLARE r text; BEGIN
  FOREACH r IN ARRAY ARRAY['aisleco_sales', 'aisleco_ops', 'aisleco_exec', 'aisleco_data'] LOOP
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r) THEN EXECUTE format('CREATE ROLE %I LOGIN', r); END IF;
  END LOOP;
END $$;
REVOKE ALL ON SCHEMA gold, stg, work, etl, sales_pii, mart_sales, mart_ops, mart_exec, mart_platform FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA mart_sales, sales_pii TO aisleco_sales;
GRANT SELECT ON ALL TABLES IN SCHEMA mart_sales TO aisleco_sales;
GRANT SELECT ON sales_pii.customer_contact TO aisleco_sales;
GRANT USAGE ON SCHEMA mart_ops TO aisleco_ops;
GRANT SELECT ON ALL TABLES IN SCHEMA mart_ops TO aisleco_ops;
GRANT USAGE ON SCHEMA mart_exec, mart_ops, mart_sales TO aisleco_exec;
GRANT SELECT ON ALL TABLES IN SCHEMA mart_exec, mart_ops TO aisleco_exec;
-- the owner may see sales performance, not the operational lead lists
GRANT SELECT ON mart_sales.v_funnel, mart_sales.v_conversion_by_channel_month, mart_sales.v_response_by_hour, mart_sales.v_response_band,
                mart_sales.v_lost_reasons, mart_sales.v_quote_share, mart_sales.v_followup_compliance, mart_sales.v_ab_result TO aisleco_exec;
GRANT USAGE ON SCHEMA gold, stg, etl, sales_pii, mart_sales, mart_ops, mart_exec, mart_platform TO aisleco_data;
GRANT SELECT ON ALL TABLES IN SCHEMA gold, stg, etl, sales_pii, mart_sales, mart_ops, mart_exec, mart_platform TO aisleco_data;
