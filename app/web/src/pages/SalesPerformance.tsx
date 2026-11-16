import { Bar, BarChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { int, Me, minutes, monthLong, monthShort, n, pct, Row } from "../api";
import { AXIS, C, Card, ChartTip, Empty, GRID, HBar, Info, Legend, Loading, PageHead, useData } from "../ui";

const STEPS = [["inquiries", "Inquiries"], ["replied", "Replied"], ["consulted", "Consulted"], ["quoted", "Quoted"], ["booked", "Booked"]] as const;
const BANDS = ["< 1 h", "1–24 h", "> 24 h", "in person", "no logged reply"];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function SalesPerformance({ me }: { me: Me }) {
  const { data: d, error } = useData<Row>("/api/sales/performance");
  if (!d) return <Loading error={error} />;
  const done = (d.funnel as Row[]).filter((f) => f.month < d.current_month);
  const [a, b] = [done[0], done[1]];
  return (
    <>
      <PageHead title="Sales performance"
        sub={<>Weekly review{me.role === "exec" ? " for the owner (no couple names)" : ""}: where couples drop out, which channels and habits win bookings, and why we lose them.</>} />

      <div className="grid lg:grid-cols-2 gap-5 mb-5">
        <Card title="Funnel" kpi="stage_rate" question={`Where do couples drop out? Inquiries received in ${monthLong(a.month)} against ${monthLong(b.month)}.`}>
          <Legend items={[{ label: monthLong(a.month), swatch: C.wine }, { label: monthLong(b.month), swatch: C.grey }]} />
          <ul className="grid gap-3 mt-3">
            {STEPS.map(([k, label], i) => {
              const prev = i ? STEPS[i - 1][0] : null;
              const rate = (m: Row) => (prev && n(m[prev]) ? n(m[k])! / n(m[prev])! : null);
              return (
                <li key={k} className="grid grid-cols-[84px_1fr_auto] gap-x-3 items-center">
                  <span className="text-[13px] font-medium">{label}</span>
                  <div className="grid gap-1">
                    <div className="flex items-center gap-2"><HBar value={n(a[k])!} max={Math.max(n(a.inquiries)!, n(b.inquiries)!)} /><span className="num text-[12.5px] w-9 text-right">{int(a[k])}</span></div>
                    <div className="flex items-center gap-2"><HBar value={n(b[k])!} max={Math.max(n(a.inquiries)!, n(b.inquiries)!)} color="bg-stone-data" /><span className="num text-[12.5px] w-9 text-right text-ink-3">{int(b[k])}</span></div>
                  </div>
                  <span className="text-[12px] text-ink-2 w-24 text-right num">{prev ? <>{pct(rate(a))} <span className="text-ink-3">/ {pct(rate(b))}</span></> : "step rate"}</span>
                </li>
              );
            })}
          </ul>
          <p className="text-[12px] text-ink-3 mt-4">Still open: {int(a.still_open)} of {monthShort(a.month)}'s couples and {int(b.still_open)} of {monthShort(b.month)}'s; their later steps may still come.</p>
        </Card>

        <ChannelConversion rows={d.channels} />
      </div>

      <div className="grid lg:grid-cols-2 gap-5 mb-5">
        <ReplySpeed bands={d.response_bands} hours={d.response_hours} />
        <LostReasons rows={d.lost} current={d.current_month} />
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        <PriceFit bins={d.quote_bins} medians={d.quote_medians} />
        <Card title="Follow-up compliance" kpi="followup_compliance" question="Is the daily call list being worked? Last 4 weeks, per salesperson.">
          {(d.compliance as Row[]).length === 0 ? (
            <Empty title="No call lists yet">The daily call list starts with the lead model. Each salesperson's listed and contacted couples appear here from its first day.</Empty>
          ) : (
            <ul className="grid gap-3">
              {(d.compliance as Row[]).map((c) => (
                <li key={c.salesperson} className="grid grid-cols-[80px_1fr_auto] items-center gap-3">
                  <span className="font-medium">{c.salesperson}</span>
                  <HBar value={n(c.contacted)!} max={n(c.listed)!} />
                  <span className="num text-[13px]">{pct(n(c.contacted)! / n(c.listed)!)} <span className="text-ink-3 text-[12px]">({int(c.contacted)} of {int(c.listed)})</span></span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function ChannelConversion({ rows }: { rows: Row[] }) {
  const max = Math.max(0.05, ...rows.map((r) => n(r.ci_high) ?? 0));
  const x = (v: unknown) => `${((n(v) ?? 0) / max) * 100}%`;
  const sorted = [...rows].sort((p, q) => (n(q.conversion) ?? 0) - (n(p.conversion) ?? 0));
  return (
    <Card title="Conversion by channel" kpi="conversion_interval" question="Which channels bring couples who book? Last 12 complete months; the line is the 95 % range.">
      <div className="grid grid-cols-[minmax(92px,auto)_1fr_auto] gap-x-3 gap-y-2.5 items-center">
        {sorted.map((r) => (
          <div key={r.channel} className="contents">
            <span className="text-[13px]"><span className="font-medium">{r.channel}</span><span className="block text-[11.5px] text-ink-3">{int(r.inquiries)} inquiries</span></span>
            <div className="relative h-5" aria-label={`${r.channel}: ${pct(r.conversion, 1)}, range ${pct(r.ci_low, 1)} to ${pct(r.ci_high, 1)}`}>
              <div className="absolute inset-y-0 left-0 right-0 my-auto h-px bg-hair" />
              <div className="absolute top-1/2 -translate-y-1/2 h-[2px] bg-stone-data rounded" style={{ left: x(r.ci_low), width: `calc(${x(r.ci_high)} - ${x(r.ci_low)})` }} />
              <div className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-wine ring-2 ring-white" style={{ left: x(r.conversion) }} />
            </div>
            <span className="num text-[13px] text-right"><span className="font-medium">{pct(r.conversion, 1)}</span><span className="block text-[11.5px] text-ink-3">{pct(r.ci_low, 0)}–{pct(r.ci_high, 0)}</span></span>
          </div>
        ))}
      </div>
      <p className="text-[12px] text-ink-3 mt-4">A wide line means few decided couples: the true rate could be anywhere on it.</p>
    </Card>
  );
}

function ReplySpeed({ bands, hours }: { bands: Row[]; hours: Row[] }) {
  const sorted = BANDS.map((b) => bands.find((r) => r.response_band === b)).filter(Boolean) as Row[];
  const max = Math.max(...sorted.map((r) => n(r.conversion) ?? 0));
  const cell = new Map(hours.map((h) => [`${h.weekday}|${h.hour}`, h]));
  const top = Math.max(...hours.map((h) => n(h.inquiries) ?? 0));
  const shade = (v: number) => (v === 0 ? "#FAF7F2" : C.ramp[Math.min(4, Math.floor((v / top) * 4.999))]);
  return (
    <Card title="Reply speed" kpi="reply_band_conversion" question="Does a fast first reply matter, and when do couples write?">
      <ul className="grid gap-2">
        {sorted.map((r) => (
          <li key={r.response_band} className="grid grid-cols-[110px_1fr_auto] items-center gap-3">
            <span className="text-[13px]"><span className="font-medium">{r.response_band === "in person" ? "In person" : r.response_band === "no logged reply" ? "No reply logged" : `Reply ${r.response_band}`}</span></span>
            <HBar value={n(r.conversion) ?? 0} max={max} />
            <span className="num text-[13px] w-28 text-right">{pct(r.conversion, 1)} <span className="text-[11.5px] text-ink-3">of {int(r.decided)}</span></span>
          </li>
        ))}
      </ul>
      <div className="text-[12.5px] font-medium text-ink-2 mt-5 mb-1.5 flex items-center gap-1.5">Online inquiries by hour of arrival <Info id="arrivals_by_hour" /></div>
      <div className="overflow-x-auto">
        <div className="grid gap-[2px] min-w-[520px]" style={{ gridTemplateColumns: "34px repeat(24, minmax(0,1fr))" }}>
          <span />
          {Array.from({ length: 24 }, (_, h) => <span key={h} className="text-[10px] text-ink-3 text-center">{h % 3 === 0 ? h : ""}</span>)}
          {WEEKDAYS.map((w, wi) => (
            <div key={w} className="contents">
              <span className="text-[11px] text-ink-3 self-center">{w}</span>
              {Array.from({ length: 24 }, (_, h) => {
                const c = cell.get(`${wi + 1}|${h}`);
                const v = n(c?.inquiries) ?? 0;
                return <span key={h} className={`h-5 rounded-[3px] text-[9.5px] grid place-items-center num ${v / top > 0.6 ? "text-white" : "text-ink-2"}`}
                  style={{ background: shade(v) }} title={`${w} ${h}:00 · ${v} inquiries · median first reply ${minutes(c?.median_reply_min)}`}>{v || ""}</span>;
              })}
            </div>
          ))}
        </div>
      </div>
      <p className="text-[12px] text-ink-3 mt-2">Hover a cell for the median wait for a first reply at that hour.</p>
    </Card>
  );
}

function LostReasons({ rows, current }: { rows: Row[]; current: string }) {
  const months = [...new Set(rows.map((r) => r.month as string))].filter((m) => m < current).sort().slice(-12);
  const data = months.map((m) => ({
    month: m,
    auto: rows.filter((r) => r.month === m && r.closed_by === "AutoRule").reduce((s, r) => s + n(r.lost)!, 0),
    sales: rows.filter((r) => r.month === m && r.closed_by !== "AutoRule").reduce((s, r) => s + n(r.lost)!, 0),
  }));
  const reasons = new Map<string, number>();
  rows.filter((r) => months.includes(r.month) && r.closed_by !== "AutoRule").forEach((r) => reasons.set(r.lost_reason, (reasons.get(r.lost_reason) ?? 0) + n(r.lost)!));
  const total = [...reasons.values()].reduce((s, v) => s + v, 0);
  const auto = data.reduce((s, r) => s + r.auto, 0);
  return (
    <Card title="Why we lose couples" kpi="lost_reason_share" question="Price, date or silence? Couples lost by closing month.">
      <Legend items={[{ label: "Closed by sales (with a reason)", swatch: C.wine }, { label: "No reply, closed after 30 days", swatch: C.grey }]} />
      <div className="h-[170px] mt-1">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="month" {...AXIS} tickFormatter={(m) => monthShort(m)} />
            <YAxis {...AXIS} width={30} axisLine={false} />
            <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip title={(m: string) => monthLong(m)} />} />
            <Bar isAnimationActive={false} dataKey="sales" name="Closed by sales" stackId="a" fill={C.wine} maxBarSize={22} stroke="#fff" strokeWidth={1} />
            <Bar isAnimationActive={false} dataKey="auto" name="No reply" stackId="a" fill={C.grey} maxBarSize={22} radius={[4, 4, 0, 0]} stroke="#fff" strokeWidth={1} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="label mt-4 mb-2">Reasons given by sales, last 12 months</div>
      <ul className="grid gap-1.5">
        {[...reasons.entries()].sort((p, q) => q[1] - p[1]).map(([r, v]) => (
          <li key={r} className="grid grid-cols-[130px_1fr_auto] items-center gap-3 text-[13px]">
            <span>{r}</span><HBar value={v} max={total} /><span className="num w-20 text-right">{pct(v / total)} <span className="text-ink-3 text-[11.5px]">({v})</span></span>
          </li>
        ))}
      </ul>
      <p className="text-[12px] text-ink-3 mt-3">Besides these, {int(auto)} couples stopped replying and were closed automatically.</p>
    </Card>
  );
}

function PriceFit({ bins, medians }: { bins: Row[]; medians: Row[] }) {
  const keys = [...new Set(bins.map((b) => Number(b.bin)))].sort((p, q) => p - q);
  const data = keys.map((k) => ({
    bin: k,
    booked: n(bins.find((b) => Number(b.bin) === k && b.group_name === "Booked")?.quotes) ?? 0,
    price: n(bins.find((b) => Number(b.bin) === k && b.group_name === "Lost on price")?.quotes) ?? 0,
  }));
  const med = (g: string) => medians.find((m) => m.group_name === g);
  return (
    <Card title="Price fit" kpi="quote_share" question="Do quotes take too big a share of the couple's budget? Quotes by share of budget.">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[13px] mb-2">
        <span>Median, booked: <b className="num">{pct(med("Booked")?.median, 1)}</b> <span className="text-ink-3">({int(med("Booked")?.quotes)})</span></span>
        <span>Median, lost on price: <b className="num">{pct(med("Lost on price")?.median, 1)}</b> <span className="text-ink-3">({int(med("Lost on price")?.quotes)})</span></span>
      </div>
      <Legend items={[{ label: "Booked", swatch: C.wine }, { label: "Lost on price", swatch: C.grey }, { label: "16 % reference", swatch: C.ink, line: true }]} />
      <div className="h-[200px] mt-1">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 14, right: 8, left: 0, bottom: 0 }} barGap={2}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="bin" {...AXIS} tickFormatter={(v) => (v >= 0.4 ? "40 %+" : pct(v))} />
            <YAxis {...AXIS} width={30} axisLine={false} />
            <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip title={(v: number) => (v >= 0.4 ? "40 % of budget or more" : `${pct(v)}–${pct(v + 0.02)} of budget`)} />} />
            <ReferenceLine x={0.16} stroke={C.ink} strokeWidth={1.5} label={{ value: "16 %", position: "top", fontSize: 11, fill: "#1F1B18" }} />
            <Bar isAnimationActive={false} dataKey="booked" name="Booked" fill={C.wine} maxBarSize={12} radius={[3, 3, 0, 0]} />
            <Bar isAnimationActive={false} dataKey="price" name="Lost on price" fill={C.grey} maxBarSize={12} radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}
