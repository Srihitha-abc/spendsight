import type { ReactNode } from "react";

export function Logo({ size = 32 }: { size?: number }) {
  return (
    <div className="rounded-lg grid place-items-center text-white font-bold shrink-0"
         style={{ width: size, height: size, background: "var(--accent)", fontSize: size * 0.5 }} aria-hidden>S</div>
  );
}

export function Brand({ sub = "Cloud cost anomalies that remember" }: { sub?: string }) {
  return (
    <div className="flex items-center gap-2">
      <Logo />
      <div>
        <div className="font-semibold leading-tight">SpendSight</div>
        <div className="text-[11px] muted leading-tight">{sub}</div>
      </div>
    </div>
  );
}

/** Centered single-column layout for the onboarding pages. */
export function Page({ children, wide }: { children: ReactNode; wide?: boolean }) {
  return (
    <div className="min-h-screen flex flex-col">
      <main className={`w-full mx-auto px-4 py-8 flex-1 ${wide ? "max-w-5xl" : "max-w-md"}`}>{children}</main>
    </div>
  );
}

export function Steps({ current }: { current: number }) {
  const steps = ["Workspace", "Data", "Columns", "Analyze", "Dashboard"];
  return (
    <ol className="flex flex-wrap gap-1.5 text-[11px] mb-6" aria-label="Setup progress">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-1.5">
          <span className="rounded-full w-5 h-5 grid place-items-center font-semibold"
                style={{ background: i <= current ? "var(--accent)" : "var(--surface-2)", color: i <= current ? "#fff" : "var(--text-muted)" }}>
            {i < current ? "✓" : i + 1}
          </span>
          <span className={i === current ? "font-semibold" : "muted"}>{s}</span>
          {i < steps.length - 1 && <span className="muted mx-1">›</span>}
        </li>
      ))}
    </ol>
  );
}
