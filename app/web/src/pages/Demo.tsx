import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Clock, Loader2, MessageCircle, Play, X } from "lucide-react";
import { api, int, post, Row } from "../api";
import { Card, Chip, PageHead } from "../ui";

// ---------- the couple's form: what the CRM's chat module records from the message ----------
const PACKAGES = [["Full", "Full Planning"], ["Partial", "Partial Planning"], ["DayOf", "Day-of Coordination"], ["Custom", "Custom"]];
const BUDGETS: { text: string; band: string | null; label: string }[] = [
  { text: "6 แสน", band: "500k-1M", label: "6 แสน (500k–1M)" },
  { text: "4 แสน", band: "300k-500k", label: "4 แสน (300k–500k)" },
  { text: "1.2M", band: "1M-1.5M", label: "1.2M (1M–1.5M)" },
  { text: "2 ล้าน", band: "1.5M+", label: "2 ล้าน (1.5M+)" },
  { text: "ยังไม่แน่ใจ", band: null, label: "ยังไม่แน่ใจ: messy, rule R05 flags it" },
];
const thaiDate = (d: string) => new Date(`${d}T00:00`).toLocaleDateString("th-TH-u-ca-gregory", { day: "numeric", month: "short", year: "numeric" });
const template = (pkg: string, date: string, guests: number, b: number) =>
  `สวัสดีค่ะ สนใจแพ็กเกจ ${PACKAGES.find((p) => p[0] === pkg)![1]} วันที่ ${thaiDate(date)} แขกประมาณ ${guests} คน งบ ${BUDGETS[b].text} ยังว่างไหมคะ`;

// ---------- the stations ----------
type Status = "todo" | "running" | "here" | "wait" | "warn" | "fail";
type Station = { id: string; name: string; tag?: string; tech: string; at: string; steps: string[] };
const STATIONS: Station[] = [
  { id: "line", name: "LINE chat", tech: "The couple writes", at: "lg:col-start-1 lg:row-start-1", steps: [] },
  { id: "landing", name: "Landing file", tech: "Signed webhook, raw JSON line", at: "lg:col-start-3 lg:row-start-1", steps: [] },
  { id: "raw_chat", name: "Raw zone: chat", tag: "Bronze", tech: "Hourly JSONL file", at: "lg:col-start-5 lg:row-start-1", steps: ["ingest_files"] },
  { id: "crm", name: "CRM database", tech: "PostgreSQL, the sales system", at: "lg:col-start-1 lg:row-start-2", steps: [] },
  { id: "raw_crm", name: "Raw zone: CRM", tag: "Bronze", tech: "CSV, changed rows only", at: "lg:col-start-5 lg:row-start-2", steps: ["ingest_crm"] },
  { id: "trusted", name: "Trusted zone", tag: "Silver", tech: "Parquet, cleaned and checked by DuckDB", at: "lg:col-start-7 lg:row-start-1 lg:row-span-2", steps: ["silver"] },
  { id: "star", name: "Star schema", tag: "Gold", tech: "PostgreSQL facts + dimensions", at: "lg:col-start-9 lg:row-start-1 lg:row-span-2",
    steps: ["load_stg", "gold_dims", "fact_inquiry", "fact_event_booking", "fact_staff_workload", "fact_sales_activity"] },
  { id: "mart", name: "Marts", tag: "Sales page", tech: "One view per role", at: "lg:col-start-11 lg:row-start-1 lg:row-span-2", steps: ["marts", "reconcile"] },
  { id: "score", name: "Lead score", tag: "ML", tech: "Model v6, scored nightly", at: "lg:col-start-13 lg:row-start-1 lg:row-span-2", steps: [] },
];
// arrow into each station: [from, to, grid placement]
const ARROWS: [string, string, string][] = [
  ["line", "landing", "lg:col-start-2 lg:row-start-1"], ["landing", "raw_chat", "lg:col-start-4 lg:row-start-1"],
  ["raw_chat", "trusted", "lg:col-start-6 lg:row-start-1"], ["crm", "raw_crm", "lg:col-start-2 lg:col-span-3 lg:row-start-2"],
  ["raw_crm", "trusted", "lg:col-start-6 lg:row-start-2"], ["trusted", "star", "lg:col-start-8 lg:row-start-1 lg:row-span-2"],
  ["star", "mart", "lg:col-start-10 lg:row-start-1 lg:row-span-2"], ["mart", "score", "lg:col-start-12 lg:row-start-1 lg:row-span-2"],
];

