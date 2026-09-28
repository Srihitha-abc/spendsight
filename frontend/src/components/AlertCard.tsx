import { useState } from "react";
import { AlertView, Label, Verdict, api, shortSvc, usd } from "../api";
import { useSession } from "../session";

export type ViewMode = "compare" | "memory" | "no_memory";

const STATUS: Record<Label, { color: string; icon: string; text: string }> = {
  EXPECTED: { color: "var(--good)", icon: "✓", text: "Expected" },
  EXPECTED_BUT_UNUSUAL: { color: "var(--warning)", icon: "▲", text: "Expected but unusual" },
  NEW_WASTE: { color: "var(--critical)", icon: "✕", text: "New waste" },
  NEEDS_REVIEW: { color: "var(--serious)", icon: "?", text: "Needs review" },
  UNKNOWN: { color: "var(--text-muted)", icon: "○", text: "Unknown: please classify" },
};

export function VerdictPill({ label, small }: { label: Label; small?: boolean }) {
  const s = STATUS[label];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full font-semibold ${small ? "text-[11px] px-2 py-0.5" : "text-xs px-2.5 py-1"}`}
          style={{ background: `color-mix(in srgb, ${s.color} 16%, transparent)`, color: "var(--text-primary)" }}>
      <span aria-hidden style={{ color: s.color }}>{s.icon}</span>{s.text}
    </span>
  );
}

function VerdictPanel({ v, title, accent }: { v?: Verdict; title: string; accent: string }) {
  if (!v) return (
    <div className="rounded-lg p-3 text-xs muted" style={{ background: "var(--surface-2)" }}>{title}: not analysed yet</div>
  );
  return (
    <div className="rounded-lg p-3 flex flex-col gap-2" style={{ background: "var(--surface-2)", borderTop: `3px solid ${accent}` }}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide secondary">{title}</span>
        <span className="text-[11px] muted tnum">{Math.round(v.confidence * 100)}% conf.</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <VerdictPill label={v.verdict} />
        {v.cold_start && v.mode === "memory" && (
          <span className="text-[11px] rounded-full px-2 py-0.5" style={{ border: "1px dashed var(--axis)" }}>cold start: no history yet</span>
        )}
      </div>
      <p className="text-[13px] leading-snug">{v.explanation}</p>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <span className="muted">Impact</span><span className="tnum font-medium">{usd(v.estimated_daily_impact_usd)}/day</span>
        {v.magnitude_vs_usual && (<><span className="muted">Magnitude</span><span>{v.magnitude_vs_usual}</span></>)}
        {v.suggested_owner && (<><span className="muted">Owner</span><span>{v.suggested_owner}</span></>)}
      </div>
      {v.cited_past_events.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold muted mb-1">Cited past events</div>
          <ul className="text-xs flex flex-col gap-1">
            {v.cited_past_events.map((c, i) => (
              <li key={i} className="pl-2" style={{ borderLeft: "2px solid var(--axis)" }}>
                {c.date && <span className="tnum muted mr-1">{c.date}</span>}{c.summary}
              </li>
            ))}
          </ul>
        </div>
      )}
      {v.guardrail_notes.map((n) => <div key={n} className="text-[11px]" style={{ color: "var(--text-secondary)" }}>🛡 {n}</div>)}
      {v.mode === "memory" && v.recalled.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer muted">{v.recalled.length} memories recalled from Hindsight</summary>
          <ul className="mt-1 flex flex-col gap-1">
            {v.recalled.map((m, i) => (
              <li key={i} className="secondary leading-snug">
                {m.occurred_at && <span className="tnum muted mr-1">{m.occurred_at.slice(0, 10)}</span>}
                {m.text.length > 260 ? m.text.slice(0, 260) + "…" : m.text}
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="text-[10px] muted">engine: {v.engine}</div>
    </div>
  );
}

export function AlertCard({ view, mode, selected, onSelect, onUpdate, showTruth }: {
  view: AlertView; mode: ViewMode; selected: boolean; showTruth: boolean;
  onSelect: () => void; onUpdate: (v: AlertView) => void;
}) {
  const a = view.anomaly;
  const [note, setNote] = useState("");
  const { user } = useSession();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const last = view.feedback[view.feedback.length - 1];

  const run = async (what: string, fn: () => Promise<AlertView>) => {
    setBusy(what); setErr(null);
    try { onUpdate(await fn()); } catch (e) { setErr(String(e)); } finally { setBusy(null); }
  };
  const send = (response: string) => run(response, async () => {
    const out = await api.feedback(a.id, { response, note, author: user?.name ?? "unknown user" });
    setNote("");
    return out;
  });

  return (
    <article className="card p-4 flex flex-col gap-3 cursor-pointer" onClick={onSelect}
             style={selected ? { outline: "2px solid var(--accent)", outlineOffset: -1 } : undefined}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="text-xs muted tnum">
            {a.start_date}{a.days > 1 ? ` → ${a.end_date} (${a.days} days)` : ""} · day {Number(a.start_date.slice(8))} of month
          </div>
          <h3 className="font-semibold text-[15px]">{shortSvc(a.service)} spike · {a.team_tag}</h3>
          <div className="text-xs secondary">{a.account} · {a.top_line_items.map((l) => `${l.usage_type} ×${l.resources}`).join(", ")}</div>
        </div>
        <div className="text-right">
          <div className="text-lg font-semibold tnum">+{usd(a.delta_usd)}<span className="text-xs muted font-normal">/day</span></div>
          <div className="text-xs muted tnum">
            {a.ratio ? `${a.ratio.toFixed(1)}× baseline` : "new spend"} · z={a.z_score} · {usd(a.total_excess_usd)} total
          </div>
        </div>
      </header>

      {view.deploys.length > 0 && (
        <div className="text-xs rounded-md px-2.5 py-1.5" style={{ background: "var(--surface-2)" }}>
          <span className="muted mr-1">Nearby deploys:</span>{view.deploys.slice(0, 2).map((d) => (
            <span key={d.deploy_id} className="mr-2">
              Deploy <b>#{d.deploy_id}</b> {d.service} by {d.author}, {d.relation}: <span className="secondary">{d.summary}</span>
            </span>
          ))}
        </div>
      )}

      <div className={`grid gap-3 ${mode === "compare" ? "md:grid-cols-2" : ""}`}>
        {mode !== "no_memory" && <VerdictPanel v={view.verdicts.memory} title="With Hindsight memory" accent="var(--series-1)" />}
        {mode !== "memory" && <VerdictPanel v={view.verdicts.no_memory} title="Without memory" accent="var(--series-2)" />}
      </div>

      <div className="flex flex-col gap-2" onClick={(e) => e.stopPropagation()}>
        {view.feedback.length > 0 && (
          <ul className="flex flex-col gap-1 text-xs">
            {view.feedback.map((f) => (
              <li key={f.id} className="flex gap-2">
                <span className="font-semibold whitespace-nowrap">{f.author}</span>
                <span className="uppercase text-[10px] font-bold rounded px-1 py-px self-start" style={{ background: "var(--surface-2)" }}>{f.response}</span>
                <span className="secondary">{f.note}</span>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <input className="input flex-1 min-w-40" placeholder={last ? "Correct the verdict… (retained as a correction)" : "Add context for the agent (optional)"}
                 value={note} onChange={(e) => setNote(e.target.value)} />
          <span className="text-[11px] muted whitespace-nowrap" title="Recorded on the feedback and retained into memory">as {user?.name}</span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="btn" disabled={!!busy} onClick={() => send("expected")}>✓ Expected</button>
          <button className="btn" disabled={!!busy} onClick={() => send("waste")}>✕ Waste (fixed)</button>
          <button className="btn" disabled={!!busy} onClick={() => send("investigating")}>… Investigating</button>
          <span className="flex-1" />
          <button className="btn btn-primary" disabled={!!busy} onClick={() => run("analyze", () => api.analyzeOne(a.id))}>
            {busy === "analyze" ? "Analysing…" : view.verdicts.memory ? "Re-analyse" : "Analyse"}
          </button>
        </div>
        {busy && busy !== "analyze" && <div className="text-xs muted">Retaining feedback into Hindsight…</div>}
        {err && <div className="text-xs" style={{ color: "var(--critical)" }}>{err}</div>}
        {view.reanalyzing && <div className="text-xs" style={{ color: "var(--accent)" }}>◐ Re-checking this alert with the feedback you just gave on a similar spike…</div>}
        {showTruth && view.truth && view.truth_source === "planted" && (
          <div className="text-[11px] muted flex items-center gap-1.5">Ground truth (planted): <VerdictPill label={view.truth} small /></div>
        )}
      </div>
    </article>
  );
}
