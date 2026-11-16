import { Fragment, useMemo, useState } from "react";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CalendarSearch } from "lucide-react";
import { api, day, isoLocal, dayYear, hours, int, Me, monthLong, monthShort, n, pct, Row, weekLabel } from "../api";
import { AXIS, C, Card, ChartTip, Chip, Drawer, Empty, GRID, Info, Legend, Loading, PageHead, Tile, useData } from "../ui";

const GROUPS = ["Planning", "Wedding day"];
const status = (u: number | null) => (u === null ? null : u >= 1 ? "Over" : u >= 0.85 ? "Tight" : u >= 0.5 ? "OK" : "Quiet");
const cellClass = (u: number) =>
  u >= 1 ? "bg-over-bg text-over font-semibold" : u >= 0.85 ? "bg-tight-bg text-tight font-semibold" : u >= 0.5 ? "bg-stone-soft text-ink" : "bg-stone-cell text-ink-3";

export default function Capacity({ me }: { me: Me }) {
  const { data: d, error } = useData<Row>("/api/ops/capacity");
  const [group, setGroup] = useState("All");
  const [cell, setCell] = useState<Row | null>(null);
  if (!d) return <Loading error={error} />;
  const t = d.tiles;
  const ws = d.this_week as string;

  return (
    <>
      <PageHead title="Capacity planner"
        sub={<>Week of {day(ws, { day: "numeric", month: "long", year: "numeric" })}. Who is overloaded, when, and which dates we can still sell.{me.role === "exec" ? " Read-only." : ""}</>} />

      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3 mb-5">
        <Tile kpi="peak_utilization_4w" value={pct(t.peak_utilization_4w?.utilization)}
          tone={(n(t.peak_utilization_4w?.utilization) ?? 0) >= 1 ? "over" : undefined}
          sub={t.peak_utilization_4w && <>{t.peak_utilization_4w.nickname} ({t.peak_utilization_4w.role_name}), week of {weekLabel(t.peak_utilization_4w.week_start)}</>} />
        <Tile kpi="person_weeks_over_4w" value={int(t.person_weeks_over_4w)} tone={t.person_weeks_over_4w > 0 ? "over" : undefined}
          sub="Times someone is booked above contract" />
        {t.forecast_gap_12w !== null
          ? <Tile kpi="forecast_gap_12w" value={hours(t.forecast_gap_12w)} tone={t.forecast_gap_12w > 0 ? "over" : undefined} sub="Expected hours above capacity: freelancers to book" />
          : <Tile kpi="overtime_hours_12w" value={hours(t.overtime_hours_12w)} sub="Freelance hours needed if nothing moves" />}
        <Tile kpi="turned_away_90d" value={int(t.turned_away_90d)} sub={<>Lost because the date was full, since {monthLong(t.turned_away_since)}</>} />
      </div>

      <Card className="mb-5" title="Staff × week" kpi="utilization"
        question="Who is overloaded, and when? Each cell is that person's utilization; click a cell to see the weddings behind it."
        right={
          <label className="flex items-center gap-2 text-[12.5px] text-ink-2">Role
            <select className="input !py-1 !text-[13px]" value={group} onChange={(e) => setGroup(e.target.value)}>
              <option>All</option>{GROUPS.map((g) => <option key={g}>{g}</option>)}
            </select>
          </label>}>
        <Heatmap rows={d.grid} roleWeeks={d.role_weeks} thisWeek={ws} group={group} onCell={setCell} />
      </Card>

      <NextWeeks d={d} />

      <div className="grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] gap-5 mb-5">
        <DateChecker />
        <LostPeak d={d} />
      </div>

      <HoursPerWedding rows={d.hours_per_wedding} />
      <WeekDrawer cell={cell} onClose={() => setCell(null)} />
    </>
  );
}

