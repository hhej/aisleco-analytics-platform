"""Demand forecast (Problem 1): how many weddings, and how many staff-hours, will each of the next
6 months need, against the event staff's capacity?

Weddings are booked 1 to 15 months ahead, so most of a near month is already on the books. The
forecast is the method hotels use: weddings already booked + the pickup still to come. Every
candidate predicts the pickup; two of them are simple baselines:

- seasonal naive: the same month last year, final
- pickup average: the average pickup seen at the same horizon and season
- Poisson regression and LightGBM (Poisson loss) on the features of feature.demand_features

The models learn from the features as they stood every Monday and every 1st of the month (about 5x
more rows than month starts alone; the weeks of one month share an outcome, so they are not independent).
A rolling-origin backtest (one forecast per month over the last 12 months, each made only with
what was known on that day) picks the production forecast. An ML candidate is used only if it
beats the best baseline; otherwise the baseline is registered as production."""
import logging
import pickle
from datetime import date

import numpy as np
import pandas as pd
from psycopg.types.json import Jsonb
from lightgbm import LGBMRegressor
from scipy.stats import poisson
from sklearn.linear_model import PoissonRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from etl import meta

from . import features as F
from .monitor import level
from .train import production

log = logging.getLogger("ml")

X = ["Horizon", "OnBooks", "OpenInquiries", "HighSeason", "AuspiciousDays", "OnBooksLastYear", "LastYearFinal",
     "LastYearPickup", "OpenQuoted", "Bookings28d"]
COLS = ["AsOf", "Month", *X]
BASELINES = ("seasonal naive", "pickup average")
WARMUP_MONTHS = 9        # the CRM's first months have no bookings far ahead yet: not used as history
BACKTEST_MONTHS = 12
OPS_ROLES = ("Lead Planner", "Planner", "Coordinator", "Event Staff")   # the staff who run weddings


# ---------- features ----------

def compute(conn, asof: date) -> pd.DataFrame:
    return pd.DataFrame(conn.execute("SELECT * FROM feature.demand_features(%s)", (asof,)).fetchall(), columns=COLS)


def materialise(conn, asof: date) -> pd.DataFrame:
    """Store the features of the next 6 months in the offline store (a rerun replaces the day)."""
    F.require_fresh(conn, asof)
    conn.execute('DELETE FROM feature."DemandFeatures" WHERE "AsOf" = %s', (asof,))
    conn.execute('INSERT INTO feature."DemandFeatures" SELECT * FROM feature.demand_features(%s)', (asof,))
    return compute(conn, asof)


def history(conn, asof: date) -> pd.DataFrame:
    """The features as they stood on every past Monday and 1st of the month, with the final number of
    weddings of the months that have ended by the as-of date (the label). The backtest scores the 1sts."""
    first = conn.execute('SELECT min("CreatedDate")::date AS d FROM stg."Event"').fetchone()["d"]
    start, end = pd.Timestamp(first) + pd.DateOffset(months=WARMUP_MONTHS), pd.Timestamp(asof)
    origins = pd.date_range(start, end, freq="MS").union(pd.date_range(start, end, freq="W-MON"))
    df = pd.concat([compute(conn, o.date()) for o in origins], ignore_index=True)
    final = pd.DataFrame(conn.execute(
        'SELECT date_trunc(\'month\', "EventDate")::date AS "Month", count(*)::int AS "Final" FROM stg."Event" '
        'WHERE "CreatedDate" < %s GROUP BY 1', (asof,)).fetchall(), columns=["Month", "Final"])
    df = df.merge(final, on="Month", how="left").fillna({"Final": 0})
    df["AsOf"], df["Month"] = pd.to_datetime(df.AsOf), pd.to_datetime(df.Month)
    df = df[df.Month + pd.DateOffset(months=1) <= pd.Timestamp(asof)].reset_index(drop=True)
    df["Pickup"] = (df.Final - df.OnBooks).clip(lower=0)
    return df


# ---------- candidates: each predicts the pickup still to come ----------

class SeasonalNaive:
    def fit(self, df):
        return self

    def predict(self, df):
        return (df.LastYearFinal - df.OnBooks).clip(lower=0).to_numpy(float)


class PickupAverage:
    def fit(self, df):
        self.by_season = df.groupby(["Horizon", "HighSeason"]).Pickup.mean()
        self.by_h, self.all = df.groupby("Horizon").Pickup.mean(), df.Pickup.mean()
        return self

    def predict(self, df):
        key = pd.MultiIndex.from_frame(df[["Horizon", "HighSeason"]])
        p = pd.Series(self.by_season.reindex(key).to_numpy(), index=df.index)
        return p.fillna(df.Horizon.map(self.by_h)).fillna(self.all).to_numpy(float)


