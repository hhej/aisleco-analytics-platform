"""Aisle & Co. dashboard: both business problems, the conversion model and the demand forecast.
Reads only the mart views of the warehouse (ETL_DW_DSN, default dbname=aisleco_dw).

  .venv/bin/streamlit run dashboard/app.py
"""
import sys
from decimal import Decimal
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl import db  # noqa: E402
from etl.config import Config  # noqa: E402

st.set_page_config(page_title="Aisle & Co. dashboard", layout="wide")


@st.cache_data(ttl=300)
def read(view, where=""):
    cfg = Config()
    with db.connect(cfg.dw_dsn, cfg, autocommit=True) as c:
        df = pd.DataFrame(c.execute(f"SELECT * FROM mart.{view} {where}").fetchall())
    for col in df.columns:                        # Postgres numeric arrives as Decimal; charts need floats
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = df[col].astype(float)
    return df


st.title("Aisle & Co.")
st.caption("Weekly meeting view. Data: the warehouse's mart views, refreshed by the daily ETL and model run.")
p1, p2, follow, health = st.tabs(["Problem 1: capacity", "Problem 2: conversion", "Follow-up today", "Model health"])

with p1:
    wl = read("v_weekly_workload", "WHERE role IN ('Lead Planner','Planner','Coordinator','Event Staff')")
    wl["week_start"] = pd.to_datetime(wl.week_start)
    wl["util"] = wl.UtilizationRate.astype(float) * 100
    lo, hi = st.slider("Weeks", min_value=wl.week_start.min().date(), max_value=wl.week_start.max().date(),
                       value=(pd.Timestamp("2025-01-01").date(), pd.Timestamp("2026-12-31").date()))
    w = wl[wl.week_start.between(pd.Timestamp(lo), pd.Timestamp(hi))]
    peak = read("v_peak_acceptance").iloc[0]
    a, b, c = st.columns(3)
    a.metric("Employee-weeks over 100%", int((w.util > 100).sum()), help="Ops staff, weeks in the selected range")
    b.metric("Peak-date acceptance", f"{float(peak.acceptance_rate):.0%}", help="Inquiries for Nov-Feb or auspicious dates that booked")
    c.metric("Lost: date unavailable", int(peak.lost_date_unavailable))
    st.subheader("Weekly utilization, ops staff")
    st.altair_chart(alt.Chart(w).mark_rect().encode(
        x=alt.X("yearmonthdate(week_start):O", title=None, axis=alt.Axis(format="%b %Y", formatType="time", labelAngle=0, labelOverlap="greedy")),
        y=alt.Y("employee:N", title=None),
        color=alt.Color("util:Q", title="% of standard hours", scale=alt.Scale(domain=[0, 100, 180], range=["#f3f1ec", "#e8a33d", "#b2301e"])),
        tooltip=[alt.Tooltip("week_start:T", title="week"), "employee", alt.Tooltip("util:Q", format=".0f", title="utilization %"),
                 "ActiveEvents"]).properties(height=260))
    fc = read("v_demand_forecast")
    st.subheader("Next 6 months: weddings and staff-hours against capacity")
    if fc.empty:
        st.info("No demand forecast yet: run python -m ml train, then python -m ml daily.")
    else:
        st.caption(f"Forecast of {fc.AsOfDate.iloc[0]} (demand model v{fc.Version.iloc[0]}): hours already assigned to booked "
                   "weddings plus the hours of the weddings still expected to book, against the event staff's standard hours.")
        fc["month"] = pd.to_datetime(fc.Month).dt.strftime("%b %Y")
        bars = fc.melt(id_vars=["month"], value_vars=["BookedHours", "ExpectedHours"], var_name="part", value_name="hours")
        bars["part"] = bars.part.map({"BookedHours": "booked", "ExpectedHours": "booked + expected"})
        order = list(fc.month)
        chart = alt.Chart(bars).mark_bar().encode(
            x=alt.X("month:N", sort=order, title=None, axis=alt.Axis(labelAngle=0)), xOffset="part:N",
            y=alt.Y("hours:Q", title="staff-hours"),
            color=alt.Color("part:N", title=None, scale=alt.Scale(range=["#9a9893", "#2a78d6"])),
            tooltip=["month", "part", alt.Tooltip("hours:Q", format=",.0f")])
        cap = alt.Chart(fc).mark_tick(color="#b2301e", thickness=2, size=60).encode(
            x=alt.X("month:N", sort=order), y="CapacityHours:Q", tooltip=[alt.Tooltip("CapacityHours:Q", title="capacity", format=",.0f")])
        st.altair_chart((chart + cap).properties(height=260), use_container_width=True)
        st.caption("Red tick: capacity. Over = 85%+ of capacity (such months had 8-12 staff-weeks over 100%), Tight = 65-85%, Quiet = under 35%.")
        fc["expected_utilization"] = fc.expected_utilization.astype(float) * 100
        st.dataframe(fc[["month", "booked", "expected_weddings", "low_80", "high_80", "expected_utilization", "status", "action"]],
                     hide_index=True, width="stretch",
                     column_config={"expected_utilization": st.column_config.ProgressColumn("Expected utilization", min_value=0, max_value=100, format="%.0f%%"),
                                    "low_80": "low (80%)", "high_80": "high (80%)"})
    t = read("v_turned_away_demand").groupby("requested_month", as_index=False).inquiries_turned_away.sum()
    st.subheader("Couples turned away (date unavailable), by requested wedding month")
    st.bar_chart(t, x="requested_month", y="inquiries_turned_away", color="#b2301e", height=240)

