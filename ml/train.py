"""Training, validation and registration of the lead-conversion model (Spec 04 §5–§6, §9).

Splits are by time only. With the as-of date C: test = score dates in [C-120, C-30) (labels mature), purge 30 days
before it (no training label window overlaps the test), inquiries seen in the test are removed from training.
Inside training: 5-fold rolling-origin CV (2-month blocks, 30-day purge) for model choice and Optuna tuning.
Isotonic calibration on the out-of-fold predictions of the last two folds. Metrics with 95 % cluster-bootstrap
intervals (by inquiry). A candidate becomes the MLflow `champion` only if it passes the gate (§6)."""
import json
import logging
import tempfile
from datetime import date, timedelta
from pathlib import Path

import lightgbm as lgb
import matplotlib
import mlflow
import numpy as np
import optuna
import pandas as pd
import psycopg
from psycopg.types.json import Jsonb
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from ml import data as D
from pipeline.config import Config, ROOT

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

log = logging.getLogger("ml")
optuna.logging.set_verbosity(optuna.logging.WARNING)
TRACKING = "postgresql+psycopg:///mlflow"
EXPERIMENT, MODEL = "lead_conversion", "lead_conversion"
TEST_DAYS, PURGE, FOLDS, FOLD_DAYS, TRIALS, K = 180, D.LABEL_DAYS, 5, 61, 30, 15   # 180: see decision C24 (power)
SNAPSHOT_AGES = {1, 2, 3, 4, 5, 7, 10, 14, 21, 30, 45, 60}   # training rows per inquiry (test keeps every day: each day is a decision)
# superiority over today's practice (B0), non-inferiority against the best simple rule (B1/B2) (decision C23)
GATE = {"min_gain_vs_practice": 0.0, "non_inferiority_margin": -0.05, "max_ece": 0.05, "max_slice_loss": 0.05, "min_slice_rows": 100,
        "min_unseen": 50}


class CalibratedModel:
    """The fitted pipeline plus the isotonic map, so predict_proba returns calibrated probabilities."""
    def __init__(self, pipe, iso, features):
        self.pipe, self.iso, self.features = pipe, iso, features

    def raw(self, X):
        return self.pipe.predict_proba(X[self.features])[:, 1]

    def predict_proba(self, X):
        p = self.iso.predict(self.raw(X))
        return np.c_[1 - p, p]


def as_category(X):
    X = X.copy()
    for c in D.CAT:
        if c in X:
            X[c] = X[c].astype("category")
    return X


