"""The simulated company's fixed setup (Spec 06 §3): roles, staff and their contract history,
channels, packages, hours per wedding, fairs. Plain data, read by engine.py."""
from datetime import date

D = date
OPEN_DAY = D(2021, 1, 4)          # master rows are dated from the company's early years

ROLES = [  # id, name, group
    (1, "Lead Planner", "Planning"), (2, "Planner", "Planning"), (3, "Coordinator", "Wedding day"),
    (4, "Event Staff", "Wedding day"), (5, "Sales Executive", "Sales"), (6, "Sales Manager", "Sales"),
    (7, "Marketing Executive", "Office"), (8, "Finance Officer", "Office")]
ROLE_ID = {name: rid for rid, name, _ in ROLES}

# code, nickname, first, last, hire date, leave date, contracts: (role, type, hours, max concurrent, from, to, entered on)
STAFF = [
    ("S-SOM", "Som", "Somchai", "Rattanakul", D(2021, 3, 1), None, [("Lead Planner", "FullTime", 44, 6, D(2021, 3, 1), None, None)]),
    ("S-NINA", "Nina", "Nina", "Charoensuk", D(2021, 6, 1), None, [
        ("Lead Planner", "FullTime", 44, 6, D(2021, 6, 1), D(2026, 1, 31), None),
        ("Lead Planner", "PartTime", 36, 5, D(2026, 2, 1), None, D(2026, 1, 20))]),
    ("S-PIM", "Pim", "Pimchanok", "Sukjai", D(2022, 1, 10), None, [("Planner", "FullTime", 44, 6, D(2022, 1, 10), None, None)]),
    ("S-TAN", "Tan", "Tanawat", "Srisuk", D(2023, 2, 1), None, [("Planner", "FullTime", 44, 6, D(2023, 2, 1), None, None)]),
    ("S-JOY", "Joy", "Jiraporn", "Meesuk", D(2021, 11, 1), D(2025, 8, 15), [
        ("Planner", "FullTime", 44, 6, D(2021, 11, 1), D(2025, 8, 15), None)]),
    ("S-BEE", "Bee", "Benjamas", "Thongdee", D(2025, 10, 1), None, [("Planner", "FullTime", 44, 6, D(2025, 10, 1), None, D(2025, 9, 15))]),
    ("S-BEAM", "Beam", "Phiraphat", "Wongsa", D(2021, 5, 1), None, [("Coordinator", "FullTime", 44, 8, D(2021, 5, 1), None, None)]),
    ("S-ICE", "Ice", "Issara", "Kaewmanee", D(2024, 6, 1), None, [("Coordinator", "FullTime", 44, 8, D(2024, 6, 1), None, D(2024, 5, 20))]),
    ("S-FAH", "Fah", "Fahsai", "Boonmee", D(2024, 12, 1), None, [
        ("Coordinator", "Freelance", 20, 4, D(2024, 12, 1), D(2025, 2, 28), D(2024, 10, 15)),
        ("Coordinator", "Freelance", 20, 4, D(2025, 12, 1), D(2026, 2, 28), D(2025, 10, 15)),
        ("Coordinator", "Freelance", 20, 4, D(2026, 12, 1), D(2027, 2, 28), D(2026, 10, 15))]),
    ("S-ARM", "Arm", "Anuwat", "Phromma", D(2022, 1, 1), None, [("Event Staff", "FullTime", 40, 8, D(2022, 1, 1), None, None)]),
    ("S-GOLF", "Golf", "Kittipong", "Saelim", D(2023, 5, 1), None, [("Event Staff", "FullTime", 40, 8, D(2023, 5, 1), None, None)]),
    ("S-MOOK", "Mook", "Mookda", "Chaiyo", D(2023, 8, 1), None, [("Event Staff", "FullTime", 40, 8, D(2023, 8, 1), None, None)]),
    ("S-MAY", "May", "Maythinee", "Sriwong", D(2022, 2, 1), None, [("Sales Executive", "FullTime", 44, None, D(2022, 2, 1), None, None)]),
    ("S-KEN", "Ken", "Kenji", "Tanaka", D(2023, 9, 1), None, [("Sales Executive", "FullTime", 44, None, D(2023, 9, 1), None, None)]),
    ("S-NOK", "Nok", "Nokkaew", "Inthara", D(2021, 1, 4), None, [("Sales Manager", "FullTime", 44, None, D(2021, 1, 4), None, None)]),
    ("S-FON", "Fon", "Fonthip", "Jaidee", D(2022, 4, 1), None, [("Marketing Executive", "FullTime", 44, None, D(2022, 4, 1), None, None)]),
    ("S-DAO", "Dao", "Daorung", "Pongpan", D(2021, 1, 4), None, [("Finance Officer", "FullTime", 44, None, D(2021, 1, 4), None, None)]),
]
SALES_CODES = ["S-MAY", "S-KEN"]

