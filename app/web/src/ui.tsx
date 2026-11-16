import { createContext, ReactNode, useCallback, useContext, useEffect, useId, useState } from "react";
import { ArrowDownRight, ArrowRight, ArrowUpRight, Info as InfoIcon, X } from "lucide-react";
import { api, Kpi, n, Row } from "./api";

// ---------- KPI catalog: every number defined (Spec 05 §4 rule 3) ----------
export const CatalogContext = createContext<Record<string, Kpi>>({});

export function Info({ id }: { id: string }) {
  const k = useContext(CatalogContext)[id];
  const tip = useId();
  if (!k) return null;
  return (
    <span className="relative inline-flex group align-middle">
      <button type="button" aria-describedby={tip} aria-label={`How ${k.label} is defined`}
        className="text-ink-3 hover:text-wine focus-visible:text-wine p-0.5 -m-0.5 rounded">
        <InfoIcon size={13} strokeWidth={2} />
      </button>
      <span id={tip} role="tooltip"
        className="pointer-events-none hidden group-hover:block group-focus-within:block
                   absolute z-50 left-1/2 -translate-x-1/2 top-6 w-72 rounded-lg bg-ink text-white text-[12px] leading-snug p-3 shadow-pop normal-case tracking-normal font-normal text-left">
        <span className="block font-semibold mb-1">{k.label}{k.unit && !["count", "status"].includes(k.unit) ? ` (${k.unit})` : ""}</span>
        <span className="block text-white/90">{k.definition}</span>
        <span className="block mt-2 text-white/60"><span className="text-white/80">Formula:</span> {k.formula}</span>
        <span className="block text-white/60"><span className="text-white/80">Level:</span> {k.level}</span>
        <span className="block text-white/50 mt-1 font-mono text-[10.5px] break-words">{k.source}</span>
      </span>
    </span>
  );
}

export const useKpi = (id: string) => useContext(CatalogContext)[id];

// ---------- data loading ----------
export function useData<T = Row>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!path) return;
    setError(null);
    api<T>(path).then(setData).catch((e) => setError(e.message));
  }, [path, ...deps]);
  useEffect(load, [load]);
  return { data, error, reload: load };
}

export function Loading({ error }: { error?: string | null }) {
  if (error) return <div className="card p-6 text-over">Could not load this page: {error}</div>;
  return (
    <div className="grid gap-4 animate-pulse" aria-busy="true" aria-label="Loading">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">{[0, 1, 2, 3].map((i) => <div key={i} className="card h-28" />)}</div>
      <div className="card h-72" />
    </div>
  );
}

// ---------- layout pieces ----------
export function Card({ title, question, kpi, right, children, className = "", pad = true }: {
  title: string; question?: string; kpi?: string; right?: ReactNode; children: ReactNode; className?: string; pad?: boolean;
}) {
  return (
    <section className={`card ${className}`}>
      <header className="card-h">
        <div className="min-w-0">
          <h2 className="card-t flex items-center gap-1.5">{title}{kpi && <Info id={kpi} />}</h2>
          {question && <p className="card-q">{question}</p>}
        </div>
        {right}
      </header>
      <div className={pad ? "px-5 pb-5 pt-1" : "pb-2"}>{children}</div>
    </section>
  );
}