def candidates(params=None, features=D.FEATURES):
    cat = [c for c in D.CAT if c in features]
    num = [c for c in features if c not in cat]

    def prep(scale):
        steps = [SimpleImputer(strategy="median", add_indicator=True)] + ([StandardScaler()] if scale else [])
        return ColumnTransformer([("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=20), cat), ("num", make_pipeline(*steps), num)])
    p = {"n_estimators": 300, "learning_rate": 0.05, "num_leaves": 15, "min_child_samples": 50, "colsample_bytree": 0.8,
         "subsample": 0.8, "subsample_freq": 1, "reg_lambda": 1.0} | (params or {})
    return {"logistic regression": make_pipeline(prep(True), LogisticRegression(C=p.get("C", 0.3), max_iter=3000)),
            "lightgbm": make_pipeline(FunctionTransformer(as_category, feature_names_out="one-to-one"),   # native categories and missing values
                                      lgb.LGBMClassifier(**{k: v for k, v in p.items() if k != "C"}, verbose=-1, random_state=0))}


def fit(model, df, features):
    return model.fit(df[features], df.y, **{model.steps[-1][0] + "__sample_weight": df.weight.values})


def folds(train):
    """Rolling-origin blocks of score dates, newest last; each trains on dates before the block minus the purge."""
    end = train.score_date.max() + timedelta(1)
    out = []
    for i in range(FOLDS, 0, -1):
        v0, v1 = end - timedelta(FOLD_DAYS * i), end - timedelta(FOLD_DAYS * (i - 1))
        val = train[(train.score_date >= v0) & (train.score_date < v1)]
        tr = train[(train.score_date < v0 - timedelta(PURGE)) & ~train.inquiry_id.isin(val.inquiry_id)]
        out.append((tr, val))
    return out


# ---------- metrics ----------
def ece(y, p, bins=10):
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    return float(sum(abs(y[idx == b].mean() - p[idx == b].mean()) * (idx == b).mean() for b in range(bins) if (idx == b).any()))


def at_k(df, score, k=K):
    return at_k_(df, score, k) | {"precision_at_5": at_k_(df, score, 5)["precision_at_15"]}


def at_k_(df, score, k):
    """Precision and recall in each salesperson's daily top k, and the share of next-30-day bookings in those lists."""
    d = df.assign(s=score).sort_values("s", ascending=False)
    top = d.groupby(["score_date", "sales_owner_id"]).head(k)
    g = top.groupby(["score_date", "sales_owner_id"]).agg(hits=("y", "sum"), n=("y", "size"))
    pos = d.groupby(["score_date", "sales_owner_id"]).y.sum()
    g = g.join(pos.rename("pos"))
    return {"precision_at_15": float((g.hits / g.n).mean()), "recall_at_15": float((g.hits / g.pos)[g.pos > 0].mean()),
            "bookings_captured": float(top.y.sum() / max(1, d.y.sum()))}


def metrics(df, p):
    y = df.y.values
    return {"pr_auc": float(average_precision_score(y, p)), "roc_auc": float(roc_auc_score(y, p)),
            "brier": float(brier_score_loss(y, np.clip(p, 0, 1))), "ece": ece(y, np.clip(p, 0, 1)), **at_k(df, p),
            "base_rate": float(y.mean()), "rows": int(len(y))}


def bootstrap(df, scores: dict, n=500, seed=0):
    """95 % intervals for PR-AUC of each score and for the paired differences, resampling inquiries (clusters)."""
    rng = np.random.default_rng(seed)
    ids = df.inquiry_id.unique()
    pos = df.groupby("inquiry_id").indices
    out = {k: [] for k in scores}
    for _ in range(n):
        rows = np.concatenate([pos[i] for i in rng.choice(ids, len(ids))])
        y = df.y.values[rows]
        if y.sum() == 0:
            continue
        for k, s in scores.items():
            out[k].append(average_precision_score(y, s[rows]))
    return {k: np.array(v) for k, v in out.items()}


def ci(a):
    return [round(float(np.percentile(a, 2.5)), 4), round(float(np.percentile(a, 97.5)), 4)]


# ---------- baselines ----------
def baselines(train, test):
    """B0 newest message first (today's practice); B1 the v1 rule of thumb; B2 historical rate of channel x stage."""
    stage = lambda d: np.select([d.quoted == 1, d.consulted == 1], ["quoted", "consulted"], "open")
    rate = train.assign(stage=stage(train)).groupby(["channel", "stage"]).y.mean()
    b2 = test.assign(stage=stage(test)).join(rate.rename("r"), on=["channel", "stage"]).r.fillna(train.y.mean()).values
    b1 = (2 * test.quoted + test.consulted + (test.hours_since_customer_msg < 72) + test.customer_msgs.clip(upper=999) / 1000).values / 5
    return {"B0 newest message first": -test.hours_since_customer_msg.fillna(1e6).values, "B1 stage rule": b1, "B2 channel x stage rate": b2}


# ---------- tuning ----------
def tune(cv, features):
    def objective(trial):
        params = {"n_estimators": trial.suggest_int("n_estimators", 100, 700, step=50),
                  "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
                  "num_leaves": trial.suggest_int("num_leaves", 7, 63), "min_child_samples": trial.suggest_int("min_child_samples", 20, 200),
                  "colsample_bytree": trial.suggest_float("colsample_bytree", 0.5, 1.0),
                  "subsample": trial.suggest_float("subsample", 0.6, 1.0), "reg_lambda": trial.suggest_float("reg_lambda", 0.0, 10.0)}
        scores = [average_precision_score(v.y, fit(candidates(params, features)["lightgbm"], t, features).predict_proba(v[features])[:, 1]) for t, v in cv]
        return float(np.mean(scores))
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=0))
    study.optimize(objective, n_trials=TRIALS)
    return study.best_params, study.best_value


def cv_score(model_name, cv, features, params=None):
    oof, scores = [], []
    for t, v in cv:
        p = fit(candidates(params, features)[model_name], t, features).predict_proba(v[features])[:, 1]
        scores.append(average_precision_score(v.y, p))
        oof.append(v.assign(raw=p))
    return float(np.mean(scores)), float(np.std(scores)), oof


# ---------- the run ----------
def train(asof: date, cfg=None, trials=None) -> dict:
    global TRIALS
    TRIALS = trials or TRIALS
    cfg = cfg or Config()
    con = D.connect(cfg)
    df = D.dataset(con, asof)
    df = df[df.known].reset_index(drop=True)
    sampled = df[df.age_days.isin(SNAPSHOT_AGES)].copy()
    sampled["weight"] = 1 / sampled.groupby("inquiry_id").inquiry_id.transform("size")
    test_start, test_end = asof - timedelta(TEST_DAYS + D.LABEL_DAYS), asof - timedelta(D.LABEL_DAYS)
    test = df[(df.score_date >= test_start) & (df.score_date < test_end)]
    train_ = sampled[(sampled.score_date < test_start - timedelta(PURGE)) & ~sampled.inquiry_id.isin(test.inquiry_id)]
    cv = folds(train_)
    log.info("train %d rows, test %d rows (%s to %s)", len(train_), len(test), test_start, test_end)

    # model choice and tuning on rolling-origin CV
    best_params, cv_best = tune(cv, D.FEATURES)
    # ablation on CV: what each feature group adds; the feature set is the prefix with the best CV PR-AUC (ties → fewer)
    ablation, used, best_set, best_cv = [], [], None, -1
    for g, cols in D.GROUPS.items():
        used = used + cols
        m_, sd_, _ = cv_score("lightgbm", cv, used, best_params)
        ablation.append({"+ group": g, "features": len(used), "cv_pr_auc": round(m_, 4), "cv_sd": round(sd_, 4)})
        if m_ > best_cv + 0.002:
            best_set, best_cv = list(used), m_
    FEAT = best_set
    cv_results = {"lightgbm": cv_score("lightgbm", cv, FEAT, best_params)[:2],
                  "logistic regression": cv_score("logistic regression", cv, FEAT)[:2]}
    name = "lightgbm" if cv_results["lightgbm"][0] > cv_results["logistic regression"][0] + 0.005 else "logistic regression"  # ties → simpler

    # calibration on out-of-fold predictions of the last two folds, then the test once
    _, _, oof = cv_score(name, cv[-2:], FEAT, best_params)
    oof = pd.concat(oof)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(oof.raw, oof.y)
    pipe = fit(candidates(best_params, FEAT)[name], train_, FEAT)
    model = CalibratedModel(pipe, iso, FEAT)
    p_test = model.predict_proba(test)[:, 1]
    m = metrics(test, p_test)
    base = {k: metrics(test, s) for k, s in baselines(train_, test).items()}
    lr = CalibratedModel(fit(candidates(None, FEAT)["logistic regression"], train_, FEAT), iso, FEAT)
    # the gate compares with the best non-ML baseline (B0-B2); logistic vs LightGBM is model selection on CV (ties → simpler)
    best_base = max(base, key=lambda k: base[k]["pr_auc"])
    if name != "logistic regression":          # reported for information: what the extra complexity buys
        base["B3 logistic regression"] = metrics(test, lr.predict_proba(test)[:, 1])
    bl = baselines(train_, test)
    bs = bootstrap(test, {"model": p_test, "practice": bl["B0 newest message first"], "rule": bl[best_base]})
    vs_practice, vs_rule = bs["model"] - bs["practice"], bs["model"] - bs["rule"]
    m.update(pr_auc_ci=ci(bs["model"]), gain_vs_practice=round(float(vs_practice.mean()), 4), gain_vs_practice_ci=ci(vs_practice),
             gain_vs_best_rule=round(float(vs_rule.mean()), 4), gain_vs_best_rule_ci=ci(vs_rule))

    # slices
    slices = {}
    b0 = baselines(train_, test)["B0 newest message first"]
    for col, groups in {"channel": test.channel, "stage": np.select([test.quoted == 1, test.consulted == 1], ["quoted", "consulted"], "open"),
                        "age": pd.cut(test.age_days, [0, 2, 14, 999], labels=["0-2 d", "3-14 d", "15+ d"]).astype(str),
                        "budget": test.budget_band_ord.astype(int).astype(str)}.items():
        for g in pd.unique(groups):
            mask = (groups == g)
            if mask.sum() >= GATE["min_slice_rows"] and test.y[mask].nunique() == 2:
                slices[f"{col}={g}"] = {"rows": int(mask.sum()), "pr_auc": round(float(average_precision_score(test.y[mask], p_test[mask])), 4),
                                        "b0_pr_auc": round(float(average_precision_score(test.y[mask], b0[mask])), 4)}
    worst = min(slices.values(), key=lambda s: s["pr_auc"] - s["b0_pr_auc"]) if slices else None

    # gate (Spec 04 §6)
    prod = champion_version()
    reasons = []
    if m["gain_vs_practice_ci"][0] <= GATE["min_gain_vs_practice"]:
        reasons.append(f"not better than today's practice (PR-AUC gain 95 % CI {m['gain_vs_practice_ci']})")
    if m["gain_vs_best_rule_ci"][0] <= GATE["non_inferiority_margin"]:
        reasons.append(f"may be worse than {best_base} by more than {-GATE['non_inferiority_margin']} (95 % CI {m['gain_vs_best_rule_ci']})")
    if m["ece"] > GATE["max_ece"]:
        reasons.append(f"ECE {m['ece']:.3f} > {GATE['max_ece']}")
    if worst and worst["b0_pr_auc"] - worst["pr_auc"] > GATE["max_slice_loss"]:
        reasons.append(f"a slice loses to today's practice by more than {GATE['max_slice_loss']}")
    vs_prod = None
    if prod and not reasons:
        unseen = test[test.score_date >= prod["trained_to"]]
        if unseen.inquiry_id.nunique() >= GATE["min_unseen"] and unseen.y.nunique() == 2:
            old = mlflow.sklearn.load_model(f"models:/{MODEL}@champion")
            vs_prod = {"inquiries": int(unseen.inquiry_id.nunique()),
                       "candidate": round(float(average_precision_score(unseen.y, model.predict_proba(unseen)[:, 1])), 4),
                       "champion": round(float(average_precision_score(unseen.y, old.predict_proba(unseen)[:, 1])), 4)}
            if vs_prod["candidate"] < vs_prod["champion"]:
                reasons.append(f"below champion v{prod['version']} on {vs_prod['inquiries']} unseen inquiries")
        else:
            reasons.append(f"kept champion v{prod['version']}: fewer than {GATE['min_unseen']} unseen test inquiries to compare")
    status = "rejected" if reasons else "champion"
    decision = "; ".join(reasons) or (f"PR-AUC {m['pr_auc']:.3f}: vs today's practice +{m['gain_vs_practice']:.3f} (CI {m['gain_vs_practice_ci']}), "
                                      f"vs {best_base} {m['gain_vs_best_rule']:+.3f} (CI {m['gain_vs_best_rule_ci']}), ECE {m['ece']:.3f}, {len(FEAT)} features")

    # production model: refit on all labelled history, calibrated with the test-period predictions (out of sample)
    full = sampled[sampled.score_date < test_end]
    final = CalibratedModel(fit(candidates(best_params, FEAT)[name], full, FEAT),
                            IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(model.raw(test), test.y), FEAT)
    final.training_stats = training_stats(full, FEAT)
    final.thresholds = {"high": 0.25, "medium": 0.10}
    version = register(asof, name, best_params, cv_results, cv_best, m, base, best_base, slices, ablation, decision, status, final,
                       model, test, p_test, train_, vs_prod, test_end)
    with psycopg.connect(cfg.dw_dsn) as c:
        c.execute("INSERT INTO ml.model_run(model, version, mlflow_run_id, asof_date, algorithm, train_window, test_window, train_rows, test_rows, "
                  "params, metrics, decision, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (model, version) DO NOTHING",
                  (MODEL, version["version"], version["run_id"], asof, name, f"{train_.score_date.min()} to {train_.score_date.max()}",
                   f"{test_start} to {test_end - timedelta(1)}", len(train_), len(test), Jsonb(best_params),
                   Jsonb(json.loads(json.dumps({"test": m, "baselines": base, "best_baseline": best_base, "cv": cv_results, "slices": slices,
                                                "ablation": ablation, "vs_champion": vs_prod}, default=float))), decision, status))
    log.info("lead_conversion v%s %s: %s", version["version"], status, decision)
    return {"version": version["version"], "status": status, "decision": decision, "algorithm": name, "test": m, "baselines": base,
            "best_baseline": best_base, "ablation": ablation, "slices": slices, "cv": cv_results, "params": best_params}


def training_stats(df, features=D.FEATURES):
    """Distributions at training time (deciles and missing share per numeric feature, shares per category) for drift checks."""
    st = {"rows": len(df), "num": {}, "cat": {}}
    for c in features:
        if c in D.CAT:
            st["cat"][c] = df[c].value_counts(normalize=True).round(4).to_dict()
        else:
            s = df[c]
            st["num"][c] = {"edges": sorted(set(np.nanquantile(s, np.linspace(.1, .9, 9)).round(4))) if s.notna().any() else [],
                            "missing": round(float(s.isna().mean()), 4), "min": float(np.nanmin(s)) if s.notna().any() else None,
                            "max": float(np.nanmax(s)) if s.notna().any() else None}
    return st


def champion_version():
    mlflow.set_tracking_uri(TRACKING)
    try:
        mv = mlflow.MlflowClient().get_model_version_by_alias(MODEL, "champion")
    except mlflow.exceptions.MlflowException:
        return None
    run = mlflow.get_run(mv.run_id)
    return {"version": mv.version, "trained_to": date.fromisoformat(run.data.params["test_end"])}


def register(asof, name, params, cv_results, cv_best, m, base, best_base, slices, ablation, decision, status, final, model, test, p_test,
             train_, vs_prod, test_end):
    mlflow.set_tracking_uri(TRACKING)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=(ROOT / "lake" / "mlflow").as_uri())
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"{name} as of {asof}") as run, tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mlflow.log_params({"asof": str(asof), "algorithm": name, "test_end": str(test_end), "label_days": D.LABEL_DAYS,
                           "features": len(final.features), **params})
        mlflow.log_metrics({f"test_{k}": v for k, v in m.items() if isinstance(v, (int, float))} | {"cv_pr_auc": cv_best} |
                           {f"baseline_{k.split()[0]}_pr_auc": v["pr_auc"] for k, v in base.items()})
        mlflow.set_tags({"decision": decision, "status": status, "best_baseline": best_base})
        (tmp / "report.json").write_text(json.dumps({"test": m, "baselines": base, "cv": cv_results, "slices": slices, "ablation": ablation,
                                                     "vs_champion": vs_prod}, indent=2, default=float))
        plots(test, p_test, model, tmp)
        mlflow.log_artifacts(str(tmp), "report")
        info = mlflow.sklearn.log_model(final, name="model", registered_model_name=MODEL, serialization_format="pickle")
        version = info.registered_model_version
        client = mlflow.MlflowClient()
        if status == "champion":
            try:
                old = client.get_model_version_by_alias(MODEL, "champion")
                client.set_registered_model_alias(MODEL, "previous", old.version)
            except mlflow.exceptions.MlflowException:
                pass
            client.set_registered_model_alias(MODEL, "champion", version)
        return {"version": str(version), "run_id": run.info.run_id}


