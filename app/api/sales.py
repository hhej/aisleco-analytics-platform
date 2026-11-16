"""Sales pages (Spec 05 §3.1-3.2): "Today" worklist, write-back of follow-ups, "Sales performance"."""
import math
from datetime import date, datetime
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import require
from app.api.db import CRM, as_role, connect

router = APIRouter(prefix="/api/sales")
LIST_SIZE = 15  # leads on a salesperson's call list (rank <= 15, as in v_followup_compliance)
NAMES = "LEFT JOIN sales_pii.customer_contact c USING (customer_id)"
NAME_COLS = "c.contact_first_name AS first_name, c.display_name AS handle, c.phone_masked AS phone"


def _scope(user):
    """SQL filter on sales_owner_id: a salesperson sees their own leads; the manager and the data team see all."""
    if user["role"] == "sales" and not user["is_manager"]:
        return "sales_owner_id = %(emp)s", {"emp": user["employee_id"]}
    return "true", {}


def _crm_ticks(user):
    """Follow-ups logged today from the call list, read back from the CRM by the write-back role (so a tick shows at once)."""
    where, p = ('AND "EmployeeID" = %(emp)s', {"emp": user["employee_id"]}) if user["role"] == "sales" and not user["is_manager"] else ("", {})
    with connect(CRM, "crm_app_writer") as conn:
        return conn.execute(f'''SELECT "ActivityID" AS activity_id, "InquiryID" AS inquiry_id, "EmployeeID" AS employee_id,
                                       "ActivityAt" AS activity_at, "ActivityType" AS activity_type, "Outcome" AS outcome
                                FROM transaction."SalesActivity" WHERE "Source" = 'CallList' AND "ListDate" = %(today)s {where}
                                ORDER BY "ActivityAt"''', {"today": date.today(), **p}).fetchall()


@router.get("/today")
def today(user: dict = Depends(require("sales"))):
    where, p = _scope(user)
    with as_role(user["role"]) as q:
        week = q.all(f"SELECT * FROM mart_sales.v_my_week WHERE {where.replace('sales_owner_id', 'employee_id')} ORDER BY nickname", p)
        ranked = q.all(f"""SELECT t.*, {NAME_COLS} FROM mart_sales.v_today_list t {NAMES}
                           WHERE {where} AND NOT holdout AND rank <= {LIST_SIZE} ORDER BY sales_owner, rank""", p)
        holdout = q.all(f"""SELECT t.inquiry_id, t.sales_owner, t.channel, t.package_interest, t.budget_band, t.requested_date,
                                   t.date_badge, t.inquiry_date, t.furthest_stage, {NAME_COLS}
                            FROM mart_sales.v_today_list t {NAMES} WHERE {where} AND holdout ORDER BY inquiry_date DESC, inquiry_id DESC""", p)
        new = q.all(f"SELECT t.*, {NAME_COLS} FROM mart_sales.v_new_today t {NAMES} WHERE {where} ORDER BY inquiry_date DESC, inquiry_id DESC", p)
        cold = q.all(f"SELECT t.*, {NAME_COLS} FROM mart_sales.v_going_cold t {NAMES} WHERE {where} ORDER BY auto_close_date, inquiry_id", p)
        score = q.one("SELECT max(score_date) AS score_date, max(model_version) AS model_version FROM mart_sales.v_today_list")
    return {"today": date.today(), "my_week": week, "list": ranked, "holdout": holdout, "new": new, "going_cold": cold,
            "ticks": _crm_ticks(user) if user["role"] == "sales" else [], "list_size": LIST_SIZE, **score}


class Activity(BaseModel):
    inquiry_id: int
    activity_type: Literal["Call", "LINE", "Meeting"]
    outcome: Literal["Reached", "No answer", "Left message", "Consult booked", "Not interested"]


