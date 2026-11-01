import type { HeatCell } from "../lib/api";

/** Discrete buckets read faster than a gradient and stay legible in both themes. */
export const HEAT_BUCKETS = [
  { min: 1, color: "var(--good)", label: "all passing" },
  { min: 0.8, color: "color-mix(in srgb, var(--good) 45%, var(--surface-2))", label: "≥ 80%" },
  { min: 0.5, color: "var(--warn)", label: "50–80%" },
  { min: 0, color: "var(--bad)", label: "< 50%" },
] as const;

export function heatColor(rate: number): string {
  const r = Math.max(0, Math.min(1, rate));
  return (HEAT_BUCKETS.find((b) => r >= b.min - 1e-9) ?? HEAT_BUCKETS[3]).color;
}

export function buildMatrix(cells: HeatCell[]) {
  const days = [...new Set(cells.map((c) => c.day))].sort();
  const tables = [...new Set(cells.map((c) => `${c.datasource}.${c.table}`))].sort();
  const lookup = new Map(cells.map((c) => [`${c.datasource}.${c.table}|${c.day}`, c]));
  return { days, tables, lookup };
}

export function Heatmap({ cells }: { cells: HeatCell[] }) {
  const { days, tables, lookup } = buildMatrix(cells);
  if (!days.length) return <div className="empty-state">No runs in this window yet.</div>;
  return (
    <div className="scroll-x">
      <div
        className="heatmap"
        style={{ gridTemplateColumns: `minmax(110px, max-content) repeat(${days.length}, minmax(22px, 1fr))` }}
        role="table"
        aria-label="Daily pass rate per table"
      >
        <span />
        {days.map((d) => (
          <span key={d} className="day" role="columnheader">
            {d.slice(8)}
          </span>
        ))}
        {tables.map((t) => (
          <div key={t} style={{ display: "contents" }} role="row">
            <span className="label mono" role="rowheader">
              {t.split(".").slice(1).join(".")}
            </span>
            {days.map((d) => {
              const c = lookup.get(`${t}|${d}`);
              return c ? (
                <div
                  key={d}
                  className="cell"
                  role="cell"
                  style={{ background: heatColor(c.pass_rate) }}
                  title={`${t} · ${d}: ${(c.pass_rate * 100).toFixed(0)}% of ${c.checks} checks passed`}
                />
              ) : (
                <div key={d} className="cell empty" role="cell" title={`${t} · ${d}: no run`} />
              );
            })}
          </div>
        ))}
      </div>
      <div className="legend" style={{ marginTop: 12 }}>
        {HEAT_BUCKETS.map((b) => (
          <span key={b.label}><i style={{ background: b.color }} />{b.label}</span>
        ))}
      </div>
    </div>
  );
}
