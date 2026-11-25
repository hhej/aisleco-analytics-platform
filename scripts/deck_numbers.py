"""Freeze the numbers and draw the charts for the final presentation.

Reads the marts and ml.model_run, writes docs/v2/deliverables/deck/numbers.json and charts/*.png.
Run it once to freeze a set of numbers (the presenters learn them); docs/v2/deliverables/deck/build.js then builds
the deck and the presenter guide from that file. Re-run only when you want new numbers on the slides.

    .venv/bin/python scripts/deck_numbers.py
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import psycopg  # noqa: E402

from pipeline.config import Config  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "docs" / "v2" / "deliverables" / "deck"
WINE, GREY, INK, MUTED, GRID = "#9E3B52", "#8C8F94", "#1F1F1F", "#5F6368", "#E6E6E6"  # wine + grey pass the CVD check


def rows(con, sql, *args):
    cur = con.execute(sql, args)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def one(con, sql, *args):
    return rows(con, sql, *args)[0]


def style(ax):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=11, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def save(fig, name):
    (OUT / "charts").mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / "charts" / name, dpi=200, bbox_inches="tight", transparent=False, facecolor="white")
    plt.close(fig)


def main():
    plt.rcParams.update({"font.family": "Arial", "font.size": 11})
    con = psycopg.connect(Config().dw_dsn)
    asof = con.execute("SELECT gold.asof()").fetchone()[0]
    n = {"asof": asof.isoformat(), "asof_text": f"{asof.day} {asof:%b %Y}"}

    # Problem 1: one calendar year of staff utilization (planning + wedding-day roles)
    yr = asof.year - 1
    m = rows(con, "SELECT month, ops_utilization u, weddings_held w, person_weeks_over pw, overtime_hours ot "
                  "FROM mart_exec.v_monthly_kpis WHERE month LIKE %s ORDER BY month", f"{yr}-%")
    util = [float(r["u"]) for r in m]
    held = sum(r["w"] for r in m)
    peak_share = sum(r["w"] for r in m if r["month"][5:] in ("01", "02", "11", "12")) / held
    n["p1"] = {"year": yr, "weddings": held, "peak_share_pct": round(100 * peak_share),
               "util_max_pct": round(100 * max(util)), "util_max_month": m[util.index(max(util))]["month"],
               "util_min_pct": round(100 * min(util)), "util_min_month": m[util.index(min(util))]["month"],
               "person_weeks_over": sum(r["pw"] for r in m), "overtime_hours": round(sum(float(r["ot"]) for r in m))}
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    labels = [date(yr, int(r["month"][5:]), 1).strftime("%b") for r in m]
    bars = ax.bar(labels, [100 * u for u in util], color=[WINE if u > 1 else GREY for u in util], width=0.66)
    ax.axhline(100, color=INK, linewidth=1.2, linestyle=(0, (4, 3)))
    ax.text(-0.35, 103, "100 % = contracted hours", ha="left", va="bottom", fontsize=10, color=INK)
    for b, u in zip(bars, util):
        if u == max(util) or u == min(util) or u > 1:
            ax.text(b.get_x() + b.get_width() / 2, 100 * u + 2, f"{100 * u:.0f}%", ha="center", fontsize=10, color=INK)
    ax.set_ylim(0, 150)
    ax.set_yticks([0, 50, 100, 150], ["0%", "50%", "100%", "150%"])
    style(ax)
    save(fig, "p1_utilization.png")

    # lost value (rolling 12 months, from the mart)
    lost = {r["lost_reason"]: r for r in rows(con, "SELECT * FROM mart_exec.v_lost_value")}
    n["lost"] = {k: {"couples": v["lost"], "fee_m": round(float(v["fee_at_stake_estimate"]) / 1e6, 2)} for k, v in lost.items()}
    n["median_fee"] = round(one(con, """SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY "AgreedFee") f FROM gold."FactEventBooking"
                                       WHERE "IsCancelled" = 0 AND "BookingDateKey" >= gold.dk(gold.asof() - 365)""")["f"])
    n["quote_to_booking_pct"] = round(100 * float(one(con, """SELECT sum("IsBooked")::numeric / nullif(sum("IsQuoted"), 0) r
                                                             FROM gold."FactInquiry" WHERE "IsOpen" = 0""")["r"]))
    n["no_reply_quoted"] = one(con, """SELECT count(*) FILTER (WHERE i."IsQuoted" = 1) c FROM mart_sales.v_inquiry i
                                    JOIN gold."DimDate" cd ON cd."DateKey" = i."ClosedDateKey"
                                    WHERE i.outcome = 'Lost' AND i.lost_reason = 'No reply' AND cd."FullDate" > gold.asof() - 365""")["c"]
    n["at_stake_total_m"] = round(n["lost"]["Date unavailable"]["fee_m"] + n["lost"]["No reply"]["fee_m"], 1)

    # Problem 2: funnel over the last 12 complete months
    end = date(asof.year, asof.month, 1) - timedelta(days=1)
    start = date(end.year - 1, end.month, 1) + timedelta(days=31)
    start = date(start.year, start.month, 1)
    f = one(con, """SELECT sum(inquiries) i, sum(consulted) c, sum(quoted) q, sum(booked) b, sum(still_open) o
                    FROM mart_sales.v_funnel WHERE inquiry_month BETWEEN %s AND %s""", f"{start:%Y-%m}", f"{end:%Y-%m}")
    yrs = rows(con, """SELECT substr(month, 1, 4) y, sum(booked)::numeric / nullif(sum(decided), 0) c
                       FROM mart_exec.v_monthly_kpis WHERE substr(month, 1, 4)::int BETWEEN %s AND %s GROUP BY 1 ORDER BY 1""",
               asof.year - 3, asof.year)
    conv = [float(r["c"]) for r in yrs]
    n["p2"] = {"from": f"{start:%b %Y}", "to": f"{end:%b %Y}", "inquiries": int(f["i"]), "consulted": int(f["c"]),
               "quoted": int(f["q"]), "booked": int(f["b"]), "still_open": int(f["o"]),
               "conv_min_pct": round(100 * min(conv), 1), "conv_max_pct": round(100 * max(conv), 1),
               "conv_years": f"{yrs[0]['y']}–{yrs[-1]['y']}",
               "open_today": one(con, """SELECT count(*) c FROM gold."FactLeadScore" WHERE "ScoreDateKey" = gold.dk(gold.asof())""")["c"]}
    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    steps = [("Wrote to us", f["i"]), ("Came to a consultation", f["c"]), ("Got a quote", f["q"]), ("Booked", f["b"])]
    y = range(len(steps))[::-1]
    ax.barh(list(y), [s[1] for s in steps], color=[GREY, GREY, GREY, WINE], height=0.62)
    for yy, (label, v) in zip(y, steps):
        ax.text(v + 15, yy, f"{int(v):,}", va="center", fontsize=12, color=INK, fontweight="bold")
    ax.set_yticks(list(y), [s[0] for s in steps], fontsize=12, color=INK)
    ax.set_xlim(0, float(f["i"]) * 1.15)
    ax.set_xticks([])
    for s in ("top", "right", "bottom", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    save(fig, "p2_funnel.png")

    rep = one(con, """SELECT month, median_first_reply_min m FROM mart_exec.v_monthly_kpis WHERE month = %s""", f"{end:%Y-%m}")
    n["reply"] = {"month": date(end.year, end.month, 1).strftime("%B %Y"), "median_hours": round(float(rep["m"]) / 60, 1)}

    # staff
    st = rows(con, """SELECT "RoleGroup" g, count(*) c FROM gold."DimEmployee" WHERE "IsCurrent" GROUP BY 1""")
    n["staff"] = {r["g"]: r["c"] for r in st}

    # capacity planner tiles and the forecast (same rules as app/api/ops.py)
    ws = asof - timedelta(days=asof.weekday())
    fwd = rows(con, "SELECT * FROM mart_ops.v_forward_demand ORDER BY week_start, role_group")
    over = [r for r in fwd if r["status"] == "Over" and r["week_start"] < ws + timedelta(weeks=12)]
    n["forecast"] = {"freelance_hours_12w": round(sum(float(r["gap_hours"]) for r in fwd if r["week_start"] < ws + timedelta(weeks=12))),
                     "over_role_weeks_12w": len(over),
                     "over_until": max(r["week_start"] for r in over).strftime("%-d %b %Y") if over else None,
                     "over_from": min(r["week_start"] for r in over).strftime("%-d %b") if over else None}
    grid = rows(con, """SELECT week_start, utilization, status FROM mart_ops.v_capacity_grid
                        WHERE week_start >= %s AND week_start < %s""", ws, ws + timedelta(weeks=4))
    n["capacity"] = {"peak_util_4w_pct": round(100 * max(float(g["utilization"] or 0) for g in grid)),
                     "person_weeks_over_4w": sum(g["status"] == "Over" for g in grid)}
    sd = one(con, """SELECT metrics FROM ml.model_run WHERE model = 'staffing_demand' AND status = 'champion'
                     ORDER BY trained_at DESC LIMIT 1""")["metrics"]
    n["forecast"].update({"wape_model": sd["wape"]["model"]["5-12"], "wape_pickup": sd["wape"]["pickup_ratio"]["5-12"],
                          "wape_booked": sd["wape"]["booked_only"]["5-12"], "wape_naive": sd["wape"]["seasonal_naive"]["5-12"],
                          "coverage_pct": round(100 * sd["coverage_conformal_loo"])})
    wk = {}
    for r in fwd:
        w = wk.setdefault(r["week_start"], {"p10": 0, "p50": 0, "p90": 0, "cap": 0})
        for k, c in (("p10", "p10_hours"), ("p50", "p50_hours"), ("p90", "p90_hours"), ("cap", "capacity_hours")):
            w[k] += float(r[c])
    xs = sorted(wk)
    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    ax.fill_between(xs, [wk[x]["p10"] for x in xs], [wk[x]["p90"] for x in xs], color=WINE, alpha=0.15, linewidth=0)
    ax.plot(xs, [wk[x]["p50"] for x in xs], color=WINE, linewidth=2.2)
    ax.step(xs, [wk[x]["cap"] for x in xs], where="mid", color=INK, linewidth=1.4, linestyle=(0, (4, 3)))
    ax.text(xs[-1], wk[xs[-1]]["cap"] + 12, "Hours we have", ha="right", fontsize=10, color=INK)
    ax.text(xs[1], wk[xs[1]]["p90"] + 12, "Hours needed (forecast, with 80 % range)", ha="left", fontsize=10, color=WINE)
    ax.set_ylim(0, max(wk[x]["p90"] for x in xs) * 1.2)
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b"))
    ax.set_ylabel("Staff hours per week", color=MUTED, fontsize=10)
    style(ax)
    save(fig, "forecast.png")

    # lead model v6 (champion) and its gate history
    v = one(con, """SELECT version, metrics, test_window FROM ml.model_run WHERE model = 'lead_conversion' AND status = 'champion'""")
    t, b = v["metrics"]["test"], v["metrics"]["baselines"]
    cap = {"Our model": t["bookings_captured"], "Stage rule": b["B1 stage rule"]["bookings_captured"],
           "Channel × stage rate": b["B2 channel x stage rate"]["bookings_captured"],
           "Today: newest first": b["B0 newest message first"]["bookings_captured"],
           "Logistic regression": b["B3 logistic regression"]["bookings_captured"]}
    n["model"] = {"version": v["version"], "test_window": v["test_window"], "test_rows": t["rows"],
                  "base_rate_pct": round(100 * t["base_rate"]), "pr_auc": round(t["pr_auc"], 3),
                  "pr_auc_ci": t["pr_auc_ci"], "roc_auc": round(t["roc_auc"], 3), "ece": round(t["ece"], 3),
                  "gain_vs_practice": t["gain_vs_practice"], "gain_vs_practice_ci": t["gain_vs_practice_ci"],
                  "gain_vs_rule": t["gain_vs_best_rule"], "gain_vs_rule_ci": t["gain_vs_best_rule_ci"],
                  "captured_pct": {k: round(100 * x) for k, x in cap.items()},
                  "precision5_pct": round(100 * t["precision_at_5"]),
                  "precision5_today_pct": round(100 * b["B0 newest message first"]["precision_at_5"]),
                  "baselines": {k: {"pr_auc": round(x["pr_auc"], 3), "roc_auc": round(x["roc_auc"], 3),
                                    "captured_pct": round(100 * x["bookings_captured"])} for k, x in b.items()}}
    abl = v["metrics"].get("ablation") or one(con, """SELECT metrics->'ablation' a FROM ml.model_run
                                                     WHERE model = 'lead_conversion' AND version = %s""", v["version"])["a"]
    prev = None
    n["model"]["ablation"] = []
    for a in abl:
        n["model"]["ablation"].append({"group": a["+ group"], "gain": None if prev is None else round(a["cv_pr_auc"] - prev, 3)})
        prev = a["cv_pr_auc"]
    n["gate"] = [{"version": r["version"], "status": r["status"], "asof": r["asof_date"].isoformat(), "decision": r["decision"]}
                 for r in rows(con, """SELECT version, status, asof_date, decision FROM ml.model_run
                                       WHERE model = 'lead_conversion' ORDER BY version::int""")]
    fig, ax = plt.subplots(figsize=(7.0, 2.7))
    items = list(cap.items())[::-1]
    items = [i for i in items if i[0] != "Logistic regression"]
    items.insert(len(items) - 1, ("Logistic regression", cap["Logistic regression"]))
    ax.barh(range(len(items)), [100 * x for _, x in items], color=[WINE if k == "Our model" else GREY for k, _ in items], height=0.6)
    for i, (k, x) in enumerate(items):
        ax.text(100 * x + 1.5, i, f"{100 * x:.0f}%", va="center", fontsize=13, color=INK, fontweight="bold")
    ax.set_yticks(range(len(items)), [k for k, _ in items], fontsize=12, color=INK)
    ax.set_xlim(0, 100)
    ax.set_xticks([])
    for s in ("top", "right", "bottom", "left"):
        ax.spines[s].set_visible(False)
    ax.tick_params(length=0)
    save(fig, "model_capture.png")

    # pipeline, quality and runs
    n["dq"] = {"rules_defined": con.execute("SELECT count(*) FROM etl.dq_rule").fetchone()[0],
               "rules": con.execute("""SELECT count(DISTINCT rule_id) FROM etl.dq_result
                                       WHERE run_id = (SELECT max(run_id) FROM etl.run WHERE status = 'success')""").fetchone()[0],
               "caught": {r["rule_id"]: r["rows_failed"] for r in rows(con, """SELECT rule_id, rows_failed FROM etl.dq_result
                          WHERE run_id = (SELECT max(run_id) FROM etl.run WHERE status = 'success') AND rows_failed > 0""")}}
    last = one(con, """SELECT run_id, round(extract(epoch FROM finished_at - started_at)::numeric, 1) s FROM etl.run
                       WHERE status = 'success' ORDER BY run_id DESC LIMIT 1""")
    n["last_run"] = {"run_id": last["run_id"], "seconds": float(last["s"])}

    # OLTP diagram from the report, without its caption and white margins (the slide has its own caption)
    from PIL import Image, ImageChops
    er = Image.open(OUT.parent / "report_figures" / "fig_er_oltp_v6.png").convert("RGB")
    er = er.crop((0, 0, er.width, int(er.height * 0.93)))
    er = er.crop(ImageChops.difference(er, Image.new("RGB", er.size, "white")).getbbox())
    er.save(OUT / "charts" / "er_oltp.png")

    (OUT / "numbers.json").write_text(json.dumps(n, indent=2, default=str, ensure_ascii=False) + "\n")
    print(f"numbers as of {asof} -> {OUT / 'numbers.json'}")


if __name__ == "__main__":
    main()
