import base64
import csv
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from fastapi.testclient import TestClient

from sources import settings
from sources.chat_gateway import app as gw
from sources.external import auspicious, holidays

LINE_BODY = json.dumps({"destination": "U1", "events": [{"type": "message", "message": {"id": "1", "text": "สวัสดี"}}]},
                       ensure_ascii=False).encode()


def line_sig(body):
    return base64.b64encode(hmac.new(b"dev-line-secret", body, hashlib.sha256).digest()).decode()


def meta_sig(body):
    return "sha256=" + hmac.new(b"dev-meta-secret", body, hashlib.sha256).hexdigest()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAKE_DIR", tmp_path)
    monkeypatch.setattr(settings, "GATEWAY_MODE", "sim")
    return TestClient(gw.app)


def lines(tmp_path, pattern):
    return [json.loads(l) for f in tmp_path.glob(pattern) for l in f.read_text().splitlines()]


def test_line_valid_lands_one_line(client, tmp_path):
    r = client.post("/webhook/line", content=LINE_BODY,
                    headers={"x-line-signature": line_sig(LINE_BODY), "X-Sim-Clock": "2024-03-01T13:02:05"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    f = tmp_path / "landing/chat/platform=line/received_date=2024-03-01/hour=13/events.jsonl"
    [rec] = [json.loads(l) for l in f.read_text().splitlines()]
    assert rec["received_at"].startswith("2024-03-01T13:02:05") and rec["received_at"].endswith("+07:00")
    assert rec["body"].encode() == LINE_BODY and rec["body_sha256"] == hashlib.sha256(LINE_BODY).hexdigest()


def test_line_invalid_json_still_landed(client, tmp_path):
    body = b"{not json"
    assert client.post("/webhook/line", content=body, headers={"x-line-signature": line_sig(body)}).status_code == 200
    assert lines(tmp_path, "landing/chat/platform=line/*/*/events.jsonl")[0]["body"] == "{not json"


def test_bad_signature_rejected(client, tmp_path):
    r = client.post("/webhook/line", content=LINE_BODY, headers={"x-line-signature": "bad"})
    assert r.status_code == 401
    [rec] = lines(tmp_path, "landing/chat/_rejected/*/requests.jsonl")
    assert rec["path"] == "/webhook/line" and rec["headers"]["x-line-signature"] == "bad"
    assert not list(tmp_path.glob("landing/chat/platform=*"))
    assert client.get("/health").json()["rejected"] >= 1


def test_meta_get_challenge(client):
    ok = client.get("/webhook/meta", params={"hub.mode": "subscribe", "hub.verify_token": "dev-verify-token",
                                              "hub.challenge": "12345"})
    assert ok.status_code == 200 and ok.text == "12345"
    bad = client.get("/webhook/meta", params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "1"})
    assert bad.status_code == 403


def test_meta_instagram_and_messenger(client, tmp_path):
    for obj in ("instagram", "page"):
        body = json.dumps({"object": obj, "entry": []}).encode()
        assert client.post("/webhook/meta", content=body, headers={"X-Hub-Signature-256": meta_sig(body)}).status_code == 200
    assert len(lines(tmp_path, "landing/chat/platform=instagram/*/*/events.jsonl")) == 1
    assert len(lines(tmp_path, "landing/chat/platform=messenger/*/*/events.jsonl")) == 1
    body = b"garbage"
    assert client.post("/webhook/meta", content=body, headers={"X-Hub-Signature-256": meta_sig(body)}).status_code == 200
    assert len(lines(tmp_path, "landing/chat/platform=messenger/*/*/events.jsonl")) == 2


def test_outbound_needs_api_key(client, tmp_path):
    body = b'{"message_id": "1"}'
    assert client.post("/outbound", content=body).status_code == 401
    assert client.post("/outbound", content=body, headers={"X-Api-Key": "wrong"}).status_code == 401
    assert client.post("/outbound", content=body, headers={"X-Api-Key": "dev-inbox-key"}).status_code == 200
    assert len(lines(tmp_path, "landing/chat/platform=outbound/*/*/events.jsonl")) == 1
    assert "dev-inbox-key" not in "".join(f.read_text() for f in tmp_path.rglob("*.jsonl"))


def test_sim_clock_ignored_in_production(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "GATEWAY_MODE", "production")
    client.post("/webhook/line", content=LINE_BODY,
                headers={"x-line-signature": line_sig(LINE_BODY), "X-Sim-Clock": "2024-03-01T13:02:05"})
    assert not list(tmp_path.glob("landing/chat/platform=line/received_date=2024-03-01"))
    assert len(lines(tmp_path, "landing/chat/platform=line/*/*/events.jsonl")) == 1


def test_concurrent_posts_intact(client, tmp_path):
    def post(i):
        body = json.dumps({"events": [{"message": {"id": str(i), "text": "x" * 5000}}]}).encode()
        return client.post("/webhook/line", content=body, headers={"x-line-signature": line_sig(body)}).status_code
    with ThreadPoolExecutor(10) as ex:
        assert set(ex.map(post, range(50))) == {200}
    recs = lines(tmp_path, "landing/chat/platform=line/*/*/events.jsonl")
    assert len(recs) == 50
    assert sorted(int(json.loads(r["body"])["events"][0]["message"]["id"]) for r in recs) == list(range(50))


def test_holidays_fixture_has_asalha_bucha():
    asalha = {d.year for d, name in holidays.parse(holidays.FIXTURE) if name == "Asalha Bucha"}
    # the real Google feed stops listing lunar holidays after 2027 (checked 16 Nov 2026)
    assert set(range(2021, 2028)) <= asalha


def test_holidays_offline_fetch_and_latest(tmp_path):
    p = holidays.fetch(tmp_path, date(2026, 11, 1), offline=True)
    assert p == tmp_path / "bronze/external/th_holidays/fetched_date=2026-11-01/basic.ics"
    holidays.fetch(tmp_path, date(2026, 12, 1), offline=True)
    assert holidays.latest(tmp_path).parent.name == "fetched_date=2026-12-01"


def test_holidays_fetch_failure_raises(tmp_path):
    with pytest.raises(holidays.HolidayFetchError):
        holidays.fetch(tmp_path, date(2026, 11, 1), url="http://127.0.0.1:9/none.ics", timeout=0.5, retries=1)


@pytest.mark.parametrize("year", [2024, 2025, 2026, 2027, 2028])
def test_auspicious_realistic(tmp_path, year):
    p = auspicious.write_year(year, tmp_path)
    assert p.read_bytes() == auspicious.write_year(year, tmp_path / "again").read_bytes()
    rows = list(csv.DictReader(p.open(encoding="utf-8")))
    days = [date.fromisoformat(r["calendar_date"]) for r in rows]
    lent_start, lent_end = auspicious.lent(year)
    assert 55 <= len(rows) <= 65 and all(d.year == year for d in days)
    assert {r["auspicious_level"] for r in rows} <= {"High", "Medium", "Low"}
    assert sum(lent_start <= d <= lent_end for d in days) <= 0.05 * len(days)
    assert sum(d.month in (11, 12, 1, 2) for d in days) >= 0.55 * len(days)
    if year == 2027:
        story = {(r["calendar_date"], r["date_type"], r["auspicious_level"]) for r in rows}
        assert {("2027-02-13", "ThaiAstrology", "High"), ("2027-02-20", "ThaiAstrology", "High")} <= story


def test_auspicious_bad_row(tmp_path):
    rows = list(csv.DictReader(auspicious.write_year(2027, tmp_path, bad_row=True).open(encoding="utf-8")))
    assert [r["auspicious_level"] for r in rows].count("VeryHigh") == 1
