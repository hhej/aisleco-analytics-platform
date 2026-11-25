# Spec 02. The data lake: landing, Bronze, Silver, quarantine, sandbox

Part of the [v2 plan](PLAN.md). Answers: where each zone is on disk, what format each file has, how Bronze becomes Silver
(every step), and which data-quality rules stop or set aside bad data.

## 1. Zones (Chapter 4 mapping)

In Chapter 6 terms the lake side is **ELT** (raw data is loaded first, then transformed) and the warehouse side is **ETL**
(transformed in Silver, then loaded into Gold).

The design is the **zone-based architecture** of Chapter 4 (transient landing → raw → trusted → refined/restricted → sandbox),
with the warehouse as the consumption layer. Medallion names are used because they are short.

| Zone (Ch.4 name) | Medallion name | Folder | Rule |
|---|---|---|---|
| Transient landing | Landing | `lake/landing/` | Sources write here. Files stay until ingestion moves them (at most a day). |
| Raw zone | **Bronze** | `lake/bronze/` | Exactly as received, plus a manifest. Append-only. Never edited, never deleted before its retention date. |
| Trusted zone | **Silver** | `lake/silver/` | Cleaned, typed, de-duplicated, conformed, no personal data. Parquet. Rebuilt from Bronze each run. |
| Refined zone with restricted access | Silver (restricted) | `lake/silver_restricted/` | Masked chat text and the couple's display names. Read only by the ML job and the `sales_pii` load. |
| (dead letter) | Quarantine | `lake/quarantine/` | Rows and files that failed a rule, with the reason. |
| Sandbox | Sandbox | `lake/sandbox/` | Ad-hoc analysis. Nothing in production reads it. |
| Govern zone (Multi-Zone Functional Architecture) | — | `aisleco_dw.etl` + `catalog/` | Run log, file checkpoints, DQ rules and results, quarantine index, alerts, the data catalog. |

Gold is not a folder: it is the star schema inside PostgreSQL (Spec 03). The lake feeds the warehouse, as in Chapter 1's
"data pipeline → data lake → data warehouse → dashboards" picture.

## 2. Folder layout

```
lake/
├── landing/                                         transient
│   ├── chat/platform=line|messenger|instagram|outbound/received_date=2026-11-16/hour=13/events.jsonl
│   ├── chat/_rejected/received_date=…/requests.jsonl       bad signatures
│   ├── fair_tablet/TAB-02_41.json                           dropped by the tablets on reconnect
│   └── external/auspicious_2027.csv                         dropped by marketing once a year
├── bronze/                                          raw, immutable
│   ├── crm/Inquiry/load_date=2026-11-16/run=00042/Inquiry.csv
│   ├── crm/Inquiry/load_date=2026-11-16/run=00042/Inquiry_keys.csv
│   ├── crm/_manifests/run=00042/manifest.json
│   ├── crm_restricted/CustomerChannelAccount/load_date=…/run=…/CustomerChannelAccount.csv   (mode 700)
│   ├── chat/platform=line/received_date=2026-11-16/hour=13/events.jsonl                   (contains text)
│   ├── fair_tablet/received_date=2026-10-17/TAB-02_41_9f3a1c2e.json
│   ├── external/th_holidays/fetched_date=2026-11-01/basic.ics
│   └── external/auspicious/year=2027/auspicious_2027.csv
├── silver/<table>/data.parquet                      trusted, one folder per table (§4)
├── silver/_prev/<table>/data.parquet                the previous build, for rollback
├── silver_restricted/lead_text/data.parquet         masked text (chat + tablet wishes)
├── silver_restricted/customer_contact/data.parquet  display names for the Sales list
├── quarantine/run=00042/<source>/<table>.jsonl      rejected rows with the rule and reason
├── sandbox/<name>/…
└── mlflow/                                          model artifacts (Spec 04)
```

Partition keys are the dates the platform **received** the data (`received_date`, `load_date`, `fetched_date`), never a
business date, so a late or corrected record lands in a new partition instead of rewriting an old one.

## 3. Formats, metadata, retention, access

