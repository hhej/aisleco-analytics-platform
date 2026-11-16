import { FormEvent, ReactNode, useEffect, useState } from "react";
import { Activity, BookOpen, CalendarRange, Clock, LayoutDashboard, ListChecks, LogOut, PlayCircle, TrendingUp } from "lucide-react";
import { api, ApiError, dayYear, Kpi, Me, post } from "./api";
import { CatalogContext } from "./ui";
import Today from "./pages/Today";
import SalesPerformance from "./pages/SalesPerformance";
import Capacity from "./pages/Capacity";
import Overview from "./pages/Overview";
import Health from "./pages/Health";
import Demo from "./pages/Demo";

const PAGES: Record<string, { label: string; icon: typeof Activity; render: (me: Me) => ReactNode }> = {
  today: { label: "Today", icon: ListChecks, render: (me) => <Today me={me} /> },
  "sales-performance": { label: "Sales performance", icon: TrendingUp, render: (me) => <SalesPerformance me={me} /> },
  capacity: { label: "Capacity planner", icon: CalendarRange, render: (me) => <Capacity me={me} /> },
  overview: { label: "Business overview", icon: LayoutDashboard, render: () => <Overview /> },
  health: { label: "Data & ML health", icon: Activity, render: () => <Health /> },
  demo: { label: "Demo panel", icon: PlayCircle, render: () => <Demo /> },
};
const ROLE_NAME = { sales: "Sales", ops: "Operations", exec: "Owner", data: "Data team" };

