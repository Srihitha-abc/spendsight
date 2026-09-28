import { useEffect, useState } from "react";
import {
  Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceArea, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, AlertView, CostSummary, GroupPoint, shortSvc, usd } from "../api";

const SLOTS = ["--series-1", "--series-2", "--series-3", "--series-4", "--series-5", "--series-6", "--series-7"];

const axis = { stroke: "var(--axis)", tick: { fill: "var(--text-muted)", fontSize: 11 }, tickLine: false };
const fmtDate = (d: string) => new Date(d + "T00:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric" });
const fmtK = (v: number) => (v >= 1000 ? `$${(v / 1000).toFixed(v >= 10000 ? 0 : 1)}k` : `$${v}`);

// Color follows the entity: a service keeps its slot whatever account filter is active.
const colorOf = (() => {
  const seen: string[] = [];
  return (svc: string) => {
    if (svc === "Other") return "var(--series-other)";
    if (!seen.includes(svc)) seen.push(svc);
    return `var(${SLOTS[seen.indexOf(svc) % SLOTS.length]})`;
  };
})();

function TipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string; dash?: boolean }[] }) {
  return (
    <div className="card px-3 py-2 text-xs shadow-lg" style={{ minWidth: 180 }}>
      <div className="font-semibold mb-1">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5 secondary">
            {r.color && <span style={{ width: 10, height: r.dash ? 2 : 10, borderRadius: 2, background: r.color, display: "inline-block" }} />}
            {r.label}
          </span>
          <span className="tnum font-medium">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export function CostChart({ focus, onClearFocus }: { focus: AlertView | null; onClearFocus: () => void }) {
  const [account, setAccount] = useState("");
  const [data, setData] = useState<CostSummary | null>(null);
  const [group, setGroup] = useState<GroupPoint[] | null>(null);

  useEffect(() => { api.costs(account || undefined).then(setData).catch(() => setData(null)); }, [account]);
  useEffect(() => {
    setGroup(null);
    if (focus) api.group(focus.anomaly).then(setGroup).catch(() => setGroup(null));
  }, [focus?.anomaly.id]);

  const a = focus?.anomaly;
  return (
    <section className="card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
        <div>
          <h2 className="text-sm font-semibold">
            {a ? `${shortSvc(a.service)} · ${a.team_tag} · ${a.account}` : "Daily cloud spend by service"}
          </h2>
          <p className="text-xs muted">
            {a ? "Daily cost vs 14-day rolling baseline; shaded = incident window"
               : "Dots mark detected anomalies (z > 3 and ≥ $150 over baseline). Click an alert to zoom in."}
          </p>
        </div>
        {a ? (
          <button className="btn" onClick={onClearFocus}>← All services</button>
        ) : (
          <select className="input" value={account} onChange={(e) => setAccount(e.target.value)} aria-label="Account filter">
            {["", ...(data?.accounts ?? [])].map((x) => <option key={x} value={x}>{x || "All accounts"}</option>)}
          </select>
        )}
      </div>

      <div style={{ height: 280 }}>
        {a && group ? (
          <ResponsiveContainer>
            <LineChart data={group} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="date" tickFormatter={fmtDate} minTickGap={40} {...axis} />
              <YAxis tickFormatter={fmtK} width={52} {...axis} axisLine={false} />
              <ReferenceArea x1={a.start_date} x2={a.end_date} fill="var(--critical)" fillOpacity={0.1} />
              <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ active, payload, label }) =>
                active && payload?.length ? (
                  <TipBox title={fmtDate(String(label))} rows={[
                    { label: "Cost", value: usd(Number(payload[0]?.payload.cost)), color: "var(--series-1)" },
                    { label: "Baseline", value: payload[0]?.payload.baseline == null ? "—" : usd(Number(payload[0].payload.baseline)), color: "var(--text-muted)", dash: true },
                  ]} />
                ) : null} />
              <Line dataKey="baseline" stroke="var(--text-muted)" strokeWidth={2} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
              <Line dataKey="cost" stroke="var(--series-1)" strokeWidth={2} dot={false} activeDot={{ r: 5, stroke: "var(--surface-1)", strokeWidth: 2 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : data ? (
          <ResponsiveContainer>
            <AreaChart data={data.series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="date" tickFormatter={fmtDate} minTickGap={40} {...axis} />
              <YAxis tickFormatter={fmtK} width={52} {...axis} axisLine={false} />
              <Tooltip cursor={{ stroke: "var(--axis)" }} content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const row = payload[0].payload;
                const hit = data.anomalies.filter((x) => x.date === label);
                return (
                  <TipBox title={`${fmtDate(String(label))} · ${usd(row.total)}`} rows={[
                    ...[...data.services].reverse().map((s) => ({ label: shortSvc(s), value: usd(Number(row[s])), color: colorOf(s) })),
                    ...hit.map((h) => ({ label: `⚠ ${shortSvc(h.service)} anomaly`, value: `+${usd(h.delta_usd)}` })),
                  ]} />
                );
              }} />
              {data.services.map((s) => (
                <Area key={s} dataKey={s} stackId="1" type="monotone" stroke="var(--surface-1)" strokeWidth={1}
                      fill={colorOf(s)} fillOpacity={0.85} isAnimationActive={false} />
              ))}
              {data.anomalies.map((x) => (
                <ReferenceDot key={x.id} x={x.date} y={x.total} r={5} fill="var(--critical)" stroke="var(--surface-1)" strokeWidth={2} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full grid place-items-center text-sm muted">No billing data loaded.</div>
        )}
      </div>

      {!a && data && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-3 text-xs secondary">
          {data.services.map((s) => (
            <span key={s} className="flex items-center gap-1.5">
              <span style={{ width: 10, height: 10, borderRadius: 2, background: colorOf(s) }} />{shortSvc(s)}
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <span style={{ width: 10, height: 10, borderRadius: 5, background: "var(--critical)" }} />Anomaly
          </span>
        </div>
      )}
    </section>
  );
}
