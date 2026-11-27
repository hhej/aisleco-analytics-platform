// Builds ../AisleCo_Final_Presentation.pptx and ../PRESENTER_GUIDE.md.
//   cd docs/v2/deliverables/deck && npm install && node build.js
// Numbers come from numbers.json (frozen by scripts/deck_numbers.py); scripts, presenters and Q&A from content.js.
const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");
const sizeOf = require("image-size");

const HERE = __dirname;
const ROOT = path.resolve(HERE, "../../../..");
const IMG = (f) => path.join(ROOT, "docs/v2/img", f);
const FIG = (f) => path.join(ROOT, "docs/v2/deliverables/report_figures", f);
const CHART = (f) => path.join(HERE, "charts", f);
const N = JSON.parse(fs.readFileSync(path.join(HERE, "numbers.json"), "utf8"));
const { members, scripts, briefs, qa } = require("./content.js")(N);

const THEME = {
  name: "Aisle and Co", headFontFace: "Cambria", bodyFontFace: "Calibri",
  colors: { dk1: "1F1F1F", lt1: "FFFFFF", dk2: "4A1D2A", lt2: "F3F3F4", accent1: "9E3B52", accent2: "8C8F94",
            accent3: "F4E8EB", accent4: "5F6368", accent5: "B7791F", accent6: "2E7D5B", hlink: "9E3B52", folHlink: "4A1D2A" },
};
const HEX = THEME.colors;
const W = 13.333, H = 7.5, X0 = 0.6, XW = W - 2 * X0;
const fmt = (n) => Number(n).toLocaleString("en-US");
const pct = (x) => `${Math.round(x)}%`;

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";
pres.theme = { headFontFace: THEME.headFontFace, bodyFontFace: THEME.bodyFontFace };
pres.title = "Aisle and Co. final presentation";
pres.author = "Aisle and Co. team";
// plain hex colours, so the deck renders the same in PowerPoint, Keynote and Google Slides without a theme patch
const C = { text1: HEX.dk1, text2: HEX.dk2, background1: HEX.lt1, background2: HEX.lt2, accent1: HEX.accent1, accent2: HEX.accent2,
            accent3: HEX.accent3, accent4: HEX.accent4, accent5: HEX.accent5, accent6: HEX.accent6 };

// ---------- layouts ----------
pres.defineSlideMaster({
  title: "DARK", background: { color: C.text2 },
  objects: [
    { placeholder: { options: { name: "title", type: "title", x: 0.9, y: 1.7, w: 11.5, h: 2.0, fontFace: "Cambria", fontSize: 44, bold: true,
                                color: C.background1, valign: "bottom", align: "left", margin: 0 }, text: "" } },
    { placeholder: { options: { name: "body", type: "body", x: 0.9, y: 4.0, w: 11.5, h: 2.6, fontFace: "Calibri", fontSize: 18,
                                color: C.background1, valign: "top", margin: 0 }, text: "" } },
  ],
});
const contentObjects = (footer) => [
  { placeholder: { options: { name: "title", type: "title", x: X0, y: 0.38, w: 9.9, h: 0.85, fontFace: "Cambria", fontSize: 32, bold: true,
                              color: C.text1, valign: "middle", align: "left", margin: 0 }, text: "" } },
  { text: { text: footer, options: { x: X0, y: 7.02, w: 8, h: 0.3, fontFace: "Calibri", fontSize: 10, color: C.accent4, margin: 0 } } },
];
pres.defineSlideMaster({ title: "CONTENT", background: { color: C.background1 }, objects: contentObjects(`Aisle & Co. · numbers as of ${N.asof_text}`),
                         slideNumber: { x: 12.2, y: 7.0, w: 0.6, h: 0.3, fontFace: "Calibri", fontSize: 10, color: C.accent4, align: "right" } });
pres.defineSlideMaster({ title: "BACKUP", background: { color: C.background1 }, objects: contentObjects("Backup slide · not presented · for questions"),
                         slideNumber: { x: 12.2, y: 7.0, w: 0.6, h: 0.3, fontFace: "Calibri", fontSize: 10, color: C.accent4, align: "right" } });

// ---------- helpers ----------
const box = (o) => Object.assign({ isTextBox: true, margin: 0, fontFace: "Calibri", color: C.text1, valign: "top" }, o);
function text(s, t, o) { s.addText(t, box(o)); }
function tag(s, label) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 10.75, y: 0.55, w: 2.0, h: 0.42, rectRadius: 0.21, fill: { color: C.accent3 }, line: { color: C.accent3 }, objectName: "rubric tag" });
  text(s, label, { x: 10.75, y: 0.55, w: 2.0, h: 0.42, fontSize: 12, bold: true, color: C.accent1, align: "center", valign: "middle", objectName: "rubric tag text" });
}
function sub(s, t, y = 1.28) { text(s, t, { x: X0, y, w: 11.6, h: 0.4, fontSize: 16, color: C.accent4 }); }
function source(s, t) { text(s, t, { x: X0, y: 6.68, w: 11.6, h: 0.3, fontSize: 10, color: C.accent4 }); }
function card(s, x, y, w, h, fill = C.background2) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: fill }, line: { color: fill } });
}
function stat(s, x, y, w, big, label, note) {
  text(s, big, { x, y, w, h: 0.75, fontFace: "Cambria", fontSize: 36, bold: true, color: C.accent1 });
  text(s, label, { x, y: y + 0.75, w, h: 0.4, fontSize: 15, bold: true });
  if (note) text(s, note, { x, y: y + 1.12, w, h: 0.6, fontSize: 13, color: C.accent4 });
}
function circle(s, n, x, y, d = 0.42, fill = C.accent1) {
  s.addShape(pres.shapes.OVAL, { x, y, w: d, h: d, fill: { color: fill }, line: { color: fill } });
  text(s, String(n), { x, y, w: d, h: d, fontSize: 14, bold: true, color: C.background1, align: "center", valign: "middle" });
}
function image(s, file, x, y, w, h) {  // fit inside w x h, keep aspect ratio, centre
  const d = sizeOf(file), r = d.width / d.height;
  let iw = w, ih = w / r;
  if (ih > h) { ih = h; iw = h * r; }
  s.addImage({ path: file, x: x + (w - iw) / 2, y: y + (h - ih) / 2, w: iw, h: ih, altText: path.basename(file) });
  return { x: x + (w - iw) / 2, y: y + (h - ih) / 2, w: iw, h: ih };
}
function table(s, rows, o) {
  const head = rows[0].map((t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.accent1 } } }));
  const body = rows.slice(1).map((r, i) => r.map((t) => (typeof t === "object" ? t : { text: String(t), options: { fill: { color: i % 2 ? C.background2 : C.background1 } } })));
  s.addTable([head, ...body], Object.assign({ x: X0, y: 1.5, w: XW, fontFace: "Calibri", fontSize: 13, color: C.text1, valign: "middle",
    border: { type: "solid", pt: 0.75, color: "D9D9DC" }, margin: [0.05, 0.08, 0.05, 0.08], autoPage: false }, o));
}
function arrow(s, x1, y1, x2, y2, color = C.accent4) {
  s.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001, h: Math.abs(y2 - y1) || 0.001,
    flipH: x2 < x1, flipV: y2 < y1, line: { color, width: 1.5, endArrowType: "triangle" } });
}
function line(s, x1, y1, x2, y2, color = C.accent2) {
  s.addShape(pres.shapes.LINE, { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1) || 0.001, h: Math.abs(y2 - y1) || 0.001,
    flipV: (x2 - x1) * (y2 - y1) < 0, line: { color, width: 1.25 } });
}
const NAME = (k) => members[k];
function notes(s, n) {
  const sc = scripts[n];
  if (!sc) return;
  const m = Math.floor(sc.sec / 60), sec = String(sc.sec % 60).padStart(2, "0");
  s.addNotes(`${NAME(sc.who)} · ${m}:${sec}\n\n${sc.th}${sc.handoff ? `\n\nส่งต่อ: ${sc.handoff}` : ""}`);
}
let slideNo = 0;
function content(tagLabel, title, sec = "Main") {
  const s = pres.addSlide({ masterName: "CONTENT", sectionTitle: sec });
  slideNo += 1;
  s.addText(title, { placeholder: "title" });
  if (tagLabel) tag(s, tagLabel);
  return s;
}
function backup(title, sec = "Backup") {
  const s = pres.addSlide({ masterName: "BACKUP", sectionTitle: sec });
  slideNo += 1;
  s.addText(title, { placeholder: "title" });
  tag(s, "Backup");
  return s;
}

