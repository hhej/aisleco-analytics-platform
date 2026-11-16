"""File sources → Bronze (Spec 01 §3.3, §4, §5): chat hourly files closed by the gateway, tablet sync files,
the yearly auspicious file, and the monthly holiday-calendar pull.

Each file is moved from landing into its Bronze partition once, recorded in etl.file_checkpoint (name, size,
SHA-256, line count, run) and never touched again. A file whose Bronze copy is already checkpointed is not
moved twice (a rerun after a crash is safe)."""
import hashlib
import logging
import re
import shutil
from datetime import datetime, time, timedelta

from pipeline import meta
from sources.external import holidays

log = logging.getLogger("pipeline")


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _land(conn, cfg, ctx, source, src, dest, note=None):
    rel = str(dest.relative_to(cfg.lake_dir))
    if conn.execute("SELECT 1 FROM etl.file_checkpoint WHERE bronze_path = %s", (rel,)).fetchone():
        src.unlink(missing_ok=True)            # moved in an earlier attempt of this run
        return 0
    data = src.read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    conn.execute("INSERT INTO etl.file_checkpoint(source, bronze_path, landing_name, bytes, sha256, rows, run_id, note) "
                 "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                 (source, rel, src.name, len(data), hashlib.sha256(data).hexdigest(), data.count(b"\n"), ctx.run_id, note))
    src.unlink()
    return 1


def cutoff(ctx):
    """Hourly chat files are closed once their hour is over. A run for today closes up to the current hour;
    a run for an earlier as-of date (replay) closes everything before that date."""
    now = datetime.now()
    return now.replace(minute=0, second=0, microsecond=0) if ctx.asof >= now.date() else datetime.combine(ctx.asof, time())


def chat(ctx, conn):
    cfg, n = ctx.cfg, 0
    landing = cfg.lake_dir / "landing" / "chat"
    end = cutoff(ctx)
    for f in sorted(landing.glob("platform=*/received_date=*/hour=*/events.jsonl")):
        d = re.search(r"received_date=(\d{4}-\d{2}-\d{2})/hour=(\d{2})", str(f))
        if datetime.fromisoformat(d.group(1)) + timedelta(hours=int(d.group(2)) + 1) <= end:
            n += _land(conn, cfg, ctx, "chat", f, cfg.bronze / "chat" / f.relative_to(landing))
    for empty in sorted(landing.glob("platform=*/received_date=*/hour=*"), reverse=True):
        if empty.is_dir() and not any(empty.iterdir()):
            empty.rmdir()
    return {"files": n}


def fair(ctx, conn):
    cfg, n = ctx.cfg, 0
    for f in sorted((cfg.lake_dir / "landing" / "fair_tablet").glob("*.json")):
        dest = cfg.bronze / "fair_tablet" / f"received_date={ctx.asof}" / f"{f.stem}_{_sha(f)[:8]}.json"
        n += _land(conn, cfg, ctx, "fair_tablet", f, dest)
    return {"files": n}


def external(ctx, conn):
    """Yearly auspicious files (drop folder) every run; the holiday calendar once a month (or when none exists)."""
    cfg, n = ctx.cfg, 0
    for f in sorted((cfg.lake_dir / "landing" / "external").glob("auspicious_*.csv")):
        year = re.search(r"(\d{4})", f.name).group(1)
        n += _land(conn, cfg, ctx, "auspicious", f, cfg.bronze / "external" / "auspicious" / f"year={year}" / f"{f.stem}_{_sha(f)[:8]}.csv")
    last = holidays.latest(cfg.lake_dir)
    fetched = None
    if last is None or ctx.asof.day == 1 or last.parent.name < f"fetched_date={ctx.asof.replace(day=1)}":
        try:
            p = holidays.fetch(cfg.lake_dir, ctx.asof, offline=cfg.offline)
            conn.execute("INSERT INTO etl.file_checkpoint(source, bronze_path, landing_name, bytes, sha256, rows, run_id, note) "
                         "VALUES ('th_holidays', %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (bronze_path) DO NOTHING",
                         (str(p.relative_to(cfg.lake_dir)), "basic.ics", p.stat().st_size, _sha(p), p.read_bytes().count(b"BEGIN:VEVENT"),
                          ctx.run_id, "offline fixture" if cfg.offline else holidays.GOOGLE_TH_ICS))
            fetched = str(p.relative_to(cfg.lake_dir))
        except holidays.HolidayFetchError as e:      # rule R40: warn and keep the last good fetch
            meta.alert(conn, ctx.run_id, "warning", f"holiday calendar not refreshed, last good fetch kept: {e}")
    return {"files": n, "holidays_fetched": fetched}


def run(ctx, conn):
    out = {"chat": chat(ctx, conn), "fair_tablet": fair(ctx, conn), "external": external(ctx, conn)}
    total = sum(v["files"] for v in out.values())
    return {"rows_in": total, "rows_out": total, **out}
