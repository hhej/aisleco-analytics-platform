"""Daily scoring (Spec 04 §7): every inquiry open at the start of the score date gets a calibrated chance to book within
the label window (60 days), an expected fee, a rank in its salesperson's list (holdout leads are not ranked), three plain reasons from SHAP
values and a next best action. Written to gold."FactLeadScore" and to the offline feature store."""
import json
import logging
from datetime import date

import mlflow
import numpy as np
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb

from ml import data as D
from ml.train import MODEL, TRACKING
from pipeline.config import Config

log = logging.getLogger("ml")

# feature → (sentence when it pushes the chance UP, sentence when it pushes it DOWN); r = the lead's feature row
REASONS = {
    "days_since_quote": ("Quote sent {days_since_quote:.0f} days ago", "Quote sent {days_since_quote:.0f} days ago, no answer yet"),
    "quoted": ("Has a quote", None),
    "days_since_consult": ("Consultation {days_since_consult:.0f} days ago", None),
    "consulted": ("Came to a consultation", None),
    "customer_msgs_3d": ("Wrote {customer_msgs_3d:.0f} times in the last 3 days", None),
    "customer_msgs_7d": ("Active in chat this week", None),
    "hours_since_customer_msg": ("Wrote {wrote}", "Silent for {days_silent} days"),
    "replied_within_1h": ("We answered within an hour", "Our first reply took over an hour"),
    "log_response_minutes": ("We answered quickly", "Our first reply was slow"),
    "n_ask_meeting": ("Asked to meet", None), "n_ask_availability": ("Asked whether a date is free", None),
    "n_mention_date": ("Has a wedding date in mind", None), "n_positive": ("Positive tone in chat", None),
    "n_commit": ("Said they want to book", None), "n_price_objection": (None, "Said the price is high"),
    "n_competitor": (None, "Comparing other planners"), "n_family_decision": (None, "Waiting for the family's decision"),
    "quote_share": ("Quote fits the budget ({quote_share:.0%})", "Quote is {quote_share:.0%} of the budget"),
    "channel": ("Came through {channel}", "Came through {channel}"),
    "last_followup_reached": ("Reached on the last follow-up", None),
    "followups_7d": ("Followed up this week", None),
    "fair_lead": ("Met us at a wedding fair", None), "previous_inquiries": ("Has asked us before", None),
    "requested_auspicious": ("Wants an auspicious date", None),
    "requested_week_utilization": (None, "The requested week is nearly full"),
    "budget_band_ord": ("Budget {budget}", None), "budget_midpoint_thb": ("Budget {budget}", None),
    "package_interest": ("Interested in {package_interest}", None),
}
BUDGET = {0: "300k–500k", 1: "500k–1M", 2: "1M–1.5M", 3: "1.5M+", -1: "not given"}


def next_action(r, badge):
    """Spec 04 §7 rules, first match wins."""
    if r.staff_msgs == 0 and r.platform != "in person":
        return "Reply now"
    if badge == "Full" and (r.n_ask_availability > 0 or r.has_requested_date):
        return "Offer the nearest auspicious dates with room (date checker)"
    if r.quoted and r.days_since_quote >= 3 and r.hours_since_customer_msg > 72 and (r.quote_share or 0) > 0.16:
        return "Follow up on the quote; offer Partial Planning or a payment plan"
    if r.quoted and r.days_since_quote >= 3 and r.hours_since_customer_msg > 72:
        return "Follow up on the quote"
    if r.consulted and not r.quoted and r.days_since_consult >= 5:
        return "Send the quote"
    if r.hours_since_customer_msg >= 240:
        return "Last check-in before the 30-day auto-close"
    return "Check in"


def contributions(model, X):
    """Per-row feature contributions on the model's log-odds scale (SHAP for LightGBM, coef × value for logistic)."""
    pipe = model.pipe
    est = pipe.steps[-1][1]
    Xt = pipe[:-1].transform(X[model.features])
    if hasattr(est, "booster_"):
        import shap
        sv = shap.TreeExplainer(est).shap_values(Xt)
        return pd.DataFrame(sv[1] if isinstance(sv, list) else sv, columns=model.features, index=X.index)
    names = pipe[:-1].get_feature_names_out()
    raw = pd.DataFrame(Xt.toarray() if hasattr(Xt, "toarray") else Xt, columns=names, index=X.index) * est.coef_[0]
    out = pd.DataFrame(0.0, index=X.index, columns=model.features)
    for n in names:   # fold one-hot and missing-indicator columns back onto their feature
        base = n.split("__", 1)[1]
        f = next((f for f in model.features if base == f or base.startswith(f + "_") or base == f"missingindicator_{f}"), None)
        if f:
            out[f] += raw[n]
    return out


