# Spec 01. Sources and ingestion

Part of the [v2 plan](PLAN.md). Answers: where the data comes from, what it looks like when it arrives, and how each
source is moved into Bronze (full dump, incremental, push or file drop), and why.

## 1. The four source systems

| # | Source | What it is on the Mac | Data type (rubric) | Format | Volume at 1× (per year) | Feeds |
|---|---|---|---|---|---|---|
| 1 | **CRM** (booking system, OLTP v6) | PostgreSQL database `aisleco_crm` | Structured | relational tables | 1,500 inquiries, 100 weddings, ~500 staff assignments, ~7,000 sales follow-ups, ~22,000 chat-message records | Problems 1 and 2 |
| 2 | **Chat gateway** (LINE Messaging API, Meta Messenger and Instagram webhooks, shared-inbox send log) | FastAPI service on port 8001 | Semi-structured (JSON), carrying **unstructured** message text | JSON lines | ~22,000 messages | Problem 2, ML text features |
| 3 | **Wedding-fair tablets** (offline app at fair booths) | batch files dropped into `lake/landing/fair_tablet/` | Semi-structured (nested JSON) with free text | JSON | ~5 fairs, ~300 sign-ups | Problem 2 (fair leads, guest count, venue) |
| 4 | **External calendars**: (a) Thai public and Buddhist holidays, (b) auspicious wedding dates | (a) Google's public Thai holiday calendar on the internet; (b) a yearly CSV file compiled by marketing | External; (a) semi-structured iCalendar, (b) structured CSV | ICS, CSV | (a) ~25 holidays/year, (b) ~60 dates/year | Problem 1 (peak and Lent flags), both models |

Source 4a is **real**: `https://calendar.google.com/calendar/ical/en.th%23holiday%40group.v.calendar.google.com/public/basic.ics`
(checked 16 Nov 2026: HTTP 200, 115 KB, 322 events 2021–2031; Buddhist lunar holidays such as Asalha Bucha are listed only up to 2027). Buddhist Lent starts the day after
Asalha Bucha and ends with Ok Phansa 88 days later (Spec 02 §4, `calendar_day`). This replaces Rev10's approximation
"20 July – 17 October". A copy is cached in `sources/external/fixtures/` so a run without internet still works.

Sources 1–3 and 4b are produced by the simulator (Spec 06) in exactly the format the real systems use.

## 2. Source 1: the CRM (OLTP v6)

### 2.1 Tables

13 tables: 8 master (who/what, rarely change) and 5 transaction (something happened at a time). Changes from v5:

| Change | Why |
|---|---|
| **New `EmployeeContract`** (EmployeeID, RoleID, EmploymentType, StandardWeeklyHours, MaxConcurrentEvents, EffectiveFrom, EffectiveTo). Role and hours move off `Employee`. | HR keeps contract history with effective dates. Capacity in a past week must use the contract valid then. Rev10 "froze" weekly rows instead; the contract history makes a proper SCD Type 2 dimension possible (Spec 03 §3). 3NF: hours depend on the contract period, not on the person. |
| **New `SalesActivity`** (InquiryID, EmployeeID, ActivityAt, ActivityType, Outcome, Source, ListDate) | Every follow-up a salesperson makes (call, LINE, meeting). Written by the web app's "mark contacted" button through the CRM. Needed to measure follow-up compliance and the model's A/B effect (Spec 04 §8). |
| **`Inquiry.AssignedSalesEmployeeID`** | Each lead has an owner, so each salesperson gets their own call list. |
| **`Event.CancelledDate`** | When a booking was cancelled. v1 had to guess it. Needed for "booked as of date D" in the forecast backtest. |
| **`AuspiciousDate` removed** | It is external data (source 4b), not something the CRM creates. |
| `CreatedAt` + `UpdatedAt` on every table, `UpdatedAt` set by trigger | Incremental extraction (§2.3). The trigger means the application cannot forget it. |
| Exclusion constraint on `EmployeeContract` (no overlapping periods per employee, `btree_gist`) | One valid contract per day is guaranteed by the database. |

| Master (8) | Transaction (5) |
|---|---|
| Customer, CustomerContact, CustomerChannelAccount, MarketingChannel, Package, EmployeeRole, Employee, EmployeeContract | Inquiry, ChatMessage, Event, EventEmployeeAssignment, SalesActivity |

The DDL is `sources/crm/oltp_v6.sql` (it imports into dbdiagram.io as is); the ER diagram is `docs/v2/deliverables/report_figures/fig_er_oltp_v6.png`.

