// Thin fetch wrapper. Rows from the marts are plain JSON objects; numeric columns may arrive as numbers or strings
// (PostgreSQL numeric), so the format helpers below accept both.
export type Row = Record<string, any>;

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T = Row>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    credentials: "same-origin",
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    ...init,
  });
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const j = await r.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(r.status, msg);
  }
  return r.json();
}

export const post = <T = Row>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });

export type Kpi = { id: string; label: string; unit: string; definition: string; formula: string; level: string; source: string; target?: number };

export type Me = {
  username: string;
  display_name: string;
  role: "sales" | "ops" | "exec" | "data";
  employee_id: number | null;
  is_manager: boolean;
  pages: string[];
  data_as_of: { run_id: number; asof_date: string; finished_at: string; stale: boolean } | null;
};

// ---------- formatting ----------
export const n = (v: unknown): number | null => (v === null || v === undefined || v === "" ? null : Number(v));
const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
export const int = (v: unknown) => (n(v) === null ? "–" : nf0.format(n(v)!));
export const pct = (v: unknown, d = 0) => (n(v) === null ? "–" : `${(n(v)! * 100).toFixed(d)} %`);
export const hours = (v: unknown) => (n(v) === null ? "–" : `${nf0.format(n(v)!)} h`);
export const baht = (v: unknown) => {
  const x = n(v);
  if (x === null) return "–";
  if (Math.abs(x) >= 1e6) return `฿${(x / 1e6).toFixed(2)}M`;
  if (Math.abs(x) >= 1e4) return `฿${(x / 1e3).toFixed(0)}k`;
  return `฿${nf0.format(x)}`;
};
export const bahtFull = (v: unknown) => (n(v) === null ? "–" : `฿${nf0.format(n(v)!)}`);
export const minutes = (v: unknown) => {
  const x = n(v);
  if (x === null) return "–";
  if (x < 120) return `${Math.round(x)} min`;
  return `${(x / 60).toFixed(1)} h`;
};

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const monthShort = (ym: string, year = false) => `${MONTHS[+ym.slice(5, 7) - 1]}${year ? ` ${ym.slice(2, 4)}` : ""}`;
export const monthLong = (ym: string) =>
  new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1, 1).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
export const day = (iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" }) =>
  iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB", opts) : "–";
export const dayYear = (iso: string | null | undefined) => day(iso, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
export const weekLabel = (iso: string) => day(iso, { day: "numeric", month: "short" });
// local calendar date as YYYY-MM-DD (toISOString would shift to UTC)
export const isoLocal = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