const hhmm = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "");
const lastAttempt = (steps: Row[], name: string) => [...steps].reverse().find((s) => s.step === name);

/** Where the couple is, station by station, and a one-line note for each. */
function statuses(t: Row | null): Record<string, [Status, string]> {
  const out: Record<string, [Status, string]> = {};
  if (!t) return out;
  const steps: Row[] = t.run?.steps ?? [];
  const runOn = t.run?.status === "running";
  for (const s of STATIONS) {
    const mine = s.steps.map((n) => lastAttempt(steps, n)).filter(Boolean) as Row[];
    const failed = mine.find((x) => x.status === "failed");
    const running = runOn && mine.some((x) => x.status === "running");
    const d = t[s.id === "line" || s.id === "landing" || s.id === "raw_chat" ? "chat" : s.id] ?? {};
    let st: [Status, string] = ["todo", "Not here yet"];
    if (s.id === "line") st = t.crm?.message ? ["here", `Sent ${hhmm(t.crm.message.SentAt)}`] : st;
    else if (s.id === "landing") st = d.found ? ["here", d.zone === "bronze" ? "Moved on to the raw zone" : `Stored ${hhmm(d.received_at)}`] : st;
    else if (s.id === "raw_chat") st = d.zone === "bronze" ? ["here", "Here"] : d.found ? ["wait", `Waits for the hour to close (${hhmm(d.sealed_at)})`] : st;
    else if (s.id === "trusted" && d.quarantined) st = ["fail", `Quarantined by ${d.quarantined.rule_id}`];
    else if (s.id === "trusted" && d.found && d.flags?.length) st = ["warn", `Kept, flagged ${d.flags.join(", ")}`];
    else if (s.id === "score" && !d.found) st = t.star?.found ? ["wait", "Scored in tomorrow's 02:00 run"] : st;
    else if (d.found) st = ["here", s.id === "mart" ? `In ${d.views.join(", ")}` : "Here"];
    else if (d.error && runOn) st = ["running", "Updating…"];
    if (running) st = ["running", "Running now…"];
    else if (failed && st[0] !== "here") st = ["fail", `${failed.step} failed`];
    out[s.id] = st;
  }
  return out;
}

const LOOK: Record<Status, { box: string; icon: JSX.Element; word: string }> = {
  todo: { box: "border-line bg-surface opacity-60", icon: <span className="h-3.5 w-3.5 rounded-full border-2 border-stone-data" />, word: "text-ink-3" },
  running: { box: "border-wine-300 bg-wine-50 animate-pulse", icon: <Loader2 size={15} className="animate-spin text-wine" aria-hidden />, word: "text-wine" },
  here: { box: "border-wine bg-wine-50", icon: <Check size={15} className="text-wine" aria-hidden />, word: "text-wine" },
  wait: { box: "border-tight bg-tight-soft", icon: <Clock size={15} className="text-tight" aria-hidden />, word: "text-tight" },
  warn: { box: "border-tight bg-tight-soft", icon: <AlertTriangle size={15} className="text-tight" aria-hidden />, word: "text-tight" },
  fail: { box: "border-over bg-over-soft", icon: <X size={15} className="text-over" aria-hidden />, word: "text-over" },
};

