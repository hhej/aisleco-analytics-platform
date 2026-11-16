"""Staffing-demand forecast (Spec 04 §11–§12): staff-hours per role group per week for the next 26 weeks.

forecast(w, g) at date t = booked B (hours of weddings booked before t, as the spread rule places them)
                         + pipeline P (open inquiries with a requested date × their stage-and-channel booking rate
                                       measured before t × the average hours profile of a wedding of that package)
                         + pickup R (bookings not yet in sight), learned by LightGBM quantile regression (10 / 50 / 90 %)
Backtest: rolling origins (the 1st Monday of each of the last 12 months); each origin's models are trained only on
origins whose target weeks were over before it. Compared with booked-only, seasonal naive and pickup-ratio baselines
by WAPE per horizon bucket; the winner at 5–12 weeks is used (it may be a baseline)."""
import logging
from datetime import date, timedelta

import lightgbm as lgb
import mlflow
import numpy as np
import pandas as pd
import psycopg

from ml import data as D
from ml.train import TRACKING
from pipeline.config import Config, ROOT

log = logging.getLogger("ml")
H, GROUPS, MODEL = 26, ["Planning", "Wedding day"], "staffing_demand"
QS = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
FEATS = ["h", "month", "high_season", "lent", "auspicious_weekend_days", "booked", "pipeline", "inquiries_28d", "last_year", "is_planning"]


def monday(d):
    return d - timedelta(d.weekday())


class Data:
    def __init__(self, cfg):
        con = D.connect(cfg)
        con.execute(f"ATTACH '{cfg.dw_dsn}' AS dw (TYPE postgres, READ_ONLY)")
        self.hours = con.execute('''
            SELECT d."WeekStart" AS week, e."RoleGroup" AS grp, f."TotalHours"::DOUBLE AS hours, f."EventID" AS event_id, ev.booked_at, ev.event_date,
                   p.package_type, date_diff('week', d."WeekStart", date_trunc('week', ev.event_date)::DATE) AS weeks_before
            FROM dw.gold."FactStaffWorkload" f JOIN dw.gold."DimDate" d ON d."DateKey" = f."WeekStartDateKey"
            JOIN dw.gold."DimEmployee" e ON e."EmployeeKey" = f."EmployeeKey" JOIN event ev ON ev.event_id = f."EventID"
            JOIN package p ON p.package_id = ev.package_id WHERE e."RoleGroup" IN ('Planning', 'Wedding day')''').df()
        self.inq = con.execute('''SELECT i.inquiry_id, i.inquiry_at, i.closed_at, i.consultation_at, i.quoted_at, i.requested_event_date,
                                         c.channel_name, coalesce(p.package_type, 'Full') AS package_type,
                                         EXISTS (SELECT 1 FROM event e WHERE e.inquiry_id = i.inquiry_id) AS booked
                                  FROM inquiry i JOIN channel c USING (channel_id) LEFT JOIN package p ON p.package_id = i.indicative_package_id''').df()
        self.cal = con.execute("SELECT week_start AS week, max(is_high_season::INT) AS high_season, max(is_buddhist_lent::INT) AS lent, "
                               "sum((auspicious_level <> 'None' AND is_weekend)::INT) AS auspicious_weekend_days FROM calendar_day GROUP BY 1").df()
        self.cap = con.execute('''SELECT d."WeekStart" AS week, e."RoleGroup" AS grp, sum(e."StandardWeeklyHours")::DOUBLE AS capacity
                                  FROM (SELECT DISTINCT "WeekStart" FROM dw.gold."DimDate" WHERE "DateKey" <> -1) d
                                  JOIN dw.gold."DimEmployee" e ON d."WeekStart" BETWEEN e."ValidFrom" AND e."ValidTo"
                                  WHERE e."RoleGroup" IN ('Planning', 'Wedding day') GROUP BY 1, 2''').df()
        for t in (self.hours, self.cal, self.cap):
            t["week"] = pd.to_datetime(t["week"]).dt.date
        self.actual = self.hours.groupby(["week", "grp"]).hours.sum()

    def features(self, t: date) -> pd.DataFrame:
        """One row per target week (1..26 weeks after the week of t) and role group, using only what was known at t."""
        t0 = monday(t)
        weeks = [t0 + timedelta(7 * h) for h in range(1, H + 1)]
        ts = pd.Timestamp(t)
        booked = self.hours[self.hours.booked_at < ts].groupby(["week", "grp"]).hours.sum()
        # pipeline: open leads with a date, × booking rate of their stage and channel measured on leads decided before t
        inq = self.inq
        past = inq[inq.closed_at < ts]
        stage = lambda d: np.select([d.quoted_at < ts, d.consultation_at < ts], ["quoted", "consulted"], "open")
        rate = past.assign(stage=stage(past)).groupby(["stage", "channel_name"]).booked.mean()
        open_ = inq[(inq.inquiry_at < ts) & ((inq.closed_at.isna()) | (inq.closed_at >= ts)) & inq.requested_event_date.notna()]
        open_ = open_.assign(stage=stage(open_))
        open_["p"] = [rate.get((s, c), past.booked.mean()) for s, c in zip(open_.stage, open_.channel_name)]
        known = self.hours[self.hours.booked_at < ts]      # average hours of one wedding, by package, role group, weeks before it
        prof = known.groupby(["package_type", "grp", "weeks_before"]).hours.sum().div(known.groupby("package_type").event_id.nunique(), level="package_type")
        pipe = {}
        for r in open_.itertuples():
            ww = monday(pd.Timestamp(r.requested_event_date).date())
            if r.package_type not in prof.index.get_level_values(0):
                continue
            for (g, k), hrs in prof[r.package_type].items():
                key = (ww - timedelta(7 * int(k)), g)
                pipe[key] = pipe.get(key, 0) + r.p * hrs
        inq28 = int(((inq.inquiry_at < ts) & (inq.inquiry_at >= ts - pd.Timedelta(days=28))).sum())
        cal = self.cal.set_index("week")
        rows = []
        for h, w in enumerate(weeks, 1):
            ly = w - timedelta(364)
            for g in GROUPS:
                c = cal.loc[w] if w in cal.index else None
                rows.append({"origin": t, "week": w, "grp": g, "h": h, "month": w.month, "high_season": c.high_season if c is not None else 0,
                             "lent": c.lent if c is not None else 0, "auspicious_weekend_days": c.auspicious_weekend_days if c is not None else 0,
                             "booked": booked.get((w, g), 0.0), "pipeline": pipe.get((w, g), 0.0), "inquiries_28d": inq28,
                             "last_year": self.actual.get((ly, g), np.nan) if ly < t0 else np.nan, "is_planning": int(g == "Planning")})
        out = pd.DataFrame(rows)
        out["actual"] = [self.actual.get((w, g), 0.0) for w, g in zip(out.week, out.grp)]
        return out


