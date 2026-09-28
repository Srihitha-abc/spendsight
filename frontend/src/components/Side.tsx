import { useState } from "react";
import { ReplayState, api } from "../api";

const monthName = (m: string) => new Date(m + "-01T00:00:00").toLocaleDateString("en-US", { month: "short" });

export function ReplayBar({ state, onAction }: { state: ReplayState | null; onAction: () => void }) {
  const [busy, setBusy] = useState(false);
  const months = state?.months ?? [];
  const done = state?.month ?? "";
  const finished = !!done && done === months[months.length - 1];
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } finally { setBusy(false); onAction(); }
  };
  const running = !!state?.running;

  return (
    <section className="card p-4 flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold">Replay: Apr → Sep 2026</h2>
          <p className="text-xs muted">Steps through each month: detect, recall, verdict, apply engineer feedback, retain.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn" disabled={busy || running} onClick={() => act(api.replayReset)}>Reset memory</button>
          <button className="btn" disabled={busy || running || !state?.active || finished} onClick={() => act(() => api.replayNext(false))}>
            Next month ▸
          </button>
          <button className="btn btn-primary" disabled={busy || running || !state?.active || finished} onClick={() => act(() => api.replayNext(true))}>
            {running ? "Running…" : "Run all"}
          </button>
        </div>
      </div>
      <ol className="flex gap-1.5" aria-label="Replay progress">
        {months.map((m) => {
          const isDone = done >= m;
          const isNext = !isDone && months.find((x) => x > done) === m;
          return (
            <li key={m} className="flex-1 text-center text-xs rounded-md py-1.5 font-medium"
                style={{
                  background: isDone ? "var(--accent)" : "var(--surface-2)",
                  color: isDone ? "#fff" : "var(--text-secondary)",
                  outline: isNext && running ? "2px dashed var(--accent)" : undefined,
                }}>
              {monthName(m)}
            </li>
          );
        })}
      </ol>
      {!state?.active && <p className="text-xs secondary">Press <b>Reset memory</b> to start from an empty Hindsight bank.</p>}
      {state?.log.length ? (
        <pre className="text-[11px] leading-relaxed rounded-md p-2.5 overflow-auto max-h-40 whitespace-pre-wrap"
             style={{ background: "var(--surface-2)", color: "var(--text-secondary)" }}>
          {state.log.slice(-18).join("\n")}
        </pre>
      ) : null}
    </section>
  );
}

export function MentalModel({ content, backend }: { content: string | null; backend: string }) {
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold">What the agent has learned</h2>
      <p className="text-xs muted mb-2">Hindsight mental model “Known recurring cost patterns” ({backend})</p>
      {content ? (
        <div className="text-[13px] leading-relaxed whitespace-pre-wrap secondary max-h-72 overflow-auto">{content}</div>
      ) : (
        <p className="text-sm muted">Nothing yet. Memory builds as engineers respond to alerts.</p>
      )}
    </section>
  );
}

const SUGGESTIONS = ["Why was August more expensive than July?", "What recurring spikes should I expect in October?",
  "What waste did we find this quarter and who fixed it?"];

export function Chat() {
  const [q, setQ] = useState("");
  const [items, setItems] = useState<{ q: string; a: string; engine: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const ask = async (question: string) => {
    if (question.trim().length < 3) return;
    setBusy(true);
    try {
      const r = await api.chat(question);
      setItems((x) => [{ q: question, a: r.answer, engine: r.engine }, ...x]);
      setQ("");
    } catch (e) {
      setItems((x) => [{ q: question, a: String(e), engine: "error" }, ...x]);
    } finally { setBusy(false); }
  };
  return (
    <section className="card p-4 flex flex-col gap-2">
      <h2 className="text-sm font-semibold">Ask SpendSight</h2>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
        <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Ask about spend, answered from memory…" />
        <button className="btn btn-primary" disabled={busy}>{busy ? "…" : "Ask"}</button>
      </form>
      <div className="flex flex-wrap gap-1.5">
        {SUGGESTIONS.map((s) => <button key={s} className="btn text-[11px]" style={{ padding: "3px 8px" }} disabled={busy} onClick={() => ask(s)}>{s}</button>)}
      </div>
      {items.map((it, i) => (
        <div key={i} className="text-[13px] rounded-md p-2.5" style={{ background: "var(--surface-2)" }}>
          <div className="font-semibold mb-1">{it.q}</div>
          <div className="secondary whitespace-pre-wrap leading-relaxed">{it.a}</div>
          <div className="text-[10px] muted mt-1">via {it.engine}</div>
        </div>
      ))}
    </section>
  );
}
