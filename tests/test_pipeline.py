"""Pipeline checks on the loaded warehouse (Ch.6 test types): source-to-target against the CRM itself,
idempotency of a re-run, role-based access, and the injected data problems landing in quarantine.
Needs the backfilled CRM and run 1 (python -m sim backfill; python -m pipeline run --full-refresh)."""
import psycopg
import pytest
from psycopg.rows import dict_row

from pipeline import pipeline
from pipeline.config import Config

CFG = Config()


@pytest.fixture(scope="module")
def dw():
    with psycopg.connect(CFG.dw_dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


@pytest.fixture(scope="module")
def crm():
    with psycopg.connect(CFG.crm_dsn, row_factory=dict_row, autocommit=True) as c:
        yield c


def one(c, q):
    return next(iter(c.execute(q).fetchone().values()))


def test_source_to_target_inquiries_by_channel(dw, crm):
    src = {r["ChannelName"]: r["n"] for r in crm.execute(
        'SELECT c."ChannelName", count(*) n FROM transaction."Inquiry" i JOIN master."MarketingChannel" c USING ("ChannelID") GROUP BY 1')}
    tgt = {r["channel"]: r["n"] for r in dw.execute("SELECT channel, sum(inquiries) n FROM mart_sales.v_funnel GROUP BY 1")}
    assert src == tgt


def test_source_to_target_bookings_and_fees(dw, crm):
    src = crm.execute('''SELECT count(*) n, sum("AgreedPackagePrice") fee FROM transaction."Event"
                         WHERE "Status" <> 'Cancelled' AND "CreatedDate" >= '2023-01-01' ''').fetchone()
    tgt = dw.execute("SELECT sum(bookings) n, sum(fee_revenue) fee FROM mart_exec.v_monthly_kpis").fetchone()
    assert (src["n"], src["fee"]) == (tgt["n"], tgt["fee"])


def test_source_to_target_staff_hours(dw, crm):
    """Every hour of every active assignment of a non-cancelled wedding lands in some week, once."""
    src = one(crm, '''SELECT sum(a."WorkloadHours") FROM transaction."EventEmployeeAssignment" a JOIN transaction."Event" e USING ("EventID")
                      WHERE a."Status" = 'Active' AND e."Status" <> 'Cancelled' ''')
    tgt = one(dw, '''SELECT sum(f."TotalHours") FROM gold."FactStaffWorkload" f JOIN gold."FactEventBooking" b USING ("EventID")
                     WHERE b."IsCancelled" = 0''')
    assert abs(float(src) - float(tgt)) < 0.001


def test_conversion_matches_crm_status(dw, crm):
    src = one(crm, '''SELECT count(*) FROM transaction."Inquiry" i WHERE EXISTS (SELECT 1 FROM transaction."Event" e WHERE e."InquiryID" = i."InquiryID")''')
    own = one(dw, '''SELECT count(*) FROM gold."FactInquiry" f WHERE "IsBooked" = 1 AND EXISTS (SELECT 1 FROM gold."FactEventBooking" b WHERE b."InquiryID" = f."InquiryID")''')
    assert src == own


def test_scd2_contract_history(dw):
    nina = dw.execute('''SELECT "StandardWeeklyHours" h, "ValidFrom", "ValidTo" FROM gold."DimEmployee" WHERE "Nickname" = 'Nina' ORDER BY "ValidFrom"''').fetchall()
    assert [float(r["h"]) for r in nina] == [44, 36]
    jan, feb = (one(dw, f"SELECT DISTINCT capacity_hours FROM mart_ops.v_capacity_grid WHERE nickname = 'Nina' AND week_start = '{w}'")
                for w in ("2026-01-12", "2026-02-09"))
    assert (float(jan), float(feb)) == (44, 36)


@pytest.mark.parametrize("role,allowed,denied", [
    ("aisleco_sales", ["mart_sales.v_funnel", "sales_pii.customer_contact"], ["mart_ops.v_capacity_grid", 'gold."FactInquiry"']),
    ("aisleco_ops", ["mart_ops.v_capacity_grid"], ["mart_sales.v_funnel", "sales_pii.customer_contact"]),
    ("aisleco_exec", ["mart_exec.v_monthly_kpis", "mart_ops.v_capacity_grid", "mart_sales.v_funnel"],
     ["sales_pii.customer_contact", "mart_sales.v_today_list", "etl.run"]),
])
def test_role_based_access(role, allowed, denied):
    with psycopg.connect(CFG.dw_dsn + f" user={role}", autocommit=True) as c:
        for t in allowed:
            c.execute(f"SELECT 1 FROM {t} LIMIT 1")
        for t in denied:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {t} LIMIT 1")


def test_injected_problems_are_quarantined_or_flagged(dw):
    rules = {r["rule_id"]: r["n"] for r in dw.execute("SELECT rule_id, max(rows_failed) n FROM etl.dq_result GROUP BY 1")}
    for rule in ("R20", "R22", "R30", "R31", "R33", "R41", "S05", "R11"):
        assert rules.get(rule, 0) > 0, rule
    assert one(dw, "SELECT count(*) FROM etl.rejected_row WHERE rule_id = 'R30'") >= 1


def test_rerun_changes_nothing(dw):
    """Idempotency: a second run on the same data inserts, updates and deletes nothing."""
    asof = one(dw, "SELECT max(asof_date) FROM etl.run WHERE status = 'success'")
    run_id = pipeline.run(CFG, asof, "incremental", "pytest")
    m = {r["step"]: r["metrics"] for r in dw.execute("SELECT step, metrics FROM etl.step_run WHERE run_id = %s", (run_id,))}
    for step in ("fact_inquiry", "fact_event_booking", "fact_staff_workload", "fact_sales_activity"):
        assert (m[step]["inserted"], m[step]["updated"], m[step]["deleted"]) == (0, 0, 0), step


def test_budget_bands_are_canonical(dw):
    """Regression: '1M-1.5M' and '1.5M+' were once mis-normalised, leaving high-budget couples without a budget."""
    bands = {r["BudgetBand"]: r["n"] for r in dw.execute('SELECT "BudgetBand", count(*) n FROM gold."FactInquiry" GROUP BY 1')}
    assert set(bands) <= {"300k-500k", "500k-1M", "1M-1.5M", "1.5M+", "Unknown"}
    assert bands.get("1M-1.5M", 0) > 0 and bands.get("1.5M+", 0) > 0
    assert one(dw, 'SELECT count(*) FROM gold."FactInquiry" WHERE "BudgetBand" <> \'Unknown\' AND "BudgetMidpoint" IS NULL') == 0