const arrived = (s?: Status) => s === "here" || s === "warn";
/** Lit when the data went from one stop to the next; moving while the next stop's step runs. */
function Arrow({ from, to, at }: { from?: Status; to?: Status; at: string }) {
  const on = arrived(from) && arrived(to), moving = arrived(from) && to === "running";
  return (
    <div className={`hidden lg:flex items-center ${at}`} aria-hidden>
      <div className={`h-[2px] flex-1 ${moving ? "flow-line" : on ? "bg-wine" : "bg-line"}`} />
      <svg width="7" height="10" className={on ? "fill-wine" : "fill-line"}><path d="M0 0 L7 5 L0 10 z" /></svg>
    </div>
  );
}

// ---------- what the selected station holds: key fields, the ones this layer made are highlighted ----------
type F = [label: string, key: string, made?: boolean];
const FIELDS: Record<string, F[]> = {
  crm: [["Inquiry", "InquiryID"], ["Channel ID", "ChannelID"], ["Package ID", "IndicativePackageID"], ["Wedding date", "RequestedEventDate"],
    ["Budget (as typed)", "BudgetRange"], ["Sales owner ID", "AssignedSalesEmployeeID"], ["Status", "Status"], ["Written", "InquiryDate"]],
  raw_crm: [["Inquiry", "InquiryID"], ["Channel ID", "ChannelID"], ["Package ID", "IndicativePackageID"], ["Wedding date", "RequestedEventDate"],
    ["Budget (as typed)", "BudgetRange"], ["Status", "Status"], ["Load date", "load_date", true], ["Run", "run", true]],
  trusted: [["Inquiry", "inquiry_id"], ["Budget text", "budget_text"], ["Budget band", "budget_band", true], ["Budget midpoint ฿", "budget_midpoint_thb", true],
    ["Wedding date", "requested_event_date"], ["Reply time", "response_band", true], ["Outside office hours", "arrived_outside_hours", true],
    ["Customer messages", "customer_msgs", true], ["Status", "status"]],
  star: [["Inquiry", "InquiryID"], ["Date key", "InquiryDateKey", true], ["Wedding date key", "RequestedEventDateKey", true],
    ["Channel key", "ChannelKey", true], ["→ channel", "ChannelName"], ["Package key", "IndicativePackageKey", true], ["→ package", "PackageName"],
    ["Sales owner key", "SalesOwnerKey", true], ["→ owner", "SalesOwner"], ["Budget band", "BudgetBand"], ["Budget midpoint ฿", "BudgetMidpoint"],
    ["Open", "IsOpen", true], ["Days to the wedding", "LeadDays", true]],
  mart: [["Channel", "channel"], ["Package", "package_interest"], ["Budget band", "budget_band"], ["Sales owner", "sales_owner"],
    ["Date badge", "date_badge"], ["Chance to book", "probability"], ["Rank", "rank"], ["Why", "reason1"], ["Next", "next_best_action"]],
  score: [["Chance to book", "Probability", true], ["Band", "Band", true], ["Rank", "Rank", true], ["Expected fee ฿", "ExpectedFee", true],
    ["Reason 1", "Reason1", true], ["Reason 2", "Reason2", true], ["Reason 3", "Reason3", true], ["Next best action", "NextBestAction", true],
    ["Model", "ModelVersion"], ["Scored on", "ScoreDateKey"]],
};
const show = (v: unknown) => (v === null || v === undefined || v === "" ? "–" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v));
const fmt = (label: string, v: unknown) => (v === null || v === undefined ? "–" : label.includes("฿") ? int(v)
  : label.startsWith("Chance") ? `${Math.round(Number(v) * 100)} %` : show(v));

