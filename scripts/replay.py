"""CRM time machine for the end-to-end test: rebuilds what the simulated CRM held at the start
of any day from its final snapshot (zones_v3/oltp_snapshot), and writes one day's changes into
the CRM as the application would (inserts and updates; the CDC trigger stamps UpdatedAt).

  python scripts/replay.py data/sim/zones_v3      # self-check: the last day equals the snapshot

Rewinding rules (every timestamp in the snapshot says when that fact became true):
  * a row exists from its own time: Inquiry InquiryDate, ChatMessage SentAt, account FirstSeenAt,
    Event CreatedDate; a customer from its first account/inquiry/event. Assignments exist from
    their event's booking: the team is planned when the wedding is booked, and AssignedDate is
    the date the person starts on it (often still in the future in the snapshot)
  * Inquiry: New -> Contacted (ContactedDate, ResponseTimeMinutes) -> Quoted (QuotedDate,
    QuotedAmount) -> final Status / LostReason / ClosedBy at ClosedDate; ConsultationDate once passed
  * Event: Confirmed, Completed the day after its EventDate (if the snapshot says Completed)
  * Cancellations carry no timestamp in the CRM. Assumption: an event (and its assignments) is
    cancelled halfway between its booking and its EventDate (or the snapshot date if earlier).
  * Master data (channels, packages, roles, staff, calendar) is loaded up front; all staff were
    hired in 2023, before the replay starts.
"""
import io
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crm import create_crm  # noqa: E402
from load_snapshot import _copy  # noqa: E402

from etl import db  # noqa: E402
from etl.config import Config  # noqa: E402

SNAPSHOT_DATE = pd.Timestamp("2027-01-15")
MASTER = ["MarketingChannel", "Package", "EmployeeRole", "AuspiciousDate", "Employee"]
# (schema, table, key), parents first
TABLES = [("master", "Customer", "CustomerID"), ("master", "CustomerChannelAccount", "CustomerAccountID"),
          ("transaction", "Inquiry", "InquiryID"), ("transaction", "ChatMessage", "ChatMessageID"),
          ("transaction", "Event", "EventID"), ("transaction", "EventEmployeeAssignment", "EventEmployeeAssignmentID")]
TIMES = {"Inquiry": ["InquiryDate", "ContactedDate", "ConsultationDate", "QuotedDate", "ClosedDate"],
         "ChatMessage": ["SentAt"], "CustomerChannelAccount": ["FirstSeenAt"], "Event": ["CreatedDate", "EventDate"],
         "EventEmployeeAssignment": []}


def frames(zones):
    """The snapshot as strings (written back unchanged) plus parsed times in fr['_t'][table]."""
    snap = Path(zones) / "oltp_snapshot"
    fr = {t: pd.read_csv(snap / f"{t}.csv", dtype=str, keep_default_na=False, na_values=[""])
          for t in MASTER + [t for _, t, _ in TABLES if t != "Customer"]}
    fr["_t"] = {t: pd.DataFrame({c: pd.to_datetime(fr[t][c]) for c in cols}) for t, cols in TIMES.items()}
    first = pd.concat([pd.DataFrame({"CustomerID": fr[t].CustomerID, "t": fr["_t"][t][c]})
                       for t, c in [("CustomerChannelAccount", "FirstSeenAt"), ("Inquiry", "InquiryDate"), ("Event", "CreatedDate")]])
    first = first.groupby("CustomerID").t.min()
    fr["Customer"] = pd.DataFrame({"CustomerID": first.index})
    fr["_t"]["Customer"] = pd.DataFrame({"first": first.values})
    ev = fr["_t"]["Event"]
    cancel_at = ev.CreatedDate + (ev.EventDate.clip(upper=SNAPSHOT_DATE) - ev.CreatedDate) / 2
    fr["_t"]["Event"]["CancelAt"] = cancel_at.where(fr["Event"].Status == "Cancelled")
    by_event = lambda col: fr["EventEmployeeAssignment"].EventID.map(dict(zip(fr["Event"].EventID, fr["_t"]["Event"][col])))
    fr["_t"]["EventEmployeeAssignment"]["Booked"] = by_event("CreatedDate")
    fr["_t"]["EventEmployeeAssignment"]["CancelAt"] = by_event("CancelAt").where(fr["EventEmployeeAssignment"].Status == "Cancelled")
    return fr


