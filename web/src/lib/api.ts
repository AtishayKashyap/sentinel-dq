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
  const url = path.startsWith("/api/") ? `${BASE}${path}` : path;
  const res = await fetch(url, { headers: { accept: "application/json" } });
  if (!res.ok) throw new ApiError(res.status, `${res.status} ${res.statusText} for ${path}`);
  return (await res.json()) as T;
}

/**
 * Static demo mode (GitHub Pages): the same dashboard, reading JSON files exported by
 * `sentinel export-static` instead of calling the API. Responses have identical shapes.
 */
export const STATIC = import.meta.env.VITE_STATIC === "1";
const DATA = `${import.meta.env.BASE_URL}data`;

/** Maps each API call to its static file (+ the client-side filtering the API would do). */
export const staticApi = {
  tables: () => get<TableHealth[]>(`${DATA}/tables.json`),
  latest: async (table?: string) => {
    const rows = await get<CheckResult[]>(`${DATA}/latest.json`);
    return table ? rows.filter((r) => r.table === table) : rows;
  },
  result: (id: number) => get<CheckResult>(`${DATA}/results/${id}.json`),
  samples: (id: number) => get<Record<string, unknown>[]>(`${DATA}/samples/${id}.json`),
  history: async (checkId: string, limit = 60) =>
    (await get<HistoryPoint[]>(`${DATA}/history/${encodeURIComponent(checkId)}.json`)).slice(-limit),
  // The export holds the default 14-day window; the argument exists for API parity.
  heatmap: () => get<HeatCell[]>(`${DATA}/heatmap.json`),
  runs: async (limit = 20) => (await get<Run[]>(`${DATA}/runs.json`)).slice(0, limit),
  meta: () => get<{ generated_at: string; checks: number }>(`${DATA}/meta.json`),
};

const liveApi = {
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

export const api = STATIC ? staticApi : liveApi;
