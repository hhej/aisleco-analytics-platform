"""Gold → role marts (Spec 03 §7) and the reconciliation checks between Silver, Gold and marts (Spec 03 §8)."""
from pathlib import Path

from pipeline import db, meta
from pipeline.quality import dq

SQL = Path(__file__).parent / "sql"

CHECKS = {
    # every Silver inquiry and event has exactly one Gold row, and Gold has no extra rows
    "C02": """SELECT (SELECT count(*) FROM stg.inquiry s FULL JOIN gold."FactInquiry" g ON g."InquiryID" = s.inquiry_id
                      WHERE s.inquiry_id IS NULL OR g."InquiryID" IS NULL)
                   + (SELECT count(*) FROM stg.event s FULL JOIN gold."FactEventBooking" g ON g."EventID" = s.event_id
                      WHERE s.event_id IS NULL OR g."EventID" IS NULL)""",
    # spreading neither creates nor loses hours: per assignment, Gold hours = Silver hours minus the weeks dropped after a cancellation
    "C03": """SELECT count(*) FROM stg.assignment a JOIN stg.event e USING (event_id)
              LEFT JOIN (SELECT "AssignmentID", sum("TotalHours") AS h FROM gold."FactStaffWorkload" GROUP BY 1) g ON g."AssignmentID" = a.assignment_id
              WHERE a.status = 'Active' AND NOT e.is_cancelled AND abs(a.workload_hours - coalesce(g.h, 0)) > 0.001""",
    # mart totals equal Gold totals
    "C05": """SELECT (SELECT abs((SELECT sum(bookings) FROM mart_exec.v_monthly_kpis) - (SELECT count(*) FROM gold."FactEventBooking" b
                       JOIN gold."DimDate" d ON d."DateKey" = b."BookingDateKey" WHERE b."IsCancelled" = 0 AND d."FullDate" BETWEEN DATE '2023-01-01' AND gold.asof())))
                 + (SELECT abs((SELECT sum(inquiries) FROM mart_sales.v_funnel) - (SELECT count(*) FROM gold."FactInquiry")))""",
}


def marts(ctx, conn):
    for f in sorted(SQL.glob("*.sql")):
        db.run_sql_file(conn, f)
    return {"views": conn.execute("SELECT count(*) AS n FROM information_schema.views WHERE table_schema LIKE 'mart_%'").fetchone()["n"]}


def reconcile(ctx, conn):
    out, blocks = {}, []
    for rule, q in CHECKS.items():
        row = conn.execute(q).fetchone()
        failed = int(next(iter(row.values())) or 0)
        dq.record(conn, ctx, rule, None, failed)
        out[rule] = failed
        if failed:
            blocks.append(f"{rule}: {failed} — {dq.BY_ID[rule]['description']}")
    # late corrections: hours in weeks that had ended at the previous run and changed since (warn, history is updated)
    prev = conn.execute("SELECT max(asof_date) AS d FROM etl.run WHERE status = 'success' AND run_id < %s", (ctx.run_id,)).fetchone()["d"]
    late = 0
    if prev:
        late = conn.execute("""SELECT count(*) AS n FROM work."FactStaffWorkload" w JOIN gold."DimDate" d ON d."DateKey" = w."WeekStartDateKey"
                               WHERE d."FullDate" + 6 < %s AND w."AssignmentID" IN (
                                 SELECT assignment_id FROM stg.assignment WHERE updated_at::date >= %s)""", (prev, prev)).fetchone()["n"]
    dq.record(conn, ctx, "C03b", None, late)
    if late:
        meta.alert(conn, ctx.run_id, "warning", f"C03b: {late} week row(s) of ended weeks changed since the last run (late correction)")
    if blocks:
        raise RuntimeError("reconciliation failed: " + "; ".join(blocks))
    return {"checks": out, "late_changes": late}
