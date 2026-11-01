import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, ErrorBox, Loading, SeverityTag, StatusPill } from "../components/ui";
import { api, type CheckResult, type HistoryPoint } from "../lib/api";
import { CHECK_LABELS, formatAxis, formatObserved, parseCheckId, relativeTime } from "../lib/format";

const DAY_FMT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

/** Threshold to draw on the history chart, when the check type has one. */
export function thresholdFor(r: CheckResult): number | null {
  const d = r.details;
  const pick = (k: string) => (typeof d[k] === "number" ? (d[k] as number) : null);
  switch (r.check_type) {
    case "null_rate":
      return pick("threshold");
    case "freshness":
      return pick("max_lag_hours");
    default:
      return null;
  }
}

/** Baseline vs current bin proportions for the drift chart. */
export function driftBins(details: Record<string, unknown>) {
  const edges = (details.edges as number[] | undefined) ?? [];
  const base = (details.baseline as number[] | undefined) ?? [];
  const cur = (details.current as number[] | undefined) ?? [];
  const sb = base.reduce((a, b) => a + b, 0) || 1;
  const sc = cur.reduce((a, b) => a + b, 0) || 1;
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1));
  return base.map((b, i) => {
    const lo = i === 0 ? "<" : fmt(edges[i - 1] ?? 0);
    const hi = i === base.length - 1 ? "+" : fmt(edges[i] ?? 0);
    const label = i === 0 ? `< ${hi}` : i === base.length - 1 ? `≥ ${lo}` : `${lo}–${hi}`;
    return { bin: label, baseline: +(b / sb).toFixed(4), current: +((cur[i] ?? 0) / sc).toFixed(4) };
  });
}

function HistoryChart({ points, result }: { points: HistoryPoint[]; result: CheckResult }) {
  const data = points.map((p) => ({ ...p, t: DAY_FMT.format(new Date(p.executed_at)) }));
  const threshold = thresholdFor(result);
  return (
    <div style={{ height: 260 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="t" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={24} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={56}
            tickFormatter={(v: number) => formatAxis(v, result.check_type)} />
          <Tooltip
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as HistoryPoint | undefined) : undefined;
              if (!p) return null;
              return (
                <div className="chart-tip">
                  <div><StatusPill status={p.status} /> <span className="muted">{new Date(p.executed_at).toLocaleString()}</span></div>
                  <div style={{ marginTop: 4 }}>{p.message}</div>
                  <div className="muted">7-run pass rate: {(p.pass_rate_7 * 100).toFixed(0)}%</div>
                </div>
              );
            }}
          />
          {threshold !== null && (
            <ReferenceLine y={threshold} stroke="var(--bad)" strokeDasharray="4 4"
              label={{ value: "threshold", fill: "var(--bad)", fontSize: 11, position: "insideTopRight" }} />
          )}
          <Line
            dataKey="observed"
            stroke="var(--chart-1)"
            strokeWidth={2}
            isAnimationActive={false}
            connectNulls
            dot={(props: { cx?: number; cy?: number; payload?: HistoryPoint; index?: number }) => (
              <circle key={props.index} cx={props.cx} cy={props.cy} r={3.5}
                fill={props.payload?.status === "pass" ? "var(--good)" : "var(--bad)"} stroke="var(--surface)" strokeWidth={1.5} />
            )}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function DriftChart({ details }: { details: Record<string, unknown> }) {
  const data = driftBins(details);
  if (!data.length) return null;
  return (
    <div style={{ height: 240 }}>
      <ResponsiveContainer>
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barGap={2}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="bin" tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} tickLine={false} axisLine={false} width={44}
            tickFormatter={(v: number) => `${Math.round(v * 100)}%`} />
          <Tooltip formatter={(v) => `${(Number(v) * 100).toFixed(1)}%`} contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8 }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="baseline" fill="var(--chart-2)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="current" fill="var(--chart-1)" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Samples({ resultId, count }: { resultId: number; count: number }) {
  const q = useQuery({ queryKey: ["samples", resultId], queryFn: () => api.samples(resultId), enabled: count > 0 });
  if (count === 0) return <div className="empty-state">No failing rows captured for this result.</div>;
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const rows = q.data ?? [];
  const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  return (
    <div className="scroll-x">
      <table className="dt mono">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{r[c] === null ? <span className="muted">NULL</span> : String(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const HIDDEN_DETAILS = new Set(["edges", "baseline", "current"]);

export function CheckPage() {
  const checkId = decodeURIComponent(useParams().checkId ?? "");
  const meta = parseCheckId(checkId);
  const hist = useQuery({ queryKey: ["history", checkId, 60], queryFn: () => api.history(checkId, 60) });
  const lastId = hist.data?.at(-1)?.id;
  const latest = useQuery({ queryKey: ["result", lastId], queryFn: () => api.result(lastId ?? -1), enabled: lastId !== undefined });

  if (hist.error) return <ErrorBox error={hist.error} />;
  const r = latest.data;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumbs">
            <Link to="/">Overview</Link> / <Link to={`/tables/${meta.datasource}/${meta.table}`}>{meta.table}</Link>
          </div>
          <h1>
            {CHECK_LABELS[meta.type] ?? meta.type} <span className="mono muted" style={{ fontSize: 16 }}>{meta.target}</span>
          </h1>
        </div>
        {r && (
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <SeverityTag severity={r.severity} />
            <StatusPill status={r.status} />
          </div>
        )}
      </div>

      {!r ? (
        <Card><Loading rows={4} /></Card>
      ) : (
        <>
          <div className="grid split">
            <Card title="History" aside={`${hist.data?.length ?? 0} runs`}>
              <HistoryChart points={hist.data ?? []} result={r} />
            </Card>
            <Card title="Latest result" aside={relativeTime(r.executed_at)}>
              <p style={{ marginTop: 0 }}>{r.message}</p>
              <dl className="kv">
                <dt>Observed</dt><dd className="mono">{formatObserved(r.observed, r.check_type)}</dd>
                {r.failing_rows !== null && (<><dt>Failing rows</dt><dd className="num">{r.failing_rows.toLocaleString()}</dd></>)}
                {r.total_rows !== null && (<><dt>Rows scanned</dt><dd className="num">{r.total_rows.toLocaleString()}</dd></>)}
                <dt>Query time</dt><dd className="num">{r.duration_ms.toFixed(1)} ms</dd>
                {Object.entries(r.details)
                  .filter(([k]) => !HIDDEN_DETAILS.has(k))
                  .map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt>{k.replaceAll("_", " ")}</dt>
                      <dd className="mono">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
                    </div>
                  ))}
                <dt>Check id</dt><dd className="mono" style={{ wordBreak: "break-all" }}>{r.check_id}</dd>
              </dl>
            </Card>
          </div>
          {r.check_type === "drift" && Array.isArray(r.details.baseline) && (
            <Card title="Distribution: baseline vs current window" aside="share of rows per quantile bin">
              <DriftChart details={r.details} />
            </Card>
          )}
          <Card title="Failing row samples" aside={r.sample_count ? `${r.sample_count} rows · PII masked` : undefined}>
            <Samples resultId={r.id} count={r.sample_count} />
          </Card>
        </>
      )}
    </>
  );
}
