"""Model checks (Spec 04): point-in-time features (no leakage), label definition, holdout rule shared with SQL,
the daily list's shape, and the forecast's backtest logic. Needs the loaded warehouse and Silver (run 1)."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import psycopg
import pytest

from ml import data as D
from pipeline.config import Config

CUT = datetime(2026, 3, 2)


@pytest.fixture(scope="module")
def con():
    return D.connect(Config())


def truncated(con, cut):
    """The same Silver tables as they would have looked at the cutoff: later rows removed, later stages unknown."""
    c = D.connect(Config())
    t = f"TIMESTAMP '{cut}'"
    c.execute(f"CREATE TEMP TABLE chat_message AS SELECT * FROM main.chat_message WHERE sent_at < {t}")
    c.execute(f"CREATE TEMP TABLE sales_activity AS SELECT * FROM main.sales_activity WHERE activity_at < {t}")
    c.execute(f"CREATE TEMP TABLE fair_lead AS SELECT * FROM main.fair_lead WHERE submitted_at < {t}")
    c.execute(f"CREATE TEMP TABLE event AS SELECT * FROM main.event WHERE booked_at < {t}")
    c.execute(f"""CREATE TEMP TABLE inquiry AS SELECT * REPLACE (
        CASE WHEN consultation_at < {t} THEN consultation_at END AS consultation_at,
        CASE WHEN quoted_at < {t} THEN quoted_at END AS quoted_at, CASE WHEN quoted_at < {t} THEN quoted_amount END AS quoted_amount,
        CASE WHEN first_staff_reply_at < {t} THEN first_staff_reply_at END AS first_staff_reply_at,
        CASE WHEN closed_at < {t} THEN closed_at END AS closed_at) FROM main.inquiry WHERE inquiry_at < {t}""")
    c.execute(f"CREATE TEMP TABLE week_hours AS SELECT * FROM main.week_hours WHERE booked_at < {t} AND assigned_at < {t}")
    return c


def test_features_are_point_in_time(con):
    spine = D.scoring_spine(con, CUT.date())
    full = D.features(con, spine).sort_values("inquiry_id").reset_index(drop=True)
    past = D.features(truncated(con, CUT), spine).sort_values("inquiry_id").reset_index(drop=True)
    assert len(full) > 50
    pd.testing.assert_frame_equal(full[D.FEATURES], past[D.FEATURES], check_dtype=False)


def test_label_is_booking_within_window(con):
    ev = con.execute("SELECT e.inquiry_id, e.booked_at FROM event e WHERE e.inquiry_id IS NOT NULL LIMIT 1").fetchall()[0]
    iid, booked = ev
    spine = pd.DataFrame({"inquiry_id": [iid] * 3,
                          "cutoff": [booked - timedelta(days=D.LABEL_DAYS + 1), booked - timedelta(days=D.LABEL_DAYS - 1), booked - timedelta(hours=1)]})
    y = D.labels(con, spine, date(2026, 11, 16)).sort_values("cutoff").y.tolist()
    assert y[1:] == [1, 1]


def test_holdout_rule_matches_sql():
    with psycopg.connect(Config().dw_dsn) as c:
        ids = [r[0] for r in c.execute('SELECT "InquiryID" FROM gold."FactInquiry" WHERE gold.is_holdout("InquiryID") = 1 LIMIT 200')]
        n = c.execute('SELECT count(*) FROM gold."FactInquiry"').fetchone()[0]
    assert all(D.is_holdout(i) for i in ids)
    assert 0.07 < sum(D.is_holdout(i) for i in range(1, n + 1)) / n < 0.13


def test_daily_list_shape():
    with psycopg.connect(Config().dw_dsn) as c:
        rows = c.execute('''SELECT "SalesOwnerKey", "Rank", "IsHoldout", "Probability", "Reason1", "NextBestAction" FROM gold."FactLeadScore"
                            WHERE "ScoreDateKey" = (SELECT max("ScoreDateKey") FROM gold."FactLeadScore")''').fetchall()
    if not rows:
        pytest.skip("no scores yet")
    df = pd.DataFrame(rows, columns=["owner", "rank", "holdout", "p", "reason", "nba"])
    ranked = df[df.holdout == 0]
    assert ranked.groupby("owner")["rank"].apply(lambda r: sorted(r) == list(range(1, len(r) + 1))).all()   # 1..n per salesperson
    assert df[df.holdout == 1]["rank"].isna().all()
    assert df.p.between(0, 1).all() and df.reason.notna().mean() > 0.9 and df.nba.notna().all()


def test_forecast_metrics():
    from ml.forecast import bucket, wape
    assert wape(np.array([10.0, 10.0]), np.array([12.0, 8.0])) == pytest.approx(0.2)
    assert [bucket(h) for h in (1, 4, 5, 12, 13, 26)] == ["1-4", "1-4", "5-12", "5-12", "13-26", "13-26"]
