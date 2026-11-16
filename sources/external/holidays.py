"""Thai public and Buddhist holidays: monthly pull of Google's public calendar into Bronze (Spec 01 §5a)."""
import shutil
import time
from datetime import date
from pathlib import Path

import httpx
from icalendar import Calendar

GOOGLE_TH_ICS = ("https://calendar.google.com/calendar/ical/"
                 "en.th%23holiday%40group.v.calendar.google.com/public/basic.ics")
FIXTURE = Path(__file__).parent / "fixtures" / "th_holidays_basic.ics"


class HolidayFetchError(RuntimeError):
    pass


def fetch(lake_dir, fetched_date: date, url=GOOGLE_TH_ICS, timeout=10, retries=3, offline=False) -> Path:
    """Write bronze/external/th_holidays/fetched_date=<D>/basic.ics and return its path."""
    out = Path(lake_dir) / "bronze/external/th_holidays" / f"fetched_date={fetched_date}" / "basic.ics"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".ics.tmp")
    if offline:
        shutil.copyfile(FIXTURE, tmp)
    else:
        for attempt in range(1, retries + 1):
            try:
                r = httpx.get(url, timeout=timeout, follow_redirects=True)
                r.raise_for_status()
                tmp.write_bytes(r.content)
                break
            except httpx.HTTPError as e:
                if attempt == retries:
                    raise HolidayFetchError(f"holiday calendar fetch failed after {retries} tries: {e}") from e
                time.sleep(2 ** attempt)
    tmp.replace(out)  # atomic: a reader never sees a half-written file
    return out


def latest(lake_dir) -> Path | None:
    """The newest fetch (folder names sort by date), or None if there is none."""
    files = sorted((Path(lake_dir) / "bronze/external/th_holidays").glob("fetched_date=*/basic.ics"))
    return files[-1] if files else None


def parse(path) -> list[tuple[date, str]]:
    """All-day events as (date, name), sorted. Only DTSTART is used: every holiday here is one day."""
    cal = Calendar.from_ical(Path(path).read_bytes())
    events = []
    for ev in cal.walk("VEVENT"):
        d = ev.decoded("DTSTART")
        if type(d) is date:  # datetime is a date subclass; timed events are not holidays
            events.append((d, str(ev.get("SUMMARY"))))
    return sorted(events)
