"""Tests run against the local PostgreSQL, in their own databases (aisleco_*_test), so the
demo databases are never touched."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

os.environ["ETL_CRM_DSN"] = "dbname=aisleco_crm_test"
os.environ["ETL_DW_DSN"] = "dbname=aisleco_dw_test"
os.environ["ETL_RETRY_BASE_SECONDS"] = "0.05"

from etl import db  # noqa: E402
from etl.config import Config  # noqa: E402
from crosscheck import facts  # noqa: E402,F401  (shared with the end-to-end notebook)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("ETL_LANDING_DIR", str(tmp_path / "landing"))
    return Config()


def q(cfg, sql, params=None, dsn=None):
    with db.connect(dsn or cfg.dw_dsn, cfg) as c:
        return c.execute(sql, params).fetchall()