class Regression:
    # Horizon as a number, not one-hot: pickup grows smoothly with it (backtest WAPE 0.116 vs 0.122)
    def __init__(self, est, scale):
        self.model = make_pipeline(StandardScaler(), est) if scale else est

    def fit(self, df):
        self.model.fit(df[X], df.Pickup)
        return self

    def predict(self, df):
        return np.clip(self.model.predict(df[X]), 0, None)


def candidates(seed=0):
    return {"seasonal naive": SeasonalNaive(), "pickup average": PickupAverage(),
            "Poisson regression": Regression(PoissonRegressor(alpha=1.0, max_iter=1000), scale=True),
            # small, shallow and subsampled: about 800 rows, most of them weeks of the same months
            "LightGBM": Regression(LGBMRegressor(
                objective="poisson", n_estimators=200, learning_rate=0.03, num_leaves=4, min_child_samples=10,
                subsample=0.8, subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1), scale=False)}


def interval(on_books, mu, q):
    return on_books + poisson.ppf(q, np.maximum(mu, 1e-9))


def scores(bt):
    err = (bt.Forecast - bt.Final).abs()
    by_h = {str(h): round(float(e.sum() / max(1, f.sum())), 3) for (h, e), (_, f) in zip(err.groupby(bt.Horizon), bt.Final.groupby(bt.Horizon))}
    return {"wape": round(float(err.sum() / max(1, bt.Final.sum())), 3), "mae": round(float(err.mean()), 2),
            "wape_by_horizon": by_h, "coverage80": round(float(bt.Final.between(bt.Lo, bt.Hi).mean()), 3)}


def backtest(df, asof: date, seed=0) -> pd.DataFrame:
    """Rolling origin: for each of the last 12 month starts, fit on the months that had ended by
    then and forecast the next 6 (only the months that have ended since are scored)."""
    out = []
    for o in pd.date_range(end=pd.Timestamp(asof), periods=BACKTEST_MONTHS, freq="MS"):
        tr, te = df[df.Month + pd.DateOffset(months=1) <= o], df[df.AsOf == o]
        if len(tr) < 30 or te.empty:
            continue
        for name, m in candidates(seed).items():
            mu = m.fit(tr).predict(te)
            out.append(te.assign(Candidate=name, Forecast=te.OnBooks + mu,
                                 Lo=interval(te.OnBooks, mu, .1), Hi=interval(te.OnBooks, mu, .9)))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---------- training, serving, monitoring ----------

def train(conn, asof: date, seed=0) -> dict:
    F.require_fresh(conn, asof)
    df = history(conn, asof)
    bt = backtest(df, asof, seed)
    if bt.empty or bt.Candidate.value_counts().min() < 30:
        raise ValueError(f"not enough monthly history before {asof} for a backtest ({len(df)} rows)")
    res = {n: scores(g) for n, g in bt.groupby("Candidate")}
    base = min(BASELINES, key=lambda n: res[n]["wape"])
    ml = min((n for n in res if n not in BASELINES), key=lambda n: res[n]["wape"])
    if res[ml]["wape"] < res[base]["wape"]:
        name, why = ml, f"WAPE {res[ml]['wape']} beats the best baseline ({base}, {res[base]['wape']})"
    else:
        name, why = base, (f"no ML candidate beat the {base} baseline (WAPE {res[base]['wape']}; best ML: {ml}, "
                           f"{res[ml]['wape']}): the baseline is the production forecast")
    final = candidates(seed)[name].fit(df)
    conn.execute('UPDATE ml."ModelVersion" SET "Status" = \'retired\' WHERE "Status" = \'production\' AND "Model" = \'demand\'')
    window = lambda s: f"{s.min():%Y-%m-%d} to {s.max():%Y-%m-%d}"
    row = conn.execute(
        'INSERT INTO ml."ModelVersion"("Model","AsOfDate","Algorithm","TrainWindow","TestWindow","TrainRows","TestRows",'
        '"Metrics","Features","TrainingStats","Thresholds","Status","Decision","Artifact") '
        'VALUES (\'demand\',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,\'production\',%s,%s) RETURNING "Version"',
        (asof, name, window(df.AsOf), window(bt.AsOf), len(df), int((bt.Candidate == name).sum()),
         Jsonb({"chosen": res[name], "candidates": res, "baseline": res[base], "baseline_name": base}),
         Jsonb({"numeric": X}), Jsonb({}), Jsonb({}), why, pickle.dumps(final))).fetchone()
    log.info("demand model v%s (%s): %s", row["Version"], name, why)
    return {"version": row["Version"], "algorithm": name, "decision": why, "candidates": res, "backtest": bt}