function Heatmap({ rows, roleWeeks, thisWeek, group, onCell }: { rows: Row[]; roleWeeks: Row[]; thisWeek: string; group: string; onCell: (r: Row) => void }) {
  const weeks = useMemo(() => [...new Set(rows.map((r) => r.week_start as string))].sort(), [rows]);
  const people = useMemo(() => {
    const m = new Map<number, Row>();
    rows.forEach((r) => m.set(r.employee_id, r));
    return [...m.values()];
  }, [rows]);
  const at = useMemo(() => new Map(rows.map((r) => [`${r.employee_id}|${r.week_start}`, r])), [rows]);
  const rw = new Map(roleWeeks.map((r) => [`${r.role_group}|${r.week_start}`, r]));
  const cols = `minmax(128px,1.3fr) repeat(${weeks.length}, minmax(42px,1fr))`;

  return (
    <div className="overflow-x-auto overflow-y-hidden -mx-1 px-1 pb-1">
      <div className="grid gap-[2px] min-w-[860px] text-[12px]" style={{ gridTemplateColumns: cols }} role="table" aria-label="Utilization by person and week">
        <div role="columnheader" className="text-ink-3 text-[11px] self-end pb-1">Person · role</div>
        {weeks.map((w) => (
          <div key={w} role="columnheader" className={`text-center text-[11px] pb-1 self-end leading-tight ${w === thisWeek ? "text-wine font-semibold" : w < thisWeek ? "text-ink-3" : "text-ink-2"}`}>
            {w === thisWeek ? "This wk" : weekLabel(w)}
          </div>
        ))}
        {GROUPS.filter((g) => group === "All" || g === group).map((g) => (
          <Fragment key={g}>
            <div className="col-span-full mt-2 mb-0.5 label">{g}</div>
            {people.filter((p) => p.role_group === g).map((p) => (
              <Fragment key={p.employee_id}>
                <div role="rowheader" className="flex items-baseline gap-1.5 pr-2 truncate py-1.5">
                  <span className="font-medium text-ink">{p.nickname}</span><span className="text-ink-3 text-[11px] truncate">{p.role_name}{p.employment_type === "PartTime" ? " · part-time" : ""}</span>
                </div>
                {weeks.map((w) => {
                  const c = at.get(`${p.employee_id}|${w}`);
                  if (!c) return <div key={w} className="rounded-[4px] bg-hair/60 text-center text-ink-3 py-1.5" title="No contract">·</div>;
                  const u = n(c.utilization) ?? 0;
                  return (
                    <button key={w} onClick={() => onCell(c)}
                      className={`rounded-[4px] num text-center py-1.5 hover:ring-2 hover:ring-wine-300 ${cellClass(u)} ${w === thisWeek ? "ring-1 ring-wine/40" : ""} ${w < thisWeek ? "opacity-70" : ""}`}
                      aria-label={`${p.nickname}, week of ${weekLabel(w)}: ${Math.round(u * 100)} %, ${int(c.assigned_hours)} of ${int(c.capacity_hours)} hours`}
                      title={`${p.nickname} · week of ${weekLabel(w)}\n${int(c.assigned_hours)} h of ${int(c.capacity_hours)} h · ${c.weddings} weddings with work this week`}>
                      {Math.round(u * 100)}
                    </button>
                  );
                })}
              </Fragment>
            ))}
            <div role="rowheader" className="py-1.5 pr-2 text-ink-2 font-medium border-t border-line">All {g.toLowerCase()}</div>
            {weeks.map((w) => {
              const r = rw.get(`${g}|${w}`);
              const u = n(r?.utilization) ?? 0;
              return <div key={w} className={`num text-center py-1.5 border-t border-line ${u >= 1 ? "text-over font-semibold" : u >= 0.85 ? "text-tight font-semibold" : "text-ink-2"}`}>{r ? Math.round(u * 100) : "–"}</div>;
            })}
          </Fragment>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-4 text-[12px] text-ink-2">
        <span>Utilization, % of contracted hours:</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-stone-cell border border-line" />under 50</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-stone-soft" />50–84</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-tight-bg" />85–99 tight</span>
        <span className="inline-flex items-center gap-1.5"><span className="w-3 h-3 rounded-sm bg-over-bg" />100 or more, over</span>
        <span className="text-ink-3">Past weeks are faded. Planning hours are spread over the months before each wedding.</span>
      </div>
    </div>
  );
}

function WeekDrawer({ cell, onClose }: { cell: Row | null; onClose: () => void }) {
  const { data, error } = useData<Row>(cell ? `/api/ops/week?employee_id=${cell.employee_id}&week_start=${cell.week_start}` : null, [cell]);
  const w = data?.weddings as Row[] | undefined;
  return (
    <Drawer open={!!cell} onClose={onClose} title={cell ? `${cell.nickname}, week of ${weekLabel(cell.week_start)}` : ""}
      sub={cell && <><Chip s={cell.status} /> <span className="num">{int(cell.assigned_hours)} h</span> assigned of <span className="num">{int(cell.capacity_hours)} h</span> ({pct(cell.utilization)}) · {cell.role_name}</>}>
      {error && <p className="text-over">{error}</p>}
      {!w ? <p className="text-ink-3">Loading…</p> : w.length === 0 ? <Empty title="No wedding work this week" /> : (
        <>
          <p className="text-[13px] text-ink-2 mb-3">{w.length} weddings have work this week. Largest first.</p>
          <table className="tbl">
            <thead><tr><th>Wedding</th><th className="text-right">Planning</th><th className="text-right">Wedding day</th><th>Also on it</th></tr></thead>
            <tbody>{w.map((r) => (
              <tr key={r.event_id}>
                <td><div className="font-medium">{dayYear(r.event_date)}</div><div className="text-[11.5px] text-ink-3">{r.package} · {int(r.guests)} guests · {r.complexity} complexity · {r.role}</div></td>
                <td className="text-right num">{hours(r.planning_hours)}</td>
                <td className="text-right num">{n(r.event_day_hours) ? hours(r.event_day_hours) : "–"}</td>
                <td className="text-[12px] text-ink-2">{r.also_on_it || "–"}</td>
              </tr>
            ))}</tbody>
          </table>
        </>
      )}
    </Drawer>
  );
}

function NextWeeks({ d }: { d: Row }) {
  const ws = d.this_week as string;
  const fc = d.forecast_available as boolean;
  // without a forecast: booked hours per role group and week (v_role_week) against capacity
  const src: Row[] = fc
    ? (d.forward as Row[]).map((f) => ({ ...f, booked: n(f.booked_hours), leads: n(f.pipeline_hours), fresh: n(f.new_booking_hours), cap: n(f.capacity_hours), expected: n(f.p50_hours), band: [n(f.p10_hours), n(f.p90_hours)], st: f.status }))
    : (d.role_weeks as Row[]).filter((r) => r.week_start >= ws).map((r) => ({ week_start: r.week_start, role_group: r.role_group, booked: n(r.assigned_hours), cap: n(r.capacity_hours), expected: n(r.assigned_hours), st: status(n(r.utilization)) }));
  const weeks = [...new Set(src.map((r) => r.week_start as string))].sort().slice(0, 26);
  const get = (g: string, w: string) => src.find((r) => r.role_group === g && r.week_start === w);
  return (
    <Card className="mb-5" title="Next 26 weeks by role group" kpi="capacity_status"
      question={fc ? "Will we have enough people, and when should we book freelancers? Booked work plus the forecast against capacity." : "Will we have enough people, and when should we book freelancers?"}>
      {!fc && (
        <p className="text-[13px] text-tight bg-tight-soft rounded-lg px-3 py-2 mb-4">
          The demand forecast is not available yet. Showing hours of weddings already booked; new bookings and open leads will add to them.
        </p>
      )}
      <div className="grid lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-6">
        <div className="grid gap-5">
          <Legend items={[{ label: "Booked hours", swatch: C.wine }, ...(fc ? [{ label: "Expected from open leads", swatch: C.ramp[2] }, { label: "Expected new bookings", swatch: C.ramp[0] }, { label: "Likely range (P10–P90)", swatch: "#E4DED6" }] : []), { label: "Capacity", swatch: C.ink, line: true }]} />
          {GROUPS.map((g) => (
            <div key={g}>
              <div className="text-[12.5px] font-medium text-ink-2 mb-1">{g}</div>
              <div className="h-[150px]">
                <ResponsiveContainer>
                  <ComposedChart data={weeks.map((w) => get(g, w))} margin={{ top: 6, right: 6, left: 0, bottom: 0 }} barCategoryGap={2}>
                    <CartesianGrid {...GRID} />
                    <XAxis dataKey="week_start" {...AXIS} tickFormatter={weekLabel} interval={3} />
                    <YAxis {...AXIS} width={38} axisLine={false} />
                    <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip title={(w: string) => `Week of ${weekLabel(w)}`} fmt={(v: unknown) => hours(v)} />} />
                    {fc && <Area dataKey="band" name="Likely range" fill="#E4DED6" stroke="none" isAnimationActive={false} />}
                    <Bar isAnimationActive={false} dataKey="booked" name="Booked" stackId="a" fill={C.wine} maxBarSize={24} radius={fc ? 0 : [3, 3, 0, 0]} />
                    {fc && <Bar isAnimationActive={false} dataKey="leads" name="From open leads" stackId="a" fill={C.ramp[2]} maxBarSize={24} />}
                    {fc && <Bar isAnimationActive={false} dataKey="fresh" name="New bookings" stackId="a" fill={C.ramp[0]} maxBarSize={24} radius={[3, 3, 0, 0]} />}
                    <Line isAnimationActive={false} dataKey="cap" name="Capacity" type="stepAfter" stroke={C.ink} strokeWidth={1.5} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          ))}
        </div>
        <div className="max-h-[400px] overflow-y-auto border border-line rounded-lg">
          <table className="tbl">
            <thead className="sticky top-0 bg-surface"><tr><th>Week</th>{GROUPS.map((g) => <th key={g}>{g}</th>)}</tr></thead>
            <tbody>{weeks.map((w) => (
              <tr key={w}>
                <td className="whitespace-nowrap">{weekLabel(w)}</td>
                {GROUPS.map((g) => {
                  const r = get(g, w);
                  const gap = r ? Math.max(0, (r.expected ?? 0) - (r.cap ?? 0)) : 0;
                  return <td key={g}><Chip s={r?.st} />{gap > 0 && <span className="num text-[12px] text-ink-2 ml-1.5">{hours(gap)} short</span>}</td>;
                })}
              </tr>
            ))}</tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}

function nextSaturday(weeksAhead: number) {
  const d = new Date();
  d.setDate(d.getDate() + weeksAhead * 7 + ((6 - d.getDay() + 7) % 7));
  return isoLocal(d);
}

function DateChecker() {
  const [date, setDate] = useState(nextSaturday(12));
  const [res, setRes] = useState<Row | null>(null);
  const [err, setErr] = useState("");
  const check = async () => {
    setErr("");
    try { setRes(await api(`/api/ops/date-check?d=${date}`)); } catch (e: any) { setErr(e.message); setRes(null); }
  };
  const r = res?.date;
  return (
    <Card title="Date checker" kpi="headroom_hours" question="Can we take this date? If not, which dates instead?">
      <form className="flex flex-wrap gap-2 mb-4" onSubmit={(e) => { e.preventDefault(); check(); }}>
        <input type="date" className="input" value={date} min={isoLocal(new Date())} max="2028-12-31"
          onChange={(e) => setDate(e.target.value)} aria-label="Wedding date" required />
        <button className="btn-primary"><CalendarSearch size={15} aria-hidden />Check date</button>
      </form>
      {err && <p className="text-over text-[13px]">{err}</p>}
      {!r && !err && <p className="text-[13px] text-ink-3">Pick the date a couple asked for. The answer uses weddings already booked and staff hours that week.</p>}
      {r && (
        <div className="grid gap-4">
          <div className="rounded-lg border border-line p-4">
            <div className="flex items-center justify-between gap-2">
              <div className="font-semibold text-[15px]">{dayYear(r.date)}</div>
              <Chip s={r.status}>{r.status === "Room" ? "Room" : r.status}</Chip>
            </div>
            <div className="text-[12.5px] text-ink-3 mt-0.5">
              Auspicious: {r.auspicious_level === "None" ? "no" : r.auspicious_level}{r.holiday ? ` · ${r.holiday}` : ""}{r.in_lent ? " · Buddhist Lent" : ""}{r.high_season ? " · high season" : ""}
            </div>
            <dl className="grid grid-cols-2 gap-3 mt-3 text-[13px]">
              <div><dt className="text-ink-3 text-[12px]">Weddings that day <Info id="weddings_that_day" /></dt><dd className="num font-semibold text-[18px]">{int(r.weddings_that_day)}</dd></div>
              <div><dt className="text-ink-3 text-[12px]">Weddings that week</dt><dd className="num font-semibold text-[18px]">{int(r.weddings_that_week)}</dd></div>
              <div><dt className="text-ink-3 text-[12px]">Planning headroom</dt><dd className={`num font-semibold ${n(r.planning_headroom_hours)! < 0 ? "text-over" : ""}`}>{hours(r.planning_headroom_hours)}</dd></div>
              <div><dt className="text-ink-3 text-[12px]">Wedding-day headroom</dt><dd className={`num font-semibold ${n(r.wedding_day_headroom_hours)! < 0 ? "text-over" : ""}`}>{hours(r.wedding_day_headroom_hours)}</dd></div>
            </dl>
          </div>
          <div>
            <div className="label mb-2">Alternatives with room, within 4 weeks</div>
            {(res!.alternatives as Row[]).length === 0 ? <p className="text-[13px] text-ink-3">No weekend or auspicious date with room within 4 weeks.</p> : (
              <ul className="grid gap-1.5">
                {(res!.alternatives as Row[]).map((a) => (
                  <li key={a.date} className="flex items-center justify-between gap-3 rounded-lg bg-page px-3 py-2 text-[13px]">
                    <span className="font-medium">{dayYear(a.date)}</span>
                    <span className="text-ink-2 text-[12px]">{a.auspicious_level === "None" ? "not auspicious" : `${a.auspicious_level} auspicious`} · {int(a.weddings_that_day)} booked</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

const LEVELS = ["None", "Low", "Medium", "High"];
function LostPeak({ d }: { d: Row }) {
  const ws = (d.this_week as string).slice(0, 7);
  const peak = (d.peak_acceptance as Row[]).filter((p) => n(p.decided)! >= 5 && p.requested_month <= ws).slice(-18)
    .map((p) => ({ month: p.requested_month, rate: n(p.acceptance_rate), decided: n(p.decided), booked: n(p.booked) }));
  const tm = new Map<string, Row>();
  (d.turned_away as Row[]).forEach((t) => {
    const r = tm.get(t.requested_month) ?? { month: t.requested_month, None: 0, Low: 0, Medium: 0, High: 0, total: 0 };
    r[t.auspicious_level] += n(t.couples_turned_away)!;
    r.total += n(t.couples_turned_away)!;
    tm.set(t.requested_month, r);
  });
  const turned = [...tm.values()].sort((a, b) => a.month.localeCompare(b.month)).slice(-18);
  return (
    <Card title="Peak demand we lose" question="How many couples asking for peak or auspicious dates do we accept, and how many do we turn away?">
      <div className="text-[12.5px] font-medium text-ink-2 mb-1 flex items-center gap-1.5">Peak-date acceptance by requested month <Info id="peak_acceptance_rate" /></div>
      <div className="h-[160px]">
        <ResponsiveContainer>
          <BarChart data={peak} margin={{ top: 14, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="month" {...AXIS} tickFormatter={(m) => monthShort(m, true)} interval={2} />
            <YAxis {...AXIS} width={38} axisLine={false} tickFormatter={(v) => pct(v)} />
            <Tooltip cursor={{ fill: "#F3EFE9" }} content={({ active, payload }: any) => active && payload?.length ? (
              <div className="rounded-lg bg-surface border border-line shadow-pop px-3 py-2 text-[12px]">
                <div className="font-semibold">{monthLong(payload[0].payload.month)}</div>
                <div className="text-ink-2">{pct(payload[0].payload.rate, 1)} accepted · {payload[0].payload.booked} of {payload[0].payload.decided} decided</div>
              </div>) : null} />
            <Bar isAnimationActive={false} dataKey="rate" name="Accepted" fill={C.wine} radius={[4, 4, 0, 0]} maxBarSize={20} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="text-[12.5px] font-medium text-ink-2 mt-4 mb-1 flex items-center gap-1.5">Couples turned away, by requested month <Info id="turned_away" /></div>
      <Legend items={LEVELS.map((l, i) => ({ label: l === "None" ? "Not auspicious" : `${l} auspicious`, swatch: C.ramp[i + 1] }))} />
      <div className="h-[160px] mt-1">
        <ResponsiveContainer>
          <BarChart data={turned} margin={{ top: 14, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="month" {...AXIS} tickFormatter={(m) => monthShort(m, true)} interval={2} />
            <YAxis {...AXIS} width={38} axisLine={false} allowDecimals={false} />
            <Tooltip cursor={{ fill: "#F3EFE9" }} content={<ChartTip title={(m: string) => monthLong(m)} />} />
            {LEVELS.map((l, i) => (
              <Bar isAnimationActive={false} key={l} dataKey={l} name={l === "None" ? "Not auspicious" : `${l} auspicious`} stackId="a" fill={C.ramp[i + 1]} maxBarSize={20}
                radius={i === 3 ? [4, 4, 0, 0] : 0} stroke="#fff" strokeWidth={1} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </Card>
  );
}

function HoursPerWedding({ rows }: { rows: Row[] }) {
  const keys = [...new Set(rows.map((r) => `${r.package}|${r.guest_band}`))];
  const cx = ["Low", "Medium", "High"];
  const get = (k: string, c: string) => rows.find((r) => `${r.package}|${r.guest_band}` === k && r.complexity === c);
  return (
    <Card title="Staff hours per wedding" kpi="hours_per_wedding" question="What does a wedding really cost in staff time? Average total hours (number of weddings).">
      <div className="overflow-x-auto">
        <table className="tbl min-w-[560px]">
          <thead><tr><th>Package</th><th>Guests</th>{cx.map((c) => <th key={c} className="text-right">{c} complexity</th>)}</tr></thead>
          <tbody>{keys.map((k, i) => {
            const [pkg, band] = k.split("|");
            const first = i === 0 || !keys[i - 1].startsWith(pkg + "|");
            return (
              <tr key={k} className={first && i ? "border-t border-line" : ""}>
                <td className="font-medium">{first ? pkg : ""}</td><td className="text-ink-2">{band}</td>
                {cx.map((c) => {
                  const r = get(k, c);
                  return <td key={c} className="text-right num">{r ? <>{hours(r.avg_total_hours)} <span className="text-ink-3 text-[11.5px]">({r.weddings})</span></> : <span className="text-ink-3">–</span>}</td>;
                })}
              </tr>
            );
          })}</tbody>
        </table>
      </div>
    </Card>
  );
}
