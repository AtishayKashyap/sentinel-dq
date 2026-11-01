import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Heatmap } from "../components/Heatmap";
import { Card, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import { api } from "../lib/api";
import { CHECK_LABELS, relativeTime, scoreTone } from "../lib/format";

export function Overview() {
  const tables = useQuery({ queryKey: ["tables"], queryFn: api.tables });
  const heat = useQuery({ queryKey: ["heatmap", 14], queryFn: () => api.heatmap(14) });
  const latest = useQuery({ queryKey: ["latest"], queryFn: () => api.latest() });

  if (tables.error) return <ErrorBox error={tables.error} />;

  const t = tables.data ?? [];
  const checks = t.reduce((n, x) => n + x.checks, 0);
  const passing = t.reduce((n, x) => n + x.passing, 0);
  const critical = t.reduce((n, x) => n + x.critical_failing, 0);
  const lastRun = t.map((x) => x.last_run_at).sort().at(-1);
  const failing = (latest.data ?? [])
    .filter((r) => r.status !== "pass")
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "critical" ? -1 : 1));

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Data health</h1>
          <div className="muted">
            {lastRun ? `Last checked ${relativeTime(lastRun)}` : "No runs yet: run `sentinel run` or `sentinel demo`"}
          </div>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Tables monitored" value={tables.isLoading ? "…" : t.length} />
        <Kpi label="Checks passing" value={tables.isLoading ? "…" : `${passing}/${checks}`} tone={passing === checks ? "good" : undefined} />
        <Kpi label="Critical failures" value={tables.isLoading ? "…" : critical} tone={critical ? "bad" : "good"} />
      </div>

      <section className="grid tables" aria-label="Tables">
        {tables.isLoading && <Card><Loading /></Card>}
        {t.map((x) => {
          const tone = scoreTone(x.score);
          return (
            <Link key={`${x.datasource}.${x.table}`} to={`/tables/${x.datasource}/${x.table}`} className="card table-card">
              <div className="row">
                <div>
                  <div className="mono">{x.table}</div>
                  <div className="muted" style={{ fontSize: 12 }}>{x.datasource}</div>
                </div>
                <div className={`score ${tone}`} aria-label={`health score ${x.score}`}>{x.score.toFixed(0)}</div>
              </div>
              <div className="bar"><span className={tone} style={{ width: `${x.score}%` }} /></div>
              <div className="row muted" style={{ fontSize: 12.5 }}>
                <span>{x.passing} passing · {x.failing} failing</span>
                {x.critical_failing > 0 && <span style={{ color: "var(--bad)" }}>{x.critical_failing} critical</span>}
              </div>
            </Link>
          );
        })}
      </section>

      <div className="grid split">
        <Card title="Pass rate by day" aside="last 14 days">
          {heat.isLoading ? <Loading /> : heat.error ? <ErrorBox error={heat.error} /> : <Heatmap cells={heat.data ?? []} />}
        </Card>
        <Card title="Needs attention" aside={failing.length ? `${failing.length} checks` : undefined}>
          {latest.isLoading ? (
            <Loading />
          ) : failing.length === 0 ? (
            <div className="empty-state">Everything is passing.</div>
          ) : (
            <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 10 }}>
              {failing.slice(0, 8).map((r) => (
                <li key={r.id}>
                  <Link to={`/checks/${encodeURIComponent(r.check_id)}`} style={{ display: "grid", gap: 2 }}>
                    <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <StatusPill status={r.status} />
                      <span className="mono">{r.table}</span>
                      <span className="muted">{CHECK_LABELS[r.check_type] ?? r.check_type}</span>
                    </span>
                    <span className="muted" style={{ fontSize: 12.5 }}>{r.message}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