const P1 = N.p1, P2 = N.p2, F = N.forecast, MO = N.model, L = N.lost;
const capt = MO.captured_pct;

// ================= MAIN =================
pres.addSection({ title: "Main" });

// 1 title
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "Main" }); slideNo += 1;
  s.addText("Every couple answered.\nEvery date staffed.", { placeholder: "title" });
  s.addText([
    { text: "A data platform for Aisle & Co., a Bangkok wedding planner", options: { breakLine: true } },
    { text: "MADT 8102 · Final project · 29 November 2026", options: { fontSize: 15, breakLine: true } },
    { text: " ", options: { fontSize: 10, breakLine: true } },
    { text: [members.m2, members.m3, members.m4, members.m5, members.you].join("  ·  "), options: { fontSize: 15 } },
  ], { placeholder: "body" });
  notes(s, 1);
}

// 2 the company + agenda
{
  const s = content("Business problem", "Aisle & Co. plans 100 weddings a year");
  sub(s, "A fictional Bangkok wedding planner. We play the analytics team it hired to build its data platform.");
  const st = [["104", "weddings held in 2025", "planning + wedding-day service"],
              [fmt(P2.inquiries), "couples wrote in 12 months", `${P2.from} – ${P2.to}`],
              ["15", "staff", "3 sales · 10 planning and wedding day · 2 office"],
              ["6", "channels", "LINE, Facebook, Instagram, TikTok, website, wedding fairs"]];
  st.forEach(([b, l, n], i) => { card(s, X0 + i * 3.06, 2.0, 2.86, 2.15); stat(s, X0 + 0.25 + i * 3.06, 2.15, 2.4, b, l, n); });
  text(s, "Today's talk follows the capstone deliverables", { x: X0, y: 4.65, w: 11, h: 0.4, fontSize: 16, bold: true });
  const ag = ["Business problem", "Platform design", "Data sources", "Pipeline", "Dashboards", "AI / ML", "Demo + actions"];
  ag.forEach((a, i) => {
    const x = X0 + i * 1.733;
    if (i < ag.length - 1) line(s, x + 0.87 + 0.3, 5.36, x + 1.733 + 0.87 - 0.3, 5.36, C.accent2);
    circle(s, i + 1, x + 0.66, 5.15);
    text(s, a, { x, y: 5.7, w: 1.733, h: 0.4, fontSize: 13, align: "center" });
  });
  notes(s, 2);
}

// 3 problem 1
{
  const s = content("Business problem", "Overloaded Oct–Dec, half idle May–Aug");
  sub(s, `Staff hours as a share of contracted hours, planning and wedding-day teams, ${P1.year}. Planning work comes weeks before each wedding.`);
  image(s, CHART("p1_utilization.png"), X0, 1.8, 7.6, 4.6);
  const x = 8.75;
  stat(s, x, 1.85, 3.9, `${P1.peak_share_pct}%`, `of ${P1.year}'s ${P1.weddings} weddings`, "fell in November – February");
  stat(s, x, 3.45, 3.9, String(P1.person_weeks_over), "person-weeks above contract", `${fmt(P1.overtime_hours)} overtime hours in ${P1.year}`);
  stat(s, x, 5.05, 3.9, String(L["Date unavailable"].couples), "couples turned away in 12 months", `date was full · ≈ ฿${L["Date unavailable"].fee_m}M fees at stake (estimate)`);
  source(s, "Source: mart_exec.v_monthly_kpis, mart_exec.v_lost_value. Estimate = couples turned away × median fee × quote-to-booking rate.");
  notes(s, 3);
}

// 4 problem 2
{
  const s = content("Business problem", "Many couples write, few book");
  sub(s, `What happened to the couples who wrote to us, ${P2.from} – ${P2.to}`);
  image(s, CHART("p2_funnel.png"), X0, 1.85, 7.6, 3.6);
  const x = 8.75;
  stat(s, x, 1.85, 3.9, `${Math.floor(P2.conv_min_pct)}–${Math.round(P2.conv_max_pct)}%`, "of decided inquiries book", `each year, ${P2.conv_years}`);
  stat(s, x, 3.45, 3.9, String(L["No reply"].couples), "couples stopped replying", `closed after 30 days silent; ${N.no_reply_quoted} of them had a quote · ≈ ฿${L["No reply"].fee_m}M at stake (estimate)`);
  card(s, X0, 5.55, 7.6, 1.0, C.accent3);
  text(s, [{ text: "How sales work today: ", options: { bold: true } },
           { text: "two salespeople answer the newest message first. A couple who got a quote and went quiet is rarely called again." }],
       { x: X0 + 0.2, y: 5.62, w: 7.2, h: 0.85, fontSize: 15, valign: "middle" });
  stat(s, x, 5.05, 3.9, `≈ ฿${N.at_stake_total_m}M`, "a year at stake, both problems", "turned away + went silent (estimates)");
  source(s, "Source: mart_sales.v_funnel, mart_exec.v_monthly_kpis, mart_exec.v_lost_value. Estimate = quoted couples × median fee × quote-to-booking rate.");
  notes(s, 4);
}

// 5 stakeholders and success metrics
{
  const s = content("Business problem", "Who decides what, and how we measure it");
  sub(s, "Stakeholders, their decision, and the success metric each one is judged by");
  table(s, [
    ["Stakeholder", "Decision", "Success metric", "Where we are"],
    ["Salesperson (May, Ken)", "Whom to contact this morning, and what to say", "Share of next-60-day bookings that were in the day's top 15", `${capt["Our model"]}% with the list in testing (newest-first: ${capt["Today: newest first"]}%)`],
    ["Sales manager (Nok)", "Is the list worked? Are replies fast?", "Median first reply time; follow-ups done", `${N.reply.median_hours} h in ${N.reply.month} (target 60 min)`],
    ["Operations (Som)", "Book freelancers; which dates we can still sell", "Freelance hours needed, next 12 weeks; person-weeks over 100%", `${F.freelance_hours_12w} h; ${N.capacity.person_weeks_over_4w} person-weeks over in the next 4 weeks`],
    ["Owner (Ann)", "Hiring, peak-date pricing", "Conversion; couples turned away; fee revenue", `${Math.floor(P2.conv_min_pct)}–${Math.round(P2.conv_max_pct)}% a year; ${L["Date unavailable"].couples} turned away in 12 months`],
    ["Data team (Dev)", "Is the data right and on time?", "Nightly run passed; quality rules failing", `run #${N.last_run.run_id} passed in ${Math.round(N.last_run.seconds)} s`],
  ], { y: 1.85, colW: [2.3, 3.2, 3.4, 3.233], fontSize: 14, rowH: 0.62 });
  source(s, "Every metric has a written formula in the data catalog (42 KPIs), shown as a tooltip on the dashboards.");
  notes(s, 5);
}

