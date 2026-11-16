"""Data-quality bookkeeping (Spec 02 §5): rule catalogue, results per run, quarantine (lake files + etl.rejected_row
index), resolution of fixed records, and the set-level checks that block a run."""
import json
from pathlib import Path

import yaml
from psycopg.types.json import Jsonb

from pipeline import meta

RULES = yaml.safe_load((Path(__file__).parent / "rules.yml").read_text())
BY_ID = {r["id"]: r for r in RULES}


def seed_rules(conn):
    for r in RULES:
        conn.execute("INSERT INTO etl.dq_rule VALUES (%s,%s,%s,%s,%s) ON CONFLICT (rule_id) DO UPDATE SET "
                     "source = EXCLUDED.source, target = EXCLUDED.target, severity = EXCLUDED.severity, description = EXCLUDED.description",
                     (r["id"], r["source"], r["target"], r["severity"], r["description"]))


def record(conn, ctx, rule_id, checked, failed):
    sev = BY_ID[rule_id]["severity"]
    conn.execute("INSERT INTO etl.dq_result(run_id, rule_id, rows_checked, rows_failed, passed) VALUES (%s,%s,%s,%s,%s) "
                 "ON CONFLICT (run_id, rule_id) DO UPDATE SET rows_checked = EXCLUDED.rows_checked, rows_failed = EXCLUDED.rows_failed, "
                 "passed = EXCLUDED.passed, checked_at = clock_timestamp()",
                 (ctx.run_id, rule_id, checked, failed, failed == 0 or sev == "info"))


def record_rows(conn, ctx, target, n_all, n_new, rejects, warns, cfg):
    """Row rules of one Silver table: results, quarantine file and index, resolution, reject-rate (S01)."""
    for r in (r for r in RULES if r["target"] == target and r["severity"] in ("reject", "warn", "info") and "check" not in r):
        failed = warns.get(r["id"], 0) + (0 if rejects is None else int((rejects["_reject"] == r["id"]).sum()))
        record(conn, ctx, r["id"], n_all, failed)
    keys = []
    if rejects is not None and len(rejects):
        qdir = cfg.lake_dir / "quarantine" / f"run={ctx.run_id:05d}"
        qdir.mkdir(parents=True, exist_ok=True)
        qfile = qdir / f"{target}.jsonl"
        recs = json.loads(rejects.drop(columns=[c for c in ("_warn",) if c in rejects]).to_json(orient="records", date_format="iso"))
        with open(qfile, "w") as f:
            for rec in recs:
                f.write(json.dumps({"rule": rec["_reject"], "reason": BY_ID[rec["_reject"]]["description"], "record": rec}, ensure_ascii=False) + "\n")
        for rec in recs:
            key = str(rec.get("_key"))
            keys.append((key, rec["_reject"]))
            conn.execute("INSERT INTO etl.rejected_row VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL) ON CONFLICT (target, record_key, rule_id) "
                         "DO UPDATE SET record = EXCLUDED.record, last_run_id = EXCLUDED.last_run_id, quarantine_file = EXCLUDED.quarantine_file, "
                         "resolved_run_id = NULL",
                         (BY_ID[rec["_reject"]]["source"], target, key, rec["_reject"], BY_ID[rec["_reject"]]["description"],
                          Jsonb(rec), str(qfile.relative_to(cfg.lake_dir)), ctx.run_id, ctx.run_id))
    # records rejected earlier and not any more: the source was fixed
    still = set(keys)
    for r in conn.execute("SELECT record_key, rule_id FROM etl.rejected_row WHERE target = %s AND resolved_run_id IS NULL",
                          (target,)).fetchall():
        if (r["record_key"], r["rule_id"]) not in still:
            conn.execute("UPDATE etl.rejected_row SET resolved_run_id = %s WHERE target = %s AND record_key = %s AND rule_id = %s",
                         (ctx.run_id, target, r["record_key"], r["rule_id"]))
    if n_new and n_new >= cfg.min_rows_for_rate and rejects is not None:
        new_rej = int((rejects["_run_id"] == ctx.run_id).sum()) if "_run_id" in rejects else 0
        rate = new_rej / n_new
        ctx.blocks = getattr(ctx, "blocks", [])
        if rate > cfg.max_reject_rate:
            ctx.blocks.append(f"S01 {target}: {new_rej} of {n_new} new rows rejected ({rate:.0%})")


def set_checks(conn, ctx, con):
    """Rules with a `check` over the whole Silver build, then the reject-rate rule. Any failed block stops the run."""
    blocks = list(getattr(ctx, "blocks", []))
    for r in (r for r in RULES if "check" in r):
        failed = con.execute(r["check"]).fetchone()[0]
        record(conn, ctx, r["id"], None, failed)
        if failed and r["severity"] == "block":
            blocks.append(f"{r['id']}: {failed} — {r['description']}")
        elif failed:
            meta.alert(conn, ctx.run_id, "warning", f"{r['id']}: {failed} — {r['description']}")
    record(conn, ctx, "S01", None, sum(b.startswith("S01") for b in blocks))
    if blocks:
        raise RuntimeError("data-quality gate stopped the run: " + "; ".join(blocks))
