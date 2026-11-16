"""Demo logins (Spec 05 §2).  python -m app.api.users seed
Password from APP_DEMO_PASSWORD (default "aisleco2026"). Salespeople get their CRM EmployeeID, read here with the
OS user, because the web app's write-back role may not read master."Employee"."""
import os
import sys
from pathlib import Path

import bcrypt

from app.api.db import CRM, DW, connect

DEMO = [  # username, display name, role, employee code, is_manager
    ("may", "May", "sales", "S-MAY", False),
    ("ken", "Ken", "sales", "S-KEN", False),
    ("nok", "Nok", "sales", "S-NOK", True),
    ("som", "Som", "ops", None, False),
    ("ann", "Ann", "exec", None, False),
    ("dev", "Dev", "data", None, False),
]


def seed():
    pw = os.environ.get("APP_DEMO_PASSWORD", "aisleco2026").encode()
    with connect(CRM) as crm:
        ids = {r["EmployeeCode"]: r["EmployeeID"] for r in crm.execute('SELECT "EmployeeCode", "EmployeeID" FROM master."Employee"')}
    with connect(DW) as dw:
        dw.execute((Path(__file__).parent / "schema.sql").read_text())
        for u, name, role, code, mgr in DEMO:
            if code and code not in ids:
                sys.exit(f"employee {code} not found in the CRM")
            dw.execute("""INSERT INTO app."user" (username, display_name, role, employee_code, employee_id, is_manager, password_hash)
                          VALUES (%s, %s, %s, %s, %s, %s, %s)
                          ON CONFLICT (username) DO UPDATE SET display_name = EXCLUDED.display_name, role = EXCLUDED.role,
                            employee_code = EXCLUDED.employee_code, employee_id = EXCLUDED.employee_id,
                            is_manager = EXCLUDED.is_manager, password_hash = EXCLUDED.password_hash""",
                       (u, name, role, code, ids.get(code), mgr, bcrypt.hashpw(pw, bcrypt.gensalt()).decode()))
    print(f"seeded {len(DEMO)} users: {', '.join(d[0] for d in DEMO)}")


if __name__ == "__main__":
    if sys.argv[1:] != ["seed"]:
        sys.exit("usage: python -m app.api.users seed")
    seed()