// 6 architecture (lecturer's six layers)
{
  const s = content("Platform design", "One platform, six layers");
  sub(s, "Laid out on the course's Unified Data Infrastructure picture (Chapter 1)");
  const cols = [
    ["Sources", ["CRM database", "LINE, Facebook, Instagram chat", "Wedding-fair tablets", "Holiday + auspicious calendars"]],
    ["Ingestion and transport", ["Nightly incremental load", "Webhook receiver (push)", "File drop", "API pull"]],
    ["Storage", ["Data lake: files, Parquet", "Data warehouse: PostgreSQL"]],
    ["Query and processing", ["DuckDB on the lake", "PostgreSQL SQL on the warehouse"]],
    ["Transformation", ["One SQL file per table", "37 quality rules", "Airflow runs it at 02:00"]],
    ["Analysis and output", ["4 role dashboards", "Lead model", "Staffing forecast"]],
  ];
  const cw = 1.93, gap = 0.1, y = 1.85, h = 3.35;
  cols.forEach(([head, items], i) => {
    const x = X0 + i * (cw + gap);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: cw, h, rectRadius: 0.08, fill: { color: C.background2 }, line: { color: C.background2 } });
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: cw, h: 0.7, rectRadius: 0.08, fill: { color: i === 2 || i === 4 ? C.accent1 : C.text2 }, line: { color: C.text2 } });
    text(s, head, { x: x + 0.08, y, w: cw - 0.16, h: 0.7, fontSize: 14, bold: true, color: C.background1, align: "center", valign: "middle" });
    text(s, items.map((t, j) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: j < items.length - 1 } })),
         { x: x + 0.1, y: y + 0.85, w: cw - 0.2, h: h - 0.95, fontSize: 13, paraSpaceAfter: 6 });
    if (i < cols.length - 1) arrow(s, x + cw - 0.02, y + h / 2, x + cw + gap + 0.02, y + h / 2, C.accent1);
  });
  const gov = ["Discovery: data catalog", "Governance: masked personal data", "Observability: run log, alerts", "Security: database grants per role"];
  card(s, X0, 5.35, XW, 0.55, C.accent3);
  text(s, gov.join("    ·    "), { x: X0 + 0.2, y: 5.35, w: XW - 0.4, h: 0.55, fontSize: 12, color: C.accent1, bold: true, valign: "middle", align: "center" });
  text(s, [{ text: "Where it runs: ", options: { bold: true } },
           { text: "self-hosted on one server, open-source software only, no licence fees. For today's demo this laptop is the server. Each part has a cloud equivalent if the business grows (backup A4)." }],
       { x: X0, y: 6.05, w: XW, h: 0.55, fontSize: 14 });
  notes(s, 6);
}

// 7 sources
{
  const s = content("Data sources", "Four sources, each loaded its own way");
  sub(s, "All four source types the capstone asks for; the method follows what each source system allows");
  const t = [
    ["", "Source", "Data type", "How it arrives", "How often", "Why this way"],
    ["1", "CRM database (inquiries, bookings, staff contracts)", "Structured", "Incremental load: only rows changed since the last run (UpdatedAt), plus a key list to catch deletes", "Nightly", "Few rows change each day; reloading everything wastes time"],
    ["2", "Chat: LINE, Facebook, Instagram", "Semi-structured JSON; the message text is unstructured", "Push: the platform sends each message to our webhook; the raw body is saved untouched", "Every message", "Platforms push and resend on errors; duplicates are removed later"],
    ["3", "Wedding-fair tablets", "Semi-structured JSON files", "File drop when the tablet is back online; checksum on arrival", "After each fair", "No internet at the booth"],
    ["4", "Google Thai holiday calendar + auspicious-date file", "External", "Pull from Google's public calendar; yearly file", "Monthly / yearly", "Outside data that changes rarely; last good copy kept"],
  ];
  table(s, t, { y: 1.85, colW: [0.4, 2.35, 2.0, 3.55, 1.25, 2.583], fontSize: 13, rowH: [0.45, 0.95, 0.95, 0.8, 0.8] });
  source(s, "Unstructured text path: chat text → phone and e-mail masked → rule-based intent tags (asked the price, asked if a date is free, …) → model features. TF-IDF was tested and dropped.");
  notes(s, 7);
}

// 8 OLTP
{
  const s = content("Platform design", "The CRM database: 13 normalized tables");
  sub(s, "The OLTP schema behind the business. Grey = master tables, wine = transaction tables (Chapter 3)");
  image(s, CHART("er_oltp.png"), X0, 1.8, 7.7, 4.85);
  const x = 8.6;
  text(s, "Master tables (8): change rarely", { x, y: 1.85, w: 4.1, h: 0.35, fontSize: 15, bold: true });
  text(s, "Customer, CustomerContact, CustomerChannelAccount, MarketingChannel, Package, EmployeeRole, Employee, EmployeeContract",
       { x, y: 2.2, w: 4.1, h: 0.9, fontSize: 13, color: C.accent4 });
  text(s, "Transaction tables (5): something happened", { x, y: 3.2, w: 4.1, h: 0.35, fontSize: 15, bold: true });
  text(s, "Inquiry, ChatMessage, Event, EventEmployeeAssignment, SalesActivity", { x, y: 3.55, w: 4.1, h: 0.6, fontSize: 13, color: C.accent4 });
  card(s, x, 4.35, 4.1, 2.25, C.accent3);
  text(s, [
    { text: "Every table has a primary key and CreatedAt / UpdatedAt; the incremental load reads UpdatedAt.", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "One deliberate copy: the price at the moment of sale is stored on Event, because package prices change later (Chapter 3).", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "Third normal form, except three lookup columns kept inline (backup A5).", options: { bullet: { indent: 14 } } },
  ], { x: x + 0.15, y: 4.45, w: 3.8, h: 2.05, fontSize: 13, paraSpaceAfter: 6 });
  notes(s, 8);
}

// 9 lake
{
  const s = content("Platform design", "The data lake: raw stays raw");
  sub(s, "A zone-based lake (Chapter 4). Bronze and Silver are the folder names in our code.");
  const z = [["Landing", "transient", "files as sent"], ["Raw zone", "Bronze", "exactly as received, never edited; manifest + SHA-256 checksum"],
             ["Trusted zone", "Silver", "cleaned, de-duplicated, times converted, no personal data; Parquet"]];
  z.forEach(([h, k, d], i) => {
    const x = X0 + i * 2.55, y = 1.95;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 2.3, h: 2.05, rectRadius: 0.08, fill: { color: i === 2 ? C.accent1 : C.text2 }, line: { color: C.text2 } });
    text(s, h, { x: x + 0.15, y: y + 0.12, w: 2.0, h: 0.4, fontSize: 16, bold: true, color: C.background1 });
    text(s, k, { x: x + 0.15, y: y + 0.5, w: 2.0, h: 0.3, fontSize: 12, italic: true, color: C.background1 });
    text(s, d, { x: x + 0.15, y: y + 0.85, w: 2.0, h: 1.15, fontSize: 12, color: C.background1 });
    if (i < 2) arrow(s, x + 2.3, y + 1.0, x + 2.55, y + 1.0, C.accent1);
  });
  const side = [["Refined zone (restricted)", "masked chat text and names; read only by the model job and the Sales-only table"], ["Quarantine", "rejected rows, each with the rule and reason"], ["Sandbox", "ad-hoc analysis, never feeds the warehouse"]];
  side.forEach(([h, d], i) => {
    const x = X0 + i * 2.55, y = 4.4;
    card(s, x, y, 2.3, 1.15);
    text(s, h, { x: x + 0.12, y: y + 0.1, w: 2.06, h: 0.35, fontSize: 13, bold: true });
    text(s, d, { x: x + 0.12, y: y + 0.45, w: 2.06, h: 0.65, fontSize: 12, color: C.accent4 });
  });
  arrow(s, X0 + 1.15 + 2.55, 4.0, X0 + 1.15 + 2.55, 4.4, C.accent2);
  const c = N.dq.caught, x = 8.4;
  card(s, x, 1.95, 4.33, 3.6, C.accent3);
  text(s, `${N.dq.rules} quality rules checked every night`, { x: x + 0.2, y: 2.05, w: 3.95, h: 0.4, fontSize: 16, bold: true, color: C.accent1 });
  text(s, "What they caught in the history so far:", { x: x + 0.2, y: 2.45, w: 3.95, h: 0.35, fontSize: 13, color: C.accent4 });
  const rows = [[fmt(c.R22), "re-sent webhooks dropped"], [fmt(c.R31), "repeated tablet uploads dropped"], [fmt(c.R20), "broken chat messages quarantined"],
                [fmt(c.R30), "cut-off tablet files quarantined"], [fmt(c.S05), "unknown-field messages flagged"], [fmt(c.R41), "bad row in the auspicious file"]];
  rows.forEach(([v, d], i) => {
    text(s, v, { x: x + 0.2, y: 2.85 + i * 0.43, w: 0.95, h: 0.4, fontSize: 15, bold: true, align: "right" });
    text(s, d, { x: x + 1.3, y: 2.85 + i * 0.43, w: 2.95, h: 0.4, fontSize: 13 });
  });
  text(s, [{ text: "Why zone-based, not Lambda or Kappa: ", options: { bold: true } },
           { text: "the business decides once a day, so no streaming layer is needed. Trusted data is rebuilt from the raw zone every night." }],
       { x: X0, y: 5.85, w: XW, h: 0.55, fontSize: 14 });
  source(s, "Rules are data (pipeline/quality/rules.yml), not code. Counts cover the whole raw-zone history, re-checked on every run.");
  notes(s, 9);
}

