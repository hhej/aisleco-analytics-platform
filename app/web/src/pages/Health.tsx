import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { int, Row } from "../api";
import { AXIS, C, Card, ChartTip, Chip, Drawer, Empty, GRID, Loading, PageHead, Tile, useData } from "../ui";

const ts = (iso: string | null) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const runChip = (s: string) => <Chip s={s === "success" ? "OK" : s === "failed" ? "Over" : "Tight"}>{s}</Chip>;

export default function Health() {
  const { data: d, error } = useData<Row>("/api/data/health");
  const [rec, setRec] = useState<Row | null>(null);
  if (!d) return <Loading error={error} />;
  const runs = d.runs as Row[];
  const last = runs[0];
  const failed = (d.dq as Row[]).filter((r) => !r.passed);
  const open = (d.quarantine as Row[]).filter((q) => !q.resolved_run_id);
  const steps = (d.steps as Row[]).map((s) => ({ ...s, duration_sec: Number(s.duration_sec ?? 0) }));
  const m = d.models;
  return (
    <>
      <PageHead title="Data & ML health" sub="Is the nightly pipeline healthy, is the data fresh and clean, and how are the models doing?" />
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-5">
        <Tile kpi="run_duration" label="Latest run" value={<span className="flex items-center gap-2">{last ? `#${last.run_id}` : "–"} {last && runChip(last.status)}</span>}
          sub={last && <>As of {last.asof_date} · {last.mode} · {last.seconds ?? "–"} s · by {last.triggered_by}</>} />
        <Tile kpi="dq_rows_failed" label="Data-quality rules failing" value={int(failed.length)} tone={failed.some((f) => f.severity === "block") ? "over" : failed.length ? "tight" : undefined}
          sub={`of ${(d.dq as Row[]).length} rules in run ${d.dq_run_id}`} />
        <Tile label="Quarantined records" value={int(open.length)} tone={open.length ? "tight" : undefined} sub="Not yet resolved" />
        <Tile label="Alerts" value={int((d.alerts as Row[]).length)} sub={(d.alerts as Row[])[0] ? `Latest ${ts((d.alerts as Row[])[0].created_at)}` : "None"} />
      </div>

      <div className="grid lg:grid-cols-2 gap-5 mb-5">
        <Card title="Pipeline runs" question="Status and duration of the last runs.">
          <div className="max-h-[300px] overflow-y-auto">
            <table className="tbl">
              <thead><tr><th>Run</th><th>As of</th><th>Mode</th><th>Status</th><th className="text-right">Seconds</th><th>Started</th></tr></thead>
              <tbody>{runs.map((r) => (
                <tr key={r.run_id}><td className="num">#{r.run_id}</td><td>{r.asof_date}</td><td>{r.mode}</td><td>{runChip(r.status)}</td>
                  <td className="text-right num">{r.seconds ?? "–"}</td><td className="text-ink-2">{ts(r.started_at)}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
        <Card title={`Step times, run #${last?.run_id ?? "–"}`} question="Where does the time go?">
          {steps.length === 0 ? <Empty title="No steps recorded" /> : (
            <div style={{ height: steps.length * 24 + 30 }}>
              <ResponsiveContainer>
                <BarChart data={steps} layout="vertical" margin={{ top: 0, right: 40, left: 0, bottom: 0 }}>
                  <CartesianGrid {...GRID} horizontal={false} vertical />
                  <XAxis type="number" {...AXIS} unit=" s" />
                  <YAxis type="category" dataKey="step" {...AXIS} width={130} axisLine={false} />
                  <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip fmt={(v: number) => `${v.toFixed(2)} s`} />} />
                  <Bar isAnimationActive={false} dataKey="duration_sec" name="Duration" fill={C.wine} radius={[0, 4, 4, 0]} maxBarSize={14}
                    label={{ position: "right", fontSize: 11, fill: "#5A534C", formatter: (v: number) => `${v.toFixed(1)}` }} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-5 mb-5">
        <Card title={`Data-quality results, run #${d.dq_run_id}`} question="Which rules found problems? Failing rules first.">
          <div className="max-h-[360px] overflow-y-auto">
            <table className="tbl">
              <thead className="sticky top-0 bg-surface"><tr><th>Rule</th><th>Source</th><th>Severity</th><th className="text-right">Checked</th><th className="text-right">Failed</th></tr></thead>
              <tbody>{(d.dq as Row[]).map((r) => (
                <tr key={r.rule_id} title={r.description}>
                  <td><span className="num font-medium">{r.rule_id}</span><div className="text-[11.5px] text-ink-3 max-w-[260px]">{r.description}</div></td>
                  <td>{r.source}</td><td>{r.severity}</td><td className="text-right num">{int(r.rows_checked)}</td>
                  <td className="text-right">{r.passed ? <span className="num text-ink-3">{int(r.rows_failed)}</span> : <Chip s={r.severity === "block" ? "Over" : "Tight"}>{int(r.rows_failed)}</Chip>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
        <Card title="Quarantine" question="Records set aside by a rule. Open one to see what arrived.">
          {(d.quarantine as Row[]).length === 0 ? <Empty title="Nothing in quarantine" /> : (
            <table className="tbl">
              <thead><tr><th>Source</th><th>Rule</th><th>Reason</th><th>Runs</th><th /></tr></thead>
              <tbody>{(d.quarantine as Row[]).map((q, i) => (
                <tr key={i}>
                  <td>{q.source}<div className="text-[11.5px] text-ink-3">{q.target}</div></td><td className="num">{q.rule_id}</td>
                  <td className="text-[12.5px] text-ink-2 max-w-[220px]">{q.reason}</td>
                  <td className="num text-[12px] whitespace-nowrap">{q.first_run_id}–{q.last_run_id}{q.resolved_run_id ? ` · fixed in ${q.resolved_run_id}` : ""}</td>
                  <td><button className="btn-ghost !py-1 !px-2 !text-[12px]" onClick={() => setRec(q)}>Show record</button></td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      </div>

      <div className="grid lg:grid-cols-3 gap-5">
        <Card title="Freshness" question="When did each source last arrive?">
          <table className="tbl">
            <thead><tr><th>Source</th><th>Last ingested</th><th className="text-right">Run</th></tr></thead>
            <tbody>{(d.freshness as Row[]).map((f) => (
              <tr key={f.source}><td>{f.source}</td><td className="text-ink-2">{ts(f.last_ingested)}</td><td className="text-right num">{f.last_run}</td></tr>
            ))}</tbody>
          </table>
        </Card>
        <Card title="Alerts" question="Errors and warnings raised by the pipeline.">
          {(d.alerts as Row[]).length === 0 ? <Empty title="No alerts" /> : (
            <ul className="grid gap-2 text-[13px]">{(d.alerts as Row[]).map((a) => (
              <li key={a.alert_id} className="rounded-lg bg-page p-2.5">
                <div className="flex items-center gap-2"><Chip s={a.severity === "error" ? "Over" : "Tight"}>{a.severity}</Chip><span className="text-ink-3 text-[12px]">run #{a.run_id} · {ts(a.created_at)}</span></div>
                <p className="mt-1 text-ink-2 break-words">{a.message}</p>
              </li>
            ))}</ul>
          )}
        </Card>
        <Card title="Models" question="Lead model and demand forecast.">
          <dl className="grid gap-3 text-[13px]">
            <div className="flex justify-between gap-2"><dt>Lead scores in the warehouse</dt><dd className="num font-medium">{int(m.lead_scores.rows)}</dd></div>
            <div className="flex justify-between gap-2"><dt>Demand forecast rows</dt><dd className="num font-medium">{int(m.demand_forecast.rows)}</dd></div>
          </dl>
          {Object.keys(m.views).length === 0 ? (
            <div className="mt-4"><Empty title="No model yet">Model metrics, drift and the registry appear here once the models publish their monitoring views.</Empty></div>
          ) : Object.entries(m.views as Record<string, Row[]>).map(([v, rows]) => (
            <div key={v} className="mt-4">
              <div className="label mb-1">{v.replace(/^v_/, "").replace(/_/g, " ")}</div>
              {rows.length === 0 ? <p className="text-ink-3 text-[12.5px]">No rows yet.</p> : (
                <div className="overflow-x-auto"><table className="tbl">
                  <thead><tr>{Object.keys(rows[0]).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                  <tbody>{rows.slice(0, 10).map((r, i) => <tr key={i}>{Object.values(r).map((x, j) => <td key={j} className="num text-[12px]">{String(x ?? "–")}</td>)}</tr>)}</tbody>
                </table></div>
              )}
            </div>
          ))}
        </Card>
      </div>

      <Drawer open={!!rec} onClose={() => setRec(null)} title={rec ? `${rec.source} · ${rec.rule_id}` : ""} sub={rec?.reason}>
        {rec && (
          <>
            <dl className="grid grid-cols-[120px_1fr] gap-y-1.5 text-[13px] mb-4">
              <dt className="text-ink-3">Target</dt><dd>{rec.target}</dd>
              <dt className="text-ink-3">Record key</dt><dd className="break-all font-mono text-[12px]">{rec.record_key}</dd>
              <dt className="text-ink-3">File</dt><dd className="break-all font-mono text-[12px]">{rec.quarantine_file}</dd>
            </dl>
            <pre className="bg-page border border-line rounded-lg p-3 text-[12px] whitespace-pre-wrap break-all">{JSON.stringify(rec.record, null, 2)}</pre>
          </>
        )}
      </Drawer>
    </>
  );
}
