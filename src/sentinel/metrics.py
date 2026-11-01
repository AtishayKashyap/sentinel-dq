"""Prometheus metrics exposed at /metrics."""

from prometheus_client import Counter, Histogram

RUNS = Counter("sentinel_runs_total", "Completed check runs", ["trigger"])
CHECK_STATUS = Counter(
    "sentinel_check_results_total", "Check outcomes by type and status", ["check_type", "status"]
)
CHECK_LATENCY = Histogram(
    "sentinel_check_duration_seconds",
    "Check execution latency",
    ["check_type"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
HTTP_LATENCY = Histogram(
    "sentinel_http_request_duration_seconds", "API latency", ["method", "route", "status"]
)