// 10 warehouse: star + conformance matrix
{
  const s = content("Platform design", "The warehouse: stars that share dimensions");
  sub(s, "Kimball design (Chapter 5). One star shown; six fact tables in total.");
  // star around FactInquiry
  const fx = 2.17, fy = 3.25, fw = 2.3, fh = 1.7;
  const dimBox = (x, y, title, lines) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: 1.75, h: 0.95, rectRadius: 0.06, fill: { color: C.background2 }, line: { color: C.accent2, width: 0.75 } });
    text(s, title, { x: x + 0.08, y: y + 0.05, w: 1.6, h: 0.3, fontSize: 11, bold: true });
    text(s, lines, { x: x + 0.08, y: y + 0.35, w: 1.6, h: 0.58, fontSize: 10, color: C.accent4 });
  };
  const dims = [[0.6, 1.85, "DimDate", "DateKey (SK)\nrole-played 7 ways"], [2.42, 1.85, "DimChannel", "ChannelKey (SK)\nChannelID (NK)"],
                [4.24, 1.85, "DimPackage", "PackageKey (SK)\nPackageID (NK)"], [0.6, 5.35, "DimEmployee · SCD 2", "EmployeeKey (SK)\nValidFrom, ValidTo"],
                [4.24, 5.35, "DimInquiryOutcome", "OutcomeKey (SK)\njunk dimension"]];
  dims.forEach(([x, y]) => line(s, x + 0.875, y + (y < fy ? 0.95 : 0), fx + fw / 2, y < fy ? fy : fy + fh));
  dims.forEach(([x, y, t, l]) => dimBox(x, y, t, l));
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: fx, y: fy, w: fw, h: fh, rectRadius: 0.06, fill: { color: C.accent1 }, line: { color: C.accent1 } });
  text(s, "FactInquiry", { x: fx + 0.1, y: fy + 0.07, w: fw - 0.2, h: 0.32, fontSize: 14, bold: true, color: C.background1 });
  text(s, "grain: one row per inquiry\nInquiryID (DD)\nChannelKey, PackageKey … (FK)\nIsBooked, ResponseMinutes, QuotedAmount",
       { x: fx + 0.1, y: fy + 0.42, w: fw - 0.2, h: 1.25, fontSize: 10, color: C.background1 });
  // conformance matrix
  const Y = "●", E = "";
  const hd = (t) => ({ text: t, options: { bold: true, color: C.background1, fill: { color: C.accent1 }, align: "center", fontSize: 9 } });
  const fact = (t, g) => ({ text: [{ text: t, options: { bold: true, breakLine: true } }, { text: g, options: { color: C.accent4 } }], options: { align: "left" } });
  const m = [["Fact table · grain", "Day", "Week", "Month", "Person", "Role group", "Package", "Channel", "Outcome"].map(hd),
             [fact("FactInquiry", "one inquiry"), Y, Y, Y, Y, Y, Y, Y, Y],
             [fact("FactEventBooking", "one wedding"), Y, Y, Y, E, E, Y, Y, E],
             [fact("FactStaffWorkload", "staff × wedding × week"), Y, Y, Y, Y, Y, E, E, E],
             [fact("FactSalesActivity", "one follow-up"), Y, Y, Y, Y, Y, E, Y, E],
             [fact("FactLeadScore", "inquiry × day"), Y, Y, Y, Y, Y, E, Y, E],
             [fact("FactDemandForecast", "role group × week"), E, Y, Y, E, Y, E, E, E]];
  s.addTable(m.map((r, i) => i === 0 ? r : r.map((c) => (typeof c === "object" ? c : { text: c, options: { align: "center", color: C.accent1 } }))),
    { x: 6.2, y: 1.85, w: 6.53, colW: [1.93, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575, 0.575], fontFace: "Calibri", fontSize: 11, color: C.text1,
      valign: "middle", border: { type: "solid", pt: 0.75, color: "D9D9DC" }, margin: [0.03, 0.05, 0.03, 0.05], rowH: [0.5, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45] });
  text(s, "Conformance matrix (Chapter 5). Hierarchies: day → week / month → quarter → year; person → role → role group.", { x: 6.2, y: 5.15, w: 6.53, h: 0.45, fontSize: 11, color: C.accent4 });
  text(s, [{ text: "Marts: ", options: { bold: true } },
           { text: "schemas of views inside the warehouse, one per role; PostgreSQL grants enforce access, and only Sales can read customer names. No snowflaking; ratios are computed from sums, never stored." }],
       { x: 6.2, y: 5.65, w: 6.53, h: 0.95, fontSize: 12 });
  notes(s, 10);
}

// 11 pipeline
{
  const s = content("ETL pipeline", "Every night, one run, checked end to end");
  sub(s, `Airflow starts it at 02:00; the last run took ${Math.round(N.last_run.seconds)} seconds. Each step has retries and a checkpoint.`);
  const steps = ["Extract changes", "Load raw zone", "Clean to trusted", "Load star schema", "Build marts", "Check totals", "Score + forecast"];
  const sw = 1.6, gap = 0.15, y = 2.25;
  steps.forEach((t, i) => {
    const x = X0 + i * (sw + gap);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w: sw, h: 0.75, rectRadius: 0.08, fill: { color: i < 3 ? C.text2 : C.accent1 }, line: { color: C.text2 } });
    text(s, t, { x: x + 0.05, y, w: sw - 0.1, h: 0.75, fontSize: 13, bold: true, color: C.background1, align: "center", valign: "middle" });
    if (i < steps.length - 1) arrow(s, x + sw, y + 0.375, x + sw + gap, y + 0.375, C.accent4);
  });
  text(s, "ELT into the lake: load raw first, transform after", { x: X0, y: 1.85, w: 3 * sw + 2 * gap, h: 0.35, fontSize: 12, bold: true, color: C.text2, align: "center" });
  text(s, "ETL into the warehouse: transform, then load", { x: X0 + 3 * (sw + gap), y: 1.85, w: 4 * sw + 3 * gap, h: 0.35, fontSize: 12, bold: true, color: C.accent1, align: "center" });
  const tests = [["Functional", "0", "differences", "30 nights replayed one by one vs a full reload: 196,070 rows in 39 tables and views compared"],
                 ["Performance", "13.3 s", "at 10× daily volume", `ten times the daily data on 9 months of history; today's run on 4 years of history: ${Math.round(N.last_run.seconds)} s; target 120 s`],
                 ["Error handling", "10 / 10", "failure cases recover", "CRM down, tampered file, crash mid-load, quality block, re-run, deletes, late commits: each stops cleanly and resumes"]];
  tests.forEach(([h, big, lab, d], i) => {
    const x = X0 + i * 4.1;
    card(s, x, 3.35, 3.9, 2.4);
    text(s, h + " tests", { x: x + 0.2, y: 3.45, w: 3.5, h: 0.35, fontSize: 14, bold: true, color: C.accent4 });
    text(s, big, { x: x + 0.2, y: 3.8, w: 3.5, h: 0.7, fontFace: "Cambria", fontSize: 34, bold: true, color: C.accent1 });
    text(s, lab, { x: x + 0.2, y: 4.5, w: 3.5, h: 0.35, fontSize: 14, bold: true });
    text(s, d, { x: x + 0.2, y: 4.85, w: 3.5, h: 0.85, fontSize: 12, color: C.accent4 });
  });
  card(s, X0, 5.95, XW, 0.65, C.accent3);
  text(s, [{ text: "It happened for real: ", options: { bold: true } },
           { text: "on 17 Nov a bad SQL edit broke one count. The reconciliation check stopped the run before anything was published; the dashboards kept yesterday's correct data." }],
       { x: X0 + 0.2, y: 5.95, w: XW - 0.4, h: 0.65, fontSize: 13, valign: "middle" });
  notes(s, 11);
}

