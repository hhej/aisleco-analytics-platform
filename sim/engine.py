"""The simulated business, one day at a time (Spec 06). World.run_day(d) returns what happened that day as
CRM writes, webhook posts and tablet files; io.py delivers them through the same doors a real company uses.
Hidden readiness z drives every couple; it is never written anywhere the pipeline can read (ground truth goes
to var/sim_truth only)."""
import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sim import company as C
from sim import phrases as P
from sources.external import auspicious

sig = lambda x: 1 / (1 + math.exp(-x))
clip = lambda p: min(0.95, max(0.02, p))
OPEN_H, CLOSE_H, AUTO_CLOSE_DAYS, FOLLOWUPS_PER_DAY, REACH = 9, 20, 30, 15, 0.55
HOUR_W = [1, .5, .3, .2, .2, .3, .6, 1, 1.5, 2, 2.5, 2.5, 3, 3, 2.5, 2.5, 2.5, 3, 3.5, 4.5, 5, 5, 4, 2.5]
MONTH_W = [1.35, 1.3, 1.25, 1.15, 1.0, .9, .8, .8, .85, .95, .85, .8]
WEDDING_MONTH_W = {11: 3, 12: 3, 1: 3, 2: 3, 3: 1.2, 4: 1.2, 10: 1.2, 5: .7, 6: .7, 7: .15, 8: .15, 9: .15}


@dataclass
class Lead:
    iid: int
    cid: int
    true_channel: str
    channel: str                     # what the CRM records (a deleted #TIKTOK code looks like LINE OA)
    z: float
    arrived: datetime
    owner: int
    lang: str
    budget: str
    pkg: str | None
    req_date: date | None
    flexible: bool
    nick: str
    account: int | None = None
    platform: str | None = None
    user_id: str | None = None
    stage: str = "new"               # new, contacted, consulted, quoted, booked, lost
    contacted: datetime | None = None
    first_reply_min: float | None = None
    consulted: datetime | None = None
    quoted: datetime | None = None
    quote: float | None = None
    last_cust: datetime | None = None
    last_staff: datetime | None = None
    fu_reached: datetime | None = None
    objected: bool = False
    guests: int | None = None
    fair: bool = False
    spam: bool = False
    ad_id: str | None = None
    closed: datetime | None = None
    outcome: str | None = None


@dataclass
class Day:
    """Everything one simulated day produced."""
    crm: list = field(default_factory=list)       # (op, table, payload)
    posts: list = field(default_factory=list)     # (ts, kind, payload, flags)
    files: list = field(default_factory=list)     # (relative path, text)


