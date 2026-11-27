"""Error handling, idempotency and incremental edge cases (Ch.6 error-handling testing; Spec 01 §2.3, Spec 02 §5, Spec 06 §7).

Self-contained: the session builds a template once (simulated 2022-07-01 → 2023-01-31, full refresh as of 2023-02-01) in
its own databases aisleco_crm_e2e_err_tpl / aisleco_dw_e2e_err_tpl and lake var/e2e_tests/tpl (gateway on :8012), and every
test starts from a fresh copy of it (aisleco_crm_e2e_err, aisleco_dw_e2e_err, var/e2e_tests/lake). The main CRM, warehouse
and lake are never touched. A bad webhook signature (401 + _rejected) is tested in test_sources.py."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from datetime import date, timedelta

import duckdb
import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from pipeline import pipeline
from pipeline.__main__ import init as dw_init
from pipeline.config import ROOT, Config
from pipeline.ingest.crm_cdc import manifest_path

sys.path.insert(0, str(ROOT / "scripts"))
from e2e_replay import compare  # noqa: E402

pytestmark = pytest.mark.slow

BASE = ROOT / "var" / "e2e_tests"
TPL, LAKE, LAKE_REF, SIM = BASE / "tpl", BASE / "lake", BASE / "lake_ref", BASE / "sim"
CRM, DW, DW_REF = "aisleco_crm_e2e_err", "aisleco_dw_e2e_err", "aisleco_dw_e2e_err_ref"
ASOF = date(2023, 2, 1)
FACTS = ("FactInquiry", "FactEventBooking", "FactStaffWorkload", "FactSalesActivity")


def admin(*statements):
    with psycopg.connect("dbname=postgres", autocommit=True) as c:
        for s in statements:
            c.execute(s)


def clone_db(name, template):
    admin(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
    for attempt in range(20):        # a backend that just closed may still hold the template for a moment
        try:
            return admin(sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(name), sql.Identifier(template)))
        except psycopg.errors.ObjectInUse:
            time.sleep(.25)
    raise RuntimeError(f"template {template} stays busy")


def sim(crm_db, dw_db, lake, state, *args):
    env = {**os.environ, "CRM_DSN": f"dbname={crm_db}", "DW_DSN": f"dbname={dw_db}", "LAKE_DIR": str(lake), "SIM_STATE_DIR": str(state),
           "GATEWAY_URL": "http://127.0.0.1:8012"}
    subprocess.run([sys.executable, "-m", "sim", *args], env=env, cwd=ROOT, check=True, capture_output=True)


def cfg_for(crm_db, dw_db, lake):
    return Config(crm_dsn=f"dbname={crm_db}", dw_dsn=f"dbname={dw_db}", lake_dir=lake, offline=True, fail_at="")


@pytest.fixture(scope="session")
def template():
    shutil.rmtree(BASE, ignore_errors=True)
    sim(CRM + "_tpl", DW + "_tpl", TPL / "lake", TPL / "sim", "init", "--reset")
    sim(CRM + "_tpl", DW + "_tpl", TPL / "lake", TPL / "sim", "backfill", "--to", str(ASOF - timedelta(1)))
    cfg = cfg_for(CRM + "_tpl", DW + "_tpl", TPL / "lake")
    dw_init(cfg, drop=True)
    pipeline.run(cfg, ASOF, "full", "pytest")


@pytest.fixture
def cfg(template):
    """A fresh copy of the template: CRM, warehouse, lake and simulator state."""
    clone_db(CRM, CRM + "_tpl")
    clone_db(DW, DW + "_tpl")
    for d in (LAKE, LAKE_REF, SIM):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(TPL / "lake", LAKE)
    shutil.copytree(TPL / "sim", SIM)
    return cfg_for(CRM, DW, LAKE)


def reference(lake_from=None):
    """A second warehouse in the template's state, with its own copy of a lake (default: the template's)."""
    clone_db(DW_REF, DW + "_tpl")
    shutil.copytree(lake_from or TPL / "lake", LAKE_REF)
    return cfg_for(CRM, DW_REF, LAKE_REF)


def dw(cfg):
    return psycopg.connect(cfg.dw_dsn, autocommit=True, row_factory=dict_row)


def crm(cfg):
    return psycopg.connect(cfg.crm_dsn, autocommit=True, row_factory=dict_row)


def val(c, q, params=()):
    return next(iter(c.execute(q, params).fetchone().values()))


def gold_print(cfg):
    with dw(cfg) as c:
        return {t: val(c, f'SELECT count(*) || md5(coalesce(string_agg(x::text, \'|\' ORDER BY x::text), \'\')) FROM gold."{t}" x')
                for t in FACTS}


def silver_print(cfg):
    return {str(p.relative_to(cfg.lake_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (cfg.lake_dir / "silver").glob("*/data.parquet") if not p.parent.name.startswith("_")}


def last_run(cfg):
    with dw(cfg) as c:
        run = c.execute("SELECT * FROM etl.run ORDER BY run_id DESC LIMIT 1").fetchone()
        run["steps"] = {r["step"]: r for r in c.execute("SELECT DISTINCT ON (step) * FROM etl.step_run WHERE run_id = %s "
                                                        "ORDER BY step, attempt DESC", (run["run_id"],))}
        run["alerts"] = [r["message"] for r in c.execute("SELECT message FROM etl.alert WHERE run_id = %s", (run["run_id"],))]
    return run


def no_differences(a, b):
    diffs, _ = compare(a.dw_dsn, b.dw_dsn)
    return {k: v for k, v in diffs.items() if v}


# (a) ---------------------------------------------------------------------------------------------------------------
def test_crm_unavailable_fails_after_retries_then_resume_completes(cfg, caplog):
    bad = replace(cfg, crm_dsn="dbname=aisleco_no_such_crm", connect_retries=2, retry_base_seconds=0.1)
    with pytest.raises(ConnectionError, match="after 2 attempts"):
        pipeline.run(bad, ASOF)
    assert "attempt 1/2" in caplog.text
    run = last_run(cfg)
    assert run["status"] == "failed" and run["steps"]["ingest_crm"]["status"] == "failed"
    assert any("ingest_crm failed" in a for a in run["alerts"])
    assert pipeline.resume(cfg, run["run_id"]) == run["run_id"]
    run = last_run(cfg)
    assert run["status"] == "success" and run["steps"]["ingest_crm"]["attempt"] == 2


# (b) ---------------------------------------------------------------------------------------------------------------
def test_bronze_file_edited_after_extraction_is_refused(cfg):
    run_id = pipeline.start(cfg, ASOF)
    for step in ("ingest_crm", "ingest_files"):
        pipeline.run_step(cfg, run_id, step)
    before = silver_print(cfg)
    f = cfg.lake_dir / json.loads(manifest_path(cfg, run_id).read_text())["tables"]["Employee"]["file"]
    f.write_text(f.read_text().replace("Active", "Inactive", 1))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        pipeline.run_step(cfg, run_id, "silver")
    assert silver_print(cfg) == before
    assert last_run(cfg)["status"] == "failed"


# (c) ---------------------------------------------------------------------------------------------------------------
def test_dq_block_stops_before_gold_and_keeps_published_silver(cfg):
    with crm(cfg) as c:
        ev = val(c, 'SELECT min("EventID") FROM transaction."Event"')
        emp = val(c, 'SELECT min("EmployeeID") FROM master."Employee"')
        c.cursor().executemany('INSERT INTO transaction."EventEmployeeAssignment" ("EventID", "EmployeeID", "AssignmentRole", '
                               '"AssignedDate", "WorkloadHours", "Status") VALUES (%s, %s, \'Coordinator\', NULL, 999, \'Active\')',
                               [(ev, emp)] * 30)
    gold, silver = gold_print(cfg), silver_print(cfg)
    with pytest.raises(RuntimeError, match="S01 assignment: 30 of 30"):
        pipeline.run(cfg, ASOF)
    run = last_run(cfg)
    assert run["status"] == "failed" and run["steps"]["silver"]["status"] == "failed" and "load_stg" not in run["steps"]
    assert gold_print(cfg) == gold and silver_print(cfg) == silver


# (d) ---------------------------------------------------------------------------------------------------------------
def test_crash_mid_load_then_resume_equals_uninterrupted_run(cfg):
    with crm(cfg) as c:
        c.execute('UPDATE transaction."Inquiry" SET "QuotedAmount" = "QuotedAmount" + 1000 WHERE "QuotedAmount" IS NOT NULL '
                  'AND "InquiryID" % 7 = 0')
        c.execute('UPDATE transaction."Event" SET "GuestCount" = "GuestCount" + 10 WHERE "EventID" % 5 = 0')
    ref = reference()
    with pytest.raises(RuntimeError, match="injected failure"):
        pipeline.run(replace(cfg, fail_at="fact_inquiry"), ASOF)
    run = last_run(cfg)
    assert run["status"] == "failed" and run["steps"]["gold_dims"]["status"] == "success"
    assert run["steps"]["fact_inquiry"]["status"] == "failed"
    pipeline.resume(cfg, run["run_id"])
    assert last_run(cfg)["status"] == "success"
    pipeline.run(ref, ASOF)
    assert gold_print(cfg) == gold_print(ref)
    assert no_differences(cfg, ref) == {}


# (e) ---------------------------------------------------------------------------------------------------------------
def test_same_asof_twice_changes_nothing(cfg):
    pipeline.run(cfg, ASOF + timedelta(1))
    before = gold_print(cfg)
    pipeline.run(cfg, ASOF + timedelta(1))
    steps = last_run(cfg)["steps"]
    for f in ("fact_inquiry", "fact_event_booking", "fact_staff_workload", "fact_sales_activity"):
        m = steps[f]["metrics"]
        assert (m["inserted"], m["updated"], m["deleted"]) == (0, 0, 0), f
    assert gold_print(cfg) == before


# (f) ---------------------------------------------------------------------------------------------------------------
def test_asof_before_last_success_is_refused(cfg):
    runs = last_run(cfg)["run_id"]
    with pytest.raises(pipeline.AsOfError):
        pipeline.run(cfg, ASOF - timedelta(10))
    assert last_run(cfg)["run_id"] == runs          # no run was created


# (g) ---------------------------------------------------------------------------------------------------------------
def test_batch_cap_drains_backlog_and_equals_full_refresh(cfg):
    sim(CRM, DW, LAKE, SIM, "backfill", "--to", "2023-02-28")
    ref = reference(lake_from=LAKE)                    # same source state, loaded without a cap
    capped, asof, backlogs = replace(cfg, max_rows_per_run=500), date(2023, 3, 1), []
    for _ in range(15):
        pipeline.run(capped, asof)
        run = last_run(cfg)
        backlogs.append(run["steps"]["ingest_crm"]["metrics"]["backlog"])
        if len(backlogs) == 1:
            assert any("batch cap" in a for a in run["alerts"])
        if not backlogs[-1]:
            break
    assert len(backlogs) >= 3 and backlogs[-1] == 0 and backlogs == sorted(backlogs, reverse=True)
    pipeline.run(ref, asof, "full")
    assert no_differences(cfg, ref) == {}


# (h) ---------------------------------------------------------------------------------------------------------------
def test_late_commit_is_picked_up_by_the_overlap(cfg):
    with crm(cfg) as c:
        a, b = [r["InquiryID"] for r in c.execute('SELECT "InquiryID" FROM transaction."Inquiry" WHERE "QuotedAmount" IS NOT NULL '
                                                   'ORDER BY 1 LIMIT 2')]
        c.execute('UPDATE transaction."Inquiry" SET "QuotedAmount" = "QuotedAmount" + 1 WHERE "InquiryID" = %s', (a,))
    pipeline.run(cfg, ASOF)
    with crm(cfg) as c, c.transaction():
        # a slow transaction commits now with a timestamp from before the snapshot (and the watermark) of that run
        c.execute("SET LOCAL aisleco.keep_updated_at = on")
        late = c.execute('UPDATE transaction."Inquiry" SET "QuotedAmount" = 4242424, "UpdatedAt" = '
                         '(SELECT "UpdatedAt" - interval \'2 minutes\' FROM transaction."Inquiry" WHERE "InquiryID" = %s) '
                         'WHERE "InquiryID" = %s RETURNING "UpdatedAt"', (a, b)).fetchone()["UpdatedAt"]
    with dw(cfg) as c:
        assert val(c, "SELECT last_updated_at FROM etl.watermark WHERE source_table = 'Inquiry'") > late
    pipeline.run(cfg, ASOF)
    with dw(cfg) as c:
        assert val(c, 'SELECT "QuotedAmount" FROM gold."FactInquiry" WHERE "InquiryID" = %s', (b,)) == 4242424


# (i) ---------------------------------------------------------------------------------------------------------------
def test_hard_delete_disappears_from_silver_and_gold(cfg):
    with crm(cfg) as c:
        iid = val(c, 'SELECT min(i."InquiryID") FROM transaction."Inquiry" i WHERE NOT EXISTS (SELECT 1 FROM transaction."Event" e '
                     'WHERE e."InquiryID" = i."InquiryID") AND EXISTS (SELECT 1 FROM transaction."SalesActivity" s '
                     'WHERE s."InquiryID" = i."InquiryID")')
        for t in ("ChatMessage", "SalesActivity", "Inquiry"):
            c.execute(f'DELETE FROM transaction."{t}" WHERE "InquiryID" = %s', (iid,))
    with dw(cfg) as c:
        assert val(c, 'SELECT count(*) FROM gold."FactSalesActivity" WHERE "InquiryID" = %s', (iid,)) > 0
    pipeline.run(cfg, ASOF)
    assert duckdb.connect().execute(f"SELECT count(*) FROM '{cfg.lake_dir}/silver/inquiry/data.parquet' WHERE inquiry_id = {iid}").fetchone()[0] == 0
    with dw(cfg) as c:
        assert val(c, 'SELECT count(*) FROM gold."FactInquiry" WHERE "InquiryID" = %s', (iid,)) == 0
        assert val(c, 'SELECT count(*) FROM gold."FactSalesActivity" WHERE "InquiryID" = %s', (iid,)) == 0


# (j) ---------------------------------------------------------------------------------------------------------------
def test_orphans_of_a_rejected_event_load_once_the_event_is_fixed(cfg):
    with crm(cfg) as c:
        ev = val(c, 'SELECT min(e."EventID") FROM transaction."Event" e JOIN transaction."EventEmployeeAssignment" a USING ("EventID") '
                    'WHERE e."Status" <> \'Cancelled\' AND a."Status" = \'Active\'')
        guests = val(c, 'SELECT "GuestCount" FROM transaction."Event" WHERE "EventID" = %s', (ev,))
        assignments = {str(r["EventEmployeeAssignmentID"]) for r in c.execute(
            'SELECT "EventEmployeeAssignmentID" FROM transaction."EventEmployeeAssignment" WHERE "EventID" = %s', (ev,))}
        c.execute('UPDATE transaction."Event" SET "GuestCount" = 0 WHERE "EventID" = %s', (ev,))
    pipeline.run(cfg, ASOF)
    with dw(cfg) as c:
        q = c.execute("SELECT rule_id, record_key, resolved_run_id FROM etl.rejected_row WHERE (target = 'event' AND record_key = %s) "
                      "OR (target = 'assignment' AND record_key = ANY(%s))", (str(ev), list(assignments))).fetchall()
        assert {(r["rule_id"], r["record_key"]) for r in q} == {("R08a", str(ev))} | {("R10b", k) for k in assignments}
        assert all(r["resolved_run_id"] is None for r in q)
        assert val(c, 'SELECT count(*) FROM gold."FactEventBooking" WHERE "EventID" = %s', (ev,)) == 0
        assert val(c, 'SELECT count(*) FROM gold."FactStaffWorkload" WHERE "EventID" = %s', (ev,)) == 0
    with crm(cfg) as c:
        c.execute('UPDATE transaction."Event" SET "GuestCount" = %s WHERE "EventID" = %s', (guests, ev))
    pipeline.run(cfg, ASOF)
    run_id = last_run(cfg)["run_id"]
    with crm(cfg) as c:
        hours = val(c, 'SELECT sum("WorkloadHours") FROM transaction."EventEmployeeAssignment" WHERE "EventID" = %s AND "Status" = \'Active\'', (ev,))
    with dw(cfg) as c:
        assert val(c, 'SELECT "GuestCount" FROM gold."FactEventBooking" WHERE "EventID" = %s', (ev,)) == guests
        assert val(c, 'SELECT sum("TotalHours") FROM gold."FactStaffWorkload" WHERE "EventID" = %s', (ev,)) == hours
        q = c.execute("SELECT resolved_run_id FROM etl.rejected_row WHERE (target = 'event' AND record_key = %s) "
                      "OR (target = 'assignment' AND record_key = ANY(%s))", (str(ev), list(assignments))).fetchall()
        assert len(q) == len(assignments) + 1 and all(r["resolved_run_id"] == run_id for r in q)


# (k) ---------------------------------------------------------------------------------------------------------------
def test_simulated_day_written_after_a_midday_run_is_not_skipped(cfg):
    """25 Nov 2026 incident: a live demo couple (stamped late in the day) and a run moved the watermark; at 01:00 the
    simulator wrote the rest of that day with earlier times and the incremental read skipped them."""
    with crm(cfg) as c, c.transaction():
        c.execute("SET LOCAL aisleco.keep_updated_at = on")
        c.execute('UPDATE transaction."Inquiry" SET "UpdatedAt" = %s WHERE "InquiryID" = (SELECT max("InquiryID") FROM transaction."Inquiry")',
                  (f"{ASOF} 23:59:00",))
    pipeline.run(cfg, ASOF)
    sim(CRM, DW, LAKE, SIM, "backfill", "--to", str(ASOF))   # the simulated day ASOF, written after that run
    with crm(cfg) as c:
        assert val(c, 'SELECT count(*) FROM transaction."Inquiry" WHERE "InquiryDate"::date = %s', (ASOF,)) > 0
    ref = reference(lake_from=LAKE)
    pipeline.run(cfg, ASOF + timedelta(1))
    pipeline.run(ref, ASOF + timedelta(1), "full")
    assert no_differences(cfg, ref) == {}
