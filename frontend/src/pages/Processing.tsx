import { useEffect, useRef, useState } from "react";
import { api, Job } from "../api";
import { Brand, Page, Steps } from "../components/Brand";
import { Route, useSession } from "../session";

const ICON = { pending: "○", running: "◐", done: "✓", error: "✕" } as const;
const COLOR = { pending: "var(--text-muted)", running: "var(--accent)", done: "var(--good)", error: "var(--critical)" } as const;

export function Processing() {
  const { go, workspace } = useSession();
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    api.analyze().then(setJob).catch(async (e) => {
      // 409 = already running (e.g. a reload): just follow the existing job.
      if (String(e.message).includes("already running")) setJob(await api.job());
      else setErr(e.message);
    });
  }, []);

  useEffect(() => {
    if (!job || !job.running) return;
    const t = setInterval(async () => { const j = await api.job().catch(() => null); if (j) setJob(j); }, 700);
    return () => clearInterval(t);
  }, [job?.running]);

  useEffect(() => {
    if (job?.done && !job.error) {
      const t = setTimeout(() => go((job.redirect ?? "dashboard") as Route), 1200);
      return () => clearTimeout(t);
    }
  }, [job?.done]);

  return (
    <Page>
      <div className="mb-6"><Brand /></div>
      <Steps current={3} />
      <section className="card p-6">
        <h1 className="text-lg font-semibold">Analyzing {workspace?.name}</h1>
        <p className="text-sm secondary mb-5">Statistics find the spikes; Hindsight memory decides what they mean.</p>
        <ol className="flex flex-col gap-4" aria-live="polite">
          {(job?.steps ?? []).map((s) => (
            <li key={s.key} className="flex gap-3">
              <span className={`w-6 h-6 rounded-full grid place-items-center text-sm font-bold shrink-0 ${s.status === "running" ? "animate-pulse" : ""}`}
                    style={{ color: COLOR[s.status], border: `2px solid ${COLOR[s.status]}` }} aria-hidden>{ICON[s.status]}</span>
              <div className="min-w-0">
                <div className={`text-sm font-medium ${s.status === "pending" ? "muted" : ""}`}>{s.label}</div>
                {s.detail && <div className="text-xs secondary break-words">{s.detail}</div>}
              </div>
            </li>
          ))}
          {!job && !err && <li className="text-sm muted">Starting…</li>}
        </ol>
        {(err || job?.error) && (
          <div className="mt-5 flex flex-col gap-3">
            <p className="text-sm" style={{ color: "var(--critical)" }} role="alert">{err || job?.error}</p>
            <button className="btn self-start" onClick={() => go("data")}>← Back to data input</button>
          </div>
        )}
        {job?.done && !job.error && <p className="text-sm mt-5" style={{ color: "var(--good-ink)" }}>Done. Opening the {job.redirect === "replay" ? "replay" : "dashboard"}…</p>}
      </section>
    </Page>
  );
}