def forecast(conn, asof: date) -> pd.DataFrame:
    """Forecast the next 6 months into ml.DemandForecast, with staff-hours against capacity."""
    prod = production(conn, "demand")
    if not prod:
        log.warning("no demand model yet: nothing forecast (run `python -m ml train` first)")
        return pd.DataFrame()
    f = materialise(conn, asof)
    mu = pickle.loads(prod["Artifact"]).predict(f)
    per_wedding = float(conn.execute(
        'SELECT coalesce(avg(b."TotalStaffHours"), 0) AS h FROM fact."FactEventBooking" b '
        'JOIN dim."DimDate" d ON d."DateKey" = b."EventDateKey" WHERE b."IsCancelled" = 0 AND d."FullDate" < %s',
        (asof,)).fetchone()["h"])
    hours = pd.DataFrame(conn.execute(
        'SELECT date_trunc(\'month\', d."FullDate")::date AS "Month", sum(w."TotalAssignedHours") AS booked, '
        '       sum(w."StandardWeeklyHours") AS capacity '
        'FROM fact."FactEmployeeWeeklyWorkload" w JOIN dim."DimDate" d ON d."DateKey" = w."WeekStartDateKey" '
        'JOIN dim."DimEmployee" e ON e."EmployeeKey" = w."EmployeeKey" '
        'WHERE e."RoleName" = ANY(%s) GROUP BY 1', (list(OPS_ROLES),)).fetchall(), columns=["Month", "booked", "capacity"])
    f = f.merge(hours, on="Month", how="left").fillna({"booked": 0, "capacity": 0})
    # shortcut: a new wedding's hours all count in its event month (its planning hours really spread over
    # the weeks before); spread them like fact_weekly_workload if weekly staffing needs it.
    # shortcut: cancelled bookings count as booked (about 4% of weddings); the CRM keeps no cancellation date.
    out = [(asof, r.Month, int(r.Horizon), prod["Version"], int(r.OnBooks), round(float(r.OnBooks + m), 2),
            int(interval(r.OnBooks, m, .1)), int(interval(r.OnBooks, m, .9)), round(float(r.booked), 1),
            round(float(r.booked) + m * per_wedding, 1), round(float(r.capacity), 1)) for r, m in zip(f.itertuples(), mu)]
    conn.execute('DELETE FROM ml."DemandForecast" WHERE "AsOfDate" = %s', (asof,))
    with conn.cursor() as cur:
        cur.executemany('INSERT INTO ml."DemandForecast" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', out)
    return pd.DataFrame(out, columns=["AsOfDate", "Month", "Horizon", "Version", "OnBooks", "Forecast", "Lo", "Hi",
                                      "BookedHours", "ExpectedHours", "CapacityHours"])


def monitor(conn, asof: date):
    """Live accuracy: the forecasts of the last 6 months that have now ended, against what happened.
    Compared with the backtest WAPE of the production version: +0.1 warns, +0.2 alerts."""
    prod = production(conn, "demand")
    if not prod:
        return None
    rows = conn.execute(
        'SELECT f."Forecast", (SELECT count(*) FROM stg."Event" e WHERE e."CreatedDate" < %(t)s '
        '  AND e."EventDate" >= f."Month" AND e."EventDate" < f."Month" + interval \'1 month\') AS actual '
        'FROM ml."DemandForecast" f WHERE f."Month" + interval \'1 month\' <= %(t)s '
        '  AND f."Month" >= %(t)s::date - interval \'6 months\'', {"t": asof}).fetchall()
    test = prod["Metrics"]["chosen"]["wape"]
    if len(rows) < 10:
        check = ("forecast WAPE", None, "n/a", f"{len(rows)} forecasts of ended months so far")
    else:
        a = np.array([r["actual"] for r in rows], float)
        wape = round(float(np.abs(np.array([float(r["Forecast"]) for r in rows]) - a).sum() / max(1, a.sum())), 3)
        check = ("forecast WAPE", wape, level(wape - test, 0.1, 0.2), f"{len(rows)} forecasts of the last 6 months; backtest WAPE {test}")
    conn.execute('DELETE FROM ml."DriftCheck" WHERE "AsOfDate" = %s AND "Version" = %s', (asof, prod["Version"]))
    conn.execute('INSERT INTO ml."DriftCheck" VALUES (%s,%s,%s,%s,%s,%s)', (asof, prod["Version"], *check))
    if check[2] == "alert":
        meta.alert(conn, None, "warning", f"demand model v{prod['Version']} on {asof}: forecast WAPE {check[1]} "
                                          f"vs backtest {test}; retrain and review")
    return check
