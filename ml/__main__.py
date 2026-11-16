"""Command line (run after the ETL; Airflow calls the same commands):

  python -m ml features --asof D     store the features of the open inquiries (offline store)
  python -m ml score --asof D        score them with the production model
  python -m ml monitor --asof D      drift and live-accuracy checks
  python -m ml forecast --asof D     demand forecast of the next 6 months, and its accuracy check
  python -m ml daily --asof D        the four above
  python -m ml train --asof D        train, validate, register both models (conversion: promote if it passes)
  python -m ml status                model versions
"""
import argparse
import logging
from datetime import date

from etl import db
from etl.config import Config

from . import features, forecast, monitor, score, train


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m ml")
    p.add_argument("cmd", choices=["features", "score", "monitor", "forecast", "daily", "train", "status"])
    p.add_argument("--asof", type=date.fromisoformat, default=date.today())
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    cfg = Config()
    with db.connect(cfg.dw_dsn, cfg, purpose="warehouse") as c:
        if a.cmd in ("features", "daily"):
            print(f"features stored for {features.materialise(c, a.asof)} open inquiries")
        if a.cmd in ("score", "daily"):
            print(f"scored {score.score(c, a.asof)} open inquiries")
        if a.cmd in ("monitor", "daily"):
            for name, v, st, detail in monitor.monitor(c, a.asof):
                print(f"{st:<8} {name:<26} {v if v is not None else '-':>8}  {detail}")
        if a.cmd in ("forecast", "daily"):
            f = forecast.forecast(c, a.asof)
            for r in f.itertuples():
                print(f"{r.Month:%b %Y}  booked {r.OnBooks:>3}  expected {r.Forecast:>6} ({r.Lo}-{r.Hi})  "
                      f"hours {r.ExpectedHours:>7} of {r.CapacityHours}")
            chk = forecast.monitor(c, a.asof)
            if chk:
                print(f"{chk[2]:<8} {chk[0]:<26} {chk[1] if chk[1] is not None else '-':>8}  {chk[3]}")
        if a.cmd == "train":
            r = train.train(c, a.asof)
            print(f"conversion model v{r['version']} ({r['algorithm']}): {r['status']} - {r['decision']}")
            c.commit()                    # keep the conversion model even if the demand model cannot train yet
            try:
                r = forecast.train(c, a.asof)
                print(f"demand model v{r['version']} ({r['algorithm']}): {r['decision']}")
            except ValueError as e:       # a new install: under a year of bookings, no backtest possible yet
                print(f"demand model: not trained - {e}")
        if a.cmd == "status":
            for r in c.execute('SELECT "Version","Model","AsOfDate","Algorithm","Status","Metrics"->\'chosen\'->>\'auc\' AS auc,'
                               '"Metrics"->\'chosen\'->>\'wape\' AS wape,"Decision" FROM ml."ModelVersion" ORDER BY 1').fetchall():
                m = f'AUC {r["auc"]}' if r["Model"] == "conversion" else f'WAPE {r["wape"]}'
                print(f'v{r["Version"]:<3} {r["Model"]:<10} data to {r["AsOfDate"]}  {r["Algorithm"]:<20} {r["Status"]:<10} {m}  {r["Decision"]}')


if __name__ == "__main__":
    main()