def plots(test, p, model, out):
    from sklearn.calibration import calibration_curve
    from sklearn.metrics import precision_recall_curve
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    pr, rc, _ = precision_recall_curve(test.y, p)
    ax[0].plot(rc, pr, color="#7A2E3B")
    ax[0].axhline(test.y.mean(), color="#999", ls="--", lw=1)
    ax[0].set(title="Precision-recall, test period", xlabel="recall", ylabel="precision")
    fy, fx = calibration_curve(test.y, p, n_bins=10, strategy="quantile")
    ax[1].plot([0, 1], [0, 1], color="#999", ls="--", lw=1)
    ax[1].plot(fx, fy, marker="o", color="#7A2E3B")
    ax[1].set(title="Reliability (calibrated)", xlabel="predicted", ylabel="observed")
    fig.tight_layout()
    fig.savefig(out / "pr_and_calibration.png", dpi=130)
    plt.close(fig)
    if hasattr(model.pipe.steps[-1][1], "booster_"):
        import shap
        X = model.pipe[:-1].transform(test[model.features].sample(min(3000, len(test)), random_state=0))
        sv = shap.TreeExplainer(model.pipe.steps[-1][1]).shap_values(X)
        sv = sv[1] if isinstance(sv, list) else sv
        shown = X.apply(lambda c: c.cat.codes if c.dtype.name == "category" else c)     # colour scale needs numbers
        shap.summary_plot(sv, shown, show=False, max_display=15)
        plt.savefig(out / "shap_summary.png", dpi=130, bbox_inches="tight")
        plt.close()