| Zone | Format | Metadata added | Retention | Access (one OS user on the demo Mac; folder modes and this table define the multi-user setup) |
|---|---|---|---|---|
| Landing | as sent | none | until moved; rejected requests 30 days | sources write, pipeline reads |
| Bronze CRM | CSV (PostgreSQL `COPY`) + key list + `manifest.json` (rows, max `UpdatedAt`, SHA-256, snapshot time, run) | manifest | indefinite (about 10 MB a year) | data team |
| Bronze chat | JSON lines: `received_at`, `platform`, raw `body` string, `body_sha256` | the gateway's envelope | text purged 2 years after the inquiry closes (metadata kept) | data team; text only by the restricted ML job |
| Bronze tablet, external | as dropped | file checkpoint row in `etl.file_checkpoint` (name, size, SHA-256, run) | indefinite | data team |
| Bronze CRM restricted | CSV | manifest | last 90 days of snapshots | restricted job only |
| Silver | Parquet, ZSTD; one file per table at 1× volume, partitioned by year above 1 M rows | `_run_id` (the run that delivered the row) is used inside the build for the reject-rate rule and the quarantine, not published; the run log records which run built each table | current build + previous build | data team, ML, warehouse load |
| Quarantine | JSON lines | rule, severity, reason, full record, first-seen run, resolved run | 90 days after it is resolved | data team |

Silver is rebuilt **in full** from Bronze on every run, then swapped in atomically: tables are written to
`silver/_build/run=N/`, all Silver checks run there, and only then each table folder is renamed into place (the old one
moves to `_prev`). A failed build leaves the published Silver untouched. Rebuilding is deterministic: the same Bronze gives
byte-identical Silver content. At this volume a full rebuild takes seconds in DuckDB; the target is under 20 s at 1× and under
60 s at 10× volume. Incremental Silver is the upgrade path if Bronze ever grows to billions of rows.

Inspecting any zone: `duckdb -c "SELECT * FROM 'lake/silver/inquiry/*.parquet' LIMIT 5"` (DuckDB CLI installed in M0).

## 4. Silver tables (every transformation, step by step)

13 tables: 11 trusted (`auspicious_date`, `calendar_day`, `channel`, `package`, `employee_contract`, `chat_message`, `fair_lead`, `inquiry`, `event`, `assignment`, `sales_activity`) and 2 restricted (`lead_text`, `customer_contact`).

Notation: **in** = Bronze inputs, **grain** = what one row is, **steps** = in order, **out** = main columns.
All SQL is in `pipeline/silver/sql/NN_<table>.sql` (DuckDB dialect), one file per table, with this spec's text as its header.

### 4.1 `calendar_day`
- **in**: latest `bronze/external/th_holidays` fetch; every `bronze/external/auspicious` year file.
- **grain**: one calendar day, 2022-01-01 to 2028-12-31.
- **steps**:
  1. Generate the date spine.
  2. Parse ICS `VEVENT`s into (date, name). Several holidays on one date are joined with " / ".
  3. Buddhist Lent per year: A = date of "Asalha Bucha"; Lent = A + 1 (Khao Phansa) through A + 89 (Ok Phansa).
     Check: 2025 gives 11 Jul – 7 Oct; 2026 gives 30 Jul – 26 Oct. The feed has no lunar holidays after 2027; for such a
     year Lent is approximated as 20 Jul – 17 Oct and `lent_source = 'approximated'` marks it (only far-future dates are affected).
  4. Auspicious level = the highest level over all date types on that day (High > Medium > Low); types joined.
  5. Flags: high season (month in Nov, Dec, Jan, Feb), Valentine's Day (from the ICS), weekend, public holiday.
  6. Week start = the Monday of the ISO week.
- **out**: `date, week_start, iso_year, iso_week, month, year, is_weekend, holiday_name, is_public_holiday, is_buddhist_lent,
  is_high_season, is_valentine, auspicious_level, auspicious_types, lent_source` (`ics` or `approximated`).

### 4.2 `channel`, `package`, `employee_contract`
- **in**: the latest snapshot of MarketingChannel; Package; Employee + EmployeeContract + EmployeeRole.
- **grain**: channel; package price version; employee contract version.
- **steps**: type casting; trimming; code-and-description pairs kept (Ch.5); `package_family` = the trimmed package name
  (package names carry no price version today); contract rows joined to the employee's name and the role name; contracts checked for overlap (rule R13).
