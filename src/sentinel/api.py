"""Read API for the dashboard plus an authenticated endpoint to trigger runs."""

from __future__ import annotations

import datetime as dt
import hmac
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

import sqlalchemy as sa
import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from . import metrics
from .alerts import Alerter
from .runner import Runner
from .spec import Project, load_project, load_suites
from .store import CheckResult, Store, init_db, make_engine

log = structlog.get_logger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_")

    config: str = "sentinel.yml"
    api_key: str | None = None  # required for POST /api/runs; None disables triggering
    cors_origins: str = "http://localhost:5173"


@dataclass
class AppState:
    project: Project
    store: Store
    runner: Runner
    settings: Settings


# ---------- response models (explicit contracts for the frontend) -------------------


class ResultOut(BaseModel):
    id: int
    run_id: int
    check_id: str
    datasource: str
    table: str
    check_type: str
    severity: str
    status: str
    observed: float | None
    failing_rows: int | None
    total_rows: int | None
    message: str
    details: dict[str, Any]
    duration_ms: float
    executed_at: dt.datetime
    sample_count: int

    @classmethod
    def of(cls, r: CheckResult) -> ResultOut:
        return cls(
            id=r.id,
            run_id=r.run_id,
            check_id=r.check_id,
            datasource=r.datasource,
            table=r.table_name,
            check_type=r.check_type,
            severity=r.severity,
            status=r.status,
            observed=r.observed,
            failing_rows=r.failing_rows,
            total_rows=r.total_rows,
            message=r.message,
            details=r.details or {},
            duration_ms=r.duration_ms,
            executed_at=r.executed_at,
            sample_count=len(r.samples or []),
        )


class RunOut(BaseModel):
    id: int
    started_at: dt.datetime
    finished_at: dt.datetime | None
    trigger: str
    total: int
    passed: int
    failed: int
    errored: int
    duration_ms: float


class TriggerIn(BaseModel):
    table: str | None = None


def _build_state(settings: Settings) -> AppState:
    project = load_project(settings.config)
    engine = make_engine(project.results_url)
    init_db(engine)
    store = Store(engine)
    return AppState(
        project, store, Runner(project, store, Alerter(project.alerts, store)), settings
    )


def get_state(request: Request) -> AppState:
    app = request.app
    if getattr(app.state, "sentinel", None) is None:
        # Built lazily on first request so importing the module never opens connections.
        app.state.sentinel = _build_state(Settings())
    state: AppState = app.state.sentinel
    return state


S = Annotated[AppState, Depends(get_state)]


def create_app(state: AppState | None = None) -> FastAPI:
    app = FastAPI(title="sentinel-dq", version="0.1.0")
    app.state.sentinel = state

    origins = (state.settings if state else Settings()).cors_origins.split(",")
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"]
    )

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=rid)
        t0 = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            route = request.scope.get("route")
            path = getattr(
                route, "path", "unmatched"
            )  # template, not raw path: bounded cardinality
            metrics.HTTP_LATENCY.labels(request.method, path, str(status)).observe(
                time.perf_counter() - t0
            )
            structlog.contextvars.unbind_contextvars("request_id")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz(st: S) -> dict[str, str]:
        try:
            with st.store.engine.connect() as c:
                c.execute(sa.text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(503, "results database unavailable") from exc
        return {"status": "ready"}

    @app.get("/metrics")
    def prom() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/tables")
    def tables(st: S) -> list[dict[str, Any]]:
        return st.store.table_health()

    @app.get("/api/results/latest", response_model=list[ResultOut])
    def latest(st: S, table: str | None = None) -> list[ResultOut]:
        return [ResultOut.of(r) for r in st.store.latest_results() if table in (None, r.table_name)]

    @app.get("/api/results/{result_id}", response_model=ResultOut)
    def result(st: S, result_id: int) -> ResultOut:
        r = st.store.result(result_id)
        if r is None:
            raise HTTPException(404, "result not found")
        return ResultOut.of(r)

    @app.get("/api/results/{result_id}/samples")
    def samples(st: S, result_id: int) -> list[dict[str, Any]]:
        r = st.store.result(result_id)
        if r is None:
            raise HTTPException(404, "result not found")
        return list(r.samples or [])

    @app.get("/api/checks/{check_id}/history")
    def history(st: S, check_id: str, limit: int = Query(60, ge=1, le=500)) -> list[dict[str, Any]]:
        return st.store.check_history(check_id, limit)

    @app.get("/api/heatmap")
    def heatmap(st: S, days: int = Query(14, ge=1, le=90)) -> list[dict[str, Any]]:
        return st.store.heatmap(days)

    @app.get("/api/runs", response_model=list[RunOut])
    def runs(st: S, limit: int = Query(20, ge=1, le=200)) -> list[Any]:
        return st.store.runs(limit)

    @app.get("/api/runs/{run_id}")
    def run_detail(st: S, run_id: int) -> dict[str, Any]:
        found = st.store.run_detail(run_id)
        if found is None:
            raise HTTPException(404, "run not found")
        run, res = found
        return {
            "run": RunOut.model_validate(run, from_attributes=True),
            "results": [ResultOut.of(r) for r in res],
        }

    @app.post("/api/runs", status_code=202)
    async def trigger(
        st: S, body: TriggerIn | None = None, x_api_key: str | None = Header(default=None)
    ) -> dict[str, Any]:
        key = st.settings.api_key
        # Constant-time compare; disabled entirely when no key is configured.
        if not key or not x_api_key or not hmac.compare_digest(key, x_api_key):
            raise HTTPException(401, "invalid or missing API key")
        suites = load_suites(st.project.suites_dir)
        table = body.table if body else None
        summary = await run_in_threadpool(st.runner.run, suites, "api", None, table)
        return {**summary.__dict__, "ok": summary.ok}

    return app


app = create_app()
