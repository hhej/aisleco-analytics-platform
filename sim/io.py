"""Delivers a simulated day through the real doors (Spec 06 §1): rows into the CRM database, signed webhook
POSTs to the chat gateway, files into the landing drop folders."""
import base64
import hashlib
import hmac
import json
import os
from datetime import date, datetime, timedelta, timezone

import httpx
import psycopg

from sources import settings

MASTER = ["EmployeeRole", "MarketingChannel", "Package", "Employee", "EmployeeContract", "Customer", "CustomerChannelAccount", "CustomerContact"]
ORDER = MASTER + ["Inquiry", "ChatMessage", "Event", "EventEmployeeAssignment", "SalesActivity"]
KEY = {"EmployeeRole": "RoleID", "MarketingChannel": "ChannelID", "Package": "PackageID", "Employee": "EmployeeID",
       "EmployeeContract": "ContractID", "Customer": "CustomerID", "CustomerChannelAccount": "CustomerAccountID",
       "CustomerContact": "CustomerContactID", "Inquiry": "InquiryID", "ChatMessage": "ChatMessageID", "Event": "EventID",
       "EventEmployeeAssignment": "EventEmployeeAssignmentID", "SalesActivity": "ActivityID"}
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8001")
LINE_NEW_FIELD_FROM = date(2026, 9, 1)     # LINE adds a field to message events (schema-change test, Spec 06 §7)
q = lambda name: f'"{name}"'


def tbl(t):
    return f'"{"master" if t in MASTER else "transaction"}".{q(t)}'


def write_crm(conn, day):
    """All of one day's CRM changes in one transaction. The simulator supplies CreatedAt/UpdatedAt itself
    (session flag aisleco.keep_updated_at), so rows carry simulated time."""
    with conn.transaction():
        conn.execute("SET LOCAL aisleco.keep_updated_at = on")
        # A real CRM stamps commit time, so no commit is older than one already in the table. Simulated time can be
        # (a day written at 01:00 after a midday demo run): the incremental read would skip it, so lift it above the max.
        # shortcut: a hard delete of a table's newest row lowers the floor below the watermark; also read etl.watermark if that bites.
        floor = {t: conn.execute(f'SELECT max("UpdatedAt") FROM {tbl(t)}').fetchone()[0] for t in {e[1] for e in day.crm if e[0] != "delete"}}
        stamp = lambda t, ts: floor[t] + timedelta(microseconds=1) if floor[t] and ts and ts <= floor[t] else ts
        inserts = {}
        for e in day.crm:
            if e[0] == "insert":
                inserts.setdefault(e[1], []).append(e[2])
        updates = sorted((e for e in day.crm if e[0] == "update"), key=lambda e: e[4])
        with conn.cursor() as cur:
            # master-data updates first: a contract's end date must be written before its successor is inserted
            for _, t, key, changes, ts in (u for u in updates if u[1] in MASTER):
                cur.execute(f"UPDATE {tbl(t)} SET " + ", ".join(f"{q(c)} = %s" for c in changes) + f', "UpdatedAt" = %s WHERE {q(KEY[t])} = %s',
                            [*changes.values(), stamp(t, ts), key])
            for t in ORDER:
                rows = inserts.get(t)
                if rows:
                    cols = list(rows[0])
                    cur.executemany(f"INSERT INTO {tbl(t)} ({', '.join(map(q, cols))}) VALUES ({', '.join(['%s'] * len(cols))})",
                                    [[stamp(t, r[c]) if c == "UpdatedAt" else r[c] for c in cols] for r in rows])
            for _, t, key, changes, ts in (u for u in updates if u[1] not in MASTER):
                sets = ", ".join(f"{q(c)} = %s" for c in changes) + ', "UpdatedAt" = %s'
                cur.execute(f"UPDATE {tbl(t)} SET {sets} WHERE {q(KEY[t])} = %s", [*changes.values(), stamp(t, ts), key])
            for op, t, key, _, ts in (e for e in day.crm if e[0] == "delete"):
                for child in ("ChatMessage", "SalesActivity"):
                    cur.execute(f'DELETE FROM {tbl(child)} WHERE "InquiryID" = %s', [key])
                cur.execute(f'DELETE FROM {tbl(t)} WHERE {q(KEY[t])} = %s', [key])