- **out (employee_contract)**: `contract_id, employee_id, employee_code, nickname, role_name, role_group, employment_type,
  standard_weekly_hours, max_concurrent_events, effective_from, effective_to (null = open), hire_date, status`.

### 4.3 `chat_message`
- **in**: `bronze/chat/**` (inbound LINE, Messenger, Instagram; outbound log); latest CRM `ChatMessage`, `Employee`.
- **grain**: one real message, unique on (platform, platform_message_id).
- **steps**:
  1. Read every JSON line; parse `body`. LINE: one row per element of `events[]` of type `message`. Meta: one row per
     `entry[].messaging[]` element. Outbound: one row per record.
  2. Key: LINE `message.id`, Meta `message.mid`, outbound `message_id`.
  3. **De-duplicate**: keep the earliest `received_at` per key. Count the extra copies (re-deliveries) for the run report.
  4. **Time**: platform `timestamp` (epoch ms, UTC) → `Asia/Bangkok` local timestamp; outbound `sent_at` (UTC ISO) → local.
  5. **Tracking code**: a leading `#[A-Z0-9]+` in the text (`#TIKTOK`, `#WEB`, `#FAIR`), else Meta `referral.ad_id`, else null.
  6. **Sender**: inbound → `customer`; outbound → `staff` or `bot`; `staff_code` → `employee_id`.
  7. **Link** to the CRM: join `ChatMessage` on `PlatformMessageID` → `inquiry_id`, `customer_account_id`.
     A Bronze message with no CRM row, or a CRM row with no Bronze message, is flagged (rule R24).
  8. **Text → features** (the text itself is not written to this table): `text_len`, `lang` (`th` if over 50 % Thai characters,
     `en` if none, else `mixed`), `has_question`, and intent flags from the rule tagger (§4.4).
  9. **Text → restricted**: the text with phone numbers, e-mail addresses and LINE IDs replaced by `<PHONE>`, `<EMAIL>`,
     `<LINEID>` goes to `silver_restricted.lead_text`.
- **out**: `platform, platform_message_id, sent_at, direction, sender_type, employee_id, inquiry_id, customer_account_id,
  tracking_code, text_len, lang, has_question, intent_*` (11 flags).

### 4.4 Intent tagger (unstructured → structured)
`pipeline/silver/intents.yml`: one regular expression per intent, Thai and English, applied to the customer's messages.

| Intent flag | Example patterns |
|---|---|
| `ask_price` | ราคา, เท่าไหร่, ค่าใช้จ่าย, how much, price, cost |
| `ask_availability` | ว่างไหม, คิวว่าง, available, still free |
| `mention_date` | a day + Thai or English month (`13 ก.พ.`, `Feb 13`), or `dd/mm` |
| `mention_guests` | a number followed by คน, ท่าน, แขก, guests, pax |
| `ask_package_detail` | รวมอะไรบ้าง, มีอะไรบ้าง, include, what's in |
| `ask_meeting` | นัด, เข้าไปคุย, consult, meeting, visit the office |
| `price_objection` | แพง, เกินงบ, ลดได้ไหม, expensive, discount, over budget |
| `family_decision` | ปรึกษาที่บ้าน, ปรึกษาผู้ใหญ่, ถามแฟน, discuss with family |
| `competitor` | ที่อื่น, เทียบราคา, another planner, compare |
| `positive` | ขอบคุณ, ชอบมาก, สวย, love, perfect, great |
| `commit` | ตกลง, จอง, มัดจำ, เซ็นสัญญา, book, deposit, sign |

The tagger is transparent on purpose: every flag can be explained to a salesperson. A data-driven text representation
(TF-IDF) is tested inside the model (Spec 04 §4) and kept only if it adds measured lift.

