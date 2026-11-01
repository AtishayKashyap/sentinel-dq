// Typed client for the sentinel-dq API. Types mirror the FastAPI response models.

export type Status = "pass" | "fail" | "error";
export type Severity = "critical" | "warning";

export interface TableHealth {
  datasource: string;
  table: string;
  checks: number;
  passing: number;
  failing: number;
  critical_failing: number;
  score: number;
  last_run_at: string;
}

export interface CheckResult {
  id: number;
  run_id: number;
  check_id: string;
  datasource: string;
  table: string;
  check_type: string;
  severity: Severity;
  status: Status;
  observed: number | null;
  failing_rows: number | null;
  total_rows: number | null;
  message: string;
  details: Record<string, unknown>;
  duration_ms: number;
  executed_at: string;
  sample_count: number;
}

export interface HistoryPoint {
  id: number;
  executed_at: string;
  status: Status;
  observed: number | null;
  message: string;
  delta: number | null;
  pass_rate_7: number;
}

export interface HeatCell {
  datasource: string;
  table: string;
  day: string;
  checks: number;
  pass_rate: number;
}

export interface Run {
  id: number;
  started_at: string;
  finished_at: string | null;
  trigger: string;
  total: number;
  passed: number;
  failed: number;
  errored: number;
  duration_ms: number;
}

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? "";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: { accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText} for ${path}`);
  return (await res.json()) as T;
}

export const api = {
  tables: () => get<TableHealth[]>("/api/tables"),
  latest: (table?: string) =>
    get<CheckResult[]>(`/api/results/latest${table ? `?table=${encodeURIComponent(table)}` : ""}`),
  result: (id: number) => get<CheckResult>(`/api/results/${id}`),
  samples: (id: number) => get<Record<string, unknown>[]>(`/api/results/${id}/samples`),
  history: (checkId: string, limit = 60) =>
    get<HistoryPoint[]>(`/api/checks/${encodeURIComponent(checkId)}/history?limit=${limit}`),
  heatmap: (days = 14) => get<HeatCell[]>(`/api/heatmap?days=${days}`),
  runs: (limit = 20) => get<Run[]>(`/api/runs?limit=${limit}`),
};
