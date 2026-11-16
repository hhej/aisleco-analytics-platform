"""Text representation experiment (Spec 04 §4): does a data-driven representation of the couples' messages add anything
over the transparent rule intents? TF-IDF on character 2-4-grams of the masked inbound text (Thai has no spaces between
words), reduced to 16 dimensions with truncated SVD, averaged over each inquiry's messages before the cutoff. The vectorizer
and SVD are fitted on messages sent before the training period ends only. Compared on the same rolling-origin CV as the
model; kept only if the mean gain is >= 0.01 PR-AUC with every fold positive. Writes docs/v2/ml_text_experiment.md."""
from datetime import date, timedelta

import mlflow
import numpy as np
import pandas as pd
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics import average_precision_score

from ml import data as D
from ml import train as T
from pipeline.config import ROOT

K = 16


def run(asof: date, features=None) -> dict:
    con = D.connect()
    df = D.dataset(con, asof)
    df = df[df.known & df.age_days.isin(T.SNAPSHOT_AGES)].reset_index(drop=True)
    df["weight"] = 1 / df.groupby("inquiry_id").inquiry_id.transform("size")
    test_start = asof - timedelta(T.TEST_DAYS + D.LABEL_DAYS)
    train_ = df[df.score_date < test_start - timedelta(T.PURGE)]
    msgs = con.execute("SELECT message_id, inquiry_id, sent_at, text_masked FROM lead_text WHERE direction = 'In' AND source = 'chat' "
                       "AND text_masked IS NOT NULL AND inquiry_id IS NOT NULL").df()
    fit_on = msgs[msgs.sent_at < pd.Timestamp(train_.score_date.max())]
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=5, max_features=20000, sublinear_tf=True)
    svd = TruncatedSVD(K, random_state=0)
    svd.fit(vec.fit_transform(fit_on.text_masked))
    emb = pd.DataFrame(svd.transform(vec.transform(msgs.text_masked)), columns=[f"txt_{i}" for i in range(K)])
    emb[["inquiry_id", "sent_at"]] = msgs[["inquiry_id", "sent_at"]].values
    con.register("emb", emb)
    con.register("rows", df[["inquiry_id", "cutoff"]])
    agg = con.execute("SELECT r.inquiry_id, r.cutoff, " + ", ".join(f"avg(e.txt_{i}) AS txt_{i}" for i in range(K)) +
                      " FROM rows r LEFT JOIN emb e ON e.inquiry_id = r.inquiry_id AND e.sent_at < r.cutoff GROUP BY 1, 2").df()
    df = df.merge(agg, on=["inquiry_id", "cutoff"], how="left")
    train_ = df[df.score_date < test_start - timedelta(T.PURGE)]
    base_feats = features or D.FEATURES
    with_text = base_feats + [f"txt_{i}" for i in range(K)]
    rows = []
    for k, (tr, va) in enumerate(T.folds(train_)):
        a = average_precision_score(va.y, T.fit(T.candidates(None, base_feats)["lightgbm"], tr, base_feats).predict_proba(va[base_feats])[:, 1])
        b = average_precision_score(va.y, T.fit(T.candidates(None, with_text)["lightgbm"], tr, with_text).predict_proba(va[with_text])[:, 1])
        rows.append({"fold": k + 1, "without_text": round(a, 4), "with_tfidf": round(b, 4), "gain": round(b - a, 4)})
    res = pd.DataFrame(rows)
    keep = bool(res.gain.mean() >= 0.01 and (res.gain > 0).all())
    mlflow.set_tracking_uri(T.TRACKING)
    mlflow.set_experiment(T.EXPERIMENT)
    with mlflow.start_run(run_name=f"text experiment as of {asof}"):
        mlflow.log_params({"vectorizer": "tfidf char_wb 2-4", "svd_dims": K, "messages": len(msgs)})
        mlflow.log_metrics({"mean_gain": float(res.gain.mean()), "folds_positive": int((res.gain > 0).sum())})
        mlflow.set_tag("decision", "keep" if keep else "drop")
    lines = [f"# Text representation experiment (as of {asof})", "",
             f"Rule intents vs. rule intents + TF-IDF (character 2–4-grams of {len(msgs):,} masked inbound messages, SVD to {K} dimensions, "
             "averaged per inquiry over the messages before the cutoff). Same rolling-origin CV folds as the model; PR-AUC per fold.", "",
             "| Fold | Without TF-IDF | With TF-IDF | Gain |", "|---|---|---|---|",
             *[f"| {r.fold} | {r.without_text:.4f} | {r.with_tfidf:.4f} | {r.gain:+.4f} |" for r in res.itertuples()], "",
             f"Mean gain {res.gain.mean():+.4f}. Rule: keep only if the mean gain is at least 0.01 and every fold gains. "
             f"Decision: **{'keep TF-IDF' if keep else 'drop TF-IDF; the transparent rule intents stay'}**.", ""]
    (ROOT / "docs" / "v2" / "ml_text_experiment.md").write_text("\n".join(lines))
    return {"mean_gain": float(res.gain.mean()), "keep": keep, "folds": rows}