def state(fr, d: date) -> dict:
    """The CRM's transaction tables at 00:00 on day d."""
    t = pd.Timestamp(datetime.combine(d, time()))
    T, out = fr["_t"], {}
    out["Customer"] = fr["Customer"][(T["Customer"]["first"] < t).values]
    out["CustomerChannelAccount"] = fr["CustomerChannelAccount"][(T["CustomerChannelAccount"].FirstSeenAt < t).values]
    out["ChatMessage"] = fr["ChatMessage"][(T["ChatMessage"].SentAt < t).values]

    ti, i = T["Inquiry"], fr["Inquiry"].copy()
    for when, cols in [("ContactedDate", ["ContactedDate", "ResponseTimeMinutes"]), ("ConsultationDate", ["ConsultationDate"]),
                       ("QuotedDate", ["QuotedDate", "QuotedAmount"]), ("ClosedDate", ["ClosedDate", "ClosedBy", "LostReason"])]:
        i.loc[~(ti[when] < t).values, cols] = None
    closed, quoted, contacted = (ti.ClosedDate < t).values, (ti.QuotedDate < t).values, (ti.ContactedDate < t).values
    i["Status"] = i.Status.where(closed, pd.Series("New", index=i.index).mask(contacted, "Contacted").mask(quoted, "Quoted"))
    out["Inquiry"] = i[(ti.InquiryDate < t).values]

    te, e = T["Event"], fr["Event"].copy()
    status = pd.Series("Confirmed", index=e.index)
    status = status.mask((e.Status == "Completed") & (te.EventDate < t), "Completed").mask(te.CancelAt < t, "Cancelled")
    e["Status"] = status
    out["Event"] = e[(te.CreatedDate < t).values]

    ta, a = T["EventEmployeeAssignment"], fr["EventEmployeeAssignment"].copy()
    a["Status"] = a.Status.where(ta.CancelAt < t, "Active")
    out["EventEmployeeAssignment"] = a[(ta.Booked < t).values]
    return out


def _changed(cur, prev):
    if prev is None or prev.empty:
        return cur
    m = cur.merge(prev.drop_duplicates(), how="left", indicator=True)
    return cur[(m["_merge"] == "left_only").values]


def _upsert(conn, schema, table, key, df):
    types = {r["column_name"]: r["data_type"] for r in conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
        (schema, table)).fetchall()}
    df = df[[c for c in df.columns if c in types]].copy()
    for c in df.columns:
        if types[c] in ("integer", "smallint", "bigint"):
            df[c] = pd.to_numeric(df[c]).astype("Int64")
    cols = ", ".join(f'"{c}"' for c in df.columns)
    sets = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in df.columns if c != key) or f'"{key}" = EXCLUDED."{key}"'
    conn.execute(f'CREATE TEMP TABLE _up ON COMMIT DROP AS SELECT {cols} FROM {schema}."{table}" WITH NO DATA')
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    with conn.cursor().copy(f"COPY _up ({cols}) FROM STDIN WITH (FORMAT csv, HEADER)") as cp:
        cp.write(buf.getvalue())
    # a row is only written when it changed, so the CDC trigger stamps exactly the changed rows
    conn.execute(f'INSERT INTO {schema}."{table}" AS t ({cols}) SELECT {cols} FROM _up '
                 f'ON CONFLICT ("{key}") DO UPDATE SET {sets}')
    conn.execute("DROP TABLE _up")


def apply(cfg, cur, prev=None) -> dict:
    """Write the rows of `cur` that are new or changed since `prev` into the CRM, one transaction
    (one working day committed as a batch). Returns rows written per table."""
    n = {}
    with db.connect(cfg.crm_dsn, cfg) as conn:
        for schema, table, key in TABLES:
            ch = _changed(cur[table], prev[table] if prev else None)
            if len(ch):
                _upsert(conn, schema, table, key, ch)
            n[table] = len(ch)
        conn.commit()
    return n


def bootstrap(cfg, fr, d: date) -> dict:
    """A fresh CRM holding master data and everything up to the start of day d."""
    create_crm(cfg)
    with db.connect(cfg.crm_dsn, cfg) as conn:
        for t in MASTER:
            _copy(conn, "master", t, fr[t])
        conn.commit()
    cur = state(fr, d)
    apply(cfg, cur)
    return cur


def matches_snapshot(fr, cur) -> list:
    """Tables where a state differs from the snapshot (empty list = identical)."""
    bad = []
    for _, t, key in TABLES:
        a = cur[t].sort_values(key).reset_index(drop=True)
        b = fr[t].sort_values(key).reset_index(drop=True)
        if not a.equals(b):
            bad.append(t)
    return bad


if __name__ == "__main__":
    fr = frames(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "data" / "sim" / "zones_v3")
    final = state(fr, SNAPSHOT_DATE.date() + timedelta(days=1))
    assert matches_snapshot(fr, final) == [], matches_snapshot(fr, final)
    mid = state(fr, date(2025, 10, 1))
    i = mid["Inquiry"]
    assert not ((i.Status.isin(["Converted", "Lost"])) & i.ClosedDate.isna()).any()
    assert set(mid["Event"].InquiryID.dropna()) <= set(i.InquiryID[i.Status == "Converted"])
    assert set(mid["ChatMessage"].InquiryID) <= set(i.InquiryID)
    print("replay self-check passed: state on 16 Jan 2027 equals the snapshot;",
          {t: len(v) for t, v in mid.items()}, "rows on 1 Oct 2025")