class World:
    def __init__(self, seed=11, scale=1.0, start=date(2022, 7, 1)):
        self.rng = random.Random(seed)
        self.seed, self.scale, self.start = seed, scale, start
        self.today = start
        self.ids = {k: 0 for k in ["cust", "contact", "acc", "inq", "msg", "event", "asg", "act", "emp", "contract"]}
        self.leads: dict[int, Lead] = {}
        self.open: set[int] = set()
        self.weddings_on: dict[date, int] = {}
        self.week_load: dict[tuple[int, date], float] = {}
        self.pending: dict[date, list] = {}        # scheduled actions: date -> [(kind, args)]
        self.emp: dict[str, dict] = {}             # code -> {"id", "contracts": [(role, from, to)]}
        self.tablet_seq = {"TAB-01": 0, "TAB-02": 0}
        self.truth = []                            # ground truth rows (never in the CRM or the lake)
        self.ranker = None                         # f(date, leads) -> ordered InquiryIDs, from M5 on
        self.aus = {}
        self.plat_seq = 0
        self.flags = set()                         # one-a-year injected problems already done
        self.k_consult, self.k_book = -5.5, -5.4       # intercepts calibrated in M1 (Spec 06 §8)

    # ---------- helpers ----------
    def new_id(self, k):
        self.ids[k] += 1
        return self.ids[k]

    def ts(self, d, lo=9, hi=20):
        return datetime.combine(d, time(self.rng.randrange(lo, hi), self.rng.randrange(60), self.rng.randrange(60)))

    def auspicious_dates(self, year):
        if year not in self.aus:
            self.aus[year] = {r[0]: r[2] for r in auspicious.rows(year, self.seed)}
        return self.aus[year]

    def in_lent(self, d):
        a, b = auspicious.lent(d.year)
        return a <= d <= b

    def is_full(self, d):
        wk = d - timedelta(days=d.weekday())
        return self.weddings_on.get(d, 0) >= 2 or sum(self.weddings_on.get(wk + timedelta(i), 0) for i in range(7)) >= 4

    def choose_date(self, from_d):
        rng = self.rng
        months = []
        for k in range(4, 15):
            y, m = divmod(from_d.month - 1 + k, 12)
            months.append((from_d.year + y, m + 1))
        y, m = rng.choices(months, [WEDDING_MONTH_W[m] for _, m in months])[0]
        days = [date(y, m, 1) + timedelta(i) for i in range(31) if (date(y, m, 1) + timedelta(i)).month == m]
        days = [d for d in days if not self.in_lent(d)] or days
        aus = [d for d in days if d in self.auspicious_dates(y)]
        if aus and rng.random() < .6:
            return rng.choice(aus)
        return rng.choice([d for d in days if d.weekday() >= 5] or days)

    def contract(self, code, d):
        return next((c for c in self.emp[code]["contracts"] if c[1] <= d and (c[2] is None or d <= c[2])), None)

    def staff_reply_at(self, t_in):
        """When the inbox answers a message sent at t_in (v1 rule): minutes in office hours, else next morning."""
        def next_open(t):
            dd = t.date() + (timedelta(1) if t.hour >= CLOSE_H else timedelta(0))
            return datetime.combine(dd, time(OPEN_H)) + timedelta(minutes=self.rng.randint(5, 120))
        if OPEN_H <= t_in.hour < CLOSE_H:
            t = t_in + timedelta(minutes=max(1, int(self.rng.lognormvariate(3.2, 1.2))))
            return next_open(t_in.replace(hour=CLOSE_H, minute=0)) if t.hour >= CLOSE_H or t.date() > t_in.date() else t
        return next_open(t_in)

    def platform_ids(self, platform):
        self.plat_seq += 1
        h = hashlib.md5(f"{self.seed}-{self.plat_seq}".encode()).hexdigest()
        return ("U" + h) if platform == "line" else str(int(h[:15], 16))[:16]

    # ---------- text ----------
    def text(self, lead, intents, greet=False):
        rng = self.rng
        parts = []
        lang = lambda: lead.lang if lead.lang != "mixed" else rng.choice(["th", "en"])
        if greet:
            parts.append(rng.choice(P.GREETING[lang()]))
        pkg = {"Full": "Full Planning", "Partial": "Partial Planning", "DayOf": "Day-of", "Custom": "แบบ Custom"}.get(lead.pkg, "")
        d = lead.req_date or date(2027, 2, 13)
        lo, hi = C.BUDGETS[lead.budget]
        slots = dict(date=f"{d.day} {P.TH_MONTHS[d.month - 1]}", date_en=d.strftime("%-d %b"), guests=lead.guests or rng.randrange(150, 500, 10),
                     budget=f"{rng.randint(lo, hi) // 100} แสน", pkg=pkg, venue=rng.choice(P.VENUES))
        for it in intents:
            parts.append(rng.choice(P.INTENT[it][lang()]).format(**slots))
        if rng.random() < .02:      # personal data typed into the chat (masking test)
            parts.append(rng.choice([f"เบอร์ 08{rng.randint(1, 9)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)} นะคะ",
                                     f"line id: {lead.nick.lower()}{rng.randint(10, 99)}"]))
        if rng.random() < .08:
            parts.append(rng.choice(["😊", "🙏", "💕", "555"]))
        return " ".join(p for p in parts if p).strip() or "สวัสดีค่ะ"

    def intents(self, lead, first=False):
        rng, z = self.rng, lead.z
        if first:
            its = [i for i, p in [("ask_price", clip(.65 - .2 * z)), ("ask_package_detail", .3),
                                  ("ask_availability", clip(.45 + .15 * z) if lead.req_date else 0),
                                  ("mention_guests", clip(.35 + .1 * z))] if rng.random() < p]
            return its or ["ask_price"]
        over = self.over16(lead)
        table = ([("ask_meeting", clip(.15 + .2 * z)), ("ask_package_detail", .25), ("ask_price", clip(.3 - .1 * z)),
                  ("positive", clip(.2 + .1 * z)), ("family_decision", clip(.1 - .05 * z)), ("competitor", clip(.08 - .05 * z))]
                 if lead.stage in ("new", "contacted") else
                 [("price_objection", clip(.08 - .06 * z + .25 * over)), ("family_decision", clip(.15 - .05 * z)),
                  ("competitor", clip(.1 - .06 * z)), ("positive", clip(.25 + .1 * z)), ("ask_meeting", .05)])
        its = [i for i, p in table if rng.random() < p][:2]
        return its or ["smalltalk"]

    def over16(self, lead):
        lo, hi = C.BUDGETS[lead.budget]
        return bool(lead.quote and lead.quote / ((lo + hi) / 2 * 1000) > .16)

    # ---------- messages ----------
    def customer_msg(self, day, lead, t, its, code=None, first=False, text=None, reply=True):
        if lead.account is None:
            return
        txt = text or self.text(lead, its, greet=first)
        if code:
            txt = f"{code} {txt}"
        mid = self.new_id("msg")
        pmid = str(500000000000000000 + mid)
        day.posts.append((t, "in", {"platform": lead.platform, "user_id": lead.user_id, "message_id": pmid, "text": txt,
                                    "ad_id": lead.ad_id if first else None}, {}))
        day.crm.append(("insert", "ChatMessage", dict(ChatMessageID=mid, CustomerAccountID=lead.account, InquiryID=lead.iid,
                        PlatformMessageID=pmid, SentAt=t, Direction="In", SenderType="Customer", EmployeeID=None,
                        TrackingCode=code, CreatedAt=t, UpdatedAt=t)))
        lead.last_cust = t
        if "price_objection" in its:
            lead.objected = True
        if not reply:            # live demo: a person in the room answers, not the simulator
            return
        # the inbox answers: bot outside hours, staff when open (6 % typed on the phone and never logged)
        if not (OPEN_H <= t.hour < CLOSE_H):
            self.staff_msg(day, lead, t + timedelta(seconds=self.rng.randint(2, 20)), bot=True)
        r = self.staff_reply_at(t)
        if r.date() > self.today:
            self.schedule(r.date(), "reply", (lead.iid, r))
        else:
            self.reply(day, lead, r)

    def reply(self, day, lead, r):
        if lead.stage in ("lost", "booked") or lead.spam or lead.iid not in self.leads:
            return
        if self.rng.random() < .06:        # typed in the LINE phone app: never logged (Rev10 known gap)
            lead.last_staff = r
        else:
            self.staff_msg(day, lead, r)

    def staff_msg(self, day, lead, t, bot=False, code=None):
        if lead.account is None:
            return
        mid = self.new_id("msg")
        pmid = str(500000000000000000 + mid)
        code = code or self.emp_code(lead.owner)
        txt = P.BOT if bot else self.rng.choice(P.STAFF[lead.lang if lead.lang != "mixed" else "th"])
        day.posts.append((t, "out", {"platform": lead.platform, "user_id": lead.user_id, "message_id": pmid, "text": txt,
                                     "sender_type": "bot" if bot else "staff", "staff_code": None if bot else code}, {}))
        emp = None if bot or self.rng.random() < .005 else self.emp[code]["id"]     # 0.5 %: CRM row without the employee (R07)
        day.crm.append(("insert", "ChatMessage", dict(ChatMessageID=mid, CustomerAccountID=lead.account, InquiryID=lead.iid,
                        PlatformMessageID=pmid, SentAt=t, Direction="Out", SenderType="Bot" if bot else "Staff",
                        EmployeeID=emp, TrackingCode=None, CreatedAt=t, UpdatedAt=t)))
        if not bot:
            lead.last_staff = t
            if lead.contacted is None:
                lead.contacted = t
                lead.first_reply_min = (t - lead.arrived).total_seconds() / 60
                if lead.stage == "new":
                    lead.stage = "contacted"
                day.crm.append(("update", "Inquiry", lead.iid, dict(ContactedDate=t, Status="Contacted"), t))

    def emp_code(self, emp_id):
        return next(c for c, e in self.emp.items() if e["id"] == emp_id)

    # ---------- master data ----------
    def setup(self):
        day = Day()
        t0 = datetime.combine(C.OPEN_DAY, time(9))
        for rid, name, group in C.ROLES:
            day.crm.append(("insert", "EmployeeRole", dict(RoleID=rid, RoleName=name, RoleGroup=group, Status="Active", CreatedAt=t0, UpdatedAt=t0)))
        for cid, name, typ, plat, code in C.CHANNELS:
            day.crm.append(("insert", "MarketingChannel", dict(ChannelID=cid, ChannelName=name, ChannelType=typ, Platform=plat,
                            TrackingCode=code, Status="Active", CreatedAt=t0, UpdatedAt=t0)))
        for pid, name, typ, base, ft, rate, f, to in C.PACKAGES:
            if f > self.start:
                self.schedule(C.PACKAGE_CHANGE_ENTERED, "package", pid)
                continue
            self.package_row(day, pid, None if to and to > self.start else to, t0)
        for code, nick, first, last, hire, leave, contracts in C.STAFF:
            entered = min(c[6] or c[4] for c in contracts)
            if entered <= self.start:
                self.employee_row(day, code)
            else:
                self.schedule(entered, "employee", code)
            if leave:
                self.schedule(leave - timedelta(days=31), "leave", code)
        for y in range(self.start.year, self.start.year + 2):
            if date(y - 1, 10, 15) <= self.start:
                day.files.append(self.auspicious_file(y))
        for y in range(self.start.year + 1, 2030):
            self.schedule(date(y - 1, 10, 15), "auspicious", y)
        return day

    def auspicious_file(self, year):
        rows = auspicious.rows(year, self.seed, bad_row=(year == 2026))
        lines = ["calendar_date,date_type,auspicious_level,description"] + [",".join(map(str, r)) for r in rows]
        return (f"landing/external/auspicious_{year}.csv", "\n".join(lines) + "\n")

    def package_row(self, day, pid, eff_to, t):
        pid_, name, typ, base, ft, rate, f, to = next(p for p in C.PACKAGES if p[0] == pid)
        day.crm.append(("insert", "Package", dict(PackageID=pid, PackageName=name, PackageType=typ, BasePrice=base, ServiceFeeType=ft,
                        ServiceFeeRate=rate, EffectiveFrom=f, EffectiveTo=eff_to, Status="Active", CreatedAt=t, UpdatedAt=t)))

    def employee_row(self, day, code, t=None):
        _, nick, first, last, hire, leave, contracts = next(s for s in C.STAFF if s[0] == code)
        eid = self.new_id("emp")
        self.emp[code] = {"id": eid, "contracts": [], "cids": []}
        t = t or datetime.combine(min(hire, self.start), time(9))
        day.crm.append(("insert", "Employee", dict(EmployeeID=eid, EmployeeCode=code, FirstName=first, LastName=last, Nickname=nick,
                        HireDate=hire, Status="Active", CreatedAt=t, UpdatedAt=t)))
        for i, (role, typ, hrs, mx, f, to, entered) in enumerate(contracts):
            later = [c for c in contracts[i + 1:] if c[6]]
            # the end date is known once the successor contract (or the leaving) has been entered
            ended = any(c[6] <= self.today for c in later) or (leave is not None and leave - timedelta(days=31) <= self.today)
            if entered and entered > self.today:
                self.schedule(entered, "contract", (code, i))
            elif typ == "Freelance" or not (later or leave) or ended:
                self.contract_row(day, code, i, to, t)
            else:
                self.contract_row(day, code, i, None, t)    # end date unknown until the change or leaving is entered

    def contract_row(self, day, code, i, eff_to, t):
        role, typ, hrs, mx, f, to, entered = next(s for s in C.STAFF if s[0] == code)[6][i]
        cid = self.new_id("contract")
        self.emp[code]["contracts"].append((role, f, to))
        self.emp[code]["cids"].append(cid)
        day.crm.append(("insert", "EmployeeContract", dict(ContractID=cid, EmployeeID=self.emp[code]["id"], RoleID=C.ROLE_ID[role],
                        EmploymentType=typ, StandardWeeklyHours=hrs, MaxConcurrentEvents=mx, EffectiveFrom=f, EffectiveTo=eff_to,
                        CreatedAt=t, UpdatedAt=t)))

    def schedule(self, d, kind, args):
        self.pending.setdefault(d, []).append((kind, args))

    # ---------- the day ----------
    def run_day(self, d) -> Day:
        self.today, day, rng = d, Day(), self.rng
        for kind, args in self.pending.pop(d, []):
            self.scheduled(day, d, kind, args)
        self.arrivals(day, d)
        if any(f <= d <= f + timedelta(2) for f in self.fair_fridays(d.year)):
            self.fair_day(day, d)
        for iid in sorted(self.open):
            self.progress(day, self.leads[iid], d)
        self.followups(day, d)
        if rng.random() < 3 / 30:
            day.posts.append((self.ts(d, 0, 24), "bad_signature", {}, {}))
        if rng.random() < 1 / 30:
            day.posts.append((self.ts(d, 0, 24), "malformed", {}, {}))
        return day

    def scheduled(self, day, d, kind, args):
        t = self.ts(d, 9, 12)
        if kind == "employee":
            self.employee_row(day, args, t)
        elif kind == "contract":
            code, i = args
            contracts = next(s for s in C.STAFF if s[0] == code)[6]
            prev = contracts[i - 1] if i else None
            if prev and prev[1] != "Freelance":       # the previous contract's end date is entered with the change
                day.crm.append(("update", "EmployeeContract", self.emp[code]["cids"][-1], dict(EffectiveTo=prev[5]), t))
            self.contract_row(day, code, i, contracts[i][5], t)
        elif kind == "reply":
            iid, r = args
            if iid in self.leads:
                self.reply(day, self.leads[iid], r)
        elif kind == "leave":
            leave = next(s[5] for s in C.STAFF if s[0] == args)
            day.crm.append(("update", "EmployeeContract", self.emp[args]["cids"][-1], dict(EffectiveTo=leave), t))
            self.schedule(leave + timedelta(1), "inactive", args)
        elif kind == "inactive":
            day.crm.append(("update", "Employee", self.emp[args]["id"], dict(Status="Inactive"), t))
        elif kind == "package":
            new = next(p for p in C.PACKAGES if p[0] == args)
            old = next(p for p in C.PACKAGES if p[2] == new[2] and p[7] and p[7] < new[6])
            day.crm.append(("update", "Package", old[0], dict(EffectiveTo=old[7]), t))
            self.package_row(day, args, None, t)
        elif kind == "auspicious":
            day.files.append(self.auspicious_file(args))
        elif kind == "assign":
            self.assign(day, d, *args)
        elif kind == "cancel":
            ev = args
            if ev["status"] == "Confirmed":
                ev["status"] = "Cancelled"
                self.weddings_on[ev["date"]] -= 1
                day.crm.append(("update", "Event", ev["id"], dict(Status="Cancelled", CancelledDate=t), t))
        elif kind == "complete":
            if args["status"] == "Confirmed":
                args["status"] = "Completed"
                day.crm.append(("update", "Event", args["id"], dict(Status="Completed"), t))
        elif kind == "fix":
            table, key, changes = args
            day.crm.append(("update", table, key, changes, t))
        elif kind == "delete_spam":
            self.open.discard(args)
            self.leads[args].stage = "lost"
            day.crm.append(("delete", "Inquiry", args, None, t))

    def sales_staff(self, d):
        return [self.emp[c]["id"] for c in C.SALES_CODES if c in self.emp and self.contract(c, d)]

    def new_lead(self, day, t, true_ch, budget=None, pkg=None, req=None, guests=None, fair=False, budget_text=None):
        rng = self.rng
        z = rng.gauss(C.READINESS[true_ch], 1)
        cid, iid = self.new_id("cust"), self.new_id("inq")
        code = dict((n, c) for _, n, _, _, c in C.CHANNELS)[true_ch]
        recorded = true_ch
        if code in ("#TIKTOK", "#WEB") and rng.random() < .2:
            recorded, code = "LINE OA", None           # the couple deleted the pre-typed code (Rev10)
        if budget is None:
            w = [.35, .4, .18, .07] if true_ch not in ("Referral", "Walk-in") else [.2, .4, .28, .12]
            budget = rng.choices(list(C.BUDGETS), w)[0]
        if pkg is None and rng.random() < sig(1.2 + .8 * z):
            pkg = rng.choices(["Full", "Partial", "DayOf", "Custom"], [.45 + .1 * (budget in ("1M-1.5M", "1.5M+")), .25, .25, .05])[0]
        if req is None and rng.random() < sig(1.0 + .9 * z):
            req = self.choose_date(t.date())
        lead = Lead(iid, cid, true_ch, recorded, z, t, rng.choice(self.sales_staff(t.date())), rng.choices(["th", "mixed", "en"], [.75, .15, .1])[0],
                    budget, pkg, req, rng.random() < .4, rng.choice(P.NICKNAMES), guests=guests, fair=fair)
        self.leads[iid] = lead
        self.open.add(iid)
        day.crm.append(("insert", "Customer", dict(CustomerID=cid, District=None, Province=None, CreatedAt=t, UpdatedAt=t)))
        budget_txt = budget_text or (rng.choice(C.BUDGET_FREE_TEXT[budget]) if rng.random() < .08 else budget)
        pid = self.package_id(pkg, t.date()) if pkg else None
        day.crm.append(("insert", "Inquiry", dict(InquiryID=iid, CustomerID=cid, ChannelID=C.CH[recorded], IndicativePackageID=pid,
                        AssignedSalesEmployeeID=lead.owner, InquiryDate=t, RequestedEventDate=req, BudgetRange=budget_txt,
                        ContactedDate=None, ConsultationDate=None, QuotedDate=None, QuotedAmount=None, ClosedDate=None, ClosedBy=None,
                        Status="New", LostReason=None, CreatedAt=t, UpdatedAt=t)))
        self.truth.append(dict(InquiryID=iid, CustomerID=cid, z=round(z, 4), true_channel=true_ch, recorded_channel=recorded))
        return lead, code

    def package_id(self, typ, d):
        return next(p[0] for p in C.PACKAGES if p[2] == typ and p[6] <= d and (p[7] is None or d <= p[7]))

    def open_account(self, day, lead, platform, t, code=None):
        rng = self.rng
        lead.account, lead.platform, lead.user_id = self.new_id("acc"), platform, self.platform_ids(platform)
        name = rng.choice([lead.nick, f"{lead.nick} 🌸", f"{lead.nick.lower()}.{rng.choice('abcdefgjkmnpst')}", f"{lead.nick} & {rng.choice(P.NICKNAMES)}"])
        day.crm.append(("insert", "CustomerChannelAccount", dict(CustomerAccountID=lead.account, CustomerID=lead.cid, Platform=platform,
                        ExternalUserID=lead.user_id, DisplayName=name, FirstSeenAt=t, FirstTrackingCode=code, CreatedAt=t, UpdatedAt=t)))

    def arrivals(self, day, d):
        rng = self.rng
        lam = 1200 / 365 * self.scale * MONTH_W[d.month - 1] / (sum(MONTH_W) / 12) * (1.2 if d.weekday() >= 5 else .92)
        mix = C.MIX_2026 if d.year >= 2026 else C.MIX
        for _ in range(self.poisson(lam)):
            ch = rng.choices(list(mix), list(mix.values()))[0]
            if ch == "Walk-in" or ch == "Referral":
                t = self.ts(d, 10, 19)
                lead, _ = self.new_lead(day, t, ch)
                lead.contacted, lead.stage = t, "contacted"
                day.crm[-1][2].update(ContactedDate=t, Status="Contacted")
                lead.last_cust = t
                if rng.random() < .5:
                    self.open_account(day, lead, "line", t + timedelta(minutes=30))
                continue
            t = datetime.combine(d, time(rng.choices(range(24), HOUR_W)[0], rng.randrange(60), rng.randrange(60)))
            lead, code = self.new_lead(day, t, ch)
            platform = dict((n, p) for _, n, _, p, _ in C.CHANNELS)[lead.channel] or "line"
            self.open_account(day, lead, platform, t, code)
            if rng.random() < .005:            # spam that sales deletes two days later
                lead.spam = True
                self.schedule(d + timedelta(2), "delete_spam", lead.iid)
            if platform != "line" and rng.random() < .3:      # click-to-Messenger ad
                lead.ad_id = str(120211234567890000 + rng.randrange(40))
            self.customer_msg(day, lead, t, self.intents(lead, first=True), code=code, first=True)

    def poisson(self, lam):
        n, p, L = 0, 1.0, math.exp(-lam)
        while True:
            p *= self.rng.random()
            if p <= L:
                return n
            n += 1

    def fair_fridays(self, year):
        return [date(year, m, dd) + timedelta((4 - date(year, m, dd).weekday()) % 7) for m, dd in C.FAIRS]

    def fair_day(self, day, d):
        rng = self.rng
        name = C.FAIR_NAMES[(d.month + d.year) % len(C.FAIR_NAMES)]
        fri = d - timedelta(days=(d.weekday() - 4) % 7)
        by_tab = {"TAB-01": [], "TAB-02": []}
        for _ in range(self.poisson(20 * self.scale)):
            t = self.ts(d, 11, 21)
            budget = rng.choices(list(C.BUDGETS), [.35, .4, .18, .07])[0]
            guests = rng.randint(*C.GUESTS[budget])
            lead, _ = self.new_lead(day, t, "Wedding Fair", budget=budget, guests=guests, fair=True)
            lead.contacted, lead.stage, lead.last_cust = t, "contacted", t
            day.crm[-1][2].update(ContactedDate=t, Status="Contacted")
            phone = None if rng.random() < .03 else f"08{rng.randint(1, 9)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}"
            sub = {"submission_id": None, "submitted_at": t.isoformat() + "+07:00", "crm_inquiry_id": lead.iid,
                   "couple": {"bride_name": lead.nick, "groom_name": rng.choice(P.NICKNAMES), "phone": phone,
                              "line_id": f"{lead.nick.lower()}.{rng.randint(1, 99)}", "district": rng.choice(["Bang Kapi", "Chatuchak", "Bang Na", "Nonthaburi", "Sathorn"]),
                              "province": rng.choice(["Bangkok", "Bangkok", "Nonthaburi", "Samut Prakan"])},
                   "wedding": {"date": lead.req_date.isoformat() if lead.req_date else None, "date_flexible": lead.flexible,
                               "guests": guests, "venue_type": rng.choice(["Hotel ballroom", "Garden", "Riverside", "Home"])},
                   "budget_band": budget, "package_interest": lead.pkg,
                   "wishes": rng.choice(["อยากได้ธีมสีพาสเทล", "งานเช้าแบบไทย งานเย็นแบบสากล", "อยากได้งานเล็กๆ อบอุ่น", "ขอธีมมินิมอล",
                                         "We want a garden theme", ""]),
                   "consent_marketing": rng.random() < .8}
            dev = rng.choice(list(by_tab))
            by_tab[dev].append(sub)
            if rng.random() < .6:      # scans the booth QR code: LINE opens with #FAIR typed
                self.open_account(day, lead, "line", t + timedelta(minutes=10), "#FAIR")
                self.customer_msg(day, lead, t + timedelta(minutes=11), ["ask_package_detail"], code="#FAIR", first=True)
        for dev, subs in by_tab.items():
            if not subs:
                continue
            self.tablet_seq[dev] += 1
            seq = self.tablet_seq[dev]
            for i, s in enumerate(subs):
                s["submission_id"] = f"{dev}-{seq:04d}-{i:03d}"
            synced = datetime.combine(d, time(rng.randint(19, 21), rng.randrange(60))) if rng.random() < .85 \
                else datetime.combine(d + timedelta(1), time(rng.randint(8, 10), rng.randrange(60)))
            batch = {"device_id": dev, "batch_seq": seq, "app_version": "2.3.1",
                     "fair": {"name": name, "date": fri.isoformat()}, "synced_at": synced.isoformat() + "+07:00", "submissions": subs}
            body = json.dumps(batch, ensure_ascii=False, indent=1)
            stamp = synced.strftime("%Y%m%dT%H%M%S")
            if d.month == 6 and d == fri and dev == "TAB-01":   # one truncated upload a year (R30); the tablet resends it
                day.files.append((f"landing/fair_tablet/{dev}_{seq}_{stamp}_partial.json", body[: len(body) // 2]))
            day.files.append((f"landing/fair_tablet/{dev}_{seq}_{stamp}.json", body))
            if rng.random() < .05:                 # retried upload of the same batch (R31)
                day.files.append((f"landing/fair_tablet/{dev}_{seq}_{stamp}_retry.json", body))

    def progress(self, day, lead, d):
        rng, z = self.rng, lead.z
        if lead.arrived.date() >= d or lead.spam:
            return
        last_touch = max(x for x in [lead.last_cust, lead.consulted, lead.arrived] if x)
        if (d - last_touch.date()).days >= AUTO_CLOSE_DAYS:
            return self.close(day, lead, datetime.combine(d, time(0, 5)), "No reply", "AutoRule")
        staff_yday = lead.last_staff is not None and (d - lead.last_staff.date()).days <= 1
        fu3 = lead.fu_reached is not None and (d - lead.fu_reached.date()).days <= 3
        if lead.account and rng.random() < sig(-2.3 + .8 * z + .9 * staff_yday - .08 * (d - (lead.last_cust or lead.arrived).date()).days):
            t = self.ts(d, 8, 23)
            for k in range(rng.choice([1, 1, 2])):
                self.customer_msg(day, lead, t + timedelta(minutes=3 * k), self.intents(lead))
        if lead.contacted is None:
            return
        fast = lead.first_reply_min is not None and lead.first_reply_min < 60
        slow = lead.first_reply_min is not None and lead.first_reply_min > 1440
        if lead.stage == "contacted" and rng.random() < sig(self.k_consult + 1.1 * z + .5 * fast - .7 * slow + .4 * bool(lead.req_date) + .5 * fu3):
            t = self.ts(d, 10, 18)
            if lead.req_date and self.is_full(lead.req_date):
                if not lead.flexible:
                    return self.close(day, lead, t, "Date unavailable", "Sales")
                lead.req_date = self.alternative(lead.req_date)
                day.crm.append(("update", "Inquiry", lead.iid, dict(RequestedEventDate=lead.req_date), t))
            lead.stage, lead.consulted = "consulted", t
            day.crm.append(("update", "Inquiry", lead.iid, dict(ConsultationDate=t, Status="Consulted"), t))
            self.contacts(day, lead, t)
        elif lead.stage == "consulted" and (d - lead.consulted.date()).days >= 1 and rng.random() < sig(-1.2 + .6 * z):
            t = self.ts(d, 10, 19)
            lead.stage, lead.quoted, lead.quote = "quoted", t, self.quote(lead, d)
            day.crm.append(("update", "Inquiry", lead.iid, dict(QuotedDate=t, QuotedAmount=lead.quote, Status="Quoted"), t))
        elif lead.stage == "quoted":
            peak = bool(lead.req_date and (lead.req_date.month in (11, 12, 1, 2) or lead.req_date in self.auspicious_dates(lead.req_date.year)))
            rushed = bool(lead.req_date and (lead.req_date - d).days < 150)
            if rng.random() < sig(self.k_book + 1.2 * z + .5 * (lead.true_channel == "Referral") - .6 * peak - .8 * self.over16(lead) - .5 * rushed + .6 * fu3):
                return self.book(day, lead, self.ts(d, 10, 19))
        if (lead.stage in ("consulted", "quoted") and rng.random() < sig(-3.9 - .6 * z + 1.5 * lead.objected + .8 * self.over16(lead))) or \
                (lead.stage == "contacted" and (d - lead.arrived.date()).days >= 3 and rng.random() < sig(-4.9 - .6 * z + 1.5 * lead.objected)):
            reason = "Price" if lead.objected or self.over16(lead) else rng.choice(["Chose competitor", "Scope mismatch", "Budget changed"])
            t = self.ts(d, 10, 19)
            self.customer_msg(day, lead, t - timedelta(hours=1), rng.choice([["family_decision"], ["competitor"], ["price_objection"]]))
            self.close(day, lead, t, reason, "Sales")

    def alternative(self, d0):
        for k in range(1, 60):
            for d in (d0 + timedelta(k), d0 - timedelta(k)):
                if d.weekday() >= 5 and not self.is_full(d) and not self.in_lent(d) and d > self.today + timedelta(60):
                    return d
        return d0

    def contacts(self, day, lead, t):
        rng = self.rng
        for title, primary in [("Bride", "Y"), ("Groom", "N")]:
            day.crm.append(("insert", "CustomerContact", dict(CustomerContactID=self.new_id("contact"), CustomerID=lead.cid,
                            ContactName=lead.nick if primary == "Y" else rng.choice(P.NICKNAMES), ContactTitle=title,
                            Phone=f"08{rng.randint(1, 9)}-{rng.randint(100, 999)}-{rng.randint(1000, 9999)}",
                            Email=f"{lead.nick.lower()}{rng.randint(1, 999)}@example.com" if primary == "Y" else None,
                            IsPrimaryContact=primary, CreatedAt=t, UpdatedAt=t)))
        day.crm.append(("update", "Customer", lead.cid, dict(District=rng.choice(["Bang Kapi", "Chatuchak", "Bang Na", "Sathorn", "Mueang"]),
                        Province=rng.choice(["Bangkok", "Bangkok", "Nonthaburi", "Samut Prakan", "Pathum Thani"])), t))

    def quote(self, lead, d):
        rng = self.rng
        typ = lead.pkg or ("Full" if lead.budget in ("1M-1.5M", "1.5M+") else rng.choice(["Full", "Partial", "DayOf"]))
        lead.pkg = typ
        p = next(x for x in C.PACKAGES if x[0] == self.package_id(typ, d))
        lo, hi = C.BUDGETS[lead.budget]
        lead.guests = lead.guests or rng.randint(*C.GUESTS[lead.budget])
        if typ == "Full":
            return float(round(rng.randint(lo, hi) * 1000 * rng.uniform(.9, 1.35) * p[5] / 100, -3))
        if typ in ("Partial", "DayOf"):
            return float(p[3] + (15000 if lead.guests > 450 else 0))
        return float(rng.randint(60, 180) * 1000)

    def close(self, day, lead, t, reason, by):
        lead.stage, lead.closed, lead.outcome = "lost", t, reason
        self.open.discard(lead.iid)
        day.crm.append(("update", "Inquiry", lead.iid, dict(Status="Lost", ClosedDate=t, ClosedBy=by, LostReason=reason), t))

    def book(self, day, lead, t):
        rng = self.rng
        self.customer_msg(day, lead, t - timedelta(minutes=30), ["commit"])
        lead.stage, lead.closed, lead.outcome = "booked", t, "Booked"
        self.open.discard(lead.iid)
        d = t.date()
        wed = lead.req_date or self.choose_date(d)
        if (wed - d).days < 30:
            wed = self.choose_date(d)
        cx = "High" if lead.guests and lead.guests > 450 and rng.random() < .5 else rng.choices(["Low", "Medium", "High"], [.35, .45, .2])[0]
        eid = self.new_id("event")
        guests = lead.guests
        if d.month == 4 and ("guests", d.year) not in self.flags:      # one event a year keyed with 0 guests (R08)
            self.flags.add(("guests", d.year))
            guests = 0
            self.schedule(d + timedelta(1), "fix", ("Event", eid, dict(GuestCount=lead.guests)))
        day.crm.append(("insert", "Event", dict(EventID=eid, CustomerID=lead.cid, InquiryID=lead.iid, PackageID=self.package_id(lead.pkg, d),
                        EventDate=wed, GuestCount=guests, ComplexityLevel=cx, AgreedPackagePrice=lead.quote, Status="Confirmed",
                        CancelledDate=None, CreatedDate=t, CreatedAt=t, UpdatedAt=t)))
        day.crm.append(("update", "Inquiry", lead.iid, dict(Status="Converted", ClosedDate=t, ClosedBy="Sales"), t))
        self.weddings_on[wed] = self.weddings_on.get(wed, 0) + 1
        ev = {"id": eid, "date": wed, "status": "Confirmed", "pkg": lead.pkg, "guests": lead.guests, "cx": cx}
        mult = (1.25 if lead.guests > 400 else 1) * (1.3 if cx == "High" else 1)
        for role, hours in C.HOURS[lead.pkg].items():
            for h in hours:
                when = min(d + timedelta(rng.randint(0, 14)), wed - timedelta(1))
                self.schedule(max(when, d), "assign", (ev, role, round(h * mult * rng.uniform(.85, 1.15), 1)))
        if rng.random() < .04 and (wed - d).days > 30:
            self.schedule(d + timedelta(rng.randint(7, (wed - d).days - 14)), "cancel", ev)
        self.schedule(wed + timedelta(1), "complete", ev)
        if rng.random() < .01:
            self.schedule(wed + timedelta(rng.randint(1, 5)), "assign", (ev, "Event Staff", 8.0))

    def assign(self, day, d, ev, role, hours):
        if ev["status"] == "Cancelled":
            return
        rng = self.rng
        wk = ev["date"] - timedelta(ev["date"].weekday())
        staff = [c for c in self.emp if (k := self.contract(c, ev["date"])) and k[0] == role and self.contract(c, d)]
        if not staff:
            staff = [c for c in self.emp if (k := self.contract(c, d)) and k[0] == role]
        if not staff:
            return
        code = min(staff, key=lambda c: (self.week_load.get((self.emp[c]["id"], wk), 0), rng.random()))
        self.week_load[(self.emp[code]["id"], wk)] = self.week_load.get((self.emp[code]["id"], wk), 0) + hours
        aid = self.new_id("asg")
        t = self.ts(d, 9, 18)
        typo = d.month == 3 and ("hours", d.year) not in self.flags
        if typo:                                    # one 999-hour typo a year, corrected a week later (R10)
            self.flags.add(("hours", d.year))
            self.schedule(d + timedelta(7), "fix", ("EventEmployeeAssignment", aid, dict(WorkloadHours=hours)))
        day.crm.append(("insert", "EventEmployeeAssignment", dict(EventEmployeeAssignmentID=aid, EventID=ev["id"], EmployeeID=self.emp[code]["id"],
                        AssignmentRole=role, AssignedDate=t, WorkloadHours=999 if typo else hours,
                        IsLead="Y" if role == C.LEAD_ROLE[ev["pkg"]] else "N", Status="Active", CreatedAt=t, UpdatedAt=t)))
        if rng.random() < .02 and d < ev["date"]:   # hours added after the weeks are over (C03b)
            self.schedule(ev["date"] + timedelta(rng.randint(3, 20)), "fix", ("EventEmployeeAssignment", aid, dict(WorkloadHours=hours + 4)))

    def followups(self, day, d):
        rng = self.rng
        ranked = self.ranker(d, self) if self.ranker else None
        for sp in self.sales_staff(d):
            mine = [self.leads[i] for i in self.open if self.leads[i].owner == sp and self.leads[i].arrived.date() < d
                    and not self.leads[i].spam and not (self.leads[i].last_staff and (d - self.leads[i].last_staff.date()).days < 2)]
            if ranked is not None:
                pos = {iid: k for k, iid in enumerate(ranked)}
                listed = sorted([l for l in mine if l.iid in pos], key=lambda l: pos[l.iid])
                rest = sorted([l for l in mine if l.iid not in pos], key=lambda l: -(l.last_cust or l.arrived).timestamp())
                order, src = listed + rest, {l.iid: "CallList" for l in listed}
            else:
                order = sorted(mine, key=lambda l: -((l.last_cust or l.arrived).timestamp() + rng.expovariate(1 / 86400)))
                src = {}
            for k, lead in enumerate(order[:round(FOLLOWUPS_PER_DAY * self.scale)]):
                t = self.ts(d, 10, 18)
                line = lead.account is not None and rng.random() < .5
                reached = rng.random() < REACH
                outcome = "Reached" if reached else ("No answer" if not line and rng.random() < .5 else "Left message")
                day.crm.append(("insert", "SalesActivity", dict(ActivityID=self.new_id("act"), InquiryID=lead.iid, EmployeeID=sp, ActivityAt=t,
                                ActivityType="LINE" if line else "Call", Outcome=outcome, Source=src.get(lead.iid, "Own"),
                                ListDate=d if lead.iid in src else None, CreatedAt=t, UpdatedAt=t)))
                if line:
                    self.staff_msg(day, lead, t, code=self.emp_code(sp))
                lead.last_staff = t
                if reached:
                    lead.fu_reached = t
