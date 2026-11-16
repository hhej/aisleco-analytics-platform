import { useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Sparkles } from "lucide-react";
import { baht, bahtFull, hours, int, minutes, monthLong, monthShort, n, pct, Row } from "../api";
import { AXIS, C, Card, ChartTip, Chip, Delta, Empty, GRID, HBar, Info, Legend, Loading, PageHead, Tile, useData } from "../ui";

type T = { month: string; value: unknown; prev: unknown; last_year: unknown; spark: { month: string; value: unknown }[]; [k: string]: any };

const shift = (ym: string, k: number) => {
  const d = new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1 + k, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
};
function Compare({ t, fmt }: { t: T; fmt: (v: unknown) => string }) {
  return (
    <>
      <Delta now={t.value} then={t.prev} label={`vs ${monthShort(shift(t.month, -1))}`} fmt={fmt} />
      <Delta now={t.value} then={t.last_year} label={`vs ${monthShort(shift(t.month, -12), true)}`} fmt={fmt} />
    </>
  );
}
const spark = (t: T) => t.spark.map((s) => n(s.value));

export default function Overview() {
  const { data: d, error } = useData<Row>("/api/exec/overview");
  const [metric, setMetric] = useState<"bookings" | "fee_revenue">("bookings");
  if (!d) return <Loading error={error} />;
  const t = d.tiles as Record<string, T>;
  const report = d.report_month as string;
  const monthly = d.monthly as Row[];
  const by = Object.fromEntries(monthly.map((m) => [m.month, m]));
  const ly = (ym: string) => shift(ym, -12);

  const months12 = t.bookings.spark.map((s) => s.month);
  const bookSeries = months12.map((m) => ({ month: m, value: n(by[m]?.[metric]), ly: n(by[ly(m)]?.[metric]) }));
  const convSeries = monthly.filter((m) => m.month > ly(report) && m.month <= report).map((m) => ({
    month: m.month,
    conversion: (m.decided_share ?? 0) >= 0.9 ? n(m.conversion) : null,
    open: (m.decided_share ?? 0) < 0.9 ? n(m.conversion) : null,
    share: m.decided_share,
  }));
  const fmtMetric = metric === "bookings" ? int : bahtFull;
  const outlookMonths = [...new Set((d.capacity_outlook as Row[]).map((o) => o.month))].slice(0, 6);
  const lost = (d.lost_value as Row[]);
  const lostMax = Math.max(...lost.map((l) => n(l.fee_at_stake_estimate) ?? 0));
  const ut = t.ops_utilization_quarter;

  return (
    <>
      <PageHead title="Business overview"
        sub={<>{monthLong(report)}, the last complete month, compared with the month before and the same month last year.</>} />

      <section className="card p-5 mb-5 bg-gradient-to-br from-surface to-wine-50/40" aria-labelledby="wc">
        <h2 id="wc" className="card-t flex items-center gap-2 mb-2"><Sparkles size={16} className="text-wine" aria-hidden />What changed</h2>
        <ul className="grid gap-1.5 text-[14px] text-ink-2 list-disc pl-5 marker:text-wine-300">
          {(d.what_changed as string[]).map((s) => <li key={s}>{s}</li>)}
        </ul>
      </section>

      <div className="grid grid-cols-2 xl:grid-cols-3 2xl:grid-cols-6 gap-3 mb-5">
        <Tile kpi="bookings" value={int(t.bookings.value)} spark={spark(t.bookings)} sub={monthLong(report)}><Compare t={t.bookings} fmt={int} /></Tile>
        <Tile kpi="fee_revenue" value={baht(t.fee_revenue.value)} spark={spark(t.fee_revenue)} sub={monthLong(report)}><Compare t={t.fee_revenue} fmt={baht} /></Tile>
        <Tile kpi="conversion_rate" value={pct(t.conversion_rate.value, 1)} spark={spark(t.conversion_rate)}
          sub={<>Inquiries of {monthLong(t.conversion_rate.month)} · {int(t.conversion_rate.decided)} of {int(t.conversion_rate.inquiries)} decided</>}>
          <Compare t={t.conversion_rate} fmt={(v) => pct(v, 1)} />
        </Tile>
        <Tile kpi="median_first_reply" value={minutes(t.median_first_reply.value)} spark={spark(t.median_first_reply)}
          sub={<>Target {minutes(60)} · {monthLong(report)}</>} tone={(n(t.median_first_reply.value) ?? 0) > 60 ? "tight" : undefined}>
          <Compare t={t.median_first_reply} fmt={minutes} />
        </Tile>
        <Tile kpi="ops_utilization_quarter" value={pct(ut.value)} spark={spark(ut)} sub={<>{monthShort(ut.from_month)}–{monthShort(report, true)}</>}
          tone={(n(ut.value) ?? 0) >= 1 ? "over" : (n(ut.value) ?? 0) >= 0.85 ? "tight" : undefined}>
          <Delta now={ut.value} then={ut.prev} label="vs previous 3 months" fmt={(v) => pct(v)} />
          <Delta now={ut.value} then={ut.last_year} label="vs a year earlier" fmt={(v) => pct(v)} />
        </Tile>
        <Tile kpi="turned_away" value={int(t.turned_away.value)} spark={spark(t.turned_away)}
          sub={<>{monthLong(report)} · fee at stake ≈ {baht(t.turned_away.fee_at_stake)}</>}><Compare t={t.turned_away} fmt={int} /></Tile>
      </div>

      <div className="grid lg:grid-cols-3 gap-5 mb-5">
        <Card className="lg:col-span-2" title={metric === "bookings" ? "Bookings by month" : "Fee revenue booked by month"} kpi={metric}
          question="Seasonality and growth: each month against the same month a year earlier."
          right={
            <div className="inline-flex rounded-lg border border-line p-0.5 text-[12.5px]" role="group" aria-label="Measure">
              {(["bookings", "fee_revenue"] as const).map((m) => (
                <button key={m} onClick={() => setMetric(m)} aria-pressed={metric === m}
                  className={`px-2.5 py-1 rounded-md ${metric === m ? "bg-wine-50 text-wine font-medium" : "text-ink-2"}`}>
                  {m === "bookings" ? "Bookings" : "Fee revenue"}
                </button>
              ))}
            </div>}>
          <Legend items={[{ label: "This year", swatch: C.wine }, { label: "Same month last year", swatch: C.grey, line: true }]} />
          <div className="h-[260px] mt-2">
            <ResponsiveContainer>
              <ComposedChart data={bookSeries} margin={{ top: 16, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="month" {...AXIS} tickFormatter={(m) => monthShort(m)} />
                <YAxis {...AXIS} width={metric === "bookings" ? 28 : 52} axisLine={false}
                  tickFormatter={(v) => (metric === "bookings" ? int(v) : baht(v))} />
                <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip title={(m: string) => monthLong(m)} fmt={(v: unknown) => fmtMetric(v)} />} />
                <Bar isAnimationActive={false} dataKey="value" name="This year" fill={C.wine} radius={[4, 4, 0, 0]} maxBarSize={24}
                  label={{ position: "top", fontSize: 10.5, fill: "#5A534C", formatter: (v: number) => (metric === "bookings" ? v : "") }} />
                <Line isAnimationActive={false} dataKey="ly" name="Same month last year" stroke={C.grey} strokeWidth={2} dot={{ r: 3, fill: C.grey, stroke: "#fff", strokeWidth: 1.5 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Capacity outlook" kpi="capacity_status" question="Do we need more people? Hours already booked against staff capacity.">
          <table className="tbl -mx-3 w-[calc(100%+1.5rem)]">
            <thead><tr><th>Month</th><th>Planning</th><th>Wedding day</th></tr></thead>
            <tbody>
              {outlookMonths.map((m) => (
                <tr key={m}>
                  <td className="font-medium whitespace-nowrap">{monthShort(m, true)}</td>
                  {["Planning", "Wedding day"].map((g) => {
                    const o = (d.capacity_outlook as Row[]).find((x) => x.month === m && x.role_group === g);
                    return (
                      <td key={g}>
                        <div className="flex items-center gap-1.5"><Chip s={o?.status} /><span className="num">{pct(o?.expected_utilization)}</span></div>
                        {n(o?.gap_hours) ? <div className="text-[11.5px] text-ink-3 num">{hours(o?.gap_hours)} short</div> : null}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[12px] text-ink-3 mt-3">
            {(d.capacity_outlook as Row[]).some((o) => o.forecast_hours !== null)
              ? "Expected hours: weddings already booked plus the demand forecast (middle estimate)."
              : "Booked weddings only: the demand forecast is not available yet, so new bookings will add to these numbers."}
          </p>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-5 mb-5">
        <Card title="Conversion by inquiry month" kpi="conversion_rate" question="Is conversion falling? Months join the line once 90 % of their couples have decided.">
          <Legend items={[{ label: "Conversion", swatch: C.wine, line: true }, { label: "Still being decided (will rise)", swatch: C.grey }]} />
          <div className="h-[230px] mt-2">
            <ResponsiveContainer>
              <ComposedChart data={convSeries} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="month" {...AXIS} tickFormatter={(m) => monthShort(m)} />
                <YAxis {...AXIS} width={40} axisLine={false} tickFormatter={(v) => pct(v)} />
                <Tooltip content={<ChartTip title={(m: string) => monthLong(m)} fmt={(v: unknown) => pct(v, 1)} />} />
                <Line isAnimationActive={false} dataKey="conversion" name="Conversion" stroke={C.wine} strokeWidth={2} dot={{ r: 3.5, fill: C.wine, stroke: "#fff", strokeWidth: 1.5 }} connectNulls={false} />
                <Line isAnimationActive={false} dataKey="open" name="Still being decided" stroke="transparent" dot={{ r: 4, fill: C.grey, stroke: "#fff", strokeWidth: 1.5 }} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card title="Where couples come from" kpi="channel_share" question="Share of inquiries by channel, last 12 months against the 12 before, and how each converts.">
          <table className="tbl -mx-3 w-[calc(100%+1.5rem)]">
            <thead><tr><th>Channel</th><th className="w-1/2">Share of inquiries</th><th className="text-right">Conversion <Info id="conversion_rate" /></th></tr></thead>
            <tbody>
              {(d.channels as Row[]).map((c) => (
                <tr key={c.channel}>
                  <td className="font-medium whitespace-nowrap">{c.channel}</td>
                  <td>
                    <div className="flex items-center gap-2"><HBar value={c.now.share} max={0.4} /><span className="num w-10 text-right">{pct(c.now.share)}</span></div>
                    <div className="flex items-center gap-2 mt-1"><HBar value={c.before?.share ?? 0} max={0.4} color="bg-stone-data" /><span className="num w-10 text-right text-ink-3">{pct(c.before?.share)}</span></div>
                  </td>
                  <td className="text-right num">{pct(c.now.conversion, 1)}<div className="text-[11.5px] text-ink-3">{int(c.now.decided)} decided</div></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3"><Legend items={[{ label: "Last 12 months", swatch: C.wine }, { label: "12 months before", swatch: C.grey }]} /></div>
        </Card>
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Where revenue leaks" kpi="fee_at_stake_lost" question="Couples lost in the last 12 months, by reason, with the fee at stake (estimate).">
          <ul className="grid gap-3">
            {lost.map((l) => (
              <li key={l.lost_reason} className="grid grid-cols-[150px_1fr_auto] items-center gap-3">
                <span className="text-[13px]"><span className="font-medium">{l.lost_reason}</span>
                  <span className="block text-[11.5px] text-ink-3">{int(l.lost)} couples · {l.closed_by === "AutoRule" ? "closed after 30 silent days" : "closed by sales"}</span></span>
                <HBar value={n(l.fee_at_stake_estimate) ?? 0} max={lostMax} />
                <span className="num text-[13px] font-medium w-16 text-right">{baht(l.fee_at_stake_estimate)}</span>
              </li>
            ))}
          </ul>
          <p className="text-[12px] text-ink-3 mt-4">Only couples who received a quote count toward the fee at stake.</p>
        </Card>

        <Card title="Is the call list paying off?" kpi="model_lift" question="Couples on the ranked call list against the standard queue kept as a control.">
          {(d.model_value as Row[]).length === 0 ? (
            <Empty title="No result yet">
              The ranked call list starts with the lead model. The comparison needs leads first scored at least 30 days ago, so the first result appears about a month after launch.
            </Empty>
          ) : (
            <table className="tbl">
              <thead><tr><th>Group</th><th className="text-right">Couples</th><th className="text-right">Booked in 30 days</th><th className="text-right">Rate</th></tr></thead>
              <tbody>{(d.model_value as Row[]).map((a) => (
                <tr key={a.arm}><td>{a.arm}</td><td className="text-right num">{int(a.leads)}</td><td className="text-right num">{int(a.booked_30d)}</td><td className="text-right num font-medium">{pct(a.booking_rate_30d, 1)}</td></tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      </div>
    </>
  );
}
