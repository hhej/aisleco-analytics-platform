"""Silver → model data (Spec 04 §2–§5): point-in-time features from the feature definition ml/sql/lead_features.sql,
labels (booked within LABEL_DAYS of the cutoff), the training spine (one row per open inquiry per day) and the scoring
spine (inquiries open at the start of the score date)."""
import hashlib
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from pipeline.config import Config

SQL = Path(__file__).parent / "sql"
SILVER = ["inquiry", "chat_message", "sales_activity", "fair_lead", "calendar_day", "event", "assignment",
          "employee_contract", "channel", "package"]
LABEL_DAYS = 60          # label window (Spec 04 §2): the smallest of 30/45/60 days holding >= 80 % of bookings (EDA: 55/72/82 %)
MAX_AGE = 60             # training rows: the first 60 days an inquiry is open
CAT = ["channel", "platform", "package_interest", "fair_venue"]
GROUPS = {               # feature groups, in the order of the ablation table (Spec 04 §6)
    "profile": ["channel", "platform", "has_tracking_code", "budget_band_ord", "budget_midpoint_thb", "package_interest",
                "has_requested_date", "lead_days", "requested_high_season", "requested_auspicious", "requested_in_lent",
                "arrived_outside_hours", "inquiry_dow", "inquiry_month", "fair_lead", "fair_guests", "fair_venue", "previous_inquiries"],
    "engagement": ["age_days", "customer_msgs", "customer_msgs_3d", "customer_msgs_7d", "staff_msgs", "hours_since_customer_msg",
                   "hours_since_staff_msg", "log_response_minutes", "replied_within_1h"],
    "stage": ["consulted", "days_since_consult", "quoted", "days_since_quote", "quote_share", "quoted_amount"],
    "text_rules": ["n_ask_price", "n_ask_availability", "n_mention_date", "n_mention_guests", "n_ask_package_detail", "n_ask_meeting",
                   "n_price_objection", "n_family_decision", "n_competitor", "n_positive", "n_commit"],
    "sales_activity": ["followups", "followups_7d", "days_since_followup", "last_followup_reached"],
    "capacity": ["requested_week_utilization"],
}
FEATURES = [f for g in GROUPS.values() for f in g]


def connect(cfg=None):
    """DuckDB over the published Silver tables, plus the hours of each assignment-week with the moment they became known."""
    cfg = cfg or Config()
    con = duckdb.connect()
    for t in SILVER:
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM read_parquet('{cfg.lake_dir / 'silver' / t / 'data.parquet'}')")
    text = cfg.lake_dir / "silver_restricted" / "lead_text" / "data.parquet"
    if text.exists():                  # masked text: only the ML job reads it (Spec 01 §6)
        con.execute(f"CREATE VIEW lead_text AS SELECT * FROM read_parquet('{text}')")
    con.execute("INSTALL postgres; LOAD postgres")
    con.execute(f"ATTACH '{cfg.dw_dsn}' AS dw (TYPE postgres, READ_ONLY)")
    con.execute('''CREATE TABLE week_hours AS
        SELECT d."WeekStart" AS week_start, f."TotalHours"::DOUBLE AS hours, a.assigned_at_effective AS assigned_at, e.booked_at
        FROM dw.gold."FactStaffWorkload" f JOIN dw.gold."DimDate" d ON d."DateKey" = f."WeekStartDateKey"
        JOIN assignment a ON a.assignment_id = f."AssignmentID" JOIN event e ON e.event_id = f."EventID"''')
    con.execute("DETACH dw")
    return con


def training_spine(con, asof: date) -> pd.DataFrame:
    """Every day each inquiry was open at 00:00, from day 1 to day 60, up to the as-of date."""
    return con.execute(f"""
        SELECT inquiry_id, (inquiry_at::DATE + k)::TIMESTAMP AS cutoff
        FROM inquiry, (SELECT range::INTEGER AS k FROM range(1, {MAX_AGE + 1})) t
        WHERE inquiry_at::DATE + k <= coalesce(closed_at::DATE, DATE '{asof}') AND inquiry_at::DATE + k <= DATE '{asof}'""").df()


def scoring_spine(con, asof: date) -> pd.DataFrame:
    return con.execute(f"""SELECT inquiry_id, TIMESTAMP '{asof}' AS cutoff FROM inquiry
                           WHERE inquiry_at < TIMESTAMP '{asof}' AND (closed_at IS NULL OR closed_at >= TIMESTAMP '{asof}')""").df()


def features(con, spine: pd.DataFrame) -> pd.DataFrame:
    con.register("spine_df", spine)
    con.execute("CREATE OR REPLACE TEMP TABLE spine AS SELECT * FROM spine_df")
    df = con.execute((SQL / "lead_features.sql").read_text()).df()
    for c in CAT:
        df[c] = df[c].astype(str)
    num = [f for f in FEATURES if f not in CAT]
    df[num] = df[num].astype(float)
    return df


def labels(con, spine: pd.DataFrame, asof: date) -> pd.DataFrame:
    """y = a booking from this inquiry, or from another inquiry of the same couple, in [cutoff, cutoff + LABEL_DAYS).
    known = the window is over by the as-of date (otherwise the row is censored)."""
    con.register("spine_df", spine)
    return con.execute(f"""
        SELECT s.inquiry_id, s.cutoff,
               EXISTS (SELECT 1 FROM event e JOIN inquiry j ON j.inquiry_id = e.inquiry_id
                       WHERE j.customer_id = i.customer_id AND e.booked_at >= s.cutoff
                         AND e.booked_at < s.cutoff + INTERVAL {LABEL_DAYS} DAY)::INT AS y,
               (s.cutoff + INTERVAL {LABEL_DAYS} DAY <= TIMESTAMP '{asof}') AS known,
               i.sales_owner_id, i.inquiry_at
        FROM spine_df s JOIN inquiry i USING (inquiry_id)""").df()


def is_holdout(inquiry_id: int) -> bool:
    """Same rule as gold.is_holdout(): the first 7 hex digits of md5(id), mod 10 = 0."""
    return int(hashlib.md5(str(inquiry_id).encode()).hexdigest()[:7], 16) % 10 == 0


def dataset(con, asof: date) -> pd.DataFrame:
    spine = training_spine(con, asof)
    df = features(con, spine).merge(labels(con, spine, asof), on=["inquiry_id", "cutoff"])
    df["score_date"] = df.cutoff.dt.date
    df["weight"] = 1 / df.groupby("inquiry_id").inquiry_id.transform("size")
    return df