### 2.2 Ingestion method, table by table

| Table | Kind | Method | Columns taken | Bronze path |
|---|---|---|---|---|
| Inquiry, ChatMessage, Event, EventEmployeeAssignment, SalesActivity | transaction | **Incremental** by `UpdatedAt` watermark + full primary-key list | all | `bronze/crm/<Table>/load_date=D/run=N/` |
| MarketingChannel, Package, EmployeeRole, Employee, EmployeeContract | master | **Full snapshot** every run | all (staff names are operational data) | same |
| CustomerContact, CustomerChannelAccount | master with personal data | **Full snapshot of allowed columns only**, restricted folder | CustomerID; primary contact's first name; platform display name; phone (masked in Silver, hashed for matching). `Customer` (district, province) is not extracted: no KPI uses it. | `bronze/crm_restricted/<Table>/load_date=D/run=N/` (folder mode 700) |

### 2.3 How the incremental extraction works

Reused from v1 (`etl/extract.py`), adapted to the new layout.

1. **One consistent snapshot.** The extractor opens a `REPEATABLE READ` transaction, exports its snapshot
   (`pg_export_snapshot()`), and every table is read in parallel by connections that import that snapshot. A child row
   (an assignment) can never arrive without the version of its parent (the event) it points to.
2. **Which rows.** For each incremental table, `etl.watermark` stores the highest `UpdatedAt` loaded (W) and the previous
   snapshot time (S_prev). The run reads rows with `UpdatedAt > min(W, S_prev − 5 minutes)`.
   The 5-minute overlap catches a slow transaction that committed after the previous snapshot with an earlier timestamp.
   Re-reading a row is harmless: Silver keeps one version per key.
3. **Deletes.** A timestamp cannot show a row that no longer exists, so every run also writes the full list of primary keys
   of each incremental table (`_keys.csv`, a few KB). Silver drops keys that are missing from the latest list.
4. **Batch cap (overflow queue).** At most `ETL_MAX_ROWS_PER_RUN` changed transaction rows (default 100,000) are taken per run,
   oldest first; parents of taken rows are pulled into the same batch; the watermark moves exactly to the cutoff; the rest
   waits in the CRM (the queue) and a warning alert records its size. The next run continues. Full refreshes are not capped.
5. **Bulk read.** `COPY (SELECT …) TO STDOUT WITH CSV HEADER` through the `UpdatedAt` index.
6. **Manifest.** `manifest.json` per run: per file the row count, the highest `UpdatedAt`, the SHA-256 checksum, the snapshot
   time and the run ID. The Silver step refuses a file whose checksum or count does not match.
7. **All or nothing.** Files are written to `bronze/_tmp/run=N/` and moved into place only when every table succeeded. The
   watermark advances only after the whole pipeline run succeeds. Connection errors are retried 3 times with backoff.

### 2.4 Why these methods

| Method | How it works | Good | Bad | Used here for |
|---|---|---|---|---|
| Full dump | read the whole table each run | simplest; deletes are visible | cost grows with history; cannot tell what changed; heaviest on the source | master tables (< 1,000 rows each), personal-data tables |
| **Incremental by timestamp** (query-based change data capture) | rows whose `UpdatedAt` is after the watermark | reads only changes; plain SQL; uses an index | needs a trustworthy `UpdatedAt` (trigger); misses hard deletes (key list fixes it); states between two runs are not seen | CRM transaction tables |
| Log-based change data capture | read the database's write-ahead log (logical replication; Debezium + Kafka) | every change including deletes and in-between states; near real time; no queries on tables | a replication slot to manage and a streaming stack to run | not used: the decisions are daily and weekly; it is the upgrade path if Sales ever needs the list refreshed within minutes |
| Push (event-driven) | the source calls our endpoint for each event | real time; the source does the work | our endpoint must be always up, answer fast, and handle retries and duplicates | chat webhooks |
| File drop | the source uploads a file to a folder | works with offline devices; simple contract | duplicates on retry; partial files | tablet sync; yearly auspicious file |
| API pull | we call the provider on a schedule | we control timing | provider limits and outages | holiday calendar |

Why the CRM is read nightly and not in real time: the decisions it feeds are made once a day (the morning call list) and
once a week (the operations meeting). The first reply to a couple happens live in the shared inbox, not in the warehouse.
A run can also be started by hand at any time (the demo does this).

