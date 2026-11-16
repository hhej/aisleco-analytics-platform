"""Command line:

  python -m pipeline init [--drop]                 create the warehouse database and seed the data-quality rules
  python -m pipeline run --asof 2026-11-16 [--full-refresh]
  python -m pipeline resume RUN_ID                 continue a failed run from its checkpoint
  python -m pipeline start --asof D                (Airflow) create a run, print its id
  python -m pipeline step NAME --run-id N          (Airflow) run one step
  python -m pipeline status [-n 10]                last runs and their steps
"""
import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import psycopg
from psycopg import sql

from pipeline import db, pipeline
from pipeline.config import Config
from pipeline.quality import dq

SQL_FILES = ["sql/etl_schema.sql", "warehouse/sql/gold_schema.sql"]


def init(cfg, drop):
    name = psycopg.conninfo.conninfo_to_dict(cfg.dw_dsn)["dbname"]
    with db.connect(cfg.admin_dsn, cfg, autocommit=True, purpose="postgres") as c:
        exists = c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists and drop:
            c.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            exists = None
        if exists:
            sys.exit(f"database {name} already exists (use --drop to recreate it)")
        c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    with db.connect(cfg.dw_dsn, cfg, purpose="warehouse") as c:
        for f in SQL_FILES:
            db.run_sql_file(c, Path(__file__).parent / f)
        dq.seed_rules(c)
    print(f"warehouse {name} created with {len(dq.RULES)} data-quality rules")


def status(cfg, n):
    with db.connect(cfg.dw_dsn, cfg, autocommit=True) as c:
        for r in c.execute("SELECT * FROM etl.run ORDER BY run_id DESC LIMIT %s", (n,)).fetchall()[::-1]:
            print(f"run {r['run_id']:>4}  as of {r['asof_date']}  {r['mode']:<11} {r['status']:<8} {r['message'] or ''}")
            for s in c.execute("SELECT step, attempt, status, duration_sec, rows_in, rows_out FROM etl.step_run WHERE run_id = %s "
                               "ORDER BY step_run_id", (r["run_id"],)).fetchall():
                print(f"        {s['step']:<16} try {s['attempt']} {s['status']:<8} {s['duration_sec'] or 0:>7}s "
                      f"in {s['rows_in'] if s['rows_in'] is not None else '-':>7}  out {s['rows_out'] if s['rows_out'] is not None else '-':>7}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("init")
    a.add_argument("--drop", action="store_true")
    for name in ("run", "start"):
        a = sub.add_parser(name)
        a.add_argument("--asof", type=date.fromisoformat, default=date.today())
        a.add_argument("--full-refresh", action="store_true")
        a.add_argument("--triggered-by", default="cli")
    a = sub.add_parser("resume")
    a.add_argument("run_id", type=int)
    a = sub.add_parser("step")
    a.add_argument("name", choices=list(pipeline.STEPS))
    a.add_argument("--run-id", type=int, required=True)
    a = sub.add_parser("status")
    a.add_argument("-n", type=int, default=10)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = Config()
    try:
        if args.cmd == "init":
            init(cfg, args.drop)
        elif args.cmd in ("run", "start"):
            fn = pipeline.run if args.cmd == "run" else pipeline.start
            out = fn(cfg, args.asof, "full" if args.full_refresh else "incremental", args.triggered_by)
            print(f"run {out} succeeded" if args.cmd == "run" else out)
        elif args.cmd == "resume":
            print(f"run {pipeline.resume(cfg, args.run_id)} succeeded")
        elif args.cmd == "step":
            pipeline.run_step(cfg, args.run_id, args.name)
        else:
            status(cfg, args.n)
    except Exception as e:  # noqa: BLE001 - already recorded in etl.step_run / etl.alert
        logging.getLogger("pipeline").error("%s: %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