@router.post("/activity")
def log_activity(body: Activity, user: dict = Depends(require("sales"))):
    """Mark contacted: one row in the CRM's SalesActivity, through the insert-only role crm_app_writer."""
    if user["role"] != "sales" or not user["employee_id"]:
        raise HTTPException(403, "only salespeople log follow-ups")
    with as_role("sales") as q:
        lead = q.one('SELECT sales_owner_id, "IsOpen" AS is_open FROM mart_sales.v_inquiry WHERE "InquiryID" = %s', (body.inquiry_id,))
    if not lead:
        raise HTTPException(404, "inquiry not found")
    if not user["is_manager"] and lead["sales_owner_id"] != user["employee_id"]:
        raise HTTPException(403, "this couple is on another salesperson's list")
    try:
        with connect(CRM, "crm_app_writer") as conn:
            row = conn.execute('''INSERT INTO transaction."SalesActivity" ("InquiryID", "EmployeeID", "ActivityAt", "ActivityType", "Outcome", "Source", "ListDate")
                                  VALUES (%s, %s, %s, %s, %s, 'CallList', %s) RETURNING "ActivityID" AS activity_id''',
                               (body.inquiry_id, user["employee_id"], datetime.now().replace(microsecond=0), body.activity_type,
                                body.outcome, date.today())).fetchone()
    except psycopg.errors.InsufficientPrivilege as e:
        raise HTTPException(503, f"The CRM refused the follow-up ({e.diag.message_primary}). Please tell the data team.")
    return {"ok": True, **row}


def wilson(booked: int, n: int) -> tuple[float | None, float | None]:
    """95 % Wilson interval, the same formula as mart_sales.v_conversion_by_channel_month, on summed counts."""
    if not n:
        return None, None
    p, z = booked / n, 1.96
    centre, half = p + z * z / (2 * n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((centre - half) / (1 + z * z / n), 4), round((centre + half) / (1 + z * z / n), 4)


@router.get("/performance")
def performance(user: dict = Depends(require("sales", "exec"))):
    with as_role(user["role"]) as q:
        cur = q.one("SELECT to_char(asof_date, 'YYYY-MM') AS month, asof_date FROM app.v_data_as_of")
        funnel = q.all("""SELECT inquiry_month AS month, sum(inquiries) AS inquiries, sum(replied) AS replied, sum(consulted) AS consulted,
                                 sum(quoted) AS quoted, sum(booked) AS booked, sum(still_open) AS still_open
                          FROM mart_sales.v_funnel GROUP BY 1 ORDER BY 1 DESC LIMIT 14""")
        # last 12 complete months, summed per channel; the interval is recomputed on the sums
        chan = q.all("""SELECT channel, sum(inquiries)::int AS inquiries, sum(decided)::int AS decided, sum(booked)::int AS booked
                        FROM mart_sales.v_conversion_by_channel_month
                        WHERE inquiry_month < %(m)s AND inquiry_month >= to_char(to_date(%(m)s, 'YYYY-MM') - interval '12 months', 'YYYY-MM')
                        GROUP BY 1 ORDER BY 1""", {"m": cur["month"]})
        bands = q.all("SELECT * FROM mart_sales.v_response_band")
        hours = q.all("SELECT hour, weekday, inquiries, median_reply_min FROM mart_sales.v_response_by_hour")
        lost = q.all("""SELECT closed_month AS month, closed_by, lost_reason, lost FROM mart_sales.v_lost_reasons
                        WHERE closed_month >= to_char(to_date(%(m)s, 'YYYY-MM') - interval '12 months', 'YYYY-MM') ORDER BY 1""", {"m": cur["month"]})
        quote = q.all("""SELECT group_name, least(floor(quote_share * 50) / 50, 0.4) AS bin, count(*) AS quotes FROM mart_sales.v_quote_share
                         WHERE group_name IN ('Booked', 'Lost on price') GROUP BY 1, 2 ORDER BY 2""")
        quote_med = q.all("""SELECT group_name, count(*) AS quotes, percentile_cont(0.5) WITHIN GROUP (ORDER BY quote_share) AS median
                             FROM mart_sales.v_quote_share GROUP BY 1""")
        comp = q.all("""SELECT salesperson, sum(listed) AS listed, sum(contacted) AS contacted FROM mart_sales.v_followup_compliance
                        WHERE day > %s::date - 28 GROUP BY 1 ORDER BY 1""", (cur["asof_date"],))
    for c in chan:
        c["conversion"] = round(c["booked"] / c["decided"], 4) if c["decided"] else None
        c["ci_low"], c["ci_high"] = wilson(c["booked"], c["decided"])
    return {"current_month": cur["month"], "funnel": funnel, "channels": chan, "response_bands": bands, "response_hours": hours,
            "lost": lost, "quote_bins": quote, "quote_medians": quote_med, "compliance": comp}
