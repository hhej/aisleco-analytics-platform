"""Login and sessions: bcrypt hashes in app.user, a signed cookie (itsdangerous) valid 8 hours."""
import os

import bcrypt
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.api.db import DW, connect

SECRET = os.environ.get("APP_SECRET", "dev-only-secret-change-me")
COOKIE = "aisleco_session"
MAX_AGE = 8 * 3600
_signer = URLSafeTimedSerializer(SECRET, salt="aisleco-session")

# pages each role may open (Spec 05 §2); the data team may open every page
PAGES = {
    "sales": ["today", "sales-performance"],
    "ops": ["capacity"],
    "exec": ["overview", "sales-performance", "capacity"],
    "data": ["health", "overview", "capacity", "sales-performance", "today", "demo"],
}
_DUMMY = bcrypt.hashpw(b"x", bcrypt.gensalt())  # equal work for unknown users (no timing hint)


def login(username: str, password: str) -> dict | None:
    with connect(DW) as conn:  # OS user: the only read of app.user
        u = conn.execute('SELECT * FROM app."user" WHERE username = %s', (username.strip().lower(),)).fetchone()
    ok = bcrypt.checkpw(password.encode(), (u["password_hash"] if u else _DUMMY.decode()).encode())
    if not (u and ok):
        return None
    return {k: u[k] for k in ("username", "display_name", "role", "employee_id", "is_manager")}


def cookie_value(user: dict) -> str:
    return _signer.dumps(user)


def current_user(request: Request) -> dict:
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "not logged in")
    try:
        return _signer.loads(raw, max_age=MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "session expired")


def require(*roles: str):
    """Dependency: the logged-in user, if their role is one of `roles` (the data team is always allowed)."""
    def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles and user["role"] != "data":
            raise HTTPException(403, "your role cannot open this page")
        return user
    return dep
