"""Silver M2 (Spec 02 §4.1, 4.3, 4.4, 4.6, 4.8): calendar, chat messages, restricted lead text and fair leads, built with the
runner's inputs (preview) over the real Bronze and the warehouse's pipeline run 1."""
import json
import re
from datetime import date

import psycopg
import pytest
import yaml
from psycopg.rows import dict_row

from pipeline.config import Config
from pipeline.silver.run import SQL, preview

FILES = ["05_auspicious_date", "06_calendar_day", "20_chat_message", "21_restricted_lead_text", "25_fair_lead"]
INTENTS = ["ask_price", "ask_availability", "mention_date", "mention_guests", "ask_package_detail", "ask_meeting",
           "price_objection", "family_decision", "competitor", "positive", "commit"]


@pytest.fixture(scope="module")
def con():
    cfg = Config()
    try:
        conn = psycopg.connect(cfg.dw_dsn, row_factory=dict_row)
    except psycopg.OperationalError as e:
        pytest.skip(f"warehouse not reachable: {e}")
    con = preview(cfg, conn, run_id=1, asof=date(2026, 11, 16))
    for f in FILES:          # what run.run() does per file: _all_<name> with the _ columns, <name> published
        name = re.sub(r"^\d+_", "", f)
        con.execute(f"CREATE TABLE _all_{name} AS {(SQL / f'{f}.sql').read_text()}")
        cols = [c[0] for c in con.execute(f"DESCRIBE _all_{name}").fetchall()]
        where = "WHERE _reject IS NULL" if "_reject" in cols else ""
        con.execute(f'CREATE TABLE {name} AS SELECT {", ".join(c for c in cols if not c.startswith("_"))} FROM _all_{name} {where}')
    yield con
    con.close()
    conn.close()


def one(con, sql):
    return con.execute(sql).fetchone()[0]


def rule_check(con, rule_id):
    rules = {r["id"]: r for r in yaml.safe_load((SQL.parent.parent / "quality" / "rules.yml").read_text())}
    return one(con, rules[rule_id]["check"])


def test_calendar(con):
    assert one(con, "SELECT count(*) FROM calendar_day") == 2557
    lent = dict(((y, src), (a, b)) for y, src, a, b in con.execute(
        "SELECT year, lent_source, min(date), max(date) FROM calendar_day WHERE is_buddhist_lent GROUP BY ALL").fetchall())
    assert lent[(2025, "ics")] == (date(2025, 7, 11), date(2025, 10, 7))
    assert lent[(2026, "ics")] == (date(2026, 7, 30), date(2026, 10, 26))
    assert lent[(2028, "approximated")] == (date(2028, 7, 20), date(2028, 10, 17))
    assert one(con, "SELECT count(*) FROM calendar_day WHERE is_valentine AND is_public_holiday") == 0
    assert one(con, "SELECT count(*) FROM _all_auspicious_date WHERE _reject = 'R41'") >= 1


def test_chat_message_keys_and_no_text(con):
    assert one(con, "SELECT count(*) - count(DISTINCT (platform, platform_message_id)) FROM chat_message") == 0
    cols = [c[0] for c in con.execute("DESCRIBE chat_message").fetchall()]
    assert "text" not in cols and not any(c.startswith("_") for c in cols)
    assert {"inquiry_id", "sent_at", "direction", "sender_type", "tracking_code"} <= set(cols)
    assert set(r[0] for r in con.execute("SELECT DISTINCT direction FROM chat_message").fetchall()) == {"In", "Out"}
    assert set(r[0] for r in con.execute("SELECT DISTINCT sender_type FROM chat_message").fetchall()) <= {"customer", "staff", "bot"}


def test_malformed_bodies_rejected(con):
    bodies = [r[0] for r in con.execute("SELECT body FROM bronze.chat").fetchall()]
    malformed = 0
    for b in bodies:
        try:
            json.loads(b)
        except ValueError:
            malformed += 1
    assert malformed > 0
    assert one(con, "SELECT count(*) FROM _all_chat_message WHERE _reject = 'R20'") == malformed


def test_redeliveries_and_reconciliation(con):
    line_redelivered = one(con, "SELECT count(*) FROM bronze.chat WHERE platform = 'line' AND CASE WHEN json_valid(body) "
                                "THEN body ->> '$.events[0].deliveryContext.isRedelivery' = 'true' END")
    assert line_redelivered > 0
    assert one(con, "SELECT sum(_copies) FROM _all_chat_message WHERE platform = 'line'") == line_redelivered
    assert one(con, "SELECT count(*) FROM _all_chat_message WHERE list_contains(_warn, 'R22')") > 0
    assert rule_check(con, "C01") == 0
    assert rule_check(con, "S04") == 0


def test_schema_change_warning_only_from_september(con):
    first, n = con.execute("SELECT min(sent_at), count(*) FROM _all_chat_message WHERE list_contains(_warn, 'S05')").fetchone()
    assert n > 0 and first.date() >= date(2026, 9, 1)


def test_inbound_sent_close_to_received(con):
    assert one(con, "SELECT count(*) FROM _all_chat_message WHERE direction = 'In' AND _reject IS NULL AND _received_at IS NOT NULL "
                    "AND abs(date_diff('second', sent_at, _received_at)) > 86400") == 0


def test_lead_text_masked(con):
    assert one(con, "SELECT count(*) FROM restricted_lead_text") > 0
    assert one(con, "SELECT count(*) FROM restricted_lead_text WHERE text_masked LIKE '%<PHONE>%'") > 0
    assert one(con, r"SELECT count(*) FROM restricted_lead_text WHERE regexp_matches(text_masked, '0\d{1,2}[-\s.]?\d{3}[-\s.]?\d{4}') "
                    r"OR regexp_matches(text_masked, '[\w.+-]+@[\w-]+\.[a-z]{2,}', 'i')") == 0
    assert set(r[0] for r in con.execute("SELECT DISTINCT source FROM restricted_lead_text").fetchall()) == {"chat", "fair"}


def test_fair_lead(con):
    assert one(con, "SELECT count(*) - count(DISTINCT submission_id) FROM fair_lead") == 0
    partial = one(con, "SELECT count(*) FROM bronze.fair_files WHERE filename LIKE '%\\_partial\\_%' ESCAPE '\\'")
    assert partial > 0
    assert one(con, "SELECT count(*) FROM _all_fair_lead WHERE _reject = 'R30' AND _key LIKE '%partial%'") == partial
    cols = [c[0] for c in con.execute("DESCRIBE fair_lead").fetchall()]
    assert not {"phone", "bride_name", "groom_name", "line_id", "wishes"} & set(cols)
    assert one(con, "SELECT count(*) FROM _all_fair_lead WHERE list_contains(_warn, 'R31')") > 0


def test_intents(con):
    for i in INTENTS:
        assert one(con, f"SELECT sum(intent_{i}) FROM chat_message WHERE direction = 'In'") > 0, i
    assert one(con, "SELECT sum(intent_ask_price + intent_positive) FROM chat_message WHERE direction = 'Out'") == 0
    hits = {r[0] for r in con.execute("SELECT intent FROM ref.intent_pattern WHERE regexp_matches('ราคาเท่าไหร่คะ', pattern, 'i')").fetchall()}
    assert hits == {"ask_price"}