### 4.5 `inquiry`
- **in**: every `bronze/crm/Inquiry` run; the latest `Inquiry_keys.csv`; Silver `chat_message`, `channel`.
- **grain**: one inquiry (current version).
- **steps**:
  1. Union all runs; keep the version with the latest `UpdatedAt` per `InquiryID` (ties: the later run).
  2. Drop IDs missing from the latest key list (deleted in the CRM).
  3. **Budget**: `BudgetRange` free text → canonical band (`300k-500k`, `500k-1M`, `1M-1.5M`, `1.5M+`, `Unknown`) by regex
     (e.g. "around 600k", "6 แสน", "600,000" → `500k-1M`); `budget_low_thb`, `budget_high_thb`,
     `budget_midpoint_thb = (low + high) / 2` (`1.5M+` → 1,500,000–2,000,000).
  4. **Messages** (from `chat_message`, this inquiry only): `first_customer_msg_at` = earliest inbound;
     `first_staff_reply_at` = earliest outbound with `sender_type = staff` after it (bot messages never count);
     `response_minutes = first_staff_reply_at − first_customer_msg_at` in minutes; `response_band`: `< 1 h`, `1–24 h`, `> 24 h`,
     `no logged reply`, `in person` (fair, walk-in, referral without chat); `first_msg_hour`; `arrived_outside_hours`
     (before 09:00 or from 20:00); `customer_msgs`, `staff_msgs`; `last_customer_msg_at`.
  5. **Stages**: stage timestamps kept as they are; an out-of-order stage (e.g. quoted before the inquiry) is flagged (R04),
     not changed.
  6. **Channel check**: the CRM's `ChannelID` is kept; if the first message carries a tracking code of another channel, rule R03b
     flags it (a couple who deleted the pre-typed `#TIKTOK` stays LINE, as in Rev10).
- **out**: CRM columns + the derived columns above.

### 4.6 `fair_lead`
- **in**: `bronze/fair_tablet/**`.
- **grain**: one sign-up (`submission_id`).
- **steps**: explode `submissions[]`; keep the first sync of each `submission_id` (repeated batches counted);
  `phone_hash = SHA-256(salt ‖ digits of phone)`; names and phone dropped; `wishes` → `silver_restricted.lead_text`;
  `sync_delay_hours = synced_at − submitted_at`; `crm_inquiry_id` checked against `inquiry` (R32).
- **out**: `submission_id, device_id, fair_name, fair_date, submitted_at, sync_delay_hours, inquiry_id, wedding_date,
  date_flexible, guests, venue_type, budget_band, package_interest, consent_marketing, phone_hash`.

### 4.7 `event`, `assignment`, `sales_activity`
- **in**: their Bronze CRM runs and key lists; Silver `inquiry`.
- **grain**: booked wedding; staff assignment; logged follow-up.
- **steps** (same latest-version and delete logic as 4.5), then:
  - `event`: `is_cancelled`; `booking_lead_days = EventDate − CreatedDate`; guest count and complexity checked (R08).
  - `assignment`: `assigned_at_effective = coalesce(AssignedDate, event.CreatedDate)` (Rev10 rule);
    `event_day_hours = min(12, WorkloadHours)`; `planning_hours = WorkloadHours − event_day_hours`.
  - `sales_activity`: outcome and type checked; `on_call_list = (Source = 'CallList')`.