// 12 dashboards
{
  const s = content("Dashboards", "One page per role, linked where problems meet");
  const shots = [["app_today.jpg", "Sales · Today", "Whom do I contact this morning, and why?"], ["app_capacity.jpg", "Operations · Capacity planner", "Who is overloaded, when? Can we take this date?"],
                 ["app_overview.jpg", "Owner · Business overview", "What changed this month, in plain sentences"], ["app_health.jpg", "Data team · Data & ML health", "Did last night's run work? Is the data fresh?"]];
  shots.forEach(([f, h, q], i) => {
    const x = X0 + (i % 2) * 6.15, y = 1.45 + Math.floor(i / 2) * 2.5;
    const r = image(s, IMG(f), x, y, 3.6, 2.3);
    s.addShape(pres.shapes.RECTANGLE, { x: r.x, y: r.y, w: r.w, h: r.h, fill: { type: "none" }, line: { color: "D9D9DC", width: 0.75 } });
    text(s, h, { x: x + 3.8, y: y + 0.35, w: 2.1, h: 0.7, fontSize: 15, bold: true, color: C.accent1 });
    text(s, q, { x: x + 3.8, y: y + 1.05, w: 2.1, h: 0.9, fontSize: 13 });
  });
  text(s, [{ text: "Separate, because each role makes a different decision. Linked: ", options: { bold: true } },
           { text: "every lead shows whether its wedding date still has staff (Room / Tight / Full), and the Operations forecast counts hours from open leads. The sales manager also has a Sales performance page." }],
       { x: X0, y: 6.4, w: XW, h: 0.55, fontSize: 13 });
  notes(s, 12);
}

// 13 lead model
{
  const s = content("AI / ML", "A ranked call list for every morning");
  const st = [["Score", "Every night, each open couple gets a chance to book within 60 days, from 49 facts about them, including what they wrote in chat."],
              ["Rank", "Each salesperson's list is ordered by chance × expected fee."],
              ["Explain", "Three reasons in plain words and a next action, for example \"Follow up on the quote\"."]];
  st.forEach(([h, d], i) => {
    circle(s, i + 1, X0, 1.6 + i * 1.25, 0.5);
    text(s, h, { x: X0 + 0.7, y: 1.58 + i * 1.25, w: 4.4, h: 0.35, fontSize: 16, bold: true });
    text(s, d, { x: X0 + 0.7, y: 1.93 + i * 1.25, w: 4.4, h: 0.85, fontSize: 13, color: C.accent4 });
  });
  card(s, X0, 5.45, 5.1, 1.15, C.accent3);
  text(s, "The model never decides alone: a salesperson reads the reasons and chooses what to do.", { x: X0 + 0.2, y: 5.45, w: 4.7, h: 1.15, fontSize: 14, valign: "middle" });
  text(s, "Of the couples who booked within 60 days, how many were already in that day's top 15?", { x: 6.1, y: 1.55, w: 6.6, h: 0.65, fontSize: 16, bold: true });
  image(s, CHART("model_capture.png"), 6.1, 2.3, 6.6, 3.0);
  text(s, `Tested on 6 months the model never saw (22 Mar – 17 Sep 2026), ${fmt(MO.test_rows)} daily rows, ranking by chance (the live list ranks by chance × fee). Logistic regression ties at ${capt["Logistic regression"]}% (backup A2).`,
       { x: 6.1, y: 5.45, w: 6.6, h: 0.8, fontSize: 13, color: C.accent4 });
  notes(s, 13);
}

// 14 validation and gate
{
  const s = content("AI / ML", "Tested on data it never saw");
  const pts = [["Split by time", "Train on the past, test on the next 6 months, with a 60-day gap so no answer leaks in."],
               ["Pass a gate", "Clearly better than today's habit, and no worse than the best simple rule. Rules set on 16 Nov after v1–v3; v4–v7 were judged by them."],
               ["Honest scores", "When the model says 30%, about 3 in 10 of those couples book (checked on the test period)."]];
  pts.forEach(([h, d], i) => {
    circle(s, i + 1, X0, 1.6 + i * 1.5, 0.5);
    text(s, h, { x: X0 + 0.7, y: 1.58 + i * 1.5, w: 4.6, h: 0.35, fontSize: 16, bold: true });
    text(s, d, { x: X0 + 0.7, y: 1.95 + i * 1.5, w: 4.6, h: 0.9, fontSize: 13, color: C.accent4 });
  });
  text(s, "7 versions trained. 5 rejected, 1 pulled, 1 in use.", { x: 6.3, y: 1.55, w: 6.4, h: 0.4, fontSize: 16, bold: true });
  const why = { 1: "compared with itself; gate redesigned", 2: "test too short to decide", 3: "test too short; extended to 6 months",
                4: "passed, then pulled: a data bug in budget bands", 5: "too few new couples to compare", 6: "chosen 17 Nov; scores the list every morning", 7: "worse than v6 on 141 new couples" };
  N.gate.forEach((g, i) => {
    const y = 2.1 + i * 0.62, col = g.status === "champion" ? C.accent6 : g.status === "retired" ? C.accent5 : C.accent2;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: 6.3, y, w: 0.75, h: 0.48, rectRadius: 0.06, fill: { color: col }, line: { color: col } });
    text(s, `v${g.version}`, { x: 6.3, y, w: 0.75, h: 0.48, fontSize: 13, bold: true, color: C.background1, align: "center", valign: "middle" });
    const st = g.status === "champion" ? "In use" : g.status === "retired" ? "Pulled" : "Rejected";
    text(s, [{ text: `${st}: `, options: { bold: true } }, { text: why[g.version] || g.decision }], { x: 7.2, y, w: 5.5, h: 0.48, fontSize: 13, valign: "middle" });
  });
  source(s, "Every version stays in the MLflow registry with its decision (ml.model_run). Details: backup A2, A3.");
  notes(s, 14);
}

// 15 forecast + MLOps
{
  const s = content("AI / ML", `Forecast: ${F.freelance_hours_12w} freelance hours needed by ${F.over_until.replace(/ \d{4}$/, "")}`);
  sub(s, "Staff hours per week, next 26 weeks, planning + wedding-day teams");
  image(s, CHART("forecast.png"), X0, 1.75, 6.9, 3.2);
  stat(s, X0, 5.0, 3.3, `${F.freelance_hours_12w} h`, "freelance hours to book", `${F.over_role_weeks_12w} role-weeks over capacity, until ${F.over_until}`);
  stat(s, X0 + 3.5, 5.0, 3.4, `${Math.round(F.wape_model * 100)}%`, "error 5–12 weeks ahead", `simple methods: ${Math.round(F.wape_pickup * 100)}–${Math.round(F.wape_naive * 100)}%`);
  const x = 7.85;
  text(s, "MLOps Level 1 (Chapter 7)", { x, y: 1.55, w: 4.9, h: 0.4, fontSize: 16, bold: true });
  const loop = [["Feature store", "one SQL definition, point in time"], ["Weekly training", "Sundays, tuned and calibrated"], ["Gate", "beat today's habit, no worse than the stage rule"],
                ["Registry", "MLflow: in use / previous"], ["Daily scoring", "list + reasons into the warehouse"], ["Monitoring", "drift, missing values, alerts"]];
  loop.forEach(([h, d], i) => {
    const y = 2.05 + i * 0.6;
    circle(s, i + 1, x, y + 0.04, 0.4);
    text(s, [{ text: h + "  ", options: { bold: true } }, { text: d, options: { color: C.accent4 } }], { x: x + 0.55, y, w: 4.35, h: 0.48, fontSize: 13, valign: "middle" });
  });
  card(s, x, 5.75, 4.88, 0.85, C.accent3);
  text(s, "10% of couples stay on the old way (holdout), so the real gain is measured. First result: mid-January 2027.",
       { x: x + 0.15, y: 5.75, w: 4.6, h: 0.85, fontSize: 13, valign: "middle" });
  notes(s, 15);
}

