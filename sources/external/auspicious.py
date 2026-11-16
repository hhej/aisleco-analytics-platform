"""Yearly auspicious wedding-date file that marketing compiles each October (Spec 01 §5b), simulated.

Real lists cluster in the high season (Nov-Feb), mostly on weekends, and avoid Buddhist Lent, when Thai weddings are rare.
"""
import argparse
import csv
import random
from functools import lru_cache
from datetime import date, timedelta
from pathlib import Path

from sources.external import holidays

N_DATES = 60
STORY_DATES = {date(2027, 2, 13): ("ThaiAstrology", "High"), date(2027, 2, 20): ("ThaiAstrology", "High")}
THAI_DAYS = ["วันจันทร์", "วันอังคาร", "วันพุธ", "วันพฤหัสบดี", "วันศุกร์", "วันเสาร์", "วันอาทิตย์"]
CHINESE = ["宜嫁娶", "宜嫁娶 纳采", "宜嫁娶 订盟", "宜嫁娶 祈福"]


@lru_cache
def lent(year: int) -> tuple[date, date]:
    asalha = {d for d, name in holidays.parse(holidays.FIXTURE) if name == "Asalha Bucha" and d.year == year}
    # shortcut: the Google feed has no lunar holidays after 2027, so later years use the old July 20 approximation
    a = min(asalha) if asalha else date(year, 7, 20)
    return a + timedelta(days=1), a + timedelta(days=89)


def rows(year: int, seed: int = 11, bad_row: bool = False) -> list[tuple]:
    rng = random.Random(year * 1000 + seed)
    lent_start, lent_end = lent(year)
    days = [date(year, 1, 1) + timedelta(days=i) for i in range((date(year + 1, 1, 1) - date(year, 1, 1)).days)]
    days = [d for d in days if d not in STORY_DATES]
    weight = {d: (6 if d.month in (11, 12, 1, 2) else 1.5 if d.month in (3, 4, 5, 6, 10) else 1)
              * (6 if d.weekday() >= 5 else 1)
              * (0.02 if lent_start <= d <= lent_end else 1) for d in days}
    stories = {d: v for d, v in STORY_DATES.items() if d.year == year}
    picked = set()
    while len(picked) < N_DATES - len(stories):
        picked.add(rng.choices(days, weights=[weight[d] for d in days])[0])
    out = []
    for d in sorted(picked):
        high_season = d.month in (11, 12, 1, 2)
        level = rng.choices(["High", "Medium", "Low"], weights=[55, 30, 15] if high_season else [20, 40, 40])[0]
        dtype = "ThaiAstrology" if rng.random() < 2 / 3 else "ChineseCalendar"
        out.append((d, dtype, level))
    out += [(d, t, lvl) for d, (t, lvl) in stories.items()]
    if bad_row:  # Spec 06 §7: one row the DQ check must reject
        out.append((rng.choice(sorted(picked)), "ThaiAstrology", "VeryHigh"))
    return [(d.isoformat(), t, lvl, f"ฤกษ์มงคลสมรส {THAI_DAYS[d.weekday()]}" if t == "ThaiAstrology"
             else rng.choice(CHINESE)) for d, t, lvl in sorted(out)]


def write_year(year: int, out_dir, seed: int = 11, bad_row: bool = False) -> Path:
    path = Path(out_dir) / f"auspicious_{year}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["calendar_date", "date_type", "auspicious_level", "description"])
        w.writerows(rows(year, seed, bad_row))
    return path


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Write the auspicious wedding-date CSV for one year.")
    p.add_argument("year", type=int)
    p.add_argument("--out", default="lake/landing/external")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--bad-row", action="store_true")
    a = p.parse_args()
    print(write_year(a.year, a.out, a.seed, a.bad_row))
