"""Results warehouse: every run, every check outcome, plus baselines and alert state.

Portable across PostgreSQL / MySQL / SQLite. Analytical reads (trends, heatmaps) use
window functions so the aggregation happens in the database, not in Python.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


JSONType = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[dt.datetime] = mapped_column(sa.DateTime(timezone=True), index=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(sa.DateTime(timezone=True))
    trigger: Mapped[str] = mapped_column(sa.String(20))  # cli | api | schedule
    total: Mapped[int] = mapped_column(default=0)
    passed: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    errored: Mapped[int] = mapped_column(default=0)
    # Measured with a monotonic clock: started_at may be simulated (demo/backfill),
    # so finished_at - started_at is not a reliable duration.
    duration_ms: Mapped[float] = mapped_column(sa.Float, default=0.0)


class CheckResult(Base):
    __tablename__ = "check_results"
    __table_args__ = (
        # Serves "history of one check" and "latest result per check" lookups.
        sa.Index("ix_results_check_time", "check_id", "executed_at"),
        sa.Index("ix_results_table_time", "datasource", "table_name", "executed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(sa.ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    check_id: Mapped[str] = mapped_column(sa.String(255))
    datasource: Mapped[str] = mapped_column(sa.String(100))
    table_name: Mapped[str] = mapped_column(sa.String(100))
    check_type: Mapped[str] = mapped_column(sa.String(40))
    severity: Mapped[str] = mapped_column(sa.String(10))
    status: Mapped[str] = mapped_column(sa.String(10))
    observed: Mapped[float | None] = mapped_column(sa.Float)
    failing_rows: Mapped[int | None]
    total_rows: Mapped[int | None]
    message: Mapped[str] = mapped_column(sa.Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    samples: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, default=list)
    duration_ms: Mapped[float] = mapped_column(sa.Float)
    executed_at: Mapped[dt.datetime] = mapped_column(sa.DateTime(timezone=True))


class Baseline(Base):
    __tablename__ = "baselines"

    check_id: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    data: Mapped[dict[str, Any]] = mapped_column(JSONType)
    created_at: Mapped[dt.datetime] = mapped_column(sa.DateTime(timezone=True))


class AlertState(Base):
    __tablename__ = "alert_state"

    check_id: Mapped[str] = mapped_column(sa.String(255), primary_key=True)
    last_sent_at: Mapped[dt.datetime] = mapped_column(sa.DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(sa.String(10))


def make_engine(url: str) -> sa.Engine:
    return sa.create_engine(url, pool_pre_ping=True, future=True)


def init_db(engine: sa.Engine) -> None:
    Base.metadata.create_all(engine)


def _aware(value: dt.datetime) -> dt.datetime:
    # SQLite drops tzinfo; normalise so arithmetic never mixes naive and aware.
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value


class Store:
    """Thin repository over the results warehouse. Implements the `History` protocol."""

    def __init__(self, engine: sa.Engine) -> None:
        self.engine = engine
        self.session_factory = sessionmaker(engine, expire_on_commit=False)

    # -- History protocol -----------------------------------------------------------
    def metric_history(self, check_id: str, limit: int) -> list[float]:
        stmt = (
            sa.select(CheckResult.observed)
            .where(
                CheckResult.check_id == check_id,
                # Learn only from healthy observations: if a spike that already failed
                # stayed in the window, it would inflate the std-dev and mask the next
                # anomaly (e.g. a drop to zero rows).
                CheckResult.status == "pass",
                CheckResult.observed.is_not(None),
            )
            .order_by(CheckResult.executed_at.desc())
            .limit(limit)
        )
        with self.session_factory() as db:
            return [float(v) for v in db.scalars(stmt) if v is not None]

    def get_baseline(self, check_id: str) -> dict[str, Any] | None:
        with self.session_factory() as db:
            b = db.get(Baseline, check_id)
            return dict(b.data) if b else None

    def save_baseline(self, check_id: str, baseline: dict[str, Any]) -> None:
        with self.session_factory.begin() as db:
            db.merge(Baseline(check_id=check_id, data=baseline, created_at=dt.datetime.now(dt.UTC)))

    def reset_baseline(self, check_id: str) -> bool:
        with self.session_factory.begin() as db:
            res = db.execute(sa.delete(Baseline).where(Baseline.check_id == check_id))
            return bool(getattr(res, "rowcount", 0))

    # -- Alert dedup ----------------------------------------------------------------
    def should_alert(
        self, check_id: str, status: str, now: dt.datetime, cooldown: dt.timedelta
    ) -> bool:
        """Alert on state change immediately; repeat an unchanged failure only after the
        cooldown. Recovery (fail -> pass) is also reported once."""
        with self.session_factory.begin() as db:
            st = db.get(AlertState, check_id)
            if st is None:
                if status == "pass":
                    return False
                db.add(AlertState(check_id=check_id, last_sent_at=now, last_status=status))
                return True
            changed = st.last_status != status
            due = status != "pass" and now - _aware(st.last_sent_at) >= cooldown
            if changed or due:
                st.last_status, st.last_sent_at = status, now
                return True
            return False

    # -- Writes ---------------------------------------------------------------------
    def session(self) -> Session:
        return self.session_factory()

    # -- Analytical reads -----------------------------------------------------------
    def latest_results(self) -> list[CheckResult]:
        """Latest result per check, via ROW_NUMBER() rather than N queries."""
        rn = (
            sa.func.row_number()
            .over(partition_by=CheckResult.check_id, order_by=CheckResult.executed_at.desc())
            .label("rn")
        )
        inner = sa.select(CheckResult.id, rn).subquery()
        stmt = (
            sa.select(CheckResult)
            .join(inner, inner.c.id == CheckResult.id)
            .where(inner.c.rn == 1)
            .order_by(CheckResult.datasource, CheckResult.table_name, CheckResult.check_id)
        )
        with self.session_factory() as db:
            return list(db.scalars(stmt))

    def table_health(self) -> list[dict[str, Any]]:
        """Severity-weighted health score per table from each check's latest result.
        critical checks weigh 3x warnings; errors count as failures."""
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for r in self.latest_results():
            key = (r.datasource, r.table_name)
            t = out.setdefault(
                key,
                {
                    "datasource": r.datasource,
                    "table": r.table_name,
                    "checks": 0,
                    "passing": 0,
                    "failing": 0,
                    "critical_failing": 0,
                    "_w": 0.0,
                    "_wp": 0.0,
                    "last_run_at": r.executed_at,
                },
            )
            w = 3.0 if r.severity == "critical" else 1.0
            t["checks"] += 1
            t["_w"] += w
            if r.status == "pass":
                t["passing"] += 1
                t["_wp"] += w
            else:
                t["failing"] += 1
                t["critical_failing"] += int(r.severity == "critical")
            t["last_run_at"] = max(_aware(t["last_run_at"]), _aware(r.executed_at))
        result = []
        for t in out.values():
            t["score"] = round(100 * t.pop("_wp") / t.pop("_w"), 1)
            result.append(t)
        return sorted(result, key=lambda x: (x["score"], x["table"]))

    def check_history(self, check_id: str, limit: int = 60) -> list[dict[str, Any]]:
        """Time series for one check with a rolling pass-rate and run-over-run delta,
        both computed in SQL with window functions."""
        passed = sa.case((CheckResult.status == "pass", 1.0), else_=0.0)
        order = CheckResult.executed_at
        inner = (
            sa.select(
                CheckResult.id,
                CheckResult.executed_at,
                CheckResult.status,
                CheckResult.observed,
                CheckResult.message,
                (
                    CheckResult.observed - sa.func.lag(CheckResult.observed).over(order_by=order)
                ).label("delta"),
                sa.func.avg(passed).over(order_by=order, rows=(-6, 0)).label("pass_rate_7"),
            )
            .where(CheckResult.check_id == check_id)
            .order_by(order.desc())
            .limit(limit)
            .subquery()
        )
        stmt = sa.select(inner).order_by(inner.c.executed_at)
        with self.session_factory() as db:
            return [dict(r) for r in db.execute(stmt).mappings()]

    def heatmap(self, days: int, now: dt.datetime | None = None) -> list[dict[str, Any]]:
        """Pass rate per table per day: a GROUP BY over a date expression."""
        now = now or dt.datetime.now(dt.UTC)
        since = now - dt.timedelta(days=days)
        day = sa.func.date(CheckResult.executed_at).label("day")
        stmt = (
            sa.select(
                CheckResult.datasource,
                CheckResult.table_name,
                day,
                sa.func.count().label("checks"),
                sa.func.sum(sa.case((CheckResult.status == "pass", 1), else_=0)).label("passed"),
            )
            .where(CheckResult.executed_at >= since)
            .group_by(CheckResult.datasource, CheckResult.table_name, day)
            .order_by(day)
        )
        with self.session_factory() as db:
            return [
                {
                    "datasource": r.datasource,
                    "table": r.table_name,
                    "day": str(r.day),
                    "checks": int(r.checks),
                    "pass_rate": round(int(r.passed) / int(r.checks), 4),
                }
                for r in db.execute(stmt)
            ]

    def runs(self, limit: int = 20) -> list[Run]:
        with self.session_factory() as db:
            return list(db.scalars(sa.select(Run).order_by(Run.started_at.desc()).limit(limit)))

    def run_detail(self, run_id: int) -> tuple[Run, list[CheckResult]] | None:
        with self.session_factory() as db:
            run = db.get(Run, run_id)
            if run is None:
                return None
            res = db.scalars(
                sa.select(CheckResult).where(CheckResult.run_id == run_id).order_by(CheckResult.id)
            )
            return run, list(res)

    def result(self, result_id: int) -> CheckResult | None:
        with self.session_factory() as db:
            return db.get(CheckResult, result_id)