CHANNELS = [  # id, name, type, platform, tracking code
    (1, "LINE OA", "Online", "line", None), (2, "Facebook", "Online", "messenger", None),
    (3, "Instagram", "Online", "instagram", None), (4, "TikTok", "Online", "line", "#TIKTOK"),
    (5, "Website", "Online", "line", "#WEB"), (6, "Wedding Fair", "Offline", None, "#FAIR"),
    (7, "Referral", "Offline", None, None), (8, "Walk-in", "Offline", None, None)]
CH = {name: cid for cid, name, *_ in CHANNELS}
# non-fair arrival mix (weights); 2026: TikTok campaign takes a third of online inquiries (the drift)
MIX = {"LINE OA": 30, "Facebook": 20, "Instagram": 15, "TikTok": 10, "Website": 8, "Referral": 5, "Walk-in": 2}
MIX_2026 = {k: v * 0.8 for k, v in MIX.items()} | {"TikTok": 41.7}
READINESS = {"Referral": 0.6, "Walk-in": 0.3, "LINE OA": 0.2, "Facebook": 0.0, "Instagram": 0.0,
             "Website": -0.2, "Wedding Fair": -0.3, "TikTok": -0.8}

PACKAGES = [  # id, name, type, base price, fee type, fee rate, from, to
    (1, "Full Planning", "Full", None, "Percent", 12.5, D(2021, 1, 1), D(2026, 6, 30)),
    (2, "Full Planning", "Full", None, "Percent", 13.5, D(2026, 7, 1), None),
    (3, "Partial Planning", "Partial", 90000, "Fixed", None, D(2021, 1, 1), None),
    (4, "Day-of Coordination", "DayOf", 45000, "Fixed", None, D(2021, 1, 1), D(2026, 6, 30)),
    (5, "Day-of Coordination", "DayOf", 52000, "Fixed", None, D(2026, 7, 1), None),
    (6, "Custom", "Custom", None, "Fixed", None, D(2021, 1, 1), None)]
PACKAGE_CHANGE_ENTERED = D(2026, 6, 15)     # the July 2026 price versions were keyed in on this day

# staff hours per wedding before multipliers: role -> list of hours (one assignment each)
HOURS = {   # calibrated in M3 so ops utilization averages ~78 % with peaks above 120 % (Spec 06 §8)
    "Full": {"Lead Planner": [61], "Planner": [52], "Coordinator": [35], "Event Staff": [23, 23]},
    "Partial": {"Planner": [70], "Coordinator": [30], "Event Staff": [17, 17]},
    "DayOf": {"Coordinator": [30], "Event Staff": [21, 21]},
    "Custom": {"Planner": [44], "Coordinator": [26], "Event Staff": [17]},
}
LEAD_ROLE = {"Full": "Lead Planner", "Partial": "Planner", "DayOf": "Coordinator", "Custom": "Planner"}

BUDGETS = {"300k-500k": (300, 500), "500k-1M": (500, 1000), "1M-1.5M": (1000, 1500), "1.5M+": (1500, 2000)}
GUESTS = {"300k-500k": (150, 300), "500k-1M": (200, 400), "1M-1.5M": (300, 550), "1.5M+": (400, 700)}
BUDGET_FREE_TEXT = {"300k-500k": ["around 400k", "4 แสน", "350,000", "ไม่เกินห้าแสน"],
                    "500k-1M": ["around 600k", "6 แสน", "800,000", "budget 700k"],
                    "1M-1.5M": ["ประมาณ 1 ล้าน", "1.2M", "1,200,000"], "1.5M+": ["2 ล้าน", "1.8M"]}

FAIRS = [(1, 23), (3, 13), (6, 5), (8, 21), (10, 16)]     # month, day of the Friday; Fri–Sun
FAIR_NAMES = ["Bangkok Wedding Fair, Siam Paragon", "Wedding Expo, Queen Sirikit Centre", "Bride & Groom Fair, CentralWorld"]