function Fields({ row, fields }: { row: Row | null | undefined; fields: F[] }) {
  if (!row) return <p className="text-ink-3 text-[13px]">Not here yet.</p>;
  return (
    <dl className="grid sm:grid-cols-2 gap-x-6 gap-y-1 text-[13px]">
      {fields.filter(([, k]) => k in row).map(([l, k, made]) => (
        <div key={k} className={`flex justify-between gap-3 rounded px-2 py-1 ${made ? "bg-wine-50" : ""}`}>
          <dt className="text-ink-3">{l}{made && <span className="text-wine" title="made at this layer"> ●</span>}</dt>
          <dd className="num font-medium text-right break-all">{fmt(l, row[k])}</dd>
        </div>
      ))}
    </dl>
  );
}

const Path = ({ p }: { p?: string }) => (p ? <p className="font-mono text-[12px] text-ink-2 break-all mb-3">{p}</p> : null);

function Detail({ id, t }: { id: string; t: Row }) {
  const chat = t.chat ?? {}, tr = t.trusted ?? {};
  const msg = chat.body?.events?.[0]?.message ?? chat.body?.entry?.[0]?.messaging?.[0]?.message;
  switch (id) {
    case "line":
      return msg ? <blockquote className="text-[16px] leading-relaxed border-l-4 border-wine pl-4">{msg.text}</blockquote>
        : <p className="text-ink-3 text-[13px]">No message found for this couple.</p>;
    case "landing":
      return chat.found ? (<>
        <Path p={chat.file} />
        <p className="text-[13px] text-ink-2 mb-2">The receiver checked LINE's HMAC signature, then appended the body <b>unchanged</b> to this hour's file. SHA-256 <span className="font-mono text-[12px]">{chat.body_sha256?.slice(0, 16)}…</span></p>
        <pre className="text-[11.5px] bg-page border border-line rounded-lg p-3 max-h-64 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(chat.body, null, 2)}</pre>
      </>) : <p className="text-ink-3 text-[13px]">Not here yet.</p>;
    case "raw_chat":
      return chat.zone === "bronze" ? (<><Path p={chat.file} /><p className="text-[13px] text-ink-2">The closed hourly file was moved here once, with its size, line count and SHA-256 logged in <span className="font-mono">etl.file_checkpoint</span>.</p></>)
        : chat.found ? <p className="text-[13px] text-ink-2">Chat files are batches of one hour. This one closes at <b>{hhmm(chat.sealed_at)}</b>; the first run after that moves it here. The CRM copy of the inquiry does not wait, so the couple still reaches the sales page in this run.</p>
        : <p className="text-ink-3 text-[13px]">Not here yet.</p>;
    case "raw_crm":
      return t.raw_crm?.found ? (<><Path p={t.raw_crm.file} /><p className="text-[13px] text-ink-2 mb-3">Only the rows changed since the last run are copied, as text, exactly as the CRM had them. The manifest holds the file's SHA-256.</p><Fields row={t.raw_crm.row} fields={FIELDS.raw_crm} /></>)
        : <p className="text-ink-3 text-[13px]">Not here yet: the next run's ingest_crm step copies it.</p>;
    case "trusted": {
      const intents = tr.chat ? Object.keys(tr.chat).filter((k) => k.startsWith("intent_") && tr.chat[k] === 1).map((k) => k.slice(7).replace(/_/g, " ")) : [];
      const failed = (tr.dq ?? []).filter((r: Row) => !r.passed);
      return (<>
        {tr.quarantined && <p className="text-over text-[13px] mb-3">Quarantined by rule {tr.quarantined.rule_id}: {tr.quarantined.reason}. Kept aside, not published.</p>}
        {tr.flags?.includes("R05") && <p className="text-tight text-[13px] mb-3 flex gap-1.5"><AlertTriangle size={15} className="shrink-0 mt-0.5" aria-hidden />Rule R05 (warning): the budget text could not be read as a band, so it is "Unknown". The row is kept and counted on the Data health page.</p>}
        <Fields row={tr.row} fields={FIELDS.trusted} />
        {tr.chat && (
          <div className="mt-4 text-[13px]">
            <p className="label mb-1">The message, as features</p>
            {tr.chat.lang ? <p>Language <b>{tr.chat.lang}</b> · {tr.chat.text_len} characters · question {show(tr.chat.has_question)} · intents: <b>{intents.join(", ") || "none"}</b>{tr.chat.tracking_code && <> · ad code <b>{tr.chat.tracking_code}</b></>}</p>
              : <p className="text-ink-3">Arrive with the closed hourly chat file. Until then the CRM copy of the message stands in (rule R24 notes the gap).</p>}
            <p className="text-ink-3 mt-1">The text itself is not kept here: it stays in the restricted zone, which only the Sales role can read.</p>
          </div>
        )}
        {tr.dq?.length > 0 && <p className="text-[12.5px] text-ink-3 mt-3">This run: {tr.dq.length} rules checked {int(tr.dq[0].rows_checked)} inquiries; {failed.length ? failed.map((r: Row) => `${r.rule_id} ${r.rows_failed}`).join(", ") : "none failed"}.</p>}
      </>);
    }
    case "mart":
      return t.mart?.found ? (<>
        <p className="text-[13px] text-ink-2 mb-3">In <b>{t.mart.views.join(" and ")}</b>, which feed <a className="text-wine underline" href="#/today">Sales · Today</a>.</p>
        <Fields row={t.mart.row} fields={FIELDS.mart} />
      </>) : <p className="text-ink-3 text-[13px]">Not here yet.</p>;
    case "score":
      return t.score?.found ? <Fields row={t.score.row} fields={FIELDS.score} />
        : <p className="text-[13px] text-ink-2">The model scores every open couple each morning, using only data from before midnight, so it never sees the future. This couple gets a score in tomorrow's 02:00 run.</p>;
    default:
      return <Fields row={t[id]?.row} fields={FIELDS[id] ?? []} />;
  }
}

/** The same couple, one layer after another: what each layer turned the data into. */
function Changes({ t }: { t: Row }) {
  const c = t.crm?.row, s = t.trusted?.row, g = t.star?.row, ch = t.trusted?.chat;
  const lines: [string, (string | undefined)[]][] = [
    ["Budget", [c && `"${c.BudgetRange}" typed`, s && `band ${s.budget_band}`, g && (g.BudgetMidpoint ? `midpoint ฿${int(g.BudgetMidpoint)}` : "no midpoint")]],
    ["Channel", [c && `ChannelID ${c.ChannelID}`, g && `ChannelKey ${g.ChannelKey} · ${g.ChannelName}`]],
    ["Wedding date", [c && show(c.RequestedEventDate), g && `DateKey ${g.RequestedEventDateKey}`]],
    ["Message", [ch && "text", ch?.lang ? `${ch.lang}, ${ch.text_len} chars, intent flags` : ch ? "features after the hour closes" : undefined]],
    ["Name and phone", [c && "CRM", "restricted zone, Sales role only"]],
  ];
  return (
    <ul className="grid gap-2 text-[13px]">
      {lines.map(([k, v]) => (
        <li key={k}><span className="text-ink-3">{k}: </span>{v.map((x, i) => <span key={i}>{i > 0 && <span className="text-wine font-semibold"> → </span>}<span className={x ? "" : "text-ink-3"}>{x ?? "…"}</span></span>)}</li>
      ))}
    </ul>
  );
}

export default function Demo() {
  const [pkg, setPkg] = useState("Full");
  const [date, setDate] = useState("2027-02-13");
  const [guests, setGuests] = useState(300);
  const [budget, setBudget] = useState(0);
  const [edited, setEdited] = useState<string | null>(null);   // the message, once typed over
  const [sent, setSent] = useState<{ id: number; messy: boolean }[]>([]);
  const [id, setId] = useState<number | null>(null);
  const [picks, setPicks] = useState<Row>({});
  const [t, setT] = useState<Row | null>(null);
  const [sel, setSel] = useState("line");
  const [watch, setWatch] = useState<{ after: number; until: number } | null>(null);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const prev = useRef<Record<string, Status>>({});
  const text = edited ?? template(pkg, date, guests, budget);
  const field = <T,>(set: (v: T) => void) => (v: T) => { set(v); setEdited(null); };

  useEffect(() => { api("/api/demo/latest").then((p) => { setPicks(p); setId((x) => x ?? p.newest); }).catch((e) => setErr(e.message)); }, []);
  const load = (x = id) => x && api(`/api/demo/trace?inquiry_id=${x}`).then(setT).catch((e) => setErr(e.message));
  useEffect(() => { prev.current = {}; load(); }, [id]);   // keep the old couple on screen until the new one loads (no jump)
  useEffect(() => { // while a run started here is going: poll, then stop once it has finished
    if (!watch) return;
    const tick = setInterval(() => load(), 2000);
    return () => clearInterval(tick);
  }, [watch, id]);
  const st = statuses(t);
  useEffect(() => { // follow the couple: select a station the moment it turns green
    const now = Object.fromEntries(Object.entries(st).map(([k, v]) => [k, v[0]]));
    const fresh = STATIONS.filter((s) => now[s.id] === "here" && prev.current[s.id] && prev.current[s.id] !== "here");
    if (fresh.length) setSel(fresh[fresh.length - 1].id);
    prev.current = now;
    if (watch && t?.run && (t.run.run_id > watch.after && t.run.status !== "running" || Date.now() > watch.until)) setWatch(null);
  }, [t]);

  const send = async () => {
    setBusy("line"); setErr("");
    try {
      const b = BUDGETS[budget];
      const r = await post("/api/demo/line", { text, package: pkg, requested_date: date, guests, budget: b.band, budget_text: b.text });
      setSent((s) => [...s, { id: r.inquiry_id, messy: !b.band }]);
      setSel("line"); setId(r.inquiry_id);
    } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const run = async () => {
    setBusy("run"); setErr("");
    try {
      await post("/api/demo/run", {});
      setWatch({ after: t?.run?.run_id ?? 0, until: Date.now() + 4 * 60_000 });
    } catch (e: any) { setErr(e.message); }
    setBusy("");
  };
  const r = t?.run;
  const secs = r?.started_at ? Math.round(((r.finished_at ? new Date(r.finished_at) : new Date()).getTime() - new Date(r.started_at).getTime()) / 1000) : null;
  const station = STATIONS.find((s) => s.id === sel)!;

  return (
    <>
      <PageHead title="Follow one couple" sub="From a LINE message to the sales list: every stop the data makes, live." />
      <div className="grid lg:grid-cols-[1.5fr_1fr] gap-5 mb-5">
        <Card title="1 · A couple writes on LINE" question="The CRM's chat module records the package, date and budget; LINE sends the signed webhook to the receiver.">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
            <label className="grid gap-1"><span className="label">Package</span>
              <select className="input w-full min-w-0" value={pkg} onChange={(e) => field(setPkg)(e.target.value)}>{PACKAGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
            <label className="grid gap-1"><span className="label">Wedding date</span>
              <input type="date" className="input w-full min-w-0" value={date} onChange={(e) => e.target.value && field(setDate)(e.target.value)} /></label>
            <label className="grid gap-1"><span className="label">Guests</span>
              <input type="number" className="input w-full min-w-0" min={20} max={2000} value={guests} onChange={(e) => field(setGuests)(Number(e.target.value))} /></label>
            <label className="grid gap-1"><span className="label">Budget, as typed</span>
              <select className="input w-full min-w-0" value={budget} onChange={(e) => field(setBudget)(Number(e.target.value))}>{BUDGETS.map((b, i) => <option key={i} value={i}>{b.label}</option>)}</select></label>
          </div>
          <textarea className="input w-full h-20" value={text} onChange={(e) => setEdited(e.target.value)} maxLength={500} aria-label="Message text" />
          <button className="btn-primary mt-2" onClick={send} disabled={busy === "line" || !text.trim() || guests < 20 || guests > 2000}><MessageCircle size={15} aria-hidden />Send on LINE</button>
        </Card>
        <div className="grid gap-5">
          <Card title="2 · Run the nightly pipeline now" question="Through Airflow (aisleco_daily), or the command line if Airflow is down.">
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-primary" onClick={run} disabled={busy === "run" || !!watch || r?.status === "running"}><Play size={15} aria-hidden />Run now</button>
              {r && <span className="flex items-center gap-2 text-[13px]"><span className="font-medium">Run #{r.run_id}</span>
                <Chip s={r.status === "success" ? "OK" : r.status === "failed" ? "Over" : "Tight"}>{r.status}</Chip>
                <span className="text-ink-3 num">{secs !== null && `${secs} s`} · as of {r.asof_date}</span></span>}
              {watch && r?.run_id === watch.after && <span className="text-[13px] text-ink-3">starting…</span>}
            </div>
            {r?.status === "failed" && <p className="text-over text-[13px] mt-2">{r.message}</p>}
          </Card>
          <Card title="Following couple" question="Any inquiry number works, with or without a run.">
            <div className="flex flex-wrap items-center gap-2">
              <input type="number" className="input w-28 num" value={id ?? ""} onChange={(e) => setId(Number(e.target.value) || null)} aria-label="Inquiry number" />
              {picks.newest && <button className="btn-ghost" onClick={() => setId(picks.newest)}>Newest #{picks.newest}</button>}
              {picks.scored && <button className="btn-ghost" onClick={() => setId(picks.scored)}>Yesterday's, scored #{picks.scored}</button>}
              {sent.map((s) => <button key={s.id} className={`btn-ghost ${s.id === id ? "border-wine text-wine" : ""}`} onClick={() => setId(s.id)}>#{s.id} {s.messy ? "messy" : "sent"}</button>)}
            </div>
          </Card>
        </div>
      </div>
      {err && <p className="text-over text-[13px] mb-4" role="alert">{err}</p>}

      <Card title={id ? `Where is couple #${id} now?` : "Where is the couple now?"} question="Click a stop to see the couple's own record there. A stop is selected by itself when the couple arrives.">
        <div className="grid gap-2 lg:gap-y-3 lg:grid-cols-[repeat(6,minmax(0,1fr)_22px)_minmax(0,1fr)]">
          {STATIONS.map((s) => {
            const [status, note] = st[s.id] ?? ["todo", "…"];
            const look = LOOK[status];
            return (
              <button key={s.id} onClick={() => setSel(s.id)} aria-pressed={sel === s.id}
                className={`text-left rounded-lg border-2 p-2.5 transition ${look.box} ${s.at} ${sel === s.id ? "ring-2 ring-wine ring-offset-2 opacity-100" : ""}`}>
                <span className="flex items-start justify-between gap-1"><span className="text-[13px] font-semibold leading-tight">{s.name}</span>{look.icon}</span>
                {s.tag && <span className="block text-[10.5px] uppercase tracking-wide text-ink-3 mt-0.5">{s.tag}</span>}
                <span className="block text-[11.5px] text-ink-3 mt-1 leading-snug">{s.tech}</span>
                <span className={`block text-[12px] font-medium mt-1.5 leading-snug ${look.word}`}>{note}</span>
              </button>
            );
          })}
          {ARROWS.map(([from, to, at]) => <Arrow key={at} at={at} from={st[from]?.[0]} to={st[to]?.[0]} />)}
        </div>
      </Card>

      {t && (
        <div className="grid lg:grid-cols-[1fr_400px] gap-5 mt-5">
          <Card title={`${station.name}${station.tag ? ` · ${station.tag}` : ""}`} question={station.tech}><Detail id={sel} t={t} /></Card>
          <Card title="What each layer did to the data" question="Read left to right: CRM → trusted zone → star schema."><Changes t={t} /></Card>
        </div>
      )}
    </>
  );
}