def sync_sequences(conn):
    for t, k in KEY.items():
        conn.execute(f"SELECT setval(pg_get_serial_sequence('{tbl(t)}', '{k}'), coalesce((SELECT max({q(k)}) FROM {tbl(t)}), 0) + 1, false)")


def epoch_ms(local: datetime) -> int:
    return int(local.replace(tzinfo=timezone(timedelta(hours=7))).timestamp() * 1000)


class Gateway:
    def __init__(self, rng, url=GATEWAY):
        self.http = httpx.Client(base_url=url, timeout=10)
        self.rng = rng

    def post(self, path, body: bytes, headers, clock):
        r = self.http.post(path, content=body, headers={**headers, "X-Sim-Clock": clock.isoformat(), "Content-Type": "application/json"})
        if r.status_code not in (200, 401):
            raise RuntimeError(f"gateway {path}: {r.status_code} {r.text}")

    def line(self, body: bytes, clock, sign=True):
        sig = base64.b64encode(hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()).decode() if sign else "invalid"
        self.post("/webhook/line", body, {"x-line-signature": sig}, clock)

    def meta(self, body: bytes, clock):
        sig = "sha256=" + hmac.new(settings.META_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        self.post("/webhook/meta", body, {"X-Hub-Signature-256": sig}, clock)

    def deliver(self, day):
        rng = self.rng
        for t, kind, p, _ in sorted(day.posts, key=lambda x: x[0]):
            if kind == "in" and p["platform"] == "line":
                msg = {"id": p["message_id"], "type": "text", "quoteToken": f"q{rng.getrandbits(64):x}", "text": p["text"]}
                if t.date() >= LINE_NEW_FIELD_FROM:
                    msg["markAsReadToken"] = f"m{rng.getrandbits(64):x}"
                ev = {"type": "message", "mode": "active", "timestamp": epoch_ms(t), "source": {"type": "user", "userId": p["user_id"]},
                      "webhookEventId": f"01J{rng.getrandbits(110):X}"[:26], "deliveryContext": {"isRedelivery": False},
                      "replyToken": f"{rng.getrandbits(128):032x}", "message": msg}
                self.line(json.dumps({"destination": "U4af4980629aisleco", "events": [ev]}, ensure_ascii=False).encode(), t)
                if rng.random() < .02:        # LINE did not get our 200 in time and re-delivers (R22)
                    ev["deliveryContext"]["isRedelivery"] = True
                    self.line(json.dumps({"destination": "U4af4980629aisleco", "events": [ev]}, ensure_ascii=False).encode(),
                              t + timedelta(seconds=rng.randint(30, 120)))
            elif kind == "in":
                m = {"sender": {"id": p["user_id"]}, "recipient": {"id": "104729381234567"}, "timestamp": epoch_ms(t),
                     "message": {"mid": p["message_id"], "text": p["text"]}}
                if p.get("ad_id"):
                    m["referral"] = {"source": "ADS", "type": "OPEN_THREAD", "ad_id": p["ad_id"]}
                body = {"object": "instagram" if p["platform"] == "instagram" else "page",
                        "entry": [{"id": "104729381234567", "time": epoch_ms(t) + 123, "messaging": [m]}]}
                self.meta(json.dumps(body, ensure_ascii=False).encode(), t)
                if rng.random() < .02:
                    self.meta(json.dumps(body, ensure_ascii=False).encode(), t + timedelta(seconds=rng.randint(30, 120)))
            elif kind == "out":
                body = {"message_id": p["message_id"], "platform": p["platform"], "to_user_id": p["user_id"],
                        "sent_at": (t - timedelta(hours=7)).isoformat(timespec="seconds") + "Z", "sender_type": p["sender_type"],
                        "staff_code": p["staff_code"], "text": p["text"]}
                self.post("/outbound", json.dumps(body, ensure_ascii=False).encode(), {"X-Api-Key": settings.INBOX_API_KEY}, t)
            elif kind == "bad_signature":
                self.line(b'{"destination": "U0000", "events": []}', t, sign=False)
            elif kind == "malformed":         # signed correctly but cut off (R20)
                self.line(b'{"destination": "U4af4980629aisleco", "events": [{"type": "message", "timest', t)


def write_files(day, lake=None):
    lake = lake or settings.LAKE_DIR
    for rel, text in day.files:
        path = lake / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
