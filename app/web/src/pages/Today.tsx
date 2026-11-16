import { useState } from "react";
import { Check, ChevronDown, HelpCircle, MessageCircle, Phone, Users } from "lucide-react";
import { baht, day, dayYear, int, Me, minutes, n, pct, post, Row } from "../api";
import { Card, Chip, Empty, Info, Loading, PageHead, Tile, useData } from "../ui";

const TYPES = [["Call", Phone], ["LINE", MessageCircle], ["Meeting", Users]] as const;
const OUTCOMES = ["Reached", "No answer", "Left message", "Consult booked", "Not interested"];
const BAND_DOTS: Record<string, number> = { High: 3, Medium: 2, Low: 1 };

// sales_pii holds a first name with a chat handle, or only a display name ("Beam & Fern"); fall back to the inquiry number
const coupleName = (r: Row) =>
  r.first_name ? `${r.first_name}${r.handle ? ` (${r.handle})` : ""}` : r.handle || `Inquiry ${r.inquiry_id}`;
const time = (iso: string) => new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });

export default function Today({ me }: { me: Me }) {
  const { data: d, error, reload } = useData<Row>("/api/sales/today");
  if (!d) return <Loading error={error} />;
  const canLog = me.role === "sales";
  const ticks = new Map<number, Row>();
  (d.ticks as Row[]).forEach((t) => ticks.set(t.inquiry_id, t));
  const list = d.list as Row[];
  const done = list.filter((r) => ticks.has(r.inquiry_id)).length;
  const week = d.my_week as Row[];
  const team = me.is_manager || me.role !== "sales";
  const bookings = week.reduce((s, w) => s + (n(w.bookings_this_month) ?? 0), 0);
  const replyOne = week.length === 1 ? n(week[0].median_first_reply_min_7d) : null;

  return (
    <>
      <PageHead title={`Today${team ? ", whole team" : ""}`}
        sub={<>{dayYear(d.today)} · {team ? "All salespeople's lists" : `Your list, ${me.display_name}`}. Reply to new couples first, then work the list from the top.</>} />

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-5">
        <Tile kpi="followups_done" value={<>{done}<span className="text-ink-3 text-[18px] font-medium"> / {list.length || d.list_size}</span></>}
          sub={list.length ? "Couples on today's list marked as contacted" : "The list appears when today's scores are in"} />
        <Tile kpi="bookings_this_month" value={int(bookings)} sub={<>Target {team ? `${8 * Math.max(1, week.length)} for the team` : 8} · month to date</>} />
        {/* team view: the slowest salesperson as the headline, everyone in the line below (a long list overflowed the tile) */}
        <Tile kpi="median_first_reply_7d"
          value={team ? minutes(Math.max(...week.map((w) => n(w.median_first_reply_min_7d) ?? 0))) : minutes(replyOne)}
          tone={(team ? Math.max(...week.map((w) => n(w.median_first_reply_min_7d) ?? 0)) : replyOne ?? 0) > 60 ? "tight" : undefined}
          sub={team ? <>Slowest · {week.map((w) => `${w.nickname} ${minutes(w.median_first_reply_min_7d)}`).join(" · ")} · target 60 min</> : "Target 60 min · last 7 days"} />
      </div>

      <Card className="mb-5" title={`New since yesterday (${(d.new as Row[]).length})`} question="Couples who wrote and have no reply yet. Reply first.">
        {(d.new as Row[]).length === 0 ? <p className="text-[13px] text-ink-3">Every new couple has had a first reply.</p> : (
          <ul className="divide-y divide-hair">
            {(d.new as Row[]).map((r) => (
              <li key={r.inquiry_id} className="py-2.5 flex flex-wrap items-center gap-x-4 gap-y-1">
                <span className="w-2 h-2 rounded-full bg-over shrink-0" aria-hidden />
                <span className="font-medium">{coupleName(r)}</span>
                <span className="text-[13px] text-ink-2">{r.channel} · {r.package_interest === "Unknown" ? "package not stated" : r.package_interest} · {r.budget_band ?? "budget not stated"}</span>
                <span className="text-[12.5px] text-over font-medium">No reply yet · wrote {day(r.inquiry_date)}</span>
                {team && <span className="text-[12px] text-ink-3">{r.sales_owner}</span>}
                <span className="ml-auto">{canLog && <MarkContacted id={r.inquiry_id} tick={ticks.get(r.inquiry_id)} onDone={reload} />}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mb-5" title={team ? "Call lists" : `Your list (${list.length})`} kpi="chance_to_book"
        question="Whom to contact now, why, and what to say. Ranked by expected fee."
        right={<RankingHelp />}>
        {list.length === 0 ? (
          <Empty title="Today's call list is not ready yet">
            The list is ranked each morning from the couples' chance to book. Scores for today have not arrived; until then, reply to new couples and work the going-cold list below.
          </Empty>
        ) : (
          <ol className="grid gap-3">
            {list.map((r) => <Lead key={r.inquiry_id} r={r} team={team} tick={ticks.get(r.inquiry_id)} canLog={canLog} onDone={reload} />)}
          </ol>
        )}
      </Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title={`Going cold (${(d.going_cold as Row[]).length})`} kpi="days_silent" question="Open couples silent for 10 days or more. They close automatically after 30.">
          {(d.going_cold as Row[]).length === 0 ? <p className="text-[13px] text-ink-3">No open couple has gone quiet.</p> : (
            <ul className="divide-y divide-hair max-h-[420px] overflow-y-auto -mx-1 px-1">
              {(d.going_cold as Row[]).map((r) => (
                <li key={r.inquiry_id} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                  <span className="font-medium min-w-[120px]">{coupleName(r)}</span>
                  <span className="text-ink-2">{r.channel} · {r.furthest_stage}</span>
                  <span className={`num ${n(r.days_silent)! >= 24 ? "text-over font-medium" : "text-ink-2"}`}>{r.days_silent} days silent · closes {day(r.auto_close_date)}</span>
                  {team && <span className="text-[12px] text-ink-3">{r.sales_owner}</span>}
                  <span className="ml-auto">{canLog && <MarkContacted id={r.inquiry_id} tick={ticks.get(r.inquiry_id)} onDone={reload} compact />}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title={`Standard queue (${(d.holdout as Row[]).length})`} question="Couples kept in today's usual order, newest first, so we can measure whether the ranked list helps.">
          {(d.holdout as Row[]).length === 0 ? <p className="text-[13px] text-ink-3">The standard queue fills with the first scores.</p> : (
            <ul className="divide-y divide-hair max-h-[420px] overflow-y-auto">
              {(d.holdout as Row[]).map((r) => (
                <li key={r.inquiry_id} className="py-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[13px]">
                  <span className="font-medium min-w-[120px]">{coupleName(r)}</span>
                  <span className="text-ink-2">{r.channel} · {r.package_interest} · wrote {day(r.inquiry_date)}</span>
                  {r.requested_date && <span className="inline-flex items-center gap-1.5 text-ink-2">wants {day(r.requested_date)} <Chip s={r.date_badge} /></span>}
                  <span className="ml-auto">{canLog && <MarkContacted id={r.inquiry_id} tick={ticks.get(r.inquiry_id)} onDone={reload} compact />}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function Lead({ r, team, tick, canLog, onDone }: { r: Row; team: boolean; tick?: Row; canLog: boolean; onDone: () => void }) {
  const dots = BAND_DOTS[r.band] ?? 0;
  const reasons = [r.reason1, r.reason2, r.reason3].filter(Boolean);
  return (
    <li className={`rounded-xl border p-4 ${tick ? "border-line bg-page/60" : "border-line bg-surface"}`}>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <span className="num text-[13px] font-semibold text-wine w-7">#{r.rank}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-semibold text-[15px]">{coupleName(r)}</span>
            {team && <span className="text-[12px] text-ink-3">· {r.sales_owner}</span>}
          </div>
          <div className="text-[13px] text-ink-2 mt-0.5 flex flex-wrap items-center gap-x-1.5">
            <span>{r.channel}</span>·<span>{r.package_interest}</span>·<span>{r.budget_band ?? "budget not stated"}</span>
            {r.requested_date && <>·<span className="inline-flex items-center gap-1.5">wants {day(r.requested_date)} <Chip s={r.date_badge} /><Info id="date_badge" /></span></>}
          </div>
        </div>
        <div className="text-right">
          <div className="flex items-center justify-end gap-1.5 text-[13px]">
            <span className="inline-flex gap-0.5" aria-hidden>{[1, 2, 3].map((i) => <span key={i} className={`w-2 h-2 rounded-full ${i <= dots ? "bg-wine" : "bg-stone-soft"}`} />)}</span>
            <span className="font-medium">{r.band} {pct(r.probability)}</span>
          </div>
          <div className="text-[12.5px] text-ink-2 num">≈ {baht(r.expected_fee)} <Info id="expected_fee" /></div>
        </div>
      </div>
      {reasons.length > 0 && <p className="text-[13px] text-ink-2 mt-2 sm:pl-10"><span className="text-ink-3">Why:</span> {reasons.join(" · ")}</p>}
      <div className="flex flex-wrap items-center justify-between gap-2 mt-2 sm:pl-10">
        <p className="text-[13.5px]"><span className="text-ink-3">Next:</span> <span className="font-medium">{r.next_best_action ?? "Follow up"}</span></p>
        {canLog && <MarkContacted id={r.inquiry_id} tick={tick} onDone={onDone} />}
      </div>
    </li>
  );
}

function MarkContacted({ id, tick, onDone, compact = false }: { id: number; tick?: Row; onDone: () => void; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState("Call");
  const [outcome, setOutcome] = useState("Reached");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (tick && !open)
    return (
      <span className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-2">
        <Check size={14} className="text-wine" aria-hidden />Contacted {time(tick.activity_at)} · {tick.activity_type} · {tick.outcome}
        <button className="underline text-ink-3 ml-1" onClick={() => setOpen(true)}>log again</button>
      </span>
    );
  if (!open)
    return <button className={compact ? "btn-ghost !py-1 !px-2 !text-[12px]" : "btn-ghost"} onClick={() => setOpen(true)}>Mark contacted <ChevronDown size={14} aria-hidden /></button>;
  const save = async () => {
    setBusy(true); setErr("");
    try { await post("/api/sales/activity", { inquiry_id: id, activity_type: type, outcome }); setOpen(false); onDone(); }
    catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border border-wine-200 bg-wine-50/60 p-2" role="group" aria-label="Log a follow-up">
      <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
        {TYPES.map(([t, Icon]) => (
          <button key={t} onClick={() => setType(t)} aria-pressed={type === t}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded text-[12.5px] ${type === t ? "bg-wine text-white" : "text-ink-2"}`}>
            <Icon size={13} aria-hidden />{t}
          </button>
        ))}
      </div>
      <select className="input !py-1 !text-[13px]" value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome">
        {OUTCOMES.map((o) => <option key={o}>{o}</option>)}
      </select>
      <button className="btn-primary !py-1" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save"}</button>
      <button className="text-[12.5px] text-ink-3 underline" onClick={() => setOpen(false)}>Cancel</button>
      {err && <span className="text-over text-[12px] w-full">{err}</span>}
    </div>
  );
}

function RankingHelp() {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button className="btn-ghost !py-1 !text-[12.5px]" onClick={() => setOpen(!open)} aria-expanded={open}><HelpCircle size={14} aria-hidden />How is this ranked?</button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-80 rounded-lg bg-surface border border-line shadow-pop p-4 text-[13px] text-ink-2 leading-relaxed">
          Each morning every open couple gets a <b>chance to book</b>, learned from thousands of past couples like them: how they found us, their package and budget, and how the conversation has moved so far.
          Multiplying that chance by the fee their package usually brings gives the <b>expected fee</b>; the list is sorted by it, so the couples most worth your time come first.
          A few couples are kept in the <b>standard queue</b> in the usual order, so we can check that the ranking really helps.
        </div>
      )}
    </div>
  );
}