## 3. Source 2: the chat gateway

### 3.1 What the platforms send

Real payload formats. The simulator produces exactly these, with valid signatures.

LINE Messaging API (`POST /webhook/line`), header `x-line-signature: base64(HMAC-SHA256(channel secret, raw body))`:

```json
{
  "destination": "U4af4980629aisleco",
  "events": [{
    "type": "message", "mode": "active", "timestamp": 1794808920000,
    "source": {"type": "user", "userId": "U8189cf6745fc0d808977bdb0b9f22995"},
    "webhookEventId": "01JCZ74A0TDDPYRVKNK77XKC3Z",
    "deliveryContext": {"isRedelivery": false},
    "replyToken": "757913772c4646b784d4b7ce46d12671",
    "message": {"id": "534573844083572737", "type": "text", "quoteToken": "q3Plxr4AgKd",
                "text": "#TIKTOK สวัสดีค่ะ ว่างวันที่ 13 ก.พ. ไหมคะ แขกประมาณ 300 คนค่ะ"}
  }]
}
```

Meta Messenger (`POST /webhook/meta`), header `X-Hub-Signature-256: sha256=<hex HMAC-SHA256(app secret, raw body)>`;
Instagram is the same with `"object": "instagram"`. A click-to-Messenger ad adds `referral.ad_id`:

```json
{"object": "page", "entry": [{"id": "104729381234567", "time": 1794808920123, "messaging": [{
  "sender": {"id": "6512345678901234"}, "recipient": {"id": "104729381234567"}, "timestamp": 1794808920000,
  "message": {"mid": "m_AbCdEf123", "text": "Hi, how much is Full Planning for 300 guests?"},
  "referral": {"source": "ADS", "type": "OPEN_THREAD", "ad_id": "120211234567890123"}}]}]}
```

Shared-inbox send log (`POST /outbound`, internal, header `X-Api-Key`): every reply staff send and every bot greeting.
Replies typed in the LINE phone app bypass the inbox and are **not** logged (a known gap, kept on purpose; Spec 06 §7).

```json
{"message_id": "534573844099990001", "platform": "line", "to_user_id": "U8189cf6745fc0d808977bdb0b9f22995",
 "sent_at": "2026-11-16T06:09:00Z", "sender_type": "staff", "staff_code": "S-MAY", "text": "สวัสดีค่ะ คุณมิ้นท์ ..."}
```

### 3.2 What the gateway does with each request

1. Verify the signature (LINE, Meta) or the API key (outbound). Failure: answer `401`, append the request to
   `lake/landing/chat/_rejected/`, count it. Meta's `GET` subscription check (`hub.challenge`) is answered.
2. Append one line to the open file `lake/landing/chat/platform=<p>/received_date=<D>/hour=<HH>/events.jsonl`:
   `{"received_at": "...", "platform": "...", "body": "<raw body string>", "body_sha256": "..."}`. The raw string is kept
   so the signature can be re-checked later. `fsync`, then answer `200`. Target: under 100 ms.
3. No deduplication at the gateway. Platforms resend an event when they do not get a fast `200`; Silver removes duplicates
   by the platform message ID (LINE `message.id`, Meta `message.mid`); a re-delivered LINE event carries the same IDs.
4. Sim mode only: the header `X-Sim-Clock` sets `received_at` to the simulated time, so a backfill lands in the right date
   partitions. Ignored when `GATEWAY_MODE=production`.

In production the CRM's chat module and this gateway would be one service (LINE allows one webhook URL per channel).
In the simulation, the simulator plays the CRM side: it writes the `ChatMessage` metadata row into the CRM itself.

### 3.3 Into Bronze

The ingestion step moves every **closed** hourly file (its hour has passed) from `lake/landing/chat/` to
`lake/bronze/chat/…` (same partition path), records file name, row count and SHA-256 in `etl.file_checkpoint`, and never
touches it again. The open file of the current hour stays in landing until the next run.

## 4. Source 3: wedding-fair tablets

The booth tablet works offline, caches sign-ups and uploads a batch when it reconnects. A failed upload is retried with the
same `batch_seq`, so the same batch can arrive twice. On a successful sync the tablet app also creates the inquiry in the CRM
and stores the returned `crm_inquiry_id`.

