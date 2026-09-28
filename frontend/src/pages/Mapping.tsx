import { useState } from "react";
import { api, DeployReport, Target, UploadReport, usd } from "../api";
import { Brand, Page, Steps } from "../components/Brand";
import { Dropzone } from "../components/Dropzone";
import { flow } from "../flow";
import { useSession } from "../session";

const LABELS: Record<Target, string> = {
  date: "Date", service: "Service", cost_usd: "Cost (USD)", account: "Account", region: "Region",
  usage_type: "Usage type", resource_id: "Resource ID", team_tag: "Team tag",
};
const HELP: Partial<Record<Target, string>> = {
  account: "defaults to “default”", region: "defaults to “unknown”", usage_type: "defaults to the service name",
  resource_id: "left blank", team_tag: "taken from a Tags column if present, else “untagged”",
};

export function Mapping() {
  const { go, workspace } = useSession();
  const info = flow.upload;
  const [mapping, setMapping] = useState(info?.mapping ?? ({} as Record<Target, string | null>));
  const [report, setReport] = useState<UploadReport | null>(null);
  const [deploys, setDeploys] = useState<DeployReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!info) {
    return (
      <Page>
        <div className="card p-6 text-sm flex flex-col gap-3">
          <p>No file in progress. Upload a billing CSV first.</p>
          <button className="btn btn-primary self-start" onClick={() => go("data")}>← Data input</button>
        </div>
      </Page>
    );
  }
  const missing = info.wide ? [] : info.required.filter((t) => !mapping[t]);
  const commit = async () => {
    setBusy("commit"); setErr(null);
    try { setReport(await api.commitBilling(info.wide ? {} : mapping)); } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const uploadDeploys = async (f: File) => {
    setBusy("deploys"); setErr(null);
    try { setDeploys(await api.uploadDeploys(f)); } catch (e) { setErr((e as Error).message); } finally { setBusy(null); }
  };
  const sample = (col: string | null) => (col ? info.preview.find((r) => r[col])?.[col] ?? "" : "");

  return (
    <Page wide>
      <div className="mb-6"><Brand /></div>
      <Steps current={2} />
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-xl font-semibold">Map columns: {info.filename}</h1>
        <span className="text-xs rounded-full px-2.5 py-1 font-semibold" style={{ background: "color-mix(in srgb, var(--accent) 14%, transparent)" }}>
          Detected: {info.format_label}
        </span>
      </div>
      <p className="text-sm secondary mb-4">{info.rows.toLocaleString()} rows · {info.columns.length} columns · importing into <b>{workspace?.name}</b></p>
      {err && <div className="card p-3 text-sm mb-4" style={{ borderColor: "var(--critical)" }} role="alert">{err}</div>}

      {!report && (
        <div className="grid lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] gap-4 items-start">
          <section className="card p-4">
            {info.wide ? (
              <div className="text-sm flex flex-col gap-2">
                <p className="font-semibold">Wide Cost Explorer export</p>
                <p className="secondary">One column per service (e.g. “{info.columns[1]}”) and one row per day. SpendSight will unpivot it to date / service / cost and drop the “Service total” row and “Total costs” column.</p>
                <p className="text-xs muted">Account, region, usage type, resource and team aren't in this export; they'll be filled with defaults.</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead><tr className="text-left text-[11px] muted"><th className="pb-2">SpendSight field</th><th className="pb-2">Your column</th></tr></thead>
                <tbody>
                  {info.targets.map((t) => (
                    <tr key={t} className="align-top">
                      <td className="py-1.5 pr-2">
                        <div className="font-medium">{LABELS[t]}{info.required.includes(t) && <span style={{ color: "var(--critical)" }}> *</span>}</div>
                        {!mapping[t] && HELP[t] && <div className="text-[11px] muted">{HELP[t]}</div>}
                        {mapping[t] && <div className="text-[11px] muted truncate max-w-[150px]" title={sample(mapping[t])}>e.g. {sample(mapping[t]) || "—"}</div>}
                      </td>
                      <td className="py-1.5">
                        <select className="input w-full" value={mapping[t] ?? ""} aria-label={`Column for ${LABELS[t]}`}
                                onChange={(e) => setMapping({ ...mapping, [t]: e.target.value || null })}
                                style={info.required.includes(t) && !mapping[t] ? { borderColor: "var(--critical)" } : undefined}>
                          <option value="">{info.required.includes(t) ? "Choose a column…" : "— not in file —"}</option>
                          {info.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="flex items-center gap-2 mt-4">
              <button className="btn" onClick={() => go("data")}>← Back</button>
              <span className="flex-1" />
              <button className="btn btn-primary" disabled={!!busy || missing.length > 0} onClick={commit}>
                {busy === "commit" ? "Validating…" : "Validate & import"}
              </button>
            </div>
            {missing.length > 0 && <p className="text-xs mt-2" style={{ color: "var(--critical)" }}>Map the required field(s): {missing.map((m) => LABELS[m]).join(", ")}</p>}
          </section>

          <section className="card p-4 min-w-0">
            <h2 className="text-sm font-semibold mb-2">Preview: first {info.preview.length} rows</h2>
            <div className="overflow-x-auto">
              <table className="text-xs tnum whitespace-nowrap">
                <thead>
                  <tr>{info.columns.map((c) => {
                    const t = (Object.keys(mapping) as Target[]).find((k) => mapping[k] === c);
                    return <th key={c} className="text-left px-2 py-1.5 font-semibold" style={{ background: t ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "var(--surface-2)" }}>
                      {c}{t && <div className="text-[10px] font-normal" style={{ color: "var(--accent)" }}>→ {LABELS[t]}</div>}
                    </th>;
                  })}</tr>
                </thead>
                <tbody>
                  {info.preview.map((r, i) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      {info.columns.map((c) => <td key={c} className="px-2 py-1 secondary max-w-[220px] truncate">{r[c]}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </div>
      )}

      {report && (
        <div className="flex flex-col gap-4">
          <section className="card p-4">
            <h2 className="text-sm font-semibold mb-3">Import summary</h2>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
              <Tile label="Valid rows" value={report.rows_valid.toLocaleString()} sub={report.aggregated ? `rolled up to ${report.rows_daily.toLocaleString()} daily rows` : `${report.rows_daily.toLocaleString()} daily rows`} />
              <Tile label="Skipped rows" value={report.rows_skipped.toLocaleString()} sub={report.rows_skipped ? Object.entries(report.skipped_by_reason).map(([k, v]) => `${v} ${k}`).join(", ") : "none"} />
              <Tile label="Date range" value={`${report.days} days`} sub={`${report.date_from} → ${report.date_to}`} />
              <Tile label="Accounts · services" value={`${report.accounts.length} · ${report.service_count}`} sub={report.accounts.slice(0, 3).join(", ")} />
              <Tile label="Total spend" value={usd(report.total_spend)} sub={`${report.anomalies_preview} anomalies detected`} />
            </div>
            {report.warnings.map((w) => <p key={w} className="text-xs mt-3" style={{ color: "var(--text-secondary)" }}>⚠ {w}</p>)}
            {report.defaulted_columns.length > 0 && <p className="text-xs muted mt-2">Not in file, filled with defaults: {report.defaulted_columns.join(", ")}.</p>}
            {report.errors.length > 0 && (
              <details className="mt-3 text-xs">
                <summary className="cursor-pointer secondary">Show skipped rows ({Math.min(report.errors.length, 10)} of {report.rows_skipped})</summary>
                <ul className="mt-2 flex flex-col gap-1">
                  {report.errors.map((e, i) => (
                    <li key={i} className="tnum"><b>{e.row ? `Row ${e.row}` : "Cell"}:</b> {e.reason} <span className="muted">({Object.values(e.values).slice(0, 4).join(" | ")})</span></li>
                  ))}
                </ul>
              </details>
            )}
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {report.services.slice(0, 8).map((s) => <span key={s.service} className="rounded px-2 py-0.5" style={{ background: "var(--surface-2)" }}>{s.service} <b className="tnum">{usd(s.cost_usd)}</b></span>)}
            </div>
          </section>

          <section className="card p-4 grid md:grid-cols-[1fr_1fr] gap-4 items-center">
            <div>
              <h2 className="text-sm font-semibold">Optional: deploy log</h2>
              <p className="text-xs secondary">A deploys.csv (date, service, summary; team, author, id optional) lets the agent tie spikes to changes, e.g. "started the day image-service deploy #421 shipped".</p>
              {deploys && <p className="text-xs mt-2" style={{ color: "var(--good-ink)" }}>✓ {deploys.rows_valid} deploys imported ({deploys.date_from} → {deploys.date_to}){deploys.rows_skipped ? `, ${deploys.rows_skipped} skipped` : ""}</p>}
            </div>
            <Dropzone compact label={deploys ? "Replace deploys.csv" : "Drop deploys.csv (optional)"} onFile={uploadDeploys} busy={busy === "deploys"} />
          </section>

          <div className="flex gap-2">
            <button className="btn" onClick={() => setReport(null)}>← Change mapping</button>
            <span className="flex-1" />
            <button className="btn btn-primary" style={{ padding: "8px 16px" }} onClick={() => go("processing")}>Analyze →</button>
          </div>
        </div>
      )}
    </Page>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg p-3" style={{ background: "var(--surface-2)" }}>
      <div className="text-[11px] muted">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
      {sub && <div className="text-[11px] muted truncate" title={sub}>{sub}</div>}
    </div>
  );
}
