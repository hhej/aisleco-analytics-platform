"""Data catalog (M8): catalog/tables.yml and catalog/kpis.yml are complete against the live databases, the Silver
Parquet files and the web app, and every lineage reference resolves. Needs the loaded aisleco_crm, aisleco_dw and lake/."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_catalog as bc  # noqa: E402


@pytest.fixture(scope="module")
def ctx():
    cat, kpis = bc.load()
    live = bc.introspect()
    return cat, kpis, live, bc.expand(cat, live)


@pytest.mark.parametrize("prefix", ["gold.", "mart_", "silver.", "silver_restricted.", "crm.", "sales_pii.", "feature.", "ml.", "etl."])
def test_every_live_column_is_documented(ctx, prefix):
    cat, kpis, live, tables = ctx
    tids = [t for t in live if t.startswith(prefix)]
    assert tids, f"no live tables found for {prefix}"
    missing = [f"{t}.{c}" for t in tids for c in live[t] if c not in tables.get(t, {}).get("columns", {})]
    stale = [f"{t}.{c}" for t in tids if t in tables for c in tables[t]["columns"] if c not in live[t]]
    assert not missing and not stale, f"undocumented: {missing}; documented but gone: {stale}"


def test_every_kpi_in_the_app_is_in_kpis_yml(ctx):
    used = bc.app_kpi_ids()
    assert len(used) >= 30
    assert set(used) <= {k["id"] for k in ctx[1]["kpis"]}


def test_every_lineage_reference_resolves(ctx):
    cat, kpis, live, tables = ctx
    ids = sorted(tables, key=len, reverse=True)
    bad = [f"{t}.{c} → {r}" for t, tb in tables.items() for c, v in tb["columns"].items() for r in v.get("from", [])
           if (lambda rt, rc: rt is None or rc not in tables[rt]["columns"])(*bc.split_ref(r, ids))]
    assert not bad, bad


def test_every_kpi_reads_real_mart_columns(ctx):
    cat, kpis, live, tables = ctx
    for k in kpis["kpis"]:
        refs, bad = bc.kpi_refs(k, tables)
        assert refs and not bad, (k["id"], bad)


def test_full_check_passes_and_page_builds(ctx):
    cat, kpis, live, _ = ctx
    assert bc.check(cat, kpis, live) == []
    html = bc.build_html(bc.page_data(cat, kpis, live))
    assert "<script src" not in html and "<link" not in html          # self-contained, offline
    assert "ops_utilization_quarter" in html and "gold.FactStaffWorkload" in html


def test_check_catches_a_missing_gold_column(ctx):
    cat, kpis, live, _ = ctx
    import copy
    broken = copy.deepcopy(cat)
    next(t for t in broken["tables"] if t["id"] == "gold.FactInquiry")["columns"].pop("IsBooked")
    assert any("gold.FactInquiry.IsBooked exists but is not documented" in e for e in bc.check(broken, kpis, live))