```json
{"device_id": "TAB-02", "batch_seq": 41, "app_version": "2.3.1",
 "fair": {"name": "Bangkok Wedding Fair, Siam Paragon", "date": "2026-10-17"},
 "synced_at": "2026-10-17T19:02:11+07:00",
 "submissions": [{
   "submission_id": "TAB-02-000731", "submitted_at": "2026-10-17T14:05:00+07:00", "crm_inquiry_id": 18342,
   "couple": {"bride_name": "Ploy", "groom_name": "Ton", "phone": "081-234-5678", "line_id": "ploy.t",
              "district": "Bang Kapi", "province": "Bangkok"},
   "wedding": {"date": "2027-01-16", "date_flexible": false, "guests": 300, "venue_type": "Hotel ballroom"},
   "budget_band": "500k-1M", "package_interest": "Full",
   "wishes": "อยากได้ธีมสีพาสเทล งานเช้าแบบไทย งานเย็นแบบสากล", "consent_marketing": true}]}
```

Ingestion: each run moves new files from `lake/landing/fair_tablet/` to `lake/bronze/fair_tablet/received_date=D/<device>_<seq>_<sha8>.json`,
records them in `etl.file_checkpoint`, and flags a repeat of the same `(device_id, batch_seq)`. A file that is not valid
JSON is moved to quarantine with the parser error. The tablet adds what the CRM does not hold: the true sign-up time,
guest count, venue type and the couple's own wishes (free text).

## 5. Source 4: external calendars

| | (a) Thai holidays | (b) Auspicious wedding dates |
|---|---|---|
| Provider | Google public calendar `th#holiday` (real) | Thai astrology publications, compiled by marketing each October for the next year |
| Method | API pull: HTTP GET, timeout 10 s, 3 retries | File drop into `lake/landing/external/` |
| Frequency | Monthly: `ingest_files` pulls it on the 1st of the month or when none exists (decision C15) | Yearly |
| Bronze | `bronze/external/th_holidays/fetched_date=D/basic.ics` (every fetch kept) | `bronze/external/auspicious/year=YYYY/auspicious_YYYY.csv` |
| Checks | parses; at least 15 events per year; Asalha Bucha present for every year up to next year (later years: Lent approximated, flagged) | header and types; level in Low/Medium/High; date inside the stated year |
| On failure | warning alert; the last good fetch is used | warning alert; the previous year's file is used |

```text
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260729
SUMMARY:Asalha Bucha
END:VEVENT
```

```csv
calendar_date,date_type,auspicious_level,description
2027-02-13,ThaiAstrology,High,ฤกษ์มงคลสมรส วันเสาร์ขึ้น 7 ค่ำ
2027-02-20,ThaiAstrology,High,ฤกษ์มงคลสมรส วันเสาร์
```

## 6. Personal data (PDPA-style minimisation)

| Data | Source | Where it may exist | Who can read it |
|---|---|---|---|
| Chat message text | gateway | Bronze chat; `silver_restricted.chat_text` (phone numbers, e-mails and LINE IDs masked) | the ML training and scoring job only |
| Couple's display name, contact first name | CRM | Bronze `crm_restricted`; `silver_restricted.customer_contact`; `aisleco_dw.sales_pii` | Sales role (the call list) |
| Phone number | CRM, tablet | Bronze only; Silver keeps a salted SHA-256 hash for matching | nobody downstream |
| Names on the tablet form | tablet | Bronze only | nobody downstream |
| Everything else in Silver, Gold, marts | — | pseudonymous keys (CustomerID, InquiryID) | per role (Spec 03 §7) |

## 7. A night of ingestion (02:00, Bangkok time)

```
ingest_crm ──┐                                        ┌► fact_inquiry ────────┐
             ├► silver (+ DQ, quarantine, block) ► load_stg ► gold_dims ┼► fact_event_booking ──┼► marts ► reconcile ► finish ► ml_score ┬► ml_monitor
ingest_files ┘  (chat, tablets, external)             ├► fact_staff_workload ─┤                                                    └► ml_forecast
                                                      └► fact_sales_activity ─┘
```

Each step writes a row to `etl.step_run` (rows in, out, rejected; seconds) and can be resumed from the failed step.

## 8. Write-back from the dashboards

The Sales page's "mark contacted" button calls the web API, which inserts a `SalesActivity` row into the **CRM** through a
database role allowed to do nothing else (`crm_app_writer`: `INSERT` on `SalesActivity` only). The CRM stays the system of
record; the activity reaches the warehouse in the next run like any other CRM change. The page shows today's activities by
reading them back from the CRM directly, so a salesperson sees the tick at once.
