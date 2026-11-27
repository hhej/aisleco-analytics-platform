"""Web app (Spec 05): logins, role limits enforced by the API and by PostgreSQL, endpoints on the loaded warehouse,
the write-back to the CRM, and the KPI catalog behind every tile. Needs the loaded aisleco_dw and aisleco_crm."""
import re
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.api import sales
from app.api.db import CRM, DW, as_role, connect
from app.api.main import ROOT, app

PW = "aisleco2026"
USERS = {"may": "sales", "ken": "sales", "nok": "sales", "som": "ops", "ann": "exec", "dev": "data"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # startup applies app/api/schema.sql and seeds users if none exist
        yield c


def login(c, user):
    c.cookies.clear()
    r = c.post("/api/login", json={"username": user, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("user,role", USERS.items())
def test_each_role_logs_in(client, user, role):
    me = login(client, user)
    assert me["role"] == role and me["pages"] and me["data_as_of"]["asof_date"]
    assert client.get("/api/me").json()["username"] == user


def test_wrong_password_and_no_session(client):
    client.cookies.clear()
    assert client.post("/api/login", json={"username": "may", "password": "nope"}).status_code == 401
    assert client.get("/api/ops/capacity").status_code == 401


FORBIDDEN = {
    "may": ["/api/ops/capacity", "/api/ops/date-check?d=2027-02-13", "/api/exec/overview", "/api/data/health"],
    "som": ["/api/sales/today", "/api/sales/performance", "/api/exec/overview", "/api/data/health"],
    "ann": ["/api/sales/today", "/api/data/health"],
}


@pytest.mark.parametrize("user", FORBIDDEN)
def test_roles_get_403_elsewhere(client, user):
    login(client, user)
    for path in FORBIDDEN[user]:
        assert client.get(path).status_code == 403, path
    if user != "may":
        assert client.post("/api/sales/activity", json={"inquiry_id": 1, "activity_type": "Call", "outcome": "Reached"}).status_code == 403
    assert client.post("/api/demo/run").status_code == 403
    assert client.get("/api/demo/latest").status_code == 403
    assert client.get("/api/demo/trace?inquiry_id=1").status_code == 403


@pytest.mark.parametrize("role,sql", [
    ("sales", "SELECT 1 FROM mart_ops.v_capacity_grid LIMIT 1"),
    ("sales", "SELECT 1 FROM mart_exec.v_monthly_kpis LIMIT 1"),
    ("sales", 'SELECT 1 FROM app."user" LIMIT 1'),
    ("ops", "SELECT 1 FROM mart_sales.v_funnel LIMIT 1"),
    ("exec", "SELECT 1 FROM sales_pii.customer_contact LIMIT 1"),
    ("exec", "SELECT 1 FROM mart_sales.v_today_list LIMIT 1"),
    ("data", 'SELECT 1 FROM app."user" LIMIT 1'),
])
def test_database_refuses(role, sql):
    """Even a bug in the API could not leak: the connection's database role is refused."""
    with as_role(role) as q, pytest.raises(psycopg.errors.InsufficientPrivilege):
        q.one(sql)


def test_executive_overview(client):
    login(client, "ann")
    d = client.get("/api/exec/overview").json()
    assert d["tiles"]["bookings"]["value"] is not None and d["tiles"]["ops_utilization_quarter"]["value"]
    assert 3 <= len(d["what_changed"]) <= 5 and d["channels"] and d["capacity_outlook"] and d["lost_value"]
    assert d["report_month"] < d["current_month"] and d["mature_month"] <= d["report_month"]


def test_capacity_planner(client):
    login(client, "som")
    d = client.get("/api/ops/capacity").json()
    assert d["grid"] and d["role_weeks"] and d["hours_per_wedding"] and d["tiles"]["peak_utilization_4w"]
    cell = d["grid"][0]
    w = client.get(f"/api/ops/week?employee_id={cell['employee_id']}&week_start={cell['week_start']}")
    assert w.status_code == 200 and w.json()["cell"]["nickname"] == cell["nickname"]
    c = client.get("/api/ops/date-check?d=2027-02-13").json()
    assert c["date"]["status"] in ("Room", "Tight", "Full")
    assert all(a["status"] == "Room" for a in c["alternatives"])
    assert client.get("/api/ops/date-check?d=2020-01-01").status_code == 404


def test_sales_performance_and_wilson(client):
    login(client, "ann")  # the owner may read it too (no names)
    d = client.get("/api/sales/performance").json()
    assert d["funnel"] and d["channels"] and d["response_bands"] and d["response_hours"] and d["quote_bins"]
    with as_role("sales") as q:  # same interval as the mart's formula
        r = q.one("SELECT booked, decided, ci_low, ci_high FROM mart_sales.v_conversion_by_channel_month WHERE decided > 20 LIMIT 1")
    lo, hi = sales.wilson(int(r["booked"]), int(r["decided"]))
    assert abs(lo - float(r["ci_low"])) < 1e-3 and abs(hi - float(r["ci_high"])) < 1e-3


def test_data_health(client):
    login(client, "dev")
    d = client.get("/api/data/health").json()
    assert d["runs"] and d["steps"] and d["dq"] and "lead_scores" in d["models"]


def test_demo_trace_follows_one_couple(client):
    """Yesterday's scored couple is found at every stop; an unknown number is found nowhere, without an error."""
    login(client, "dev")
    scored = client.get("/api/demo/latest").json()["scored"]
    t = client.get(f"/api/demo/trace?inquiry_id={scored}").json()
    assert all(t[s]["found"] for s in ("crm", "raw_crm", "trusted", "star", "mart", "score")), t
    assert t["star"]["row"]["InquiryID"] == scored and t["raw_crm"]["file"].startswith("lake/bronze/crm/Inquiry/")
    assert t["run"]["steps"]
    t = client.get("/api/demo/trace?inquiry_id=999999999").json()
    assert not any(t[s]["found"] for s in ("crm", "chat", "raw_crm", "trusted", "star", "mart", "score"))
    assert not any("error" in t[s] for s in ("crm", "chat", "raw_crm", "trusted", "star", "mart", "score"))


@pytest.mark.parametrize("bad", [{"package": "Gold"}, {"budget": "2M"}, {"guests": 5}, {"requested_date": "2020-01-01"}])
def test_demo_line_refuses_bad_fields(client, bad):
    """Checked before anything reaches the CRM or the gateway."""
    login(client, "dev")
    assert client.post("/api/demo/line", json={"text": "hi", **bad}).status_code == 422


def test_today_is_scoped_to_the_salesperson(client):
    me = login(client, "may")
    d = client.get("/api/sales/today").json()
    assert {"list", "new", "going_cold", "holdout", "my_week", "ticks"} <= d.keys()
    owners = {r["sales_owner_id"] for r in d["going_cold"] + d["new"] + d["list"]}
    assert owners <= {me["employee_id"]} and d["going_cold"]
    assert [w["employee_id"] for w in d["my_week"]] == [me["employee_id"]]


def test_write_back_inserts_a_sales_activity(client):
    with connect(DW) as dw:
        inq = dw.execute('SELECT "InquiryID" AS id FROM mart_sales.v_inquiry WHERE sales_owner_id = 7 AND "IsOpen" = 1 LIMIT 1').fetchone()["id"]
    login(client, "ken")  # another salesperson's couple
    assert client.post("/api/sales/activity", json={"inquiry_id": inq, "activity_type": "Call", "outcome": "Reached"}).status_code == 403
    login(client, "may")
    assert client.post("/api/sales/activity", json={"inquiry_id": inq, "activity_type": "Fax", "outcome": "Reached"}).status_code == 422
    r = client.post("/api/sales/activity", json={"inquiry_id": inq, "activity_type": "LINE", "outcome": "Left message"})
    if r.status_code == 503 and "schema aisleco" in r.text:
        pytest.xfail("CRM grant missing: crm_app_writer needs USAGE on schema aisleco (the UpdatedAt trigger calls aisleco.clock())")
    assert r.status_code == 200, r.text
    aid = r.json()["activity_id"]
    try:
        with connect(CRM) as crm:
            row = crm.execute('SELECT * FROM transaction."SalesActivity" WHERE "ActivityID" = %s', (aid,)).fetchone()
        assert row["InquiryID"] == inq and row["EmployeeID"] == 7 and row["Source"] == "CallList" and row["CreatedAt"]
        assert any(t["activity_id"] == aid for t in sales._crm_ticks({"role": "sales", "is_manager": False, "employee_id": 7}))
    finally:
        with connect(CRM) as crm:
            crm.execute('DELETE FROM transaction."SalesActivity" WHERE "ActivityID" = %s', (aid,))


def test_every_kpi_on_screen_is_in_the_catalog(client):
    login(client, "may")
    ids = {k["id"] for k in client.get("/api/catalog/kpis").json()["kpis"]}
    used = set()
    for f in (ROOT / "app/web/src").rglob("*.tsx"):
        used |= set(re.findall(r'(?:kpi|Info id)="([a-z0-9_]+)"', f.read_text()))
    assert used and used <= ids, used - ids
