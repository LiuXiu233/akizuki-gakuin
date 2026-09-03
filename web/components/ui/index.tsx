"use client";

import { ReactNode, useEffect, useRef } from "react";

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`card ${className}`}>{children}</div>;
}

export function Section({
  title, action, children, className = "",
}: { title: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={className}>
      <div className="panel-title">
        <span className="h-px flex-1 bg-paper-edge" />
        <span>{title}</span>
        <span className="h-px flex-1 bg-paper-edge" />
        {action}
      </div>
      {children}
    </section>
  );
}

export function Meter({
  label, value, max = 100, tone = "dusk", suffix,
}: { label: string; value: number; max?: number; tone?: "dusk" | "sakura" | "moss" | "amber"; suffix?: string }) {
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const colors: Record<string, string> = {
    dusk: "bg-dusk", sakura: "bg-sakura-deep", moss: "bg-moss", amber: "bg-amber",
  };
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-xs">
        <span className="text-ink-mute">{label}</span>
        <span className="tabular-nums text-ink">{value}{suffix ?? `/${max}`}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-black/10">
        <div className={`h-full rounded-full transition-all duration-500 ${colors[tone]}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Modal({
  open, onClose, title, children, wide = false,
}: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-ink/40 p-0 backdrop-blur-sm sm:items-center sm:p-6"
         onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={ref}
           className={`flex max-h-[92vh] w-full flex-col overflow-hidden rounded-t-3xl bg-paper shadow-2xl sm:rounded-3xl ${wide ? "sm:max-w-4xl" : "sm:max-w-lg"}`}>
        <header className="flex items-center justify-between border-b border-paper-edge px-5 py-3.5">
          <h2 className="font-serif text-lg">{title}</h2>
          <button className="btn-quiet btn-sm" onClick={onClose} aria-label="关闭">✕</button>
        </header>
        <div className="scroll-thin flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({
  label, hint, children,
}: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-3">
      <label className="label">{label}</label>
      {children}
      {hint ? <p className="mt-1 text-[11px] leading-relaxed text-ink-mute">{hint}</p> : null}
    </div>
  );
}

export function Toggle({
  checked, onChange, label, hint,
}: { checked: boolean; onChange: (value: boolean) => void; label: string; hint?: string }) {
  return (
    <button type="button" onClick={() => onChange(!checked)}
            className="mb-3 flex w-full items-start gap-3 rounded-xl border border-paper-edge bg-white/60 px-3 py-2.5 text-left transition hover:bg-white">
      <span className={`mt-0.5 h-5 w-9 shrink-0 rounded-full p-0.5 transition ${checked ? "bg-dusk" : "bg-black/15"}`}>
        <span className={`block h-4 w-4 rounded-full bg-white transition-transform ${checked ? "translate-x-4" : ""}`} />
      </span>
      <span className="min-w-0">
        <span className="block text-sm">{label}</span>
        {hint ? <span className="mt-0.5 block text-[11px] leading-relaxed text-ink-mute">{hint}</span> : null}
      </span>
    </button>
  );
}

export function Tabs<T extends string>({
  tabs, active, onChange,
}: { tabs: Array<{ id: T; label: string; badge?: number }>; active: T; onChange: (id: T) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto scroll-thin">
      {tabs.map((tab) => (
        <button key={tab.id} onClick={() => onChange(tab.id)}
                className={`whitespace-nowrap rounded-lg px-3 py-1.5 text-xs transition ${
                  active === tab.id ? "bg-dusk text-paper" : "text-ink-mute hover:bg-black/5"}`}>
          {tab.label}
          {tab.badge ? <span className="ml-1 opacity-70">{tab.badge}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-ink-mute">
      <span className="h-3 w-3 animate-spin rounded-full border-2 border-dusk/25 border-t-dusk" />
      {label}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-xs text-ink-mute">{children}</p>;
}
