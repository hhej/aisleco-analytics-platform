"""Operations "Capacity planner" (Spec 05 §3.3): staff x week grid, week detail, next 26 weeks, date checker, lost peak demand."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import require
from app.api.db import as_role

router = APIRouter(prefix="/api/ops")
AUSPICIOUS_RANK = {"High": 3, "Medium": 2, "Low": 1, "None": 0}


def _this_week(q) -> date:
    asof = q.one("SELECT asof_date FROM app.v_data_as_of")["asof_date"]
    return asof - timedelta(days=asof.weekday())


@router.get("/capacity")
def capacity(user: dict = Depends(require("ops", "exec"))):
    with as_role(user["role"]) as q:
        ws = _this_week(q)
        grid = q.all("""SELECT week_start, employee_id, nickname, role_name, role_group, employment_type, capacity_hours,
                               round(assigned_hours, 1) AS assigned_hours, round(utilization, 4) AS utilization,
                               round(overtime_hours, 1) AS overtime_hours, weddings, status
                        FROM mart_ops.v_capacity_grid WHERE week_start BETWEEN %(a)s AND %(b)s ORDER BY role_group, role_name, nickname, week_start""",
                     {"a": ws - timedelta(weeks=4), "b": ws + timedelta(weeks=11)})
        role_weeks = q.all("""SELECT week_start, role_group, capacity_hours, round(assigned_hours, 1) AS assigned_hours, utilization,
                                     round(overtime_hours, 1) AS overtime_hours, person_weeks_over
                              FROM mart_ops.v_role_week WHERE week_start BETWEEN %(a)s AND %(b)s ORDER BY 1, 2""",
                           {"a": ws - timedelta(weeks=4), "b": ws + timedelta(weeks=25)})
        forward = q.all("SELECT * FROM mart_ops.v_forward_demand ORDER BY week_start, role_group")
        turned = q.all("""SELECT requested_month, auspicious_level, closed_month, couples_turned_away, fee_at_stake_estimate
                          FROM mart_ops.v_turned_away ORDER BY requested_month""")
        peak = q.all("SELECT * FROM mart_ops.v_peak_acceptance ORDER BY requested_month")
        hours = q.all("SELECT * FROM mart_ops.v_hours_per_wedding ORDER BY package, guest_band, complexity")

    # header tiles (ratios of sums are already in the views; here only max, count and sum over the chosen weeks)
    next4 = [g for g in grid if ws <= g["week_start"] < ws + timedelta(weeks=4)]
    peak_cell = max(next4, key=lambda g: g["utilization"] or 0, default=None)
    since = (ws - timedelta(days=90)).strftime("%Y-%m")  # v_turned_away is monthly: "last 90 days" = closing months since then
    return {
        "this_week": ws,
        "tiles": {
            "peak_utilization_4w": peak_cell and {k: peak_cell[k] for k in ("utilization", "nickname", "role_name", "week_start")},
            "person_weeks_over_4w": sum(g["status"] == "Over" for g in next4),
            "overtime_hours_12w": round(sum(g["overtime_hours"] for g in grid if g["week_start"] >= ws), 1),
            "forecast_gap_12w": round(sum(float(f["gap_hours"]) for f in forward if f["week_start"] < ws + timedelta(weeks=12)), 1) if forward else None,
            "turned_away_90d": sum(t["couples_turned_away"] for t in turned if t["closed_month"] >= since),
            "turned_away_since": since,
        },
        "grid": grid, "role_weeks": role_weeks, "forward": forward, "forecast_available": bool(forward),
        "turned_away": turned, "peak_acceptance": peak, "hours_per_wedding": hours,
    }


@router.get("/week")
def week_detail(employee_id: int, week_start: date, user: dict = Depends(require("ops", "exec"))):
    with as_role(user["role"]) as q:
        cell = q.one("SELECT * FROM mart_ops.v_capacity_grid WHERE employee_id = %s AND week_start = %s", (employee_id, week_start))
        rows = q.all("""SELECT event_id, event_date, package, guests, complexity, role, round(planning_hours, 1) AS planning_hours,
                               round(event_day_hours, 1) AS event_day_hours, round(total_hours, 1) AS total_hours, also_on_it
                        FROM mart_ops.v_week_detail WHERE employee_id = %s AND week_start = %s ORDER BY total_hours DESC, event_date""",
                     (employee_id, week_start))
    if not cell:
        raise HTTPException(404, "no contract that week")
    return {"cell": cell, "weddings": rows}


def date_status(d: dict) -> str:
    """Same rule as the date badge in mart_sales.v_today_list: Full / Tight / Room."""
    head = min(d["planning_headroom_hours"] or 0, d["wedding_day_headroom_hours"] or 0)
    if d["weddings_that_day"] >= 2 or head < 0:
        return "Full"
    if d["weddings_that_day"] == 1 or head < 20:
        return "Tight"
    return "Room"


@router.get("/date-check")
def date_check(d: date, user: dict = Depends(require("ops", "exec"))):
    with as_role(user["role"]) as q:
        rows = q.all("SELECT * FROM mart_ops.v_date_check WHERE date BETWEEN %s AND %s ORDER BY date", (d - timedelta(days=28), d + timedelta(days=28)))
    for r in rows:
        r["status"] = date_status(r)
    target = next((r for r in rows if r["date"] == d), None)
    if not target:
        raise HTTPException(404, "pick a date from today up to the end of 2028")
    # alternatives: weekend or auspicious (High/Medium) dates within 4 weeks that have room; weekends first (most couples
    # marry on a weekend), then the better auspicious level, then the nearest
    alts = [r for r in rows if r["date"] != d and r["status"] == "Room"
            and (r["day_name"] in ("Sat", "Sun") or r["auspicious_level"] in ("High", "Medium"))]
    alts.sort(key=lambda r: (r["day_name"] not in ("Sat", "Sun"), -AUSPICIOUS_RANK.get(r["auspicious_level"], 0), abs((r["date"] - d).days)))
    return {"date": target, "alternatives": alts[:4]}