// 16 live demo
{
  const s = content("Live demo", "Live demo: one couple, from LINE to decision");
  sub(s, "Follow one new couple through the whole platform. About 5 minutes.");
  const d = [["0:45", "A couple writes on LINE", "A signed webhook lands as a raw file; the CRM records the inquiry. Each stop of the flow lights up"],
             ["1:00", "The nightly run, now", "Watch the couple move: raw zone, trusted zone (quality rules), star schema, marts, and what each layer changed"],
             ["1:00", "Sales · Today", "The couple appears as new; the ranked list with reasons; Mark contacted writes back to the CRM"],
             ["1:00", "Operations · Capacity", "An overloaded week and its weddings; can we take 13 Feb 2027? Dates with room"],
             ["0:30", "Owner · Overview", "What changed, in plain sentences"],
             ["0:45", "Proof", "A KPI traced back to its CRM column; every model version and why it was kept or rejected"]];
  d.forEach(([t, h, w], i) => {
    const x = X0 + (i % 3) * 4.1, y = 1.95 + Math.floor(i / 3) * 2.3;
    card(s, x, y, 3.9, 2.1);
    circle(s, i + 1, x + 0.2, y + 0.2, 0.45);
    text(s, t, { x: x + 2.9, y: y + 0.22, w: 0.8, h: 0.4, fontSize: 13, color: C.accent4, align: "right" });
    text(s, h, { x: x + 0.2, y: y + 0.75, w: 3.5, h: 0.4, fontSize: 15, bold: true });
    text(s, w, { x: x + 0.2, y: y + 1.15, w: 3.5, h: 0.9, fontSize: 12, color: C.accent4 });
  });
  source(s, "If anything fails: backup A1 shows every step as a screenshot.");
  notes(s, 16);
}

// 17 recommendations
{
  const s = content("Recommendations", "Four things to do now");
  const r = [[`Book ${F.freelance_hours_12w} freelance hours before ${F.over_until}`, `${F.over_role_weeks_12w} role-weeks are over capacity; the forecast is within ${Math.round(F.wape_model * 100)}% at 5–12 weeks`, `Staffs the weddings already sold; up to ${N.capacity.peak_util_4w_pct}% load today`],
             ["Make the ranked list the morning routine; keep the 10% holdout", `Top 15 holds ${capt["Our model"]}% of coming bookings vs ${capt["Today: newest first"]}% newest-first`, `${N.no_reply_quoted} quoted couples went silent ≈ ฿${L["No reply"].fee_m}M (estimate); the holdout measures the real gain`],
             ["Offer couples who ask for a full date an auspicious date with room", `${L["Date unavailable"].couples} couples turned away in 12 months; the date checker lists alternatives`, `≈ ฿${L["Date unavailable"].fee_m}M at stake (estimate)`],
             ["Cover peaks with freelancers, not permanent hires", `Utilization ${P1.util_min_pct}% in July vs ${P1.util_max_pct}% in November`, "Pay for extra hours only in the weeks that need them"]];
  text(s, "Action", { x: X0 + 0.7, y: 1.5, w: 4.6, h: 0.35, fontSize: 13, bold: true, color: C.accent4 });
  text(s, "Evidence", { x: 5.6, y: 1.5, w: 3.6, h: 0.35, fontSize: 13, bold: true, color: C.accent4 });
  text(s, "Value", { x: 9.5, y: 1.5, w: 3.2, h: 0.35, fontSize: 13, bold: true, color: C.accent4 });
  r.forEach(([a, e, v], i) => {
    const y = 1.95 + i * 1.18;
    card(s, X0, y, XW, 1.02, i % 2 ? C.background1 : C.background2);
    circle(s, i + 1, X0 + 0.15, y + 0.28, 0.45);
    text(s, a, { x: X0 + 0.75, y: y + 0.08, w: 4.0, h: 0.86, fontSize: 15, bold: true, valign: "middle" });
    text(s, e, { x: 5.6, y: y + 0.08, w: 3.65, h: 0.86, fontSize: 13, valign: "middle" });
    text(s, v, { x: 9.5, y: y + 0.08, w: 3.2, h: 0.86, fontSize: 13, color: C.accent1, bold: true, valign: "middle" });
  });
  source(s, "Estimates = couples × median fee × quote-to-booking rate; they are not measured results.");
  notes(s, 17);
}

// 18 limits
{
  const s = content("Recommendations", "What is not proven yet, and what comes next");
  const lim = ["The business is simulated. Behaviour rules are assumptions; the platform and method carry over to a real CRM.",
               "Bookings take up to 60 days to show, so the A/B result arrives in mid-January 2027.",
               "Some follow-up features now come from the model's own list (a feedback loop, Chapter 7). They add little; the next retrain drops them.",
               "The CRM keeps only the latest quote, so a quote edited later could leak into training. A quote-history table fixes it."];
  const nxt = ["Connect the real LINE Official Account and Meta app to the same receiver (needs a public HTTPS address).",
               "Move to a company server or cloud with the same layout (backup A4).",
               "Read the holdout result in January and report the gain in baht as a measured number.",
               "Add automated testing on every change (MLOps Level 2)."];
  [["Limits we know", lim, 0], ["Next steps", nxt, 1]].forEach(([h, items, k]) => {
    const x = X0 + k * 6.15;
    card(s, x, 1.55, 5.95, 4.3, k ? C.accent3 : C.background2);
    text(s, h, { x: x + 0.25, y: 1.7, w: 5.4, h: 0.45, fontSize: 18, bold: true, color: k ? C.accent1 : C.text1 });
    text(s, items.map((t, j) => ({ text: t, options: { bullet: { indent: 14 }, breakLine: j < items.length - 1 } })),
         { x: x + 0.25, y: 2.3, w: 5.45, h: 3.4, fontSize: 15, paraSpaceAfter: 10 });
  });
  notes(s, 18);
}

// 19 closing
{
  const s = pres.addSlide({ masterName: "DARK", sectionTitle: "Main" }); slideNo += 1;
  s.addText("Three decisions we ask for", { placeholder: "title" });
  s.addText([
    { text: `1   Approve booking ${F.freelance_hours_12w} freelance hours before ${F.over_until}`, options: { breakLine: true } },
    { text: "2   Make the ranked list every salesperson's morning routine", options: { breakLine: true } },
    { text: "3   Read the holdout result in January 2027 and keep the list only if it wins", options: { breakLine: true } },
    { text: " ", options: { breakLine: true } },
    { text: "Questions", options: { fontFace: "Cambria", fontSize: 28, bold: true } },
  ], { placeholder: "body", paraSpaceAfter: 8 });
  notes(s, 19);
}

// ================= BACKUP =================
pres.addSection({ title: "Backup" });
// A1 demo fallback (two slides)
[[["app_demo_panel.jpg", "1 · A couple writes on LINE"], ["app_demo_flow.jpg", "2 · The run: the couple at every layer"], ["app_today.jpg", "3 · Sales: today's list"], ["app_today_may_mark.jpg", "3 · Sales: mark contacted"]],
 [["app_capacity_drawer.jpg", "4 · Operations: an overloaded week"], ["app_date_checker.jpg", "4 · Date checker: dates with room"], ["app_overview.jpg", "5 · Owner: what changed"],
  ["catalog_lineage.jpg", "6 · Proof: KPI lineage"], ["mlflow_registry.jpg", "6 · Proof: model registry"], ["app_health.jpg", "6 · Proof: nightly run health"]]]
  .forEach((shots, k) => {
    const s = backup(`A1 · Demo fallback (${k + 1} of 2)`);
    const n = shots.length, cols = n > 4 ? 3 : 2, rows = Math.ceil(n / cols), cw = (XW - (cols - 1) * 0.25) / cols, ch = rows > 1 ? 2.2 : 4.3;
    shots.forEach(([f, cap], i) => {
      const x = X0 + (i % cols) * (cw + 0.25), y = 1.5 + Math.floor(i / cols) * (ch + 0.45);
      const r = image(s, IMG(f), x, y, cw, ch);
      s.addShape(pres.shapes.RECTANGLE, { x: r.x, y: r.y, w: r.w, h: r.h, fill: { type: "none" }, line: { color: "D9D9DC", width: 0.75 } });
      text(s, cap, { x, y: y + ch + 0.05, w: cw, h: 0.35, fontSize: 12, bold: true });
    });
    s.addNotes("Backup: if the live demo fails, show these and keep talking. Never debug on stage.");
  });