export function PageHead({ title, sub, right }: { title: string; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-5">
      <div>
        <h1 className="text-[22px] font-semibold leading-tight">{title}</h1>
        {sub && <p className="text-ink-2 mt-1 text-[13.5px]">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line bg-page/60 px-5 py-6 text-center">
      <p className="font-medium text-ink-2">{title}</p>
      {children && <p className="text-[13px] text-ink-3 mt-1 max-w-xl mx-auto">{children}</p>}
    </div>
  );
}

// ---------- status: colour + word, never colour alone ----------
const STATUS: Record<string, string> = {
  Over: "bg-over-bg text-over", Full: "bg-over-bg text-over",
  Tight: "bg-tight-bg text-tight",
  OK: "bg-stone-soft text-ink-2", Room: "bg-stone-soft text-ink-2", Quiet: "bg-stone-cell text-ink-3",
};
export function Chip({ s, children }: { s: string | null | undefined; children?: ReactNode }) {
  if (!s) return <span className="text-ink-3">–</span>;
  return <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11.5px] font-semibold leading-none whitespace-nowrap ${STATUS[s] ?? "bg-stone-soft text-ink-2"}`}>{children ?? s}</span>;
}

// ---------- KPI tile: value, comparison, target, sparkline ----------
// direction is shown by the arrow and the printed number; colour stays reserved for status (Spec 05 §4 rule 5)
export function Delta({ now, then, label, fmt }: { now: unknown; then: unknown; label: string; fmt: (v: unknown) => string }) {
  const a = n(now), b = n(then);
  if (a === null || b === null) return <span className="text-ink-3">{label}: –</span>;
  const Icon = a > b ? ArrowUpRight : a < b ? ArrowDownRight : ArrowRight;
  return (
    <span className="inline-flex items-center gap-0.5 text-ink-2 whitespace-nowrap">
      <Icon size={13} className="text-ink-3" aria-hidden />
      <span className="sr-only">{a > b ? "up" : a < b ? "down" : "unchanged"} from</span>
      <span className="text-ink-3">{label}</span>&nbsp;<span className="num">{fmt(then)}</span>
    </span>
  );
}

export function Spark({ values, highlightLast = true, height = 28 }: { values: (number | null)[]; highlightLast?: boolean; height?: number }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p) => p[1] !== null) as [number, number][];
  if (pts.length < 2) return null;
  const w = 84, max = Math.max(...pts.map((p) => p[1])), min = Math.min(0, ...pts.map((p) => p[1]));
  const x = (i: number) => (i / (values.length - 1)) * (w - 6) + 3;
  const y = (v: number) => height - 3 - ((v - min) / (max - min || 1)) * (height - 6);
  const last = pts[pts.length - 1];
  return (
    <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} aria-hidden className="overflow-visible">
      <polyline fill="none" stroke="#ADA59B" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round"
        points={pts.map(([i, v]) => `${x(i)},${y(v)}`).join(" ")} />
      {highlightLast && <circle cx={x(last[0])} cy={y(last[1])} r={3.5} fill="#7A2E3B" stroke="#fff" strokeWidth={1.5} />}
    </svg>
  );
}

export function Tile({ kpi, label, value, sub, children, spark, tone }: {
  kpi?: string; label?: string; value: ReactNode; sub?: ReactNode; children?: ReactNode; spark?: (number | null)[]; tone?: "over" | "tight";
}) {
  const k = useKpi(kpi ?? "");
  return (
    <div className={`card px-4 py-3.5 flex flex-col gap-1 min-w-0 ${tone === "over" ? "border-l-[3px] border-l-over" : tone === "tight" ? "border-l-[3px] border-l-tight" : ""}`}>
      <div className="flex items-center gap-1.5 text-[12.5px] text-ink-2 font-medium">
        <span className="truncate">{label ?? k?.label}</span>{kpi && <Info id={kpi} />}
      </div>
      <div className="flex items-end justify-between gap-2">
        <div className="text-[26px] font-semibold leading-none tracking-tight whitespace-nowrap">{value}</div>
        {spark && <Spark values={spark} />}
      </div>
      {sub && <div className="text-[12px] text-ink-3">{sub}</div>}
      {children && <div className="flex flex-col gap-0.5 text-[12px] mt-0.5">{children}</div>}
    </div>
  );
}

// ---------- side drawer (details on demand) ----------
export function Drawer({ open, title, sub, onClose, children }: { open: boolean; title: string; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40" role="dialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-ink/25" onClick={onClose} />
      <aside className="absolute right-0 top-0 h-full w-full max-w-[560px] bg-surface shadow-pop flex flex-col">
        <header className="flex items-start justify-between gap-3 px-5 py-4 border-b border-line">
          <div><h2 className="text-[16px] font-semibold">{title}</h2>{sub && <div className="text-[13px] text-ink-2 mt-0.5">{sub}</div>}</div>
          <button className="btn-ghost !p-1.5" onClick={onClose} aria-label="Close"><X size={16} /></button>
        </header>
        <div className="overflow-y-auto p-5 flex-1">{children}</div>
      </aside>
    </div>
  );
}

// ---------- simple horizontal bar (HTML, value always printed) ----------
export function HBar({ value, max, color = "bg-wine", track = true }: { value: number; max: number; color?: string; track?: boolean }) {
  const w = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={`h-2.5 rounded-sm ${track ? "bg-hair" : ""} flex-1 min-w-[40px]`}>
      <div className={`h-full rounded-r-[3px] ${color}`} style={{ width: `${w}%` }} />
    </div>
  );
}

export function Legend({ items }: { items: { label: string; swatch: string; line?: boolean }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-ink-2">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          {i.line ? <span className="inline-block w-4 h-0.5 rounded" style={{ background: i.swatch }} />
                  : <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: i.swatch }} />}
          {i.label}
        </span>
      ))}
    </div>
  );
}

export const AXIS = { stroke: "#ECE6DD", tick: { fill: "#857D74", fontSize: 11 }, tickLine: false } as const;
export const GRID = { stroke: "#F3EFE9", vertical: false } as const;
export const C = { wine: "#7A2E3B", grey: "#ADA59B", ink: "#1F1B18", over: "#A3221A", ramp: ["#F0DDE1", "#E2BEC4", "#C98893", "#A5525F", "#7A2E3B"] };

export function ChartTip({ active, payload, label, fmt, title }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg bg-surface border border-line shadow-pop px-3 py-2 text-[12px]">
      <div className="font-semibold mb-1">{title ? title(label) : label}</div>
      {payload.filter((p: any) => p.value !== null && p.value !== undefined).map((p: any) => (
        <div key={p.dataKey} className="flex items-center gap-2 text-ink-2">
          <span className="w-2 h-2 rounded-sm" style={{ background: p.color || p.stroke || p.fill }} />
          <span>{p.name}</span><span className="ml-auto pl-3 num text-ink font-medium">{fmt ? fmt(p.value, p.dataKey) : p.value}</span>
        </div>
      ))}
    </div>
  );
}
