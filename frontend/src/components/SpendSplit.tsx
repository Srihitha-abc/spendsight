import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Insights, usd } from "../api";

const fmtWeek = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const fmtK = (v: number) => (v >= 1000 ? `$${(v / 1000).toFixed(0)}k` : `$${v}`);

/** Weekly anomaly spend: explained by memory (known) vs not (new/unknown). */
export function SpendSplit({ data }: { data: Insights | null }) {
  const weekly = data?.weekly ?? [];
  const known = weekly.reduce((s, w) => s + w.known, 0), fresh = weekly.reduce((s, w) => s + w.new, 0);
  return (
    <section className="card p-4">
      <h2 className="text-sm font-semibold">Anomaly spend: known vs new</h2>
      <p className="text-xs muted mb-3">Excess over baseline, by week the spike started. Known = memory explained it (expected / expected but unusual).</p>
      <div className="grid grid-cols-2 gap-3 mb-3">
        <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}><div className="text-[11px] muted">Known (auto-explained)</div><div className="text-xl font-semibold">{usd(known)}</div></div>
        <div className="rounded-lg p-2.5" style={{ background: "var(--surface-2)" }}><div className="text-[11px] muted">New / unexplained</div><div className="text-xl font-semibold">{usd(fresh)}</div></div>
      </div>
      <div style={{ height: 180 }}>
        {weekly.length === 0 ? <div className="h-full grid place-items-center text-sm muted">No analysed anomalies yet.</div> : (
          <ResponsiveContainer>
            <BarChart data={weekly} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="week" tickFormatter={fmtWeek} stroke="var(--axis)" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} minTickGap={20} />
              <YAxis tickFormatter={fmtK} stroke="var(--axis)" tick={{ fill: "var(--text-muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={48} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} content={({ active, payload }) => {
                if (!active || !payload?.length) return null;
                const w = payload[0].payload as { week: string; known: number; new: number };
                return <div className="card px-3 py-2 text-xs shadow-lg"><div className="font-semibold mb-1">Week of {fmtWeek(w.week)}</div>
                  <div className="secondary">Known: <b className="tnum">{usd(w.known)}</b></div><div className="secondary">New: <b className="tnum">{usd(w.new)}</b></div></div>;
              }} />
              <Bar dataKey="known" stackId="s" fill="var(--series-3)" stroke="var(--surface-1)" strokeWidth={2} maxBarSize={26} isAnimationActive={false} />
              <Bar dataKey="new" stackId="s" fill="var(--series-4)" stroke="var(--surface-1)" strokeWidth={2} radius={[4, 4, 0, 0]} maxBarSize={26} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
      <div className="flex gap-4 mt-2 text-xs secondary">
        <span className="flex items-center gap-1.5"><span style={{ width: 10, height: 10, borderRadius: 2, background: "var(--series-3)" }} />Known</span>
        <span className="flex items-center gap-1.5"><span style={{ width: 10, height: 10, borderRadius: 2, background: "var(--series-4)" }} />New / unexplained</span>
      </div>
    </section>
  );
}
