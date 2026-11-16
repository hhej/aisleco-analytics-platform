"""Database access. Every mart query runs on a connection opened AS the logged-in user's database role, so PostgreSQL
enforces the same limits as the API (Spec 03 §7). Local trust authentication: no passwords."""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DW = os.environ.get("APP_DW_DB", "aisleco_dw")
CRM = os.environ.get("APP_CRM_DB", "aisleco_crm")
DB_ROLE = {"sales": "aisleco_sales", "ops": "aisleco_ops", "exec": "aisleco_exec", "data": "aisleco_data"}


@contextmanager
def connect(dbname: str, user: str | None = None):
    """user=None: the OS user (owner); used only for app.user and the seed."""
    kw = {"user": user} if user else {}
    with psycopg.connect(dbname=dbname, autocommit=True, row_factory=dict_row, **kw) as conn:
        yield conn


class Q:
    """One connection per request, as one database role: q.all(sql, params) / q.one(sql, params)."""

    def __init__(self, conn):
        self.conn = conn

    def all(self, sql, params=None):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=None):
        return self.conn.execute(sql, params).fetchone()


@contextmanager
def as_role(app_role: str):
    with connect(DW, DB_ROLE[app_role]) as conn:
        yield Q(conn)
