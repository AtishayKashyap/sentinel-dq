import { useQuery } from "@tanstack/react-query";
import { Card, ErrorBox, Loading } from "../components/ui";
import { api } from "../lib/api";
import { relativeTime } from "../lib/format";

export function RunsPage() {
  const q = useQuery({ queryKey: ["runs", 50], queryFn: () => api.runs(50) });
  return (
    <>
      <div className="page-head"><h1>Runs</h1></div>
      <Card>
        {q.isLoading ? <Loading rows={6} /> : q.error ? <ErrorBox error={q.error} /> : (
          <div className="scroll-x">
            <table className="dt">
              <thead>
                <tr><th>#</th><th>Started</th><th>Trigger</th><th className="right">Checks</th>
                  <th className="right">Passed</th><th className="right">Failed</th><th className="right">Errored</th><th>Duration</th></tr>
              </thead>
              <tbody>
                {(q.data ?? []).map((r) => (
                    <tr key={r.id}>
                      <td className="mono">{r.id}</td>
                      <td title={r.started_at}>{relativeTime(r.started_at)}</td>
                      <td><span className="tag">{r.trigger}</span></td>
                      <td className="right num">{r.total}</td>
                      <td className="right num" style={{ color: "var(--good)" }}>{r.passed}</td>
                      <td className="right num" style={{ color: r.failed ? "var(--bad)" : undefined }}>{r.failed}</td>
                      <td className="right num" style={{ color: r.errored ? "var(--warn)" : undefined }}>{r.errored}</td>
                      <td className="muted num">{(r.duration_ms / 1000).toFixed(2)}s</td>
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
