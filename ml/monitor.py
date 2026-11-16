"""Model monitoring (Spec 04 §9): input drift (PSI per feature against the training distribution), missing-as-a-feature,
edge cases, score mix, and performance on matured labels (overall and on the holdout, which the list did not influence).
Results go to ml.monitor; anything at alert level also to etl.alert."""
import logging
from datetime import date, timedelta

import mlflow
import numpy as np
import pandas as pd
import psycopg
from sklearn.metrics import average_precision_score

from ml import data as D
from ml.train import MODEL, TRACKING, ece
from pipeline.config import Config

log = logging.getLogger("ml")
SEASONAL = {"inquiry_month", "inquiry_dow"}     # calendar position, not drift


def psi(expected: dict, actual: pd.Series) -> float:
    keys = set(expected) | set(actual.index)
    e = np.array([max(expected.get(k, 0), 1e-4) for k in keys])
    a = np.array([max(actual.get(k, 0), 1e-4) for k in keys])
    return float(np.sum((a - e) * np.log(a / e)))


def bins(s: pd.Series, edges) -> pd.Series:
    keys = np.where(s.isna(), "missing", np.digitize(s.fillna(0), edges).astype(str))
    return pd.Series(keys).value_counts(normalize=True)


def monitor(asof: date, cfg=None) -> dict:
    cfg = cfg or Config()
    mlflow.set_tracking_uri(TRACKING)
    mv = mlflow.MlflowClient().get_model_version_by_alias(MODEL, "champion")
    model = mlflow.sklearn.load_model(f"models:/{MODEL}@champion")
    st = model.training_stats
    rows = []
    with psycopg.connect(cfg.dw_dsn) as c:
        today = pd.DataFrame([r[0] for r in c.execute("SELECT features FROM feature.lead_features_daily WHERE score_date = %s", (asof,))])
        if not today.empty:
            # reference: every open lead on every day of the champion's test period (the same population as a scoring day)
            test_end = date.fromisoformat(mlflow.get_run(mv.run_id).data.params["test_end"])
            con = D.connect(cfg)
            ref = D.dataset(con, test_end)
            ref = ref[ref.score_date >= test_end - timedelta(180)]
            for f in model.features:
                if f in SEASONAL or f not in today:
                    continue
                if f in D.CAT:
                    p = psi(ref[f].astype(str).value_counts(normalize=True).to_dict(), today[f].astype(str).value_counts(normalize=True))
                    rows.append(("psi", f, p, 0.2, "alert" if p > 0.2 else "watch" if p > 0.1 else "ok", "category shares"))
                    continue
                s = st["num"][f]
                v = pd.to_numeric(today[f], errors="coerce")
                p = psi(bins(ref[f], s["edges"]).to_dict(), bins(v, s["edges"]))
                rows.append(("psi", f, p, 0.2, "alert" if p > 0.2 else "watch" if p > 0.1 else "ok", f"{len(v)} rows vs {len(ref)} reference rows"))
                miss = float(v.isna().mean()) - float(ref[f].isna().mean())
                rows.append(("missing_share_change", f, miss, 0.10, "alert" if miss > 0.10 else "ok",
                             f"now {v.isna().mean():.0%}, reference {ref[f].isna().mean():.0%}"))
                if s["max"] is not None:
                    out = int(((v > s["max"]) | (v < s["min"])).sum())
                    rows.append(("outside_training_range", f, out, None, "watch" if out else "ok", f"range {s['min']}..{s['max']}"))
        # performance on matured labels: scores made LABEL_DAYS to LABEL_DAYS + 56 days ago, outcome known now
        lab = pd.DataFrame(c.execute('''
            SELECT s."InquiryID", d."FullDate" AS score_date, s."Probability"::float AS p, s."IsHoldout" AS holdout, s."Rank" AS rank,
                   (b."FullDate" IS NOT NULL AND b."FullDate" >= d."FullDate" AND b."FullDate" < d."FullDate" + %s)::int AS y
            FROM gold."FactLeadScore" s JOIN gold."DimDate" d ON d."DateKey" = s."ScoreDateKey"
            JOIN gold."FactInquiry" i USING ("InquiryID") JOIN gold."DimDate" b ON b."DateKey" = i."BookingDateKey"
            WHERE d."FullDate" >= %s AND d."FullDate" < %s''', (D.LABEL_DAYS, asof - timedelta(D.LABEL_DAYS + 56), asof - timedelta(D.LABEL_DAYS))).fetchall(),
            columns=["inquiry_id", "score_date", "p", "holdout", "rank", "y"])
        if len(lab) and lab.y.nunique() == 2:
            for name, part in (("all", lab), ("holdout", lab[lab.holdout == 1])):
                if len(part) >= 100 and part.y.nunique() == 2:
                    rows.append(("matured_pr_auc", name, average_precision_score(part.y, part.p), None, "ok", f"{len(part)} rows, {part.y.sum()} booked"))
                    e = ece(part.y.values, part.p.values)
                    rows.append(("matured_ece", name, e, 0.08, "alert" if e > 0.08 else "ok", ""))
        label_rate = float(lab.y.mean()) if len(lab) else None
        if label_rate is not None:
            rows.append(("label_rate", "all", label_rate, None, "ok", "base rate of matured labels (label drift)"))
        for r in rows:
            c.execute("INSERT INTO ml.monitor VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (check_date, model, check_name, subject) "
                      "DO UPDATE SET value = EXCLUDED.value, status = EXCLUDED.status, detail = EXCLUDED.detail, model_version = EXCLUDED.model_version",
                      (asof, MODEL, f"v{mv.version}", r[0], r[1], None if r[2] is None else round(float(r[2]), 4), r[3], r[4], r[5]))
        alerts = [r for r in rows if r[4] == "alert"]
        for r in alerts:
            c.execute("INSERT INTO etl.alert(severity, message) VALUES ('warning', %s)", (f"model {MODEL} v{mv.version}: {r[0]} {r[1]} = {r[2]:.3f}",))
    return {"checks": len(rows), "alerts": len(alerts)}
