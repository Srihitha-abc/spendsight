import { useEffect, useState } from "react";
import { api, Status } from "../api";
import { Brand, Page, Steps } from "../components/Brand";
import { Dropzone } from "../components/Dropzone";
import { flow } from "../flow";
import { useSession } from "../session";

export function DataInput() {
  const { workspace, go, user, selectWorkspace } = useSession();
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const isDemo = workspace?.kind === "demo";

  useEffect(() => { api.status().then(setStatus).catch(() => setStatus(null)); }, [workspace?.id]);

  const loadDemo = async () => {
    setBusy("demo"); setErr(null);
    try { await api.loadDemo(); go("processing"); } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const upload = async (f: File) => {
    setBusy("upload"); setErr(null);
    try { flow.upload = await api.uploadBilling(f); go("mapping"); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };

  return (
    <Page wide>
      <div className="flex items-center justify-between mb-6">
        <Brand />
        <div className="text-xs secondary flex items-center gap-3">
          <span>{user?.name}</span>
          <button className="btn" onClick={() => { selectWorkspace(null); go("workspaces"); }}>Switch workspace</button>
        </div>
      </div>
      <Steps current={1} />
      <h1 className="text-xl font-semibold">Add billing data to {workspace?.name}</h1>
      <p className="text-sm secondary mb-5">
        SpendSight needs daily cost data, ideally 30+ days (the detector uses a 14-day baseline).
        {status?.has_data && <> This workspace already has {status.billing_rows.toLocaleString()} rows ({status.date_from} → {status.date_to}). <button className="underline" onClick={() => go("dashboard")}>Go to dashboard</button>.</>}
      </p>
      {err && <div className="card p-3 text-sm mb-4" style={{ borderColor: "var(--critical)" }} role="alert">{err}</div>}

      <div className="grid md:grid-cols-3 gap-4 items-start">
        <section className="card p-5 flex flex-col gap-3" style={isDemo ? { outline: "2px solid var(--accent)", outlineOffset: -1 } : undefined}>
          <span className="text-2xl" aria-hidden>🚚</span>
          <h2 className="font-semibold">Load demo data (Acme Logistics)</h2>
          <p className="text-sm secondary">Apr–Sep 2026, 3 AWS accounts, ~23k rows, deploy log and engineer feedback. One click.</p>
          <button className="btn btn-primary mt-auto" disabled={!!busy} onClick={loadDemo}>{busy === "demo" ? "Loading…" : "Load demo data"}</button>
        </section>

        <section className="card p-5 flex flex-col gap-3">
          <span className="text-2xl" aria-hidden>📄</span>
          <h2 className="font-semibold">Upload billing CSV</h2>
          <p className="text-sm secondary">AWS CUR, Cost Explorer, GCP, Azure, FOCUS, or any CSV with date, service and cost columns. Formats are detected automatically.</p>
          <Dropzone label="Drop a CSV here or click to browse" hint="Max 50 MB · hourly rows are rolled up to daily" onFile={upload} busy={busy === "upload"} />
          <div className="flex flex-wrap gap-3 text-xs">
            <a className="underline secondary" href={api.templateUrl("billing")} download>Download sample template</a>
            <a className="underline secondary" href={api.templateUrl("deploys")} download>deploys.csv template</a>
          </div>
        </section>

        <section className="card p-5 flex flex-col gap-3 opacity-60" aria-disabled>
          <span className="text-2xl" aria-hidden>🔌</span>
          <h2 className="font-semibold">Connect AWS account</h2>
          <p className="text-sm secondary">Pull costs straight from your AWS organization.</p>
          <span className="text-[11px] rounded-full px-2 py-0.5 self-start" style={{ border: "1px dashed var(--axis)" }}>Coming soon: AWS Cost Explorer / CUR</span>
          <button className="btn mt-auto" disabled>Connect</button>
        </section>
      </div>
    </Page>
  );
}
