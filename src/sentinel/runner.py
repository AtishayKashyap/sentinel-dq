"""Orchestrates a run: execute every check, isolate failures, persist, alert."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa
import structlog

from . import metrics
from .alerts import Alerter
from .checks import RUNNERS, Ctx, Outcome
from .spec import Project, Suite, check_id
from .store import CheckResult, Run, Store, make_engine

log = structlog.get_logger(__name__)

STATEMENT_TIMEOUT_MS = 30_000


@dataclass
class RunSummary:
    run_id: int
    total: int
    passed: int
    failed: int
    errored: int

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.errored == 0


def _guard(conn: sa.Connection) -> None:
    """Make check connections read-only with a statement timeout where supported, so a
    bad custom_sql can neither write data nor hang the scheduler."""
    name = conn.dialect.name
    if name == "postgresql":
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        conn.exec_driver_sql(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
    elif name == "mysql":
        conn.exec_driver_sql(f"SET SESSION MAX_EXECUTION_TIME = {STATEMENT_TIMEOUT_MS}")


class Runner:
    def __init__(
        self,
        project: Project,
        store: Store,
        alerter: Alerter | None = None,
        engines: dict[str, sa.Engine] | None = None,
    ) -> None:
        self.project = project
        self.store = store
        self.alerter = alerter
        self._engines = engines or {}

    def engine(self, datasource: str) -> sa.Engine:
        if datasource not in self._engines:
            ds = self.project.datasources.get(datasource)
            if ds is None:
                raise KeyError(f"unknown datasource {datasource!r}")
            self._engines[datasource] = make_engine(ds.url)
        return self._engines[datasource]

    def run(
        self,
        suites: Iterable[Suite],
        trigger: str = "cli",
        now: dt.datetime | None = None,
        only_table: str | None = None,
    ) -> RunSummary:
        started = now or dt.datetime.now(dt.UTC)
        t0 = time.perf_counter()
        with self.store.session() as db:
            run = Run(started_at=started, trigger=trigger)
            db.add(run)
            db.commit()
            run_id = run.id

        results: list[CheckResult] = []
        bound = structlog.contextvars.bound_contextvars(run_id=run_id)
        with bound:
            for suite in suites:
                if only_table and suite.table != only_table:
                    continue
                for check in suite.checks:
                    results.append(self._execute(run_id, suite, check, started))

        counts = {k: sum(r.status == k for r in results) for k in ("pass", "fail", "error")}
        with self.store.session() as db:
            db.add_all(results)
            run = db.get_one(Run, run_id)
            run.finished_at = dt.datetime.now(dt.UTC)
            run.total = len(results)
            run.passed, run.failed, run.errored = counts["pass"], counts["fail"], counts["error"]
            run.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            db.commit()

        summary = RunSummary(run_id, len(results), counts["pass"], counts["fail"], counts["error"])
        log.info("run.finished", **summary.__dict__)
        metrics.RUNS.labels(trigger=trigger).inc()
        if self.alerter:
            self.alerter.process(results, started)
        return summary

    def _execute(self, run_id: int, suite: Suite, check: object, now: dt.datetime) -> CheckResult:
        cid = check_id(suite, check)  # type: ignore[arg-type]
        ctype: str = check.type  # type: ignore[attr-defined]
        t0 = time.perf_counter()
        try:
            with self.engine(suite.datasource).connect() as conn, conn.begin():
                _guard(conn)
                outcome = RUNNERS[ctype](Ctx(conn, suite, check, cid, self.store, now))
        except Exception as exc:  # one broken check must never abort the whole run
            log.warning("check.error", check_id=cid, error=str(exc))
            outcome = Outcome("error", None, f"{type(exc).__name__}: {str(exc)[:500]}")
        ms = (time.perf_counter() - t0) * 1000
        metrics.CHECK_LATENCY.labels(check_type=ctype).observe(ms / 1000)
        metrics.CHECK_STATUS.labels(check_type=ctype, status=outcome.status).inc()
        log.info("check.done", check_id=cid, status=outcome.status, ms=round(ms, 1))
        return CheckResult(
            run_id=run_id,
            check_id=cid,
            datasource=suite.datasource,
            table_name=suite.table,
            check_type=ctype,
            severity=check.severity,  # type: ignore[attr-defined]
            status=outcome.status,
            observed=outcome.observed,
            failing_rows=outcome.failing_rows,
            total_rows=outcome.total_rows,
            message=outcome.message,
            details=outcome.details,
            samples=outcome.samples,
            duration_ms=round(ms, 2),
            executed_at=now,
        )
