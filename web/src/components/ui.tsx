import type { ReactNode } from "react";
import type { Severity, Status } from "../lib/api";

export function StatusPill({ status }: { status: Status }) {
  const label = { pass: "Passing", fail: "Failing", error: "Error" }[status];
  return <span className={`pill ${status}`}>{label}</span>;
}

export function SeverityTag({ severity }: { severity: Severity }) {
  return <span className={`tag ${severity}`}>{severity}</span>;
}

export function Card({ title, aside, children }: { title?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {(title || aside) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {aside && <div className="muted">{aside}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, tone }: { label: string; value: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="card">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-box" role="alert">
      Could not load data: {msg}. Is the API running?
    </div>
  );
}
