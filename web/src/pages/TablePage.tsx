import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Sparkline } from "../components/Sparkline";
import { Card, ErrorBox, Loading, SeverityTag, StatusPill } from "../components/ui";
import { api } from "../lib/api";
import { CHECK_LABELS, formatObserved, parseCheckId, relativeTime } from "../lib/format";

export function TablePage() {
  const { datasource = "", table = "" } = useParams();
  const q = useQuery({ queryKey: ["latest", table], queryFn: () => api.latest(table) });
  const rows = (q.data ?? []).filter((r) => r.datasource === datasource);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="crumbs"><Link to="/">Overview</Link> / {datasource}</div>
          <h1 className="mono">{table}</h1>
        </div>
      </div>
      <Card title="Checks" aside={rows[0] ? `checked ${relativeTime(rows[0].executed_at)}` : undefined}>
        {q.isLoading ? (
          <Loading rows={5} />
        ) : q.error ? (
          <ErrorBox error={q.error} />
        ) : rows.length === 0 ? (
          <div className="empty-state">No results for this table.</div>
        ) : (
          <div className="scroll-x">
            <table className="dt">
              <thead>
                <tr>
                  <th>Status</th><th>Check</th><th>Target</th><th>Severity</th>
                  <th>Result</th><th className="right">Observed</th><th>Trend</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <td><StatusPill status={r.status} /></td>
                    <td><Link to={`/checks/${encodeURIComponent(r.check_id)}`}>{CHECK_LABELS[r.check_type] ?? r.check_type}</Link></td>
                    <td className="mono">{parseCheckId(r.check_id).target}</td>
                    <td><SeverityTag severity={r.severity} /></td>
                    <td className="muted" style={{ maxWidth: 360 }}>{r.message}</td>
                    <td className="right num mono">{formatObserved(r.observed, r.check_type)}</td>
                    <td><Sparkline checkId={r.check_id} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
