import { useCallback, useEffect, useRef, useState } from "react";
import { AlertView, CurveRow, Insights, ReplayState, Status, api, usd } from "./api";
import { AlertCard, ViewMode } from "./components/AlertCard";
import { Logo } from "./components/Brand";
import { CostChart } from "./components/CostChart";
import { LearningCurve } from "./components/LearningCurve";
import { Chat, MentalModel, ReplayBar } from "./components/Side";
import { SpendSplit } from "./components/SpendSplit";
import { DataInput } from "./pages/DataInput";
import { Login } from "./pages/Login";
import { Mapping } from "./pages/Mapping";
import { Processing } from "./pages/Processing";
import { Workspaces } from "./pages/Workspaces";
import { Route, SessionProvider, isAppRoute, useSession } from "./session";

export default function App() {
  return <SessionProvider><Router /></SessionProvider>;
}

function Router() {
  const { route, workspace } = useSession();
  switch (route) {
    case "login": return <Login />;
    case "workspaces": return <Workspaces />;
    case "data": return <DataInput />;
    case "mapping": return <Mapping />;
    case "processing": return <Processing />;
    default: return isAppRoute(route) ? <Shell key={workspace?.id} tab={route} /> : <Workspaces />;
  }
}

/** All workspace data the app views need, with polling while replay or re-analysis runs. */
function useWorkspaceData() {
  const [status, setStatus] = useState<Status | null>(null);
  const [alerts, setAlerts] = useState<AlertView[]>([]);
  const [curve, setCurve] = useState<CurveRow[]>([]);
  const [replay, setReplay] = useState<ReplayState | null>(null);
  const [insights, setInsights] = useState<Insights | null>(null);
  const [model, setModel] = useState<{ content: string | null; backend: string }>({ content: null, backend: "" });
  const [error, setError] = useState<string | null>(null);
  const wasRunning = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const [s, a, c, r, i] = await Promise.all([api.status(), api.alerts(), api.curve(), api.replayState(), api.insights()]);
      setStatus(s); setAlerts(a); setCurve(c); setReplay(r); setInsights(i); setError(null);
      api.mentalModel().then(setModel).catch(() => undefined);
    } catch (e) {
      setError(`Can't reach the SpendSight API (${(e as Error).message}). Is the backend running on :8000?`);
    }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  const reanalyzing = alerts.some((a) => a.reanalyzing);
  useEffect(() => {
    if (!replay?.running && !reanalyzing) {
      if (wasRunning.current) { wasRunning.current = false; refresh(); }
      return;
    }
    wasRunning.current = true;
    const t = setInterval(async () => {
      const [r, a, c] = await Promise.all([api.replayState(), api.alerts(), api.curve()]).catch(() => [null, null, null]);
      if (r) setReplay(r as ReplayState);
      if (a) setAlerts(a as AlertView[]);
      if (c) setCurve(c as CurveRow[]);
    }, 1500);
    return () => clearInterval(t);
  }, [replay?.running, reanalyzing, refresh]);

  const update = (v: AlertView) => {
    setAlerts((xs) => xs.map((x) => (x.anomaly.id === v.anomaly.id ? v : x)));
    api.alerts().then(setAlerts);  // related alerts may now be re-analysing
    api.status().then(setStatus);  // feedback_count drives the cold-start banner
    api.curve().then(setCurve);
    api.insights().then(setInsights);
    api.mentalModel().then(setModel).catch(() => undefined);
  };
  const afterReplayAction = async () => {
    const r = await api.replayState();
    setReplay(r.running ? r : { ...r, running: true });
    if (!r.running) setTimeout(refresh, 300);
  };
  return { status, alerts, curve, replay, insights, model, error, refresh, update, afterReplayAction };
}

const TABS: { key: Route; label: string }[] = [
  { key: "dashboard", label: "Dashboard" }, { key: "replay", label: "Replay" },
  { key: "compare", label: "Memory ON / OFF" }, { key: "insights", label: "Insights" },
];

