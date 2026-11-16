"""Simulated business CLI (Spec 06 §2).

  python -m sim init [--reset]                 create the CRM (OLTP v6) and the company's master data
  python -m sim backfill --to 2026-11-15       simulate every day from the last simulated day up to --to
  python -m sim day --date 2026-11-16          simulate one more day
  python -m sim report                         calibration numbers (Spec 06 §8)

The chat gateway must run in sim mode; backfill and day start one if none answers on port 8001.
"""
import argparse
import csv
import os
import pickle
import subprocess
import sys
import time as _time
from datetime import date, timedelta
from pathlib import Path

import httpx
import psycopg

from sim import io
from sim.engine import World
from sources import settings

CRM_DSN = os.environ.get("CRM_DSN", "dbname=aisleco_crm")
TRUTH = Path(os.environ.get("SIM_STATE_DIR", settings.REPO / "var" / "sim_truth"))
STATE = TRUTH / "state.pkl"


def admin(sql):
    with psycopg.connect("dbname=postgres", autocommit=True) as c:
        c.execute(sql)


def init(reset, seed, scale, start):
    db = psycopg.conninfo.conninfo_to_dict(CRM_DSN)["dbname"]
    if reset:
        admin(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        STATE.unlink(missing_ok=True)
    admin(f'CREATE DATABASE "{db}"')
    ddl = settings.REPO / "sources" / "crm"
    with psycopg.connect(CRM_DSN, autocommit=True) as c:
        c.execute((ddl / "oltp_v6.sql").read_text())
        c.execute((ddl / "oltp_v6_support.sql").read_text())
    w = World(seed=seed, scale=scale, start=start)
    day = w.setup()
    with psycopg.connect(CRM_DSN) as c:
        io.write_crm(c, day)
        io.sync_sequences(c)
        c.commit()
    io.write_files(day)
    w.last_day = start - timedelta(1)
    save(w)
    print(f"CRM {db} created; simulation starts {start}")


def save(w):
    TRUTH.mkdir(parents=True, exist_ok=True)
    rng_ranker, w.ranker = w.ranker, None
    STATE.write_bytes(pickle.dumps(w))
    w.ranker = rng_ranker
    with open(TRUTH / "couples.csv", "w", newline="") as f:
        out = csv.DictWriter(f, fieldnames=list(w.truth[0]) if w.truth else ["InquiryID"])
        out.writeheader()
        out.writerows(w.truth)


def gateway_up():
    try:
        h = httpx.get(io.GATEWAY + "/health", timeout=1).json()
    except httpx.HTTPError:
        h = None
    if h and h.get("mode") != "sim":
        sys.exit("the chat gateway on :8001 runs in production mode; restart it with GATEWAY_MODE=sim")
    if h:
        return None
    log = settings.REPO / "var" / "log"
    log.mkdir(parents=True, exist_ok=True)
    port = io.GATEWAY.rsplit(":", 1)[1]
    p = subprocess.Popen([sys.executable, "-m", "sources.chat_gateway"], env={**os.environ, "GATEWAY_MODE": "sim", "GATEWAY_PORT": port},
                         stdout=open(log / "gateway.log", "a"), stderr=subprocess.STDOUT, cwd=settings.REPO)
    for _ in range(50):
        _time.sleep(.2)
        try:
            httpx.get(io.GATEWAY + "/health", timeout=1)
            return p
        except httpx.HTTPError:
            pass
    sys.exit("could not start the chat gateway")


DW_DSN = os.environ.get("DW_DSN", "dbname=aisleco_dw")


def list_ranker(d, world):
    """From model launch on, salespeople work the day's ranked list (gold.FactLeadScore, holdout excluded, Spec 04 §8);
    before launch, or on a day without scores, they work newest-first as today (returns None)."""
    try:
        with psycopg.connect(DW_DSN, connect_timeout=3) as c:
            rows = c.execute('SELECT "InquiryID" FROM gold."FactLeadScore" WHERE "ScoreDateKey" = %s AND "IsHoldout" = 0 '
                             'ORDER BY "Rank"', (int(d.strftime("%Y%m%d")),)).fetchall()
    except psycopg.Error:
        return None
    return [r[0] for r in rows] or None


def run(to, ranker=list_ranker):
    w = pickle.loads(STATE.read_bytes())
    w.ranker = ranker
    started = gateway_up()
    gw = io.Gateway(w.rng)
    d, n, t0 = w.last_day + timedelta(1), 0, _time.time()
    try:
        with psycopg.connect(CRM_DSN) as c:
            while d <= to:
                day = w.run_day(d)
                io.write_crm(c, day)
                gw.deliver(day)
                io.write_files(day)
                w.last_day, n = d, n + 1
                if d.day == 1:
                    print(f"{d}  open {len(w.open):4d}  inquiries {w.ids['inq']:5d}  events {w.ids['event']:4d}  {_time.time() - t0:5.0f}s", flush=True)
                d += timedelta(1)
            io.sync_sequences(c)
            c.commit()
    finally:
        save(w)
        if started:
            started.terminate()
    print(f"simulated {n} days to {w.last_day} in {_time.time() - t0:.0f}s")


REPORT = """
WITH i AS (SELECT *, extract(year FROM "InquiryDate")::int y FROM transaction."Inquiry"),
     e AS (SELECT *, extract(year FROM "EventDate")::int y FROM transaction."Event" WHERE "Status" <> 'Cancelled')
SELECT y AS year,
  (SELECT count(*) FROM i WHERE i.y = yy.y) AS inquiries,
  (SELECT count(*) FROM e WHERE e.y = yy.y) AS weddings_held,
  (SELECT round(100.0 * count(*) FILTER (WHERE "Status" = 'Converted') / nullif(count(*) FILTER (WHERE "Status" IN ('Converted','Lost')), 0), 1) FROM i WHERE i.y = yy.y) AS conversion_pct,
  (SELECT round(100.0 * count(*) FILTER (WHERE extract(month FROM "EventDate") IN (11,12,1,2)) / nullif(count(*), 0)) FROM e WHERE e.y = yy.y) AS pct_nov_feb,
  (SELECT round(100.0 * count(*) FILTER (WHERE "LostReason" = 'No reply') / nullif(count(*) FILTER (WHERE "Status" = 'Lost'), 0)) FROM i WHERE i.y = yy.y) AS pct_lost_no_reply,
  (SELECT round(100.0 * count(*) FILTER (WHERE "LostReason" = 'Date unavailable') / nullif(count(*) FILTER (WHERE "Status" = 'Lost'), 0), 1) FROM i WHERE i.y = yy.y) AS pct_lost_date,
  (SELECT round(100.0 * count(*) FILTER (WHERE c."ChannelName" = 'TikTok') / nullif(count(*) FILTER (WHERE c."ChannelType" = 'Online'), 0)) FROM i JOIN master."MarketingChannel" c USING ("ChannelID") WHERE i.y = yy.y) AS pct_tiktok_online,
  (SELECT round(count(*)::numeric / nullif((SELECT count(*) FROM i WHERE i.y = yy.y), 0), 1) FROM transaction."ChatMessage" m JOIN i USING ("InquiryID") WHERE i.y = yy.y) AS msgs_per_inquiry
FROM (SELECT DISTINCT y FROM i) yy ORDER BY 1
"""
REPLY = """
WITH f AS (SELECT "InquiryID", min("SentAt") FILTER (WHERE "Direction" = 'In') AS fin FROM transaction."ChatMessage" GROUP BY 1),
     r AS (SELECT f."InquiryID", extract(epoch FROM min(m."SentAt") - f.fin) / 60 AS mins FROM f JOIN transaction."ChatMessage" m
           ON m."InquiryID" = f."InquiryID" AND m."SenderType" = 'Staff' AND m."SentAt" > f.fin GROUP BY f."InquiryID", f.fin)
SELECT round(percentile_cont(.5) WITHIN GROUP (ORDER BY mins)::numeric, 1) FROM r
"""


def report():
    with psycopg.connect(CRM_DSN) as c:
        cur = c.execute(REPORT)
        cols = [d.name for d in cur.description]
        print("  ".join(f"{x:>17}" for x in cols))
        for r in cur.fetchall():
            print("  ".join(f"{'' if v is None else v!s:>17}" for v in r))
        print("median first staff reply (min):", c.execute(REPLY).fetchone()[0])
        lent = c.execute("""SELECT count(*) FILTER (WHERE "EventDate" BETWEEN make_date(extract(year FROM "EventDate")::int, 7, 20)
                            AND make_date(extract(year FROM "EventDate")::int, 10, 17)) * 100.0 / count(*) FROM transaction."Event" """).fetchone()[0]
        print(f"weddings in (approximate) Lent: {lent:.1f} %")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--reset", action="store_true")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--start", type=date.fromisoformat, default=date(2022, 7, 1))
    p = sub.add_parser("backfill")
    p.add_argument("--to", type=date.fromisoformat, default=date.today() - timedelta(1))
    p = sub.add_parser("day")
    p.add_argument("--date", type=date.fromisoformat, required=True)
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "init":
        init(a.reset, a.seed, a.scale, a.start)
    elif a.cmd == "backfill":
        run(a.to)
    elif a.cmd == "day":
        run(a.date)
    else:
        report()


if __name__ == "__main__":
    main()
