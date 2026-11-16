"""Conversion model: point-in-time features, training gate, scoring, drift. Runs on the
simulated three years (data/sim/zones_v3), loaded once for the module."""
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from etl import db, pipeline
from etl.__main__ import init_dw
from etl.config import Config
from load_snapshot import load
from ml import features as F
from ml import forecast as FC
from ml import monitor, score, train

ZONES = Path(__file__).resolve().parent.parent / "data" / "sim" / "zones_v3"
pytestmark = pytest.mark.skipif(not (ZONES / "oltp_snapshot").exists(), reason="run the v3 notebook first (README)")


@pytest.fixture(scope="module")
def dw(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("ETL_LANDING_DIR", str(tmp_path_factory.mktemp("landing")))
    cfg = Config()
    load(ZONES, 1, cfg)
    init_dw(cfg, drop=True)
    pipeline.run(cfg, date(2027, 1, 15), "full")
    yield cfg
    mp.undo()


def conn(cfg):
    return db.connect(cfg.dw_dsn, cfg)


def test_features_do_not_see_the_future(dw):
    """Scenario: Features of 50 inquiries as of 1 Jun 2026; then new events dated after that day are added: a couple message for each, and a consultation and a quote for those that had none.
    Expected: Recomputed features as of 1 Jun 2026 are identical: nothing dated after the cutoff leaks in."""
    t = datetime(2026, 6, 1)
    with conn(dw) as c:
        ids = [r["InquiryID"] for r in c.execute('SELECT "InquiryID" FROM stg."Inquiry" WHERE "InquiryDate" < %s '
                                                 'ORDER BY "InquiryDate" DESC LIMIT 50', (t,)).fetchall()]
        before = F.compute(c, ids, [t] * len(ids))
        assert len(before) > 0
        c.execute('UPDATE stg."Inquiry" SET "ConsultationDate" = %s, "QuotedDate" = %s, "QuotedAmount" = 1 WHERE "InquiryID" = ANY(%s) '
                  'AND "ConsultationDate" IS NULL AND "QuotedDate" IS NULL', (t + timedelta(hours=1), t + timedelta(days=2), ids))
        c.execute('INSERT INTO stg."ChatMessage"("ChatMessageID","InquiryID","SentAt","Direction","SenderType") '
                  'SELECT 900000 + "InquiryID", "InquiryID", %s, \'In\', \'Customer\' FROM stg."Inquiry" WHERE "InquiryID" = ANY(%s)',
                  (t + timedelta(minutes=5), ids))
        after = F.compute(c, ids, [t] * len(ids))
        c.rollback()
    key = ["InquiryID"]
    assert before.sort_values(key).reset_index(drop=True).equals(after.sort_values(key).reset_index(drop=True))


def test_closed_inquiries_are_not_features(dw):
    """Scenario: Every training row is an inquiry as it looked 1, 7, 14 or 30 days after arrival.
    Expected: Each row is dated before the inquiry closed, the label is not a feature, and a booked inquiry asked about after its booking returns nothing."""
    with conn(dw) as c:
        ts = F.training_set(c, date(2027, 1, 15))
        lab = F.labels(c, date(2027, 1, 15)).set_index("InquiryID")
        booked = lab[lab.Booked == 1].iloc[0]
        late = F.compute(c, [int(booked.name)], [booked.ClosedAt + timedelta(days=1)])
    assert (ts.AsOf < ts.InquiryID.map(lab.ClosedAt)).all()
    assert set(ts.AgeDays) == set(F.HORIZONS)
    assert "Booked" not in F.CAT + F.NUM
    assert late.empty


def test_label_is_a_fixed_120_day_window(dw):
    """Scenario: Three couples marked Lost. The first comes back on day 90 and books (no inquiry: the booking carries only their CustomerID); the second books on day 150; the third's inquiry is reopened in the CRM (status back to Contacted, ClosedDate kept).
    Expected: The first is Booked (final on the booking day); the second is not booked (final on day 120, a late comeback is new business); the reopened inquiry gets features again, the day after its old closing date."""
    with conn(dw) as c:
        lost = c.execute('SELECT "InquiryID", "CustomerID", "InquiryDate", "ClosedDate" FROM stg."Inquiry" '
                         'WHERE "Status" = \'Lost\' ORDER BY "InquiryID" LIMIT 3').fetchall()
        for k, (r, days) in enumerate(zip(lost[:2], (90, 150))):
            c.execute('INSERT INTO stg."Event"("EventID","CustomerID","CreatedDate","EventDate") VALUES (%s,%s,%s,%s)',
                      (990000 + k, r["CustomerID"], r["InquiryDate"] + timedelta(days=days), r["InquiryDate"].date() + timedelta(days=300)))
        lab = {x["InquiryID"]: x for x in c.execute('SELECT * FROM feature."InquiryLabel" WHERE "InquiryID" = ANY(%s)',
                                                      ([r["InquiryID"] for r in lost],)).fetchall()}
        r3, after_close = lost[2], lost[2]["ClosedDate"] + timedelta(days=1)
        closed = F.compute(c, [r3["InquiryID"]], [after_close])
        c.execute('UPDATE stg."Inquiry" SET "Status" = \'Contacted\' WHERE "InquiryID" = %s', (r3["InquiryID"],))
        reopened = F.compute(c, [r3["InquiryID"]], [after_close])
        c.rollback()
    a, b = lab[lost[0]["InquiryID"]], lab[lost[1]["InquiryID"]]
    assert (a["Booked"], a["ClosedAt"]) == (1, lost[0]["InquiryDate"] + timedelta(days=90))
    assert (b["Booked"], b["ClosedAt"]) == (0, lost[1]["InquiryDate"] + timedelta(days=train.LABEL_DAYS))
    assert closed.empty and len(reopened) == 1


def test_training_gate_and_registry(dw, monkeypatch):
    """Scenario: Train as of 1 Oct 2025 twice with the same seed, then once more with a gate the model cannot pass.
    Expected: Same metrics both times and the model beats the baseline; the second is not promoted, because production has no test inquiries it never saw to compare on; the impossible gate gives 'rejected'; production stays v1."""
    with conn(dw) as c:
        c.execute('TRUNCATE ml."InquiryScore", ml."DriftCheck", ml."ModelVersion" RESTART IDENTITY CASCADE')
        r1 = train.train(c, date(2025, 10, 1))
        r2 = train.train(c, date(2025, 10, 1))
        monkeypatch.setattr(train, "MIN_GAIN_OVER_BASELINE", 1.0)
        r3 = train.train(c, date(2025, 10, 1))
        status = {r["Version"]: r["Status"] for r in c.execute('SELECT "Version","Status" FROM ml."ModelVersion"').fetchall()}
        c.commit()
    assert r1["test"] == r2["test"] and r1["test"]["auc"] >= r1["baseline"]["auc"] + 0.05
    assert r2["status"] == "rejected" and r2["decision"].startswith("kept production v1: only 0 ")
    assert status == {1: "production", 2: "rejected", 3: "rejected"}
    assert r3["status"] == "rejected"


def test_production_is_replaced_only_after_a_fair_comparison(dw):
    """Scenario: Production trained as of 1 Oct 2025; a new model trained as of 1 Feb 2026, when four months of inquiries have outcomes that production never learnt from.
    Expected: Both models are scored on at least 50 of those inquiries (counted once each, not per scoring age), the decision quotes the comparison, and exactly one model is in production afterwards."""
    with conn(dw) as c:
        c.execute('TRUNCATE ml."InquiryScore", ml."DriftCheck", ml."ModelVersion" RESTART IDENTITY CASCADE')
        train.train(c, date(2025, 10, 1))
        r = train.train(c, date(2026, 2, 1))
        n_prod = c.execute('SELECT count(*) AS n FROM ml."ModelVersion" WHERE "Status" = \'production\'').fetchone()["n"]
        c.commit()
    assert r["vs_production"]["inquiries"] >= train.MIN_UNSEEN and r["vs_production"]["rows"] > r["vs_production"]["inquiries"]
    assert "production v1" in r["decision"] and f'{r["vs_production"]["inquiries"]} ' in r["decision"]
    assert n_prod == 1


def test_daily_scoring_covers_every_open_inquiry(dw):
    """Scenario: The daily steps on 1 Mar 2026 (features, then scores), run twice.
    Expected: One score per open inquiry, bands High/Medium/Low, and the rerun does not duplicate anything."""
    d = date(2026, 3, 1)
    with conn(dw) as c:
        if not train.production(c):
            train.train(c, date(2025, 10, 1))
        for _ in range(2):
            n_feat = F.materialise(c, d)
            n_scored = score.score(c, d)
        rows = c.execute('SELECT "Band", count(*) AS n FROM ml."InquiryScore" WHERE "AsOfDate" = %s GROUP BY 1', (d,)).fetchall()
        c.commit()
    assert n_feat == n_scored == sum(r["n"] for r in rows) > 0
    assert {r["Band"] for r in rows} <= {"High", "Medium", "Low"}


def test_drift_check_flags_the_2026_campaign(dw):
    """Scenario: A model trained on data to 1 Oct 2025 is monitored in Oct 2025 and in May 2026, after the TikTok campaign changed the channel mix.
    Expected: Channel PSI is ok in Oct 2025 and an alert in May 2026, and the alert is written to etl.Alert."""
    with conn(dw) as c:
        if not train.production(c):
            train.train(c, date(2025, 10, 1))
        before = {n: (v, s) for n, v, s, _ in monitor.monitor(c, date(2025, 10, 1))}
        after = {n: (v, s) for n, v, s, _ in monitor.monitor(c, date(2026, 5, 1))}
        alerts = c.execute('SELECT "Message" FROM etl."Alert" WHERE "Message" LIKE \'%%drift on 2026-05-01%%\'').fetchall()
        c.commit()
    assert before["PSI Channel"][1] == "ok"
    assert after["PSI Channel"][1] == "alert"
    assert alerts


def test_ml_refuses_a_warehouse_the_etl_did_not_load(dw):
    """Scenario: The ETL run for 16 Jan 2027 fails (or never ran); the warehouse is loaded to 15 Jan.
    Expected: Features and training for 16 Jan refuse with StaleWarehouse; 15 Jan and earlier still work."""
    with conn(dw) as c:
        c.execute('INSERT INTO etl."Run"("AsOfDate","Mode","TriggeredBy","Status","StartedAt") '
                  "VALUES ('2027-01-16','incremental','test','failed',now())")
        with pytest.raises(F.StaleWarehouse):
            F.materialise(c, date(2027, 1, 16))
        with pytest.raises(F.StaleWarehouse):
            train.train(c, date(2027, 1, 16))
        assert F.materialise(c, date(2027, 1, 15)) > 0
        c.rollback()


# ---------- rule baseline, expected value, demand forecast ----------

def test_gate_uses_the_stronger_of_two_baselines(dw):
    """Scenario: Train as of 1 Oct 2025.
    Expected: Both baselines (channel rate, the salesperson's rule) are measured; the gate's baseline is the stronger one, and the model beats it by the margin."""
    with conn(dw) as c:
        r = train.train(c, date(2025, 10, 1))
        c.rollback()
    b = r["baselines"]
    assert set(b) == {"channel rate", "rule"}
    assert r["baseline"]["auc"] == max(v["auc"] for v in b.values())
    assert r["test"]["auc"] >= r["baseline"]["auc"] + train.MIN_GAIN_OVER_BASELINE


def test_followup_list_has_expected_value(dw):
    """Scenario: Score the open inquiries of 1 Mar 2026 and read the follow-up list.
    Expected: ExpectedValue = chance to book x the budget midpoint, on every row with a budget."""
    with conn(dw) as c:
        if not train.production(c):
            train.train(c, date(2025, 10, 1))
        F.materialise(c, date(2026, 3, 1)); score.score(c, date(2026, 3, 1))
        rows = c.execute('SELECT "Score", "BudgetMidpoint", "ExpectedValue" FROM mart.v_followup_list').fetchall()
        c.rollback()
    with_budget = [r for r in rows if r["BudgetMidpoint"] is not None]
    assert with_budget
    assert all(r["ExpectedValue"] == round(r["Score"] * r["BudgetMidpoint"]) for r in with_budget)


def test_demand_features_do_not_see_the_future(dw):
    """Scenario: Demand features as of 1 Jun 2026; then a booking and an inquiry dated after that day are added for July 2026.
    Expected: The features as of 1 Jun 2026 do not change; as of 1 Jul 2026 they count the new rows."""
    t = date(2026, 6, 1)
    with conn(dw) as c:
        before = FC.compute(c, t)
        c.execute('INSERT INTO stg."Event"("EventID","EventDate","CreatedDate","Status") VALUES (990001, %s, %s, \'Confirmed\')',
                  (date(2026, 7, 20), datetime(2026, 6, 3)))
        c.execute('INSERT INTO stg."Inquiry"("InquiryID","InquiryDate","RequestedEventDate","Status") VALUES (990001, %s, %s, \'Open\')',
                  (datetime(2026, 6, 4), date(2026, 7, 25)))
        after, later = FC.compute(c, t), FC.compute(c, date(2026, 6, 10))
        c.rollback()
    assert before.equals(after)
    jul = lambda df: df[df.Month == date(2026, 7, 1)].iloc[0]
    assert jul(later).OnBooks >= jul(before).OnBooks + 1


def test_demand_forecast_and_registry(dw, monkeypatch):
    """Scenario: Train both models as of 15 Jan 2027 and forecast twice; then retrain the demand model with every ML candidate made to lose.
    Expected: One production version per model; 6 months forecast with Lo <= Forecast <= Hi and Forecast >= booked, and no duplicates on the rerun; with no ML candidate winning, a baseline becomes the production forecast."""
    d = date(2027, 1, 15)
    with conn(dw) as c:
        train.train(c, d)
        r = FC.train(c, d)
        for _ in range(2):
            f = FC.forecast(c, d)
        n = c.execute('SELECT count(*) AS n FROM ml."DemandForecast" WHERE "AsOfDate" = %s', (d,)).fetchone()["n"]
        prod = {x["Model"]: x["n"] for x in c.execute(
            'SELECT "Model", count(*) AS n FROM ml."ModelVersion" WHERE "Status" = \'production\' GROUP BY 1').fetchall()}
        monkeypatch.setattr(FC.Regression, "predict", lambda self, df: np.full(len(df), 50.0))
        r2 = FC.train(c, d)
        c.rollback()
    assert prod == {"conversion": 1, "demand": 1}
    assert n == len(f) == 6 and list(f.Horizon) == [1, 2, 3, 4, 5, 6]
    assert (f.Lo <= f.Forecast).all() and (f.Forecast <= f.Hi).all() and (f.Forecast >= f.OnBooks).all()
    assert set(r["candidates"]) == {"seasonal naive", "pickup average", "Poisson regression", "LightGBM"}
    assert r2["algorithm"] in FC.BASELINES and "baseline is the production forecast" in r2["decision"]