function Shell({ tab }: { tab: Route }) {
  const { user, workspace, logout, go, selectWorkspace } = useSession();
  const d = useWorkspaceData();
  const [focus, setFocus] = useState<AlertView | null>(null);
  const [mode, setMode] = useState<ViewMode>("memory");
  const [filter, setFilter] = useState<"all" | "alerts">("all");
  const [menu, setMenu] = useState(false);
  const s = d.status;

  const analysed = d.alerts.filter((a) => a.verdicts.memory);
  const pending = analysed.filter((a) => a.verdicts.memory!.verdict !== "EXPECTED");
  const unknown = analysed.filter((a) => a.verdicts.memory!.verdict === "UNKNOWN");
  const knownSpend = analysed.filter((a) => ["EXPECTED", "EXPECTED_BUT_UNUSUAL"].includes(a.verdicts.memory!.verdict)).reduce((x, a) => x + a.anomaly.total_excess_usd, 0);
  const wasteSpend = analysed.filter((a) => a.verdicts.memory!.verdict === "NEW_WASTE").reduce((x, a) => x + a.anomaly.total_excess_usd, 0);

  const feed = (m: ViewMode) => {
    const shown = d.alerts.filter((a) => filter === "all" || (a.verdicts.memory && a.verdicts.memory.verdict !== "EXPECTED"));
    return (
      <section className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Alert feed <span className="muted font-normal">(newest first)</span></h2>
          <div className="flex gap-1.5 items-center">
            {tab === "dashboard" && (<>
              <span className="text-xs muted mr-1">Memory</span>
              {([["memory", "ON"], ["no_memory", "OFF"]] as const).map(([k, l]) => (
                <button key={k} className={`btn ${mode === k ? "active" : ""}`} onClick={() => setMode(k)}>{l}</button>
              ))}
            </>)}
            <select className="input ml-2" value={filter} onChange={(e) => setFilter(e.target.value as "all" | "alerts")} aria-label="Filter alerts">
              <option value="all">All incidents</option><option value="alerts">Only unexplained</option>
            </select>
          </div>
        </div>
        {shown.length === 0 && (
          <div className="card p-6 text-sm muted text-center">
            {d.alerts.length === 0 ? (s?.has_replay ? "No anomalies yet. Open Replay, reset memory and run it." : "No anomalies detected in this data.")
              : "Nothing needs attention. Memory explained every spike."}
          </div>
        )}
        {shown.map((v) => (
          <AlertCard key={v.anomaly.id} view={v} mode={m} selected={focus?.anomaly.id === v.anomaly.id}
                     showTruth={!!d.replay?.active} onSelect={() => setFocus(v)} onUpdate={d.update} />
        ))}
      </section>
    );
  };

  return (
    <div className="min-h-screen">
      <header className="border-b sticky top-0 z-10" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
        <div className="max-w-[1400px] mx-auto px-4 py-2.5 flex flex-wrap items-center gap-3">
          <button className="flex items-center gap-2" onClick={() => go("dashboard")} aria-label="SpendSight home">
            <Logo size={28} /><span className="font-semibold hidden sm:inline">SpendSight</span>
          </button>
          <button className="btn text-left" onClick={() => { selectWorkspace(null); go("workspaces"); }} title="Switch workspace">
            <span className="text-[10px] muted block leading-none">Workspace</span>
            <span className="font-semibold">{workspace?.name}</span> <span className="muted">▾</span>
          </button>
          <nav className="flex gap-1 overflow-x-auto" aria-label="Views">
            {TABS.map((t) => (
              <button key={t.key} onClick={() => go(t.key)} className="px-3 py-1.5 rounded-md text-sm font-medium whitespace-nowrap"
                      style={tab === t.key ? { background: "var(--surface-2)", color: "var(--text-primary)" } : { color: "var(--text-secondary)" }}
                      aria-current={tab === t.key ? "page" : undefined}>{t.label}</button>
            ))}
          </nav>
          <span className="flex-1" />
          {s && (
            <div className="hidden md:flex gap-2 text-[11px]">
              <Badge ok={s.memory_backend === "hindsight-cloud"} label={`memory: ${s.memory_backend}`} title={`bank ${s.bank_id}`} />
              <Badge ok={s.llm.startsWith("groq")} label={`llm: ${s.llm.replace("groq:", "")}`} />
            </div>
          )}
          <div className="relative">
            <button className="btn flex items-center gap-2" onClick={() => setMenu(!menu)} aria-expanded={menu}>
              <span className="w-6 h-6 rounded-full grid place-items-center text-[11px] font-bold text-white" style={{ background: "var(--series-7)" }}>
                {(user?.name ?? "?").slice(0, 1)}
              </span>
              <span className="hidden sm:inline text-xs">{user?.name}</span>
            </button>
            {menu && (
              <div className="card absolute right-0 mt-1 p-2 w-60 shadow-lg z-20 text-sm">
                <div className="px-2 py-1.5"><div className="font-semibold">{user?.name}</div><div className="text-xs muted">{user?.email} · {user?.provider}</div></div>
                <button className="w-full text-left px-2 py-1.5 rounded hover:bg-[var(--surface-2)]" onClick={() => { setMenu(false); go("data"); }}>Add / replace data</button>
                <button className="w-full text-left px-2 py-1.5 rounded hover:bg-[var(--surface-2)]" onClick={() => { setMenu(false); selectWorkspace(null); go("workspaces"); }}>Switch workspace</button>
                <button className="w-full text-left px-2 py-1.5 rounded hover:bg-[var(--surface-2)]" style={{ color: "var(--critical)" }} onClick={logout}>Log out</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-[1400px] mx-auto px-4 py-4 flex flex-col gap-4">
        {d.error && <div className="card p-3 text-sm" style={{ borderColor: "var(--critical)" }} role="alert">{d.error}</div>}
        {s && !s.has_data ? (
          <div className="card p-8 text-center flex flex-col items-center gap-3">
            <p className="font-semibold">No billing data in {workspace?.name} yet.</p>
            <button className="btn btn-primary" onClick={() => go("data")}>Add data →</button>
          </div>
        ) : (<>
          {tab === "dashboard" && (<>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <Kpi label="Incidents analysed" value={String(analysed.length)} sub={`of ${d.alerts.length} detected`} />
              <Kpi label="Needs attention" value={String(pending.length)} sub={unknown.length ? `${unknown.length} unknown: please classify` : "not explained by memory"} />
              <Kpi label="Known spend (explained)" value={usd(knownSpend)} sub="auto-explained, no page" />
              <Kpi label="New waste caught" value={usd(wasteSpend)} sub="excess over baseline" />
            </div>
            {s && s.feedback_count === 0 && !s.has_replay && analysed.length > 0 && (
              <div className="card p-3 text-sm" style={{ borderColor: "var(--accent)" }}>
                <b>Cold start.</b> This workspace's memory is empty, so every spike is <i>Unknown</i>. Classify a few with the buttons below; related alerts are re-checked with your answer automatically.
              </div>
            )}
            <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4 items-start">
              <div className="flex flex-col gap-4 lg:sticky lg:top-20">
                <CostChart focus={focus} onClearFocus={() => setFocus(null)} />
                <MentalModel content={d.model.content} backend={d.model.backend} />
              </div>
              {feed(mode)}
            </div>
          </>)}

          {tab === "replay" && (s?.has_replay ? (
            <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] gap-4 items-start">
              <div className="flex flex-col gap-4 lg:sticky lg:top-20">
                <ReplayBar state={d.replay} onAction={d.afterReplayAction} />
                <LearningCurve rows={d.curve} scoredBy={s?.has_replay ? "planted" : "feedback"} />
              </div>
              {feed("compare")}
            </div>
          ) : (
            <div className="card p-6 text-sm flex flex-col gap-2 max-w-2xl">
              <p className="font-semibold">Replay is available for the Acme Logistics demo.</p>
              <p className="secondary">Replay walks through six months with a scripted history of engineer feedback, so you can watch memory build up. Uploaded data has no scripted history: the agent learns from <i>your</i> feedback instead. Classify spikes on the Dashboard and watch the learning curve in Insights.</p>
              <button className="btn self-start" onClick={() => go("dashboard")}>Go to dashboard</button>
            </div>
          ))}

          {tab === "compare" && (<>
            <p className="text-sm secondary max-w-3xl">The same anomalies, analysed twice: <b>with Hindsight memory</b> (recall + reflect over past spikes, feedback and deploys) and <b>without memory</b> (the same model with only the numbers). Same guardrails apply to both.</p>
            {feed("compare")}
          </>)}

          {tab === "insights" && (
            <div className="grid lg:grid-cols-2 gap-4 items-start">
              <LearningCurve rows={d.curve} scoredBy={s?.has_replay ? "planted" : "feedback"} />
              <SpendSplit data={d.insights} />
              <MentalModel content={d.model.content} backend={d.model.backend} />
              <Chat />
            </div>
          )}
        </>)}
      </main>
    </div>
  );
}

function Kpi({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card p-3">
      <div className="text-xs muted">{label}</div>
      <div className="text-2xl font-semibold mt-0.5">{value}</div>
      <div className="text-[11px] muted">{sub}</div>
    </div>
  );
}

function Badge({ ok, label, title }: { ok: boolean; label: string; title?: string }) {
  return (
    <span className="rounded-full px-2 py-0.5 flex items-center gap-1" style={{ border: "1px solid var(--border)" }} title={title}>
      <span aria-hidden style={{ color: ok ? "var(--good)" : "var(--warning)" }}>●</span>
      <span className="secondary">{label}</span>
    </span>
  );
}