def fit(train):
    y = train.actual - train.booked - train.pipeline
    return {q: lgb.LGBMRegressor(objective="quantile", alpha=a, n_estimators=300, learning_rate=0.05, num_leaves=15,
                                 min_child_samples=20, verbose=-1, random_state=0).fit(train[FEATS], y) for q, a in QS.items()}


def predict(models, df):
    base = (df.booked + df.pipeline).values
    out = {q: np.maximum(df.booked.values, base + m.predict(df[FEATS])) for q, m in models.items()}
    out["p10"], out["p90"] = np.minimum(out["p10"], out["p50"]), np.maximum(out["p90"], out["p50"])
    return out


def wape(a, f):
    return float(np.abs(a - f).sum() / max(1e-9, a.sum()))


def bucket(h):
    return "1-4" if h <= 4 else "5-12" if h <= 12 else "13-26"


def run(asof: date, cfg=None) -> dict:
    cfg = cfg or Config()
    dd = Data(cfg)
    first = date(2023, 9, 4)
    origins = [first + timedelta(7 * i) for i in range(((asof - first).days) // 7 + 1)]
    table = pd.concat([dd.features(t) for t in origins], ignore_index=True)
    complete = table[table.week < monday(asof)]                      # targets that are over
    bt_origins = sorted({date(m.year, m.month, 1) for m in pd.date_range(asof - timedelta(365), asof - timedelta(7 * 13), freq="MS")})
    bt_origins = [monday(o + timedelta(6)) for o in bt_origins]
    res = []
    for o in bt_origins:
        tr = complete[pd.to_datetime(complete.week) < pd.Timestamp(o)]
        te = complete[complete.origin == o]
        if tr.empty or te.empty:
            continue
        pr = predict(fit(tr), te)
        ratio = (tr.actual / tr.booked.replace(0, np.nan)).groupby(tr.h).median()
        te = te.assign(model=pr["p50"], p10=pr["p10"], p90=pr["p90"], booked_only=te.booked,
                       seasonal_naive=te.last_year.fillna(te.booked), pickup_ratio=te.booked * te.h.map(ratio).fillna(1).values)
        res.append(te)
    bt = pd.concat(res)
    bt["bucket"] = bt.h.map(bucket)
    methods = ["model", "booked_only", "seasonal_naive", "pickup_ratio"]
    table_bt = {m: {b: round(wape(g.actual.values, g[m].values), 3) for b, g in bt.groupby("bucket")} for m in methods}
    bias = {m: round(float((bt[m] - bt.actual).sum() / bt.actual.sum()), 3) for m in methods}
    coverage = round(float(((bt.actual >= bt.p10) & (bt.actual <= bt.p90)).mean()), 3)
    # the quantile models' band is too narrow (coverage above); use the backtest's own relative errors per horizon bucket
    # instead (split-conformal style), checked by leaving each backtest origin out in turn
    bt["rel"] = (bt.actual - bt.model) / bt.model.clip(lower=1)
    band = bt.groupby("bucket").rel.quantile([0.1, 0.9]).unstack()
    loo = []
    for o, g in bt.groupby("origin"):
        b_ = bt[bt.origin != o].groupby("bucket").rel.quantile([0.1, 0.9]).unstack()
        lo, hi = g.model * (1 + g.bucket.map(b_[0.1])), g.model * (1 + g.bucket.map(b_[0.9]))
        loo.append(((g.actual >= lo) & (g.actual <= hi)).mean())
    coverage_conformal = round(float(np.mean(loo)), 3)
    winner = min(methods, key=lambda m: table_bt[m]["5-12"])
    # production forecast from today
    models = fit(complete)
    now = dd.features(asof)
    pr = predict(models, now)
    if winner != "model":   # honest outcome: a baseline wins → use it as P50, keep the model's band for uncertainty
        ratio = (complete.actual / complete.booked.replace(0, np.nan)).groupby(complete.h).median()
        pr["p50"] = {"booked_only": now.booked.values, "seasonal_naive": now.last_year.fillna(now.booked).values,
                     "pickup_ratio": (now.booked * now.h.map(ratio).fillna(1)).values}[winner]
    bk = now.h.map(bucket)
    pr["p10"] = np.maximum(now.booked.values, pr["p50"] * (1 + bk.map(band[0.1]).values))
    pr["p90"] = np.maximum(pr["p50"], pr["p50"] * (1 + bk.map(band[0.9]).values))
    cap = dd.cap.set_index(["week", "grp"]).capacity
    now = now.assign(p10=pr["p10"], p50=pr["p50"], p90=pr["p90"], new=np.maximum(0, pr["p50"] - now.booked - now.pipeline),
                     capacity=[cap.get((w, g), 0.0) for w, g in zip(now.week, now.grp)])
    mlflow.set_tracking_uri(TRACKING)
    if mlflow.get_experiment_by_name(MODEL) is None:
        mlflow.create_experiment(MODEL, artifact_location=(ROOT / "lake" / "mlflow").as_uri())
    mlflow.set_experiment(MODEL)
    with mlflow.start_run(run_name=f"forecast as of {asof}") as run:
        mlflow.log_params({"asof": str(asof), "winner": winner, "origins": len(origins), "backtest_origins": len(bt_origins)})
        mlflow.log_metrics({f"wape_{m}_{b.replace('-', '_')}": v for m, d in table_bt.items() for b, v in d.items()} | {"p10_p90_coverage_quantile_models": coverage, "p10_p90_coverage_conformal": coverage_conformal})
        version = run.info.run_id[:8]
    dk = lambda d: int(d.strftime("%Y%m%d"))
    with psycopg.connect(cfg.dw_dsn) as c:
        c.execute('DELETE FROM gold."FactDemandForecast" WHERE "ForecastDateKey" = %s', (dk(asof),))
        with c.cursor() as cur:
            cur.executemany('INSERT INTO gold."FactDemandForecast" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            [(dk(asof), dk(r.week), r.grp, round(r.booked, 1), round(r.pipeline, 1), round(r.new, 1), round(r.p10, 1),
                              round(r.p50, 1), round(r.p90, 1), round(r.capacity, 1), f"{winner}:{version}") for r in now.itertuples()])
        c.execute("INSERT INTO ml.model_run(model, version, mlflow_run_id, asof_date, algorithm, train_window, test_window, train_rows, test_rows, "
                  "metrics, decision, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'champion') ON CONFLICT (model, version) DO NOTHING",
                  (MODEL, version, run.info.run_id, asof, "booked + stage-rate pipeline + LightGBM quantile pickup",
                   f"{first} to {asof}", f"{bt_origins[0]} to {bt_origins[-1]}", len(complete), len(bt),
                   pd.Series({"wape": table_bt, "bias": bias, "coverage_quantile_models": coverage, "coverage_conformal_loo": coverage_conformal}).to_json(),
                   f"lowest WAPE at 5-12 weeks: {winner} ({table_bt[winner]['5-12']})"))
    log.info("forecast as of %s: winner %s, WAPE 5-12 wk %s", asof, winner, table_bt[winner]["5-12"])
    return {"winner": winner, "wape": table_bt, "bias": bias, "coverage_quantile_models": coverage, "coverage_conformal_loo": coverage_conformal,
            "rows": len(now)}
