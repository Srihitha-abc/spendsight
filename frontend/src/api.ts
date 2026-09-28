export type Label = "EXPECTED" | "NEW_WASTE" | "EXPECTED_BUT_UNUSUAL" | "NEEDS_REVIEW" | "UNKNOWN";

export interface LineItem { usage_type: string; resources: number; cost_usd: number }
export interface Anomaly {
  id: string; account: string; service: string; team_tag: string;
  start_date: string; end_date: string; peak_date: string;
  peak_cost_usd: number; baseline_usd: number; delta_usd: number; z_score: number;
  total_excess_usd: number; days: number; ratio: number | null; top_line_items: LineItem[];
}
export interface MemoryItem { id?: string; text: string; occurred_at?: string; type?: string }
export interface Verdict {
  verdict: Label; confidence: number; explanation: string;
  cited_past_events: { date?: string; summary: string }[];
  suggested_owner?: string; estimated_daily_impact_usd: number; magnitude_vs_usual?: string;
  mode: "memory" | "no_memory"; engine: string; cold_start: boolean;
  recalled: MemoryItem[]; guardrail_notes: string[];
}
export interface Feedback { id: number; response: string; author: string; note: string; source: string; submitted_at: string }
export interface Deploy { deploy_id: number; date: string; time_utc: string; service: string; team: string; author: string; summary: string; relation: string }
export interface AlertView {
  anomaly: Anomaly; verdicts: Partial<Record<"memory" | "no_memory", Verdict>>;
  feedback: Feedback[]; deploys: Deploy[]; truth: Label | null; truth_source: "planted" | "feedback" | null;
  history: number; reanalyzing: boolean;
}
export interface Workspace {
  id: string; name: string; kind: "demo" | "custom"; bank_id: string; description?: string;
  created_at: string | null; has_data?: boolean; anomalies?: number | null;
}
export interface Status {
  workspace: Workspace; memory_backend: string; llm: string; bank_id: string; replay_month: string;
  replay_running: boolean; has_replay: boolean; months: string[]; has_data: boolean; billing_rows: number;
  anomalies: number; deploys: number; date_from: string | null; date_to: string | null; feedback_count: number;
}
export interface CurveRow {
  month: string; incidents: number;
  memory_false_alarms: number; no_memory_false_alarms: number;
  memory_correct: number; no_memory_correct: number;
  memory_missed_waste: number; no_memory_missed_waste: number;
}
export interface ReplayState { month: string; running: boolean; log: string[]; active: boolean; months: string[]; available: boolean }
export interface CostSummary {
  series: ({ date: string; total: number } & Record<string, number | string>)[];
  services: string[]; accounts: string[];
  anomalies: { id: string; date: string; total: number; service: string; delta_usd: number }[];
}
export interface GroupPoint { date: string; cost: number; baseline: number | null }
export type Target = "date" | "service" | "cost_usd" | "account" | "region" | "usage_type" | "resource_id" | "team_tag";
export interface UploadInfo {
  filename: string; format: string; format_label: string; wide: boolean; columns: string[]; rows: number;
  mapping: Record<Target, string | null>; required: Target[]; targets: Target[]; missing_required: Target[];
  preview: Record<string, string>[];
}
export interface UploadReport {
  format_label: string; rows_total: number; rows_valid: number; rows_skipped: number;
  skipped_by_reason: Record<string, number>; errors: { row: number | null; reason: string; values: Record<string, string> }[];
  rows_daily: number; aggregated: boolean; date_from: string; date_to: string; days: number; accounts: string[];
  services: { service: string; cost_usd: number }[]; service_count: number; total_spend: number;
  defaulted_columns: string[]; warnings: string[]; anomalies_preview: number;
}
export interface DeployReport { rows_total: number; rows_valid: number; rows_skipped: number; date_from: string; date_to: string; mapping: Record<string, string | null> }
export interface JobStep { key: string; label: string; status: "pending" | "running" | "done" | "error"; detail: string }
export interface Job { running: boolean; done: boolean; error: string | null; steps: JobStep[]; redirect?: string }
export interface Insights { by_verdict: Record<string, number>; weekly: { week: string; known: number; new: number }[] }

let workspaceId = "acme";
export const setWorkspaceId = (id: string) => { workspaceId = id; };

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Workspace": workspaceId };
  if (!(init.body instanceof FormData)) headers["content-type"] = "application/json";
  const r = await fetch(`/api${path}`, { ...init, headers });
  if (!r.ok) {
    let msg = `${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return r.json();
}
const enc = encodeURIComponent;
const post = <T,>(path: string, body?: unknown) =>
  req<T>(path, { method: "POST", body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body) });
const file = (f: File) => { const fd = new FormData(); fd.append("file", f); return fd; };

export const api = {
  workspaces: () => req<Workspace[]>("/workspaces"),
  createWorkspace: (name: string) => post<Workspace>("/workspaces", { name }),
  status: () => req<Status>("/status"),
  loadDemo: () => post<Status>("/demo/load"),
  uploadBilling: (f: File) => post<UploadInfo>("/upload/billing", file(f)),
  commitBilling: (mapping: Partial<Record<Target, string | null>>) => post<UploadReport>("/upload/billing/commit", { mapping }),
  uploadDeploys: (f: File) => post<DeployReport>("/upload/deploys", file(f)),
  templateUrl: (kind: "billing" | "deploys") => `/api/upload/template?kind=${kind}`,
  analyze: () => post<Job>("/analyze"),
  job: () => req<Job>("/analyze/status"),
  costs: (account?: string) => req<CostSummary>(`/costs/summary${account ? `?account=${enc(account)}` : ""}`),
  group: (a: Anomaly) => req<GroupPoint[]>(`/costs/group?account=${enc(a.account)}&service=${enc(a.service)}&team_tag=${enc(a.team_tag)}`),
  alerts: () => req<AlertView[]>("/anomalies"),
  alert: (id: string) => req<AlertView>(`/anomalies/${enc(id)}/view`),
  analyzeOne: (id: string) => post<AlertView>(`/anomalies/${enc(id)}/analyze?mode=both`),
  feedback: (id: string, body: { response: string; note: string; author: string }) => post<AlertView>(`/anomalies/${enc(id)}/feedback`, body),
  curve: () => req<CurveRow[]>("/learning-curve"),
  insights: () => req<Insights>("/insights"),
  replayState: () => req<ReplayState>("/replay/state"),
  replayReset: () => post<ReplayState>("/replay/reset"),
  replayNext: (all = false) => post<{ started: boolean }>(`/replay/next?all=${all}`),
  mentalModel: () => req<{ name: string; content: string | null; backend: string }>("/memory/model"),
  chat: (question: string) => post<{ answer: string; engine: string }>("/chat", { question }),
};

export const usd = (n: number) => `$${Math.round(n).toLocaleString("en-US")}`;
export const shortSvc = (s: string) => s.replace(/^(Amazon|AWS)(?=[A-Z])/, "");
