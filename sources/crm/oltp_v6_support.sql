-- OLTP v6 support objects (run after oltp_v6.sql; not importable into dbdiagram).
--  * clock: CreatedAt/UpdatedAt come from aisleco.clock() = the session setting "aisleco.now"
--    when the simulator sets it (simulated time), else now(). Production never sets it.
--  * trigger: every INSERT/UPDATE stamps UpdatedAt, so the application cannot forget it
--    (the simulator may set "aisleco.keep_updated_at" to inject a late commit, Spec 06 §7)
--  * UpdatedAt indexes for incremental extraction; no overlapping contracts (btree_gist)
SET client_min_messages = warning;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE SCHEMA IF NOT EXISTS aisleco;

CREATE OR REPLACE FUNCTION aisleco.clock() RETURNS timestamp LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('aisleco.now', true), '')::timestamp, localtimestamp)
$$;

CREATE OR REPLACE FUNCTION aisleco.stamp() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW."CreatedAt" := coalesce(NEW."CreatedAt", aisleco.clock());
  END IF;
  IF NOT (coalesce(current_setting('aisleco.keep_updated_at', true), '') = 'on' AND NEW."UpdatedAt" IS NOT NULL) THEN
    NEW."UpdatedAt" := aisleco.clock();
  END IF;
  RETURN NEW;
END $$;

DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT table_schema, table_name FROM information_schema.tables
           WHERE table_schema IN ('master', 'transaction') AND table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS trg_stamp ON %I.%I', t.table_schema, t.table_name);
    EXECUTE format('CREATE TRIGGER trg_stamp BEFORE INSERT OR UPDATE ON %I.%I
                    FOR EACH ROW EXECUTE FUNCTION aisleco.stamp()', t.table_schema, t.table_name);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.%I ("UpdatedAt")',
                   'ix_' || lower(t.table_name) || '_upd', t.table_schema, t.table_name);
  END LOOP;
END $$;

ALTER TABLE "master"."EmployeeContract" DROP CONSTRAINT IF EXISTS no_overlapping_contracts;
ALTER TABLE "master"."EmployeeContract" ADD CONSTRAINT no_overlapping_contracts
  EXCLUDE USING gist ("EmployeeID" WITH =, daterange("EffectiveFrom", "EffectiveTo", '[]') WITH &&);

-- the web app's write-back role: may insert follow-ups and nothing else (Spec 01 §8)
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crm_app_writer') THEN
    CREATE ROLE crm_app_writer LOGIN;
  END IF;
END $$;
GRANT USAGE ON SCHEMA "transaction", "master" TO crm_app_writer;
GRANT INSERT ON "transaction"."SalesActivity" TO crm_app_writer;
GRANT USAGE ON SEQUENCE "transaction"."SalesActivity_ActivityID_seq" TO crm_app_writer;
GRANT SELECT ("ActivityID", "InquiryID", "EmployeeID", "ActivityAt", "ActivityType", "Outcome", "Source", "ListDate")
  ON "transaction"."SalesActivity" TO crm_app_writer;
GRANT USAGE ON SCHEMA aisleco TO crm_app_writer;   -- the stamp trigger calls aisleco.clock() as the inserting role