def reasons(row, contrib):
    days = int((row.hours_since_customer_msg or 0) // 24)
    vals = row.to_dict() | {"days_silent": days, "wrote": "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago",
                            "budget": BUDGET.get(int(row.budget_band_ord), "")}
    up = [(c, v) for c, v in contrib.sort_values(ascending=False).items() if v > 0 and REASONS.get(c, (None,))[0]]
    down = [(c, v) for c, v in contrib.sort_values().items() if v < 0 and REASONS.get(c, (None, None))[1]]
    out, seen = [], set()
    for c, _ in up[:3] + down[:1]:
        text = REASONS[c][0 if contrib[c] > 0 else 1]
        try:
            s = text.format(**vals)
        except (ValueError, TypeError, KeyError):
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return (out + [None, None, None])[:3]


def expected_fee(con, X, asof):
    pk = con.execute(f"""SELECT package_type, base_price, fee_rate FROM package WHERE effective_from <= DATE '{asof}'
                         AND (effective_to IS NULL OR effective_to >= DATE '{asof}')""").df().set_index("package_type")
    median = float(con.execute(f"SELECT median(agreed_fee) FROM event WHERE NOT is_cancelled AND booked_at >= DATE '{asof}' - 365").fetchall()[0][0])
    fee = np.where(X.package_interest == "Full", X.budget_midpoint_thb * float(pk.loc["Full", "fee_rate"]) / 100,
          np.where(X.package_interest.isin(["Partial", "DayOf"]), X.package_interest.map(lambda t: float(pk.loc[t, "base_price"]) if t in pk.index else median), median))
    return pd.Series(fee, index=X.index).fillna(float(median))


def score(asof: date, cfg=None) -> dict:
    cfg = cfg or Config()
    mlflow.set_tracking_uri(TRACKING)
    mv = mlflow.MlflowClient().get_model_version_by_alias(MODEL, "champion")
    model = mlflow.sklearn.load_model(f"models:/{MODEL}@champion")
    con = D.connect(cfg)
    spine = D.scoring_spine(con, asof)
    X = D.features(con, spine).set_index("inquiry_id", drop=False)
    if X.empty:
        return {"scored": 0}
    owners = con.execute("SELECT inquiry_id, sales_owner_id FROM inquiry").df().set_index("inquiry_id").sales_owner_id
    X["owner"] = owners.reindex(X.index).values
    X["p"] = model.predict_proba(X)[:, 1]
    X["fee"] = expected_fee(con, X, asof)
    X["ev"] = X.p * X.fee
    X["holdout"] = [int(D.is_holdout(i)) for i in X.index]
    X["list_rank"] = X[X.holdout == 0].groupby("owner").ev.rank(ascending=False, method="first")
    X["band"] = np.select([X.p >= model.thresholds["high"], X.p >= model.thresholds["medium"]], ["High", "Medium"], "Low")
    X["badge"] = np.where(X.requested_week_utilization >= 1.0, "Full", np.where(X.requested_week_utilization >= 0.85, "Tight", "Room"))
    C = contributions(model, X)
    R = [reasons(X.loc[i], C.loc[i]) for i in X.index]
    X["nba"] = [next_action(r, b) for r, b in zip(X.itertuples(), X.badge)]
    with psycopg.connect(cfg.dw_dsn) as c:
        dk = int(asof.strftime("%Y%m%d"))
        ch = dict(c.execute('SELECT "ChannelName", "ChannelKey" FROM gold."DimChannel"').fetchall())
        ow = dict(c.execute('SELECT "EmployeeID", "EmployeeKey" FROM gold."DimEmployee" WHERE %s BETWEEN "ValidFrom" AND "ValidTo"', (asof,)).fetchall())
        c.execute('DELETE FROM gold."FactLeadScore" WHERE "ScoreDateKey" = %s', (dk,))
        c.execute("DELETE FROM feature.lead_features_daily WHERE score_date = %s", (asof,))
        with c.cursor() as cur:
            cur.executemany('INSERT INTO gold."FactLeadScore" VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            [(dk, int(i), ch.get(r.channel, -1), ow.get(int(r.owner), -1) if pd.notna(r.owner) else -1, f"v{mv.version}",
                              round(float(r.p), 4), round(float(r.ev), 2), None if pd.isna(r.list_rank) else int(r.list_rank), r.band, int(r.holdout),
                              *rs, r.nba) for (i, r), rs in zip(X.iterrows(), R)])
            cur.executemany("INSERT INTO feature.lead_features_daily VALUES (%s,%s,%s)",
                            [(asof, int(i), Jsonb(json.loads(r[D.FEATURES].to_json()))) for i, r in X.iterrows()])
    log.info("scored %d open inquiries with v%s", len(X), mv.version)
    return {"scored": len(X), "model_version": mv.version, "high": int((X.band == "High").sum()), "holdout": int(X.holdout.sum())}
