export function relativeTime(iso: string, now: Date = new Date()): string {
  const diff = (now.getTime() - new Date(iso).getTime()) / 1000;
  const abs = Math.abs(diff);
  const units: [number, string][] = [
    [86400, "d"],
    [3600, "h"],
    [60, "m"],
  ];
  for (const [secs, label] of units) {
    if (abs >= secs) {
      const v = Math.floor(abs / secs);
      return diff >= 0 ? `${v}${label} ago` : `in ${v}${label}`;
    }
  }
  return "just now";
}

export function formatObserved(value: number | null, checkType: string): string {
  if (value === null) return "—";
  if (checkType === "null_rate") return `${(value * 100).toFixed(2)}%`;
  if (checkType === "freshness") return `${value.toFixed(1)}h`;
  if (checkType === "drift") return `PSI ${value.toFixed(3)}`;
  return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
}

/** Compact axis ticks: units live in the card title, not on every tick. */
export function formatAxis(value: number, checkType: string): string {
  if (checkType === "null_rate") return `${+(value * 100).toFixed(1)}%`;
  if (Math.abs(value) >= 1000) return `${+(value / 1000).toFixed(1)}k`;
  return `${+value.toFixed(2)}`;
}

/** Splits "ds.table.type.target" into readable parts (target may contain dots). */
export function parseCheckId(id: string): { datasource: string; table: string; type: string; target: string } {
  const [datasource = "", table = "", type = "", ...rest] = id.split(".");
  return { datasource, table, type, target: rest.join(".") };
}

export function scoreTone(score: number): "good" | "warn" | "bad" {
  if (score >= 90) return "good";
  if (score >= 70) return "warn";
  return "bad";
}

export const CHECK_LABELS: Record<string, string> = {
  not_null: "Not null",
  null_rate: "Null rate",
  unique: "Unique",
  referential: "Referential integrity",
  accepted_values: "Accepted values",
  range: "Range",
  freshness: "Freshness",
  row_count_anomaly: "Volume anomaly",
  drift: "Distribution drift",
  custom_sql: "Custom SQL",
};