### 4.8 Restricted: `lead_text`, `customer_contact`
- `lead_text`: (source `chat`|`fair`, message or submission id, inquiry_id, sent_at, direction, sender_type, masked text).
- `customer_contact`: `customer_id`, `display_name` (the platform name of the couple's most recent account),
  `contact_first_name` (primary contact), `phone_masked` (`081-xxx-5678`), `phone_hash`.

## 5. Data-quality rules

Rules are data (`pipeline/quality/rules.yml`, loaded into `etl.dq_rule`), not hidden in code. Every evaluation is stored in
`etl.dq_result` (run, rule, rows checked, rows failed). Severities:
**reject** (row to quarantine, not loaded), **warn** (logged, row loaded; an unknown key becomes the −1 member),
**block** (the run stops before Gold; nothing published), **info** (counted).

39 rules are defined; 37 are checked on every run. S02 (a master snapshot shrank by more than 20 %) and C04 (Silver keys differ
from the CRM's key list) are defined but have no check yet; deletes are caught today by the primary-key list at ingestion.

| ID | Source / table | Rule | Severity |
|---|---|---|---|
| R01 | CRM Inquiry | `InquiryID` not null | reject |
| R02 | CRM Inquiry | `InquiryDate` not null and between 2022-01-01 and the run date | reject |
| R03 | CRM Inquiry | channel exists | warn (unknown channel) |
| R03b | CRM Inquiry | tracking code of the first message matches the channel | warn |
| R04 | CRM Inquiry | stage timestamps not before `InquiryDate`; quote not before consultation | warn |
| R05 | CRM Inquiry | budget text maps to a band | warn (`Unknown`) |
| R06 | CRM Inquiry | `Converted` has an event; `Lost` has a reason | warn |
| R07 | CRM ChatMessage | staff message has an employee | reject |
| R08 | CRM Event | `EventDate` not null; guests 20–2,000; complexity valid | reject / reject / warn |
| R09 | CRM Event | `InquiryID` exists or is null (walk-in) | warn |
| R10 | CRM Assignment | hours 0–300; event and employee exist (a child of a rejected parent is rejected and re-read every run) | reject |
| R11 | CRM Assignment | assigned on or before the wedding | warn (all hours go in the wedding week) |
| R12 | CRM SalesActivity | inquiry exists; activity not before the inquiry | reject / warn |
| R13 | CRM EmployeeContract | no overlapping contract periods | block |
| R20 | Chat | body is JSON with the platform's required fields | reject |
| R21 | Chat | message time within [received − 7 days, received + 5 min] | warn |
| R22 | Chat | re-delivered duplicate | info (dropped) |
| R23 | Chat outbound | staff code maps to an employee | warn |
| R24 | Chat ↔ CRM | Bronze message has a CRM row and vice versa | warn |
| R30 | Tablet | batch file is valid JSON | reject (file) |
| R31 | Tablet | repeated `(device_id, batch_seq)` | info (dropped) |
| R32 | Tablet | `crm_inquiry_id` present and known | warn |
| R33 | Tablet | phone present and 9–10 digits | warn |
| R40 | Holidays ICS | parses; ≥ 15 events per year; Asalha Bucha every year | warn (last good fetch used) |
| R41 | Auspicious CSV | level in Low/Medium/High; date inside its year | reject |
| S01 | any table | rejected rows > 5 % of the run's new rows | block |
| S02 | CRM master snapshot | row count fell by more than 20 % since the last run | block |
| S03 | CRM | no child row points to a missing parent after rejections | block |
| S04 | Chat | newest message older than 26 h at run time (gateway down?) | warn |
| S05 | Chat | unknown top-level field in a payload (schema change) | warn (kept in Bronze, ignored by Silver) |
| C01 | Bronze → Silver | inbound events = unique messages + duplicates + rejected | block |
| C02–C05 | Silver → Gold → marts | Spec 03 §8 | block |

A rejected row is not lost. When the source is fixed (a CRM row's `UpdatedAt` moves; a corrected file is dropped again),
the next run picks it up and the quarantine entry is marked with the run that resolved it.

## 6. Worked example: one LINE message, landing → Silver

1. 13:02, Mint taps a TikTok link; LINE opens with `#TIKTOK` typed; she sends "ว่างวันที่ 13 ก.พ. ไหมคะ แขกประมาณ 300 คนค่ะ".
2. LINE POSTs the JSON of Spec 01 §3.1. The gateway checks the signature and appends the line to
   `landing/chat/platform=line/received_date=2026-11-16/hour=13/events.jsonl`. LINE did not get the 200 in time and re-sends:
   a second identical line.
3. 02:00 next night: `ingest_chat` moves the closed file to `bronze/chat/…/hour=13/events.jsonl` (2 lines, SHA-256 recorded).
4. `silver_build`, `chat_message`: 2 lines → 1 message (1 re-delivery counted, rule R22); `1794808920000` ms UTC →
   `2026-11-16 13:02:00` Bangkok; tracking code `#TIKTOK`; `ask_availability = 1`, `mention_date = 1`, `mention_guests = 1`;
   masked text to `lead_text`.
5. `inquiry`: May's reply at 13:09 is the first staff outbound → `response_minutes = 7`, `response_band = '< 1 h'`.
6. Gold and the dashboards take it from there (Spec 03 §9).
