import { useEffect, useState } from "react";
import { api, Workspace } from "../api";
import { Brand, Page, Steps } from "../components/Brand";
import { useSession } from "../session";

export function Workspaces() {
  const { user, selectWorkspace, go, logout } = useSession();
  const [list, setList] = useState<Workspace[] | null>(null);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.workspaces().then(setList).catch((e) => setErr(String(e.message ?? e))); }, []);

  const open = (w: Workspace) => { selectWorkspace(w); go(w.has_data ? "dashboard" : "data"); };
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const w = await api.createWorkspace(name || "My company");
      selectWorkspace(w); go("data");
    } catch (e) { setErr(String((e as Error).message)); } finally { setBusy(false); }
  };
  const demo = list?.find((w) => w.kind === "demo");
  const custom = list?.filter((w) => w.kind === "custom") ?? [];

  return (
    <Page wide>
      <div className="flex items-center justify-between mb-6">
        <Brand />
        <div className="text-xs secondary flex items-center gap-3">
          <span>{user?.name}</span><button className="btn" onClick={logout}>Log out</button>
        </div>
      </div>
      <Steps current={0} />
      <h1 className="text-xl font-semibold">Choose a workspace</h1>
      <p className="text-sm secondary mb-5">Each workspace has its own data and its own Hindsight memory bank, so what the agent learns about one company never leaks into another.</p>
      {err && <div className="card p-3 text-sm mb-4" style={{ borderColor: "var(--critical)" }}>{err}</div>}

      <div className="grid md:grid-cols-2 gap-4">
        <button className="card p-5 text-left flex flex-col gap-2 hover:shadow-md transition-shadow" disabled={!demo}
                onClick={() => demo && (selectWorkspace(demo), go("data"))}>
          <span className="text-2xl" aria-hidden>🚚</span>
          <span className="font-semibold">Acme Logistics <span className="muted font-normal">(demo data)</span></span>
          <span className="text-sm secondary">6 months of synthetic AWS billing across 3 accounts, with 7 planted incidents, a deploy log and engineer feedback. Includes Replay.</span>
          <span className="text-[11px] muted mt-1">Memory bank: {demo?.bank_id ?? "…"}</span>
        </button>

        <div className="card p-5 flex flex-col gap-2">
          <span className="text-2xl" aria-hidden>➕</span>
          <span className="font-semibold">New workspace <span className="muted font-normal">(upload your own data)</span></span>
          <span className="text-sm secondary">Starts with an empty memory. The agent says "Unknown" until you classify a spike, then learns from your answers.</span>
          <div className="flex gap-2 mt-1">
            <input className="input flex-1" placeholder="Company name, e.g. Nimbus Retail" value={name}
                   onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} />
            <button className="btn btn-primary" disabled={busy} onClick={create}>{busy ? "Creating…" : "Create"}</button>
          </div>
        </div>
      </div>

      {custom.length > 0 && (
        <section className="mt-6">
          <h2 className="text-sm font-semibold mb-2">Your workspaces</h2>
          <div className="card divide-y" style={{ borderColor: "var(--border)" }}>
            {custom.map((w) => (
              <button key={w.id} className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-[var(--surface-2)]" onClick={() => open(w)}>
                <span>
                  <span className="font-medium">{w.name}</span>
                  <span className="text-xs muted ml-2">{w.has_data ? "has data" : "no data yet"} · bank {w.bank_id}</span>
                </span>
                <span className="text-xs secondary">Open →</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </Page>
  );
}