// A2 model metrics
{
  const s = backup("A2 · Lead model: test-period results");
  const b = MO.baselines, ci = (a) => `${a[0].toFixed(3)} – ${a[1].toFixed(3)}`;
  table(s, [["Ranking method", "PR-AUC", "ROC-AUC", "Bookings in daily top 15"],
            ["Model v6 (LightGBM, calibrated)", `${MO.pr_auc} (95% CI ${ci(MO.pr_auc_ci)})`, MO.roc_auc, `${capt["Our model"]}%`],
            ["B3 logistic regression", b["B3 logistic regression"].pr_auc, b["B3 logistic regression"].roc_auc.toFixed(3), `${b["B3 logistic regression"].captured_pct}%`],
            ["B1 stage rule (quoted > consulted > new)", b["B1 stage rule"].pr_auc, b["B1 stage rule"].roc_auc, `${b["B1 stage rule"].captured_pct}%`],
            ["B2 channel × stage booking rate", b["B2 channel x stage rate"].pr_auc, b["B2 channel x stage rate"].roc_auc, `${b["B2 channel x stage rate"].captured_pct}%`],
            ["B0 newest message first (today)", b["B0 newest message first"].pr_auc, b["B0 newest message first"].roc_auc, `${b["B0 newest message first"].captured_pct}%`]],
        { y: 1.5, colW: [4.2, 3.2, 1.8, 2.933], fontSize: 14, rowH: 0.5 });
  text(s, [
    { text: `Gain over today's practice: +${MO.gain_vs_practice.toFixed(3)} PR-AUC (95% CI +${MO.gain_vs_practice_ci[0].toFixed(3)} to +${MO.gain_vs_practice_ci[1].toFixed(3)}). Gain over the stage rule: +${MO.gain_vs_rule.toFixed(3)} (CI ${MO.gain_vs_rule_ci[0].toFixed(3)} to +${MO.gain_vs_rule_ci[1].toFixed(3)}): no worse, not significantly better.`, options: { bullet: { indent: 14 }, breakLine: true } },
    { text: `Calibration: expected calibration error ${MO.ece}. Base rate ${MO.base_rate_pct}%, so accuracy would mislead (predicting "nobody books" scores ${100 - MO.base_rate_pct}%).`, options: { bullet: { indent: 14 }, breakLine: true } },
    { text: "Logistic regression scored slightly higher on the test. LightGBM was chosen by 5-fold rolling cross-validation before the test was opened; switching now would be choosing on the test, and the gap is inside the interval.", options: { bullet: { indent: 14 }, breakLine: true } },
    { text: `Cross-validated ablation, adding one group at a time: ${MO.ablation.filter((a) => a.gain !== null).map((a) => `${a.group.replace("text_rules", "chat-text intents").replace("_", " ")} +${a.gain}`).join(", ")}. A TF-IDF text representation was tested and dropped (mean gain −0.001). The 74% is measured ranking by chance.`, options: { bullet: { indent: 14 } } },
  ], { x: X0, y: 4.75, w: XW, h: 1.9, fontSize: 13, paraSpaceAfter: 4 });
  s.addNotes("Backup for questions on metrics, logistic regression, accuracy, ablation.");
}
// A3 gate history
{
  const s = backup("A3 · Gate history: every version and why");
  table(s, [["Version", "Trained (data as of)", "Decision", "Reason recorded"],
            ...N.gate.map((g) => [`v${g.version}`, g.asof, g.status, g.decision])], { y: 1.5, colW: [1.0, 2.0, 1.4, 7.733], fontSize: 13, rowH: 0.55 });
  source(s, "Source: ml.model_run, MLflow. v5 was a scheduled run on data to 15 Nov, so its date is earlier than v4's. v4 was retired after the budget-band bug was fixed.");
  s.addNotes("Backup for questions on the gate, v4 and v7.");
}
// A4 where it runs
{
  const s = backup("A4 · Where it runs, and the cloud path");
  table(s, [["Part", "What we run (open-source)", "Cloud equivalent if the business grows"],
            ["Lake storage", "Folders on the server disk; Parquet files", "Object storage (e.g. Amazon S3, Google Cloud Storage); same folder layout"],
            ["Lake engine", "DuckDB inside the pipeline job", "Same, or a cloud query engine (e.g. BigQuery)"],
            ["Warehouse and marts", "PostgreSQL 16 with role grants", "Managed PostgreSQL (e.g. Cloud SQL, Amazon RDS)"],
            ["Orchestration", "Apache Airflow 2.10", "Managed Airflow (e.g. Cloud Composer, Amazon MWAA)"],
            ["Model registry", "MLflow", "Managed MLflow or the cloud's model registry"],
            ["Web app and API", "FastAPI + React", "A container service (e.g. Cloud Run)"],
            ["Chat receiver", "FastAPI webhook receiver", "Same code behind a public HTTPS address (required by LINE and Meta)"]],
        { y: 1.5, colW: [2.4, 4.3, 5.433], fontSize: 14, rowH: 0.56 });
  source(s, "Today: one server, no licence fees; for the demo this laptop is the server. Each part runs in its own environment.");
  s.addNotes("Backup for 'where does it run / why not cloud'.");
}
// A5 OLTP exceptions
{
  const s = backup("A5 · Where the CRM is not fully normalized, and why");
  table(s, [["Where", "Kind", "Why it is there", "Fix"],
            ["Customer.District → Province", "Transitive dependency", "Kept as the couple gave it; no KPI uses it", "District lookup table"],
            ["MarketingChannel.Platform → ChannelType", "Transitive dependency", "8-row reference table", "Platform lookup table"],
            ["ChatMessage.SenderType → Direction", "Transitive dependency", "Written together by the chat receiver", "Derive Direction in a view"],
            ["Event.CustomerID", "Looks derivable via Inquiry", "Needed: a booking can exist without an inquiry (InquiryID is optional)", "Keep"],
            ["Event.AgreedPackagePrice", "Deliberate copy", "Price at the moment of sale; package prices change later (Chapter 3)", "Keep"]],
        { y: 1.5, colW: [3.4, 2.4, 4.4, 1.933], fontSize: 13, rowH: 0.62 });
  source(s, "The first three are planned for the next schema version; changing them now would ripple through the pipeline and tests.");
  s.addNotes("Backup for normalization questions.");
}
// A6 KPI formulas
{
  const s = backup("A6 · Headline KPI formulas");
  table(s, [["KPI", "Formula"],
            ["Conversion", "sum(IsBooked) / (sum(InquiryCount) − sum(IsOpen)), inquiries received in the period"],
            ["Utilization", "sum(assigned hours) / sum(contracted hours); contracted hours from the contract valid that week (SCD 2)"],
            ["Person-weeks over 100%", "count of staff-weeks where assigned hours reach or pass contracted hours"],
            ["Freelance hours needed (12 weeks)", "sum over role group × week of max(0, forecast P50 hours − capacity hours)"],
            ["Median first reply", "median of ResponseMinutes for online inquiries received in the month"],
            ["Bookings in daily top 15", "bookings within 60 days that were ranked 1–15 on the salesperson's list that day ÷ all such bookings"],
            ["Couples turned away", "inquiries lost with reason \"Date unavailable\", by closing month"],
            ["Fee at stake (estimate)", `(quoted couples lost + couples turned away) × median fee of the last 12 months (฿${fmt(N.median_fee)}) × quote-to-booking rate (${N.quote_to_booking_pct}%)`]],
        { y: 1.5, colW: [3.4, 8.733], fontSize: 13, rowH: 0.52 });
  source(s, "All 42 KPI definitions: catalog/kpis.yml and the data catalog page (lineage back to the CRM column).");
  s.addNotes("Backup for KPI formula questions.");
}
// A7 course map
{
  const s = backup("A7 · Course map: where each chapter shows up");
  table(s, [["Chapter", "What we applied", "Slide"],
            ["1 End-to-end analytics system", "Six-layer platform (Unified Data Infrastructure); ROLAP: star schema + SQL views", "6, 10"],
            ["2 Data collection and normalization", "Four source types; OLTP normalized to 3NF, exceptions listed", "7, 8, A5"],
            ["3 Master vs transaction tables", "8 master, 5 transaction tables; price copied at the moment of sale", "8"],
            ["4 Data lake design", "Zone-based lake: landing, raw, trusted, refined, sandbox; manifests, checksums, catalog", "9"],
            ["5 Star schema and conformance", "Grain, surrogate keys, SCD 2, junk and role-playing dimensions, no snowflakes, conformance matrix, marts inside the warehouse", "10"],
            ["6 Ingestion and ETL design", "Incremental load, ELT then ETL, quality rules + quarantine, checkpoints, idempotent re-runs, functional / performance / error-handling tests", "7, 9, 11"],
            ["7 MLOps and feature stores", "Level 1: feature store with point-in-time features, baseline gate, registry, drift and missing-value checks; feedback loop noted", "13–15, A2, A3"]],
        { y: 1.5, colW: [3.2, 7.6, 1.333], fontSize: 13, rowH: 0.6 });
  s.addNotes("Backup for 'how does this follow the course'.");
}