const hashPage = () => window.location.hash.replace(/^#\/?/, "");

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const [catalog, setCatalog] = useState<Record<string, Kpi>>({});
  const [page, setPage] = useState(hashPage());

  useEffect(() => {
    api<Me>("/api/me").then(setMe).catch(() => setMe(null));
    const h = () => setPage(hashPage());
    window.addEventListener("hashchange", h);
    return () => window.removeEventListener("hashchange", h);
  }, []);
  useEffect(() => {
    if (me) api<{ kpis: Kpi[] }>("/api/catalog/kpis").then((c) => setCatalog(Object.fromEntries(c.kpis.map((k) => [k.id, k]))));
  }, [me]);

  if (me === undefined) return null;
  if (me === null) return <Login onLogin={(m) => { setMe(m); window.location.hash = `/${m.pages[0]}`; }} />;

  const current = me.pages.includes(page) ? page : me.pages[0];
  const logout = async () => { await post("/api/logout", {}); window.location.hash = ""; setMe(null); };

  return (
    <CatalogContext.Provider value={catalog}>
      <div className="min-h-screen flex">
        <nav aria-label="Pages" className="shrink-0 w-[60px] lg:w-[228px] bg-surface border-r border-line flex flex-col sticky top-0 h-screen">
          <div className="h-16 flex items-center gap-2.5 px-3.5 lg:px-5 border-b border-line">
            <span className="w-8 h-8 rounded-lg bg-wine text-white grid place-items-center font-serif text-[17px]" aria-hidden>A</span>
            <span className="hidden lg:block font-semibold tracking-tight text-[15px]">Aisle &amp; Co.</span>
          </div>
          <ul className="p-2 lg:p-3 flex flex-col gap-0.5">
            {me.pages.map((p) => {
              const P = PAGES[p], on = p === current;
              return (
                <li key={p}>
                  <a href={`#/${p}`} aria-current={on ? "page" : undefined} title={P.label}
                    className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium transition-colors
                      ${on ? "bg-wine-50 text-wine" : "text-ink-2 hover:bg-hair hover:text-ink"}`}>
                    <P.icon size={17} strokeWidth={on ? 2.2 : 1.8} aria-hidden />
                    <span className="hidden lg:inline">{P.label}</span>
                  </a>
                </li>
              );
            })}
          </ul>
          <div className="px-2 lg:px-3">
            <a href="/catalog" target="_blank" rel="noopener" title="Data catalog"
              className="flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] font-medium text-ink-2 hover:bg-hair hover:text-ink transition-colors">
              <BookOpen size={17} strokeWidth={1.8} aria-hidden /><span className="hidden lg:inline">Data catalog</span>
            </a>
          </div>
          <div className="mt-auto p-2 lg:p-3 border-t border-line">
            <div className="hidden lg:block px-2.5 pb-2">
              <div className="text-[13px] font-semibold">{me.display_name}</div>
              <div className="text-[12px] text-ink-3">{ROLE_NAME[me.role]}{me.is_manager ? " · manager" : ""}</div>
            </div>
            <button onClick={logout} className="w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] text-ink-2 hover:bg-hair" title="Sign out">
              <LogOut size={16} aria-hidden /><span className="hidden lg:inline">Sign out</span>
            </button>
          </div>
        </nav>
        <div className="flex-1 min-w-0 flex flex-col">
          <header className="h-16 shrink-0 flex items-center justify-between gap-3 px-4 lg:px-8 border-b border-line bg-page/90 backdrop-blur sticky top-0 z-30">
            <div className="text-[13px] text-ink-2 truncate">
              <span className="font-medium text-ink">{PAGES[current].label}</span>
              <span className="hidden sm:inline"> · {me.display_name}, {ROLE_NAME[me.role]}</span>
            </div>
            <Freshness me={me} />
          </header>
          <main className="flex-1 px-4 lg:px-8 py-6 max-w-[1440px] w-full">{PAGES[current].render(me)}</main>
        </div>
      </div>
    </CatalogContext.Provider>
  );
}

function Freshness({ me }: { me: Me }) {
  const f = me.data_as_of;
  if (!f) return <span className="text-[12.5px] text-tight">No pipeline run yet</span>;
  const t = new Date(f.finished_at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return (
    <span className={`inline-flex items-center gap-1.5 text-[12.5px] whitespace-nowrap rounded-full px-2.5 py-1
      ${f.stale ? "bg-tight-bg text-tight font-medium" : "bg-surface border border-line text-ink-2"}`}
      title={`Pipeline run ${f.run_id}, finished ${new Date(f.finished_at).toLocaleString("en-GB")}`}>
      <Clock size={13} aria-hidden />
      Data as of {dayYear(f.asof_date)} <span className="hidden md:inline">· refreshed {t}</span>{f.stale && " · out of date"}
    </span>
  );
}

function Login({ onLogin }: { onLogin: (m: Me) => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try { onLogin(await post<Me>("/api/login", { username: u, password: p })); }
    catch (x) { setErr(x instanceof ApiError && x.status === 401 ? "Wrong username or password." : "The server is not reachable."); }
    finally { setBusy(false); }
  };
  return (
    <div className="min-h-screen grid lg:grid-cols-2">
      <div className="hidden lg:flex flex-col justify-between bg-wine text-white p-12">
        <div className="flex items-center gap-3">
          <span className="w-9 h-9 rounded-lg bg-white/15 grid place-items-center font-serif text-[19px]">A</span>
          <span className="font-semibold text-[17px] tracking-tight">Aisle &amp; Co.</span>
        </div>
        <div>
          <p className="font-serif text-[34px] leading-tight max-w-md">Every couple answered. Every date staffed.</p>
          <p className="text-white/70 mt-4 max-w-md">The daily call list for sales, the capacity plan for operations and the business overview for the owner, from one warehouse refreshed every night.</p>
        </div>
        <p className="text-white/50 text-[12px]">Wedding planning · Bangkok</p>
      </div>
      <div className="flex items-center justify-center p-6">
        <form onSubmit={submit} className="w-full max-w-sm">
          <h1 className="text-[22px] font-semibold">Sign in</h1>
          <p className="text-ink-2 mt-1 mb-6 text-[13.5px]">Use your staff login. You will see the pages for your role.</p>
          <label className="label block mb-1.5" htmlFor="u">Username</label>
          <input id="u" className="input w-full mb-4" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus required />
          <label className="label block mb-1.5" htmlFor="p">Password</label>
          <input id="p" type="password" className="input w-full" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required />
          {err && <p className="text-over text-[13px] mt-3" role="alert">{err}</p>}
          <button className="btn-primary w-full justify-center mt-6 !py-2.5" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          <p className="text-[12px] text-ink-3 mt-6 leading-relaxed">
            Demo logins: <b>may</b>, <b>ken</b> (sales), <b>nok</b> (sales manager), <b>som</b> (operations), <b>ann</b> (owner), <b>dev</b> (data team).
          </p>
        </form>
      </div>
    </div>
  );
}
