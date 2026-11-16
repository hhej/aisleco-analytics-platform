"""Machine learning command line (Spec 04).

  python -m ml init                       feature-store and ml schemas, feature registry
  python -m ml train --asof D [--trials N]   train, validate, gate, register in MLflow (weekly)
  python -m ml score --asof D             score today's open inquiries → gold.FactLeadScore (daily)
  python -m ml monitor --asof D           drift and matured-label performance → ml.monitor (daily)
  python -m ml daily --asof D             score + monitor
  python -m ml forecast --asof D          staffing-demand forecast + backtest → gold.FactDemandForecast
  python -m ml text --asof D              TF-IDF text experiment → docs/v2/ml_text_experiment.md
  python -m ml eda --asof D               exploratory report → docs/v2/ml_eda.md
  python -m ml rollback                   champion ← previous
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

import mlflow
import psycopg

from ml import data as D
from ml.train import MODEL, TRACKING
from pipeline.config import Config

DOCS = {   # feature registry descriptions (formulas are in ml/sql/lead_features.sql)
    "profile": "Known when the inquiry arrives: channel, budget, package of interest, requested date and its calendar, fair sign-up",
    "engagement": "Chat activity up to the cutoff: messages, recency, first-reply speed",
    "stage": "Consultation and quote reached before the cutoff, days since, quote share of budget",
    "text_rules": "Count of the couple's messages carrying each intent (Spec 02 §4.4) before the cutoff",
    "sales_activity": "Follow-ups logged by sales before the cutoff",
    "capacity": "Booked staff hours ÷ capacity of the requested wedding week, as known at the cutoff",
}


def init(cfg):
    with psycopg.connect(cfg.dw_dsn) as c:
        c.execute((Path(__file__).parent / "sql" / "ml_schema.sql").read_text())
        for g, cols in D.GROUPS.items():
            for f in cols:
                c.execute("INSERT INTO feature.registry(feature, feature_group, dtype, description, source) VALUES (%s,%s,%s,%s,%s) "
                          "ON CONFLICT (feature) DO UPDATE SET description = EXCLUDED.description",
                          (f, g, "category" if f in D.CAT else "number", DOCS[g], "ml/sql/lead_features.sql over Silver"))
    print(f"feature registry: {len(D.FEATURES)} features")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m ml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    for n in ("train", "score", "monitor", "daily", "eda", "forecast", "text"):
        a = sub.add_parser(n)
        a.add_argument("--asof", type=date.fromisoformat, default=date.today())
        if n == "train":
            a.add_argument("--trials", type=int)
    sub.add_parser("rollback")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = Config()
    if args.cmd == "init":
        init(cfg)
    elif args.cmd == "train":
        from ml import train
        r = train.train(args.asof, cfg, args.trials)
        print(json.dumps({k: r[k] for k in ("version", "status", "decision", "algorithm")}, indent=1))
    elif args.cmd in ("score", "daily"):
        from ml import score
        print(score.score(args.asof, cfg))
        if args.cmd == "daily":
            from ml import monitor
            print(monitor.monitor(args.asof, cfg))
    elif args.cmd == "forecast":
        from ml import forecast
        print(json.dumps(forecast.run(args.asof, cfg), indent=1, default=str))
    elif args.cmd == "text":
        from ml import text_experiment
        print(json.dumps(text_experiment.run(args.asof), indent=1))
    elif args.cmd == "eda":
        from ml import eda
        print(eda.run(args.asof))
    elif args.cmd == "monitor":
        from ml import monitor
        print(monitor.monitor(args.asof, cfg))
    else:
        mlflow.set_tracking_uri(TRACKING)
        client = mlflow.MlflowClient()
        prev = client.get_model_version_by_alias(MODEL, "previous")
        client.set_registered_model_alias(MODEL, "champion", prev.version)
        print(f"champion is now v{prev.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