// ---------- write deck, theme, guide ----------
(async () => {
  const out = path.join(HERE, "..", "AisleCo_Final_Presentation.pptx");
  await pres.writeFile({ fileName: out });
  // folder entries in the zip break opening through some cloud services (e.g. Google Drive); drop them
  try { require("child_process").execSync(`zip -dq "${out}" '*/'`); } catch (e) { /* none present */ }
  console.log("deck ->", out, `(${slideNo} slides)`);
  fs.writeFileSync(path.join(HERE, "..", "PRESENTER_GUIDE.md"), guide());
  console.log("guide ->", path.join(HERE, "..", "PRESENTER_GUIDE.md"));
})();

function guide() {
  const order = Object.entries(scripts).map(([n, s]) => ({ n: Number(n), ...s }));
  let t = 0;
  const clock = (x) => `${Math.floor(x / 60)}:${String(x % 60).padStart(2, "0")}`;
  const parts = [];
  for (const o of order) {
    const last = parts[parts.length - 1];
    if (last && last.who === o.who) { last.to = o.n; last.sec += o.sec; } else parts.push({ who: o.who, from: o.n, to: o.n, start: t, sec: o.sec });
    t += o.sec;
  }
  const total = t;
  const titles = {};
  let L_ = [];
  L_.push(`# คู่มือผู้นำเสนอ: Aisle & Co. final presentation`, "",
    `วันนำเสนอ: อาทิตย์ 29 พฤศจิกายน 2026 · เวลาในห้อง 25 นาที · พูด ${clock(total)} นาที แล้วตอบคำถามประมาณ ${clock(25 * 60 - total)} นาที`, "",
    `ตัวเลขบนสไลด์ทั้งหมดเป็นข้อมูล ณ ${N.asof_text} (ไฟล์ \`deck/numbers.json\`) ท่องตัวเลขชุดนี้ชุดเดียว ตัวเลขบนหน้าจอเดโมเป็นข้อมูลสดของวันนั้น อาจต่างเล็กน้อย`, "",
    "สคริปต์ไม่ได้ใส่ ครับ/ค่ะ ทุกประโยค ให้เติมตามผู้พูด อ่านช้าๆ ได้ สไลด์เขียนเป็นภาษาอังกฤษ คำศัพท์เทคนิคในสคริปต์จึงคงเป็นภาษาอังกฤษให้ตรงกับสไลด์และบทเรียน", "",
    "แก้ชื่อสมาชิกได้ที่ `docs/v2/deliverables/deck/content.js` (ตัวแปร `members`) แล้วรัน `node build.js` ในโฟลเดอร์ `deck` ทั้งสไลด์และคู่มือนี้จะอัปเดตพร้อมกัน", "",
    "## 1. ลำดับการนำเสนอ", "",
    "| ช่วง | ผู้นำเสนอ | สไลด์ | เริ่ม | ยาว |", "|---|---|---|---|---|");
  parts.forEach((p, i) => L_.push(`| ${i + 1} | ${members[p.who]} | ${p.from === p.to ? p.from : `${p.from}–${p.to}`} | ${clock(p.start)} | ${clock(p.sec)} |`));
  L_.push(`| | **รวม** | | | **${clock(total)}** |`, "",
    "เหตุผลที่คุณ (pollot) ทำ live demo: เป็นส่วนที่มีโอกาสผิดพลาดมากที่สุด และคนที่สร้างระบบเท่านั้นที่แก้สถานการณ์สดได้ ระหว่างตอบคำถาม เจ้าของหัวข้อตอบก่อน แล้วคุณช่วยเสริม", "");
  L_.push("## 2. สิ่งที่แต่ละคนต้องรู้", "");
  for (const k of ["m2", "m3", "m4", "m5", "you"]) {
    L_.push(`### ${members[k]}`, "", ...briefs[k].map((b) => `- ${b}`), "");
  }
  L_.push("## 3. สคริปต์ทีละสไลด์", "");
  for (const o of order) {
    L_.push(`### สไลด์ ${o.n} · ${members[o.who]} · ${clock(o.sec)}`, "", o.th, "");
    if (o.handoff) L_.push(`**ส่งต่อ:** ${o.handoff}`, "");
  }
  L_.push("## 4. คำถามที่น่าจะโดน (Q&A bank)", "",
    "เจ้าของหัวข้อตอบก่อน ตอบสั้น 2–3 ประโยค ถ้าไม่แน่ใจให้พูดว่า \"ขอให้ [ชื่อ] ช่วยเสริม\" แล้วส่งต่อ ห้ามเดา คอลัมน์สุดท้ายคือสไลด์สำรองที่เปิดประกอบได้", "",
    "| # | คำถาม | ผู้ตอบ | คำตอบ | สไลด์สำรอง |", "|---|---|---|---|---|");
  qa.forEach(([who, q, a, ref], i) => L_.push(`| ${i + 1} | ${q} | ${members[who]} | ${a.replace(/\|/g, "/")} | ${ref} |`));
  L_.push("", "## 5. แผนซ้อม", "",
    "1. **26–27 พ.ย.:** แต่ละคนอ่านสคริปต์ของตัวเองออกเสียงพร้อมจับเวลา ถ้าเกินเวลา ตัดประโยคท้ายก่อน อย่าพูดเร็วขึ้น",
    "2. **27 พ.ย. (ซ้อมรวมรอบ 1):** เปิดสไลด์จริง ทุกคนพูดตามลำดับ ฝึกประโยคส่งต่อ คุณ (pollot) ทำเดโมจริงตาม DEMO_RUNBOOK.md แล้วอัดหน้าจอ (Cmd+Shift+5) ไว้เป็นสำรองชั้นที่สอง",
    "3. **28 พ.ย. (ซ้อมรวมรอบ 2):** จับเวลาทั้งชุด เป้า 18 นาที แล้วซ้อม Q&A: คนหนึ่งสุ่มถามจากตารางข้อ 4 เจ้าของหัวข้อตอบภายใน 30 วินาที",
    "4. **คืน 28 พ.ย.:** เสียบสายชาร์จ ปล่อยเครื่องเปิดไว้ (DEMO_RUNBOOK.md ข้อ 2) เพื่อให้รอบ 01:00 และ 02:00 รันเอง",
    "5. **เช้า 29 พ.ย.:** ทำ checklist ใน DEMO_RUNBOOK.md ข้อ 2–3 และ 6 อย่างน้อย 60 นาทีก่อนขึ้นพูด", "",
    "## 6. กฎการพูด", "",
    "- พูดสิ่งที่อยู่บนสไลด์ ไม่ต้องอ่านทุกคำบนสไลด์",
    "- ห้ามพูดว่า \"รันบน MacBook\" ให้พูดว่า \"server เครื่องเดียว ใช้ open-source ทั้งหมด วันนี้ laptop เครื่องนี้ทำหน้าที่เป็น server\"",
    "- ตัวเลขเงินทุกตัวต้องตามด้วยคำว่า \"ประมาณการ\"",
    "- ถ้าเดโมล่ม: เปิดสไลด์สำรอง A1 แล้วเล่าต่อ ห้าม debug บนเวที", "");
  return L_.join("\n");
}
