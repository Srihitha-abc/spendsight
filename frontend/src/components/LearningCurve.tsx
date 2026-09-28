import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CurveRow } from "../api";

const month = (m: string) => new Date(m + "-01T00:00:00").toLocaleDateString("en-US", { month: "short" });

export function LearningCurve({ rows, scoredBy = "planted" }: { rows: CurveRow[]; scoredBy?: "planted" | "feedback" }) {
  const data = rows.map((r) => ({ ...r, label: month(r.month) }));
  const tot = (k: keyof CurveRow) => rows.reduce((s, r) => s + Number(r[k]), 0);
  const incidents = tot("incidents");
  const withFA = tot("memory_false_alarms"), withoutFA = tot("no_memory_false_alarms");
  const empty = incidents === 0;

  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold">False alarms per month</h2>
      <p className="text-xs muted mb-3">
        Alerts raised on spikes that were actually expected, scored against {scoredBy === "planted"
          ? "the planted ground truth" : "your team's final classification of each spike"}.
      </p>
      <div className="grid grid-cols-3 gap-3 mb-3">
        <Stat label="Without memory" value={empty ? "—" : String(withoutFA)} />
        <Stat label="With Hindsight memory" value={empty ? "—" : String(withFA)} />
        <Stat label="Correct verdicts (with / without)" value={empty ? "—" : `${tot("memory_correct")} / ${tot("no_memory_correct")} of ${incidents}`} />
      </div>
      <div style={{ height: 190 }}>
        {empty ? (
          <div className="h-full grid place-items-center text-sm muted text-center px-6">
            {scoredBy === "planted" ? "Run replay to watch the agent learn month by month."
              : "Classify spikes on the Dashboard; each answer becomes a data point here."}
          </div>
        ) : (
          <ResponsiveContainer>
            <BarChart data={data} barGap={2} margin={{ top: 16, right: 8, bottom: 0, left: -20 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="label" stroke="var(--axis)" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} />
              <YAxis allowDecimals={false} stroke="var(--axis)" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const r = payload[0].payload as CurveRow & { label: string };
                return (
                  <div className="card px-3 py-2 text-xs shadow-lg">
                    <div className="font-semibold mb-1">{r.label} · {r.incidents} incident{r.incidents === 1 ? "" : "s"}</div>
                    <div className="secondary">Without memory: <b className="tnum">{r.no_memory_false_alarms}</b> false alarms</div>
                    <div className="secondary">With memory: <b className="tnum">{r.memory_false_alarms}</b> false alarms</div>
                    <div className="secondary">Missed waste (with memory): <b className="tnum">{r.memory_missed_waste}</b></div>
                  </div>
                );
              }} />
              <Bar dataKey="no_memory_false_alarms" name="Without memory" fill="var(--series-2)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false}>
                <LabelList dataKey="no_memory_false_alarms" position="top" fill="var(--text-secondary)" fontSize={11} formatter={(v: number) => (v ? v : "")} />
              </Bar>
              <Bar dataKey="memory_false_alarms" name="With memory" fill="var(--series-1)" radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false}>
                <LabelList dataKey="memory_false_alarms" position="top" fill="var(--text-secondary)" fontSize={11} formatter={(v: number) => (v ? v : "")} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="flex gap-4 mt-2 text-xs secondary">
        <span className="flex items-center gap-1.5"><span style={{ width: 10, height: 10, borderRadius: 2, background: "var(--series-2)" }} />Without memory</span>
        <span className="flex items-center gap-1.5"><span style={{ width: 10, height: 10, borderRadius: 2, background: "var(--series-1)" }} />With Hindsight memory</span>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}>
      <div className="text-[11px] muted leading-tight">{label}</div>
      <div className="text-xl font-semibold mt-0.5">{value}</div>
    </div>
  );
}
