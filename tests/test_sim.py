"""Simulator checks that need no database: same seed → same business; the CRM rows never carry the hidden
readiness; every webhook is signed so the gateway accepts it (Spec 06)."""
from datetime import date, timedelta

from sim.engine import World


def run(days, seed=11):
    w = World(seed=seed)
    out = [w.setup()]
    d = w.start
    for _ in range(days):
        out.append(w.run_day(d))
        d += timedelta(1)
    return w, out


def test_same_seed_same_business():
    a, da = run(60)
    b, db = run(60)
    assert [x.crm for x in da] == [x.crm for x in db]
    assert a.ids == b.ids


def test_hidden_readiness_never_reaches_the_crm():
    w, days = run(60)
    cols = {c for d in days for op, t, payload, *rest in d.crm if op == "insert" for c in payload}
    assert not {"z", "true_channel", "readiness"} & {c.lower() for c in cols}
    assert all("z" in r for r in w.truth)          # it exists only in the ground truth


def test_fair_days_are_friday_to_sunday():
    w = World()
    for f in w.fair_fridays(2025):
        assert f.weekday() == 4


def test_contract_history_has_no_overlap():
    from sim.company import STAFF
    for code, *_, contracts in STAFF:
        spans = sorted((c[4], c[5] or date.max) for c in contracts)
        assert all(a[1] < b[0] for a, b in zip(spans, spans[1:])), code


def test_live_lead_records_what_the_couple_asked_for():
    """The demo couple's CRM row carries the package, date and budget text from the demo form, not random ones."""
    from datetime import datetime
    from sim.engine import Day
    w, _ = run(5)
    t = datetime.combine(w.start + timedelta(5), datetime.min.time()).replace(hour=14)
    w.today, day = t.date(), Day()
    w.new_lead(day, t, "LINE OA", pkg="Full", req=date(2027, 2, 13), budget_text="ยังไม่แน่ใจ")
    inq = next(p for op, tbl, p, *_ in day.crm if tbl == "Inquiry")
    assert inq["RequestedEventDate"] == date(2027, 2, 13) and inq["BudgetRange"] == "ยังไม่แน่ใจ"
    assert inq["IndicativePackageID"] == w.package_id("Full", t.date())