with p2:
    f = read("v_funnel_by_channel").sort_values("inquiries", ascending=False)
    a, b, c = st.columns(3)
    decided = f.inquiries.sum() - f.still_open.sum()
    a.metric("Inquiries", int(f.inquiries.sum()))
    b.metric("Conversion (decided)", f"{f.booked.sum() / decided:.1%}")
    nr = read("v_no_reply_rate")
    c.metric("No-reply rate", f"{nr.no_reply.sum() / nr.decided.sum():.0%}", help="Closed by the 30-day silence rule, of decided inquiries")
    left, right = st.columns(2)
    left.subheader("Funnel by channel")
    left.dataframe(f.rename(columns={"conversion_rate_decided": "conversion"}), hide_index=True, width="stretch")
    m = nr.groupby("inquiry_month", as_index=False)[["decided", "no_reply"]].sum()
    m["no_reply_rate"] = m.no_reply / m.decided
    right.subheader("No-reply rate by inquiry month")
    right.line_chart(m, x="inquiry_month", y="no_reply_rate", color="#b2301e", height=300)
    left, right = st.columns(2)
    left.subheader("Reply speed vs conversion")
    left.bar_chart(read("v_response_vs_conversion"), x="ResponseTimeBand", y="conversion_rate", height=260)
    right.subheader("Why we lose couples")
    right.bar_chart(read("v_lost_reasons"), x="LostReason", y="lost", height=260)

with follow:
    fl = read("v_followup_list")
    if fl.empty:
        st.info("No scores yet: run the daily model steps (python -m ml daily).")
    else:
        st.caption(f"Open inquiries on {fl.AsOfDate.iloc[0]}, scored by model v{fl.Version.iloc[0]}. "
                   "Call the High band first; a long silence means the couple is about to drop out.")
        by = st.radio("Order by", ["Chance to book", "Expected value (chance x budget)"], horizontal=True)
        fl = fl.sort_values("Score" if by == "Chance to book" else "ExpectedValue", ascending=False)
        fl["Score"] = fl.Score.astype(float)
        st.dataframe(fl.drop(columns=["AsOfDate", "Version"]), hide_index=True, width="stretch",
                     column_config={"Score": st.column_config.ProgressColumn("Chance to book", min_value=0, max_value=1, format="%.2f"),
                                    "HoursSilent": st.column_config.NumberColumn("Hours since couple wrote", format="%.0f"),
                                    "BudgetMidpoint": st.column_config.NumberColumn("Budget (THB)", format="localized"),
                                    "ExpectedValue": st.column_config.NumberColumn("Expected value (THB)", format="localized")})

with health:
    hh = read("v_model_health")
    h = hh[hh.Model == "conversion"] if not hh.empty else hh
    if h.empty:
        st.info("No production model yet: run python -m ml train.")
    else:
        r = h.iloc[0]
        st.subheader("Conversion model (Problem 2)")
        a, b, c = st.columns(3)
        a.metric("Production model", f"v{r.Version} · {r.Algorithm}")
        b.metric("Test AUC", f"{float(r.test_auc):.2f}", f"{float(r.test_auc) - float(r.baseline_auc):+.2f} vs the best baseline")
        c.metric("Trained on data to", str(r.trained_on_data_to))
        if r.checked_on is not None:
            st.caption(f"Monitoring on {r.checked_on}")
            st.dataframe(h[["Check", "Value", "Status", "Detail"]], hide_index=True, width="stretch")
    d = hh[hh.Model == "demand"] if not hh.empty else hh
    if not d.empty:
        r = d.iloc[0]
        st.subheader("Demand forecast (Problem 1)")
        a, b, c = st.columns(3)
        a.metric("Production forecast", f"v{r.Version} · {r.Algorithm}")
        b.metric("Backtest WAPE", f"{float(r.test_wape):.0%}", help="Average error as a share of the weddings in a month, over a 12-month rolling backtest")
        c.metric("Trained on data to", str(r.trained_on_data_to))
        if r.checked_on is not None:
            st.dataframe(d[["Check", "Value", "Status", "Detail"]], hide_index=True, width="stretch")
    st.subheader("Model registry")
    st.dataframe(read("v_model_versions").sort_values("Version", ascending=False), hide_index=True, width="stretch")
