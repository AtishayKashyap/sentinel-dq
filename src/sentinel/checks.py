"""Check compilers.

Each check type compiles to a single aggregated SQL statement built with the SQLAlchemy
Core expression language. That gives us:

* one round trip per check, with the database doing the heavy lifting (never
  `SELECT *` into Python),
* bound parameters and dialect-correct identifier quoting, so YAML values cannot inject
  SQL,
* portability across PostgreSQL, MySQL and SQLite with no per-dialect string templates.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Literal, Protocol

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from . import spec as s
from .masking import mask_row
from .stats import edges_from_tiles, psi, zscore

Status = Literal["pass", "fail", "error"]


@dataclass
class Outcome:
    status: Status
    observed: float | None
    message: str
    failing_rows: int | None = None
    total_rows: int | None = None
    details: dict[str, Any] = field(default_factory=dict)
    samples: list[dict[str, Any]] = field(default_factory=list)


class History(Protocol):
    """What a check needs from the results store. A protocol keeps checks testable
    without a database for history."""

    def metric_history(self, check_id: str, limit: int) -> list[float]: ...
    def get_baseline(self, check_id: str) -> dict[str, Any] | None: ...
    def save_baseline(self, check_id: str, baseline: dict[str, Any]) -> None: ...


@dataclass
class Ctx:
    conn: Connection
    suite: s.Suite
    check: Any
    check_id: str
    history: History
    now: dt.datetime
    sample_limit: int = 20

    @cached_property
    def t(self) -> sa.TableClause:
        # One TableClause per check, so every column() binds to the same FROM entry.
        return sa.table(self.suite.table)

    def col(self, name: str) -> sa.ColumnClause[Any]:
        return sa.column(name, _selectable=self.t)

    def samples(self, where: sa.ColumnElement[bool]) -> list[dict[str, Any]]:
        stmt = sa.select(sa.text("*")).select_from(self.t).where(where).limit(self.sample_limit)
        rows = self.conn.execute(stmt).mappings().all()
        return [mask_row(dict(r), self.suite.pii_columns) for r in rows]


def _int(v: Any) -> int:
    return int(v or 0)


def _count_failing(
    ctx: Ctx, predicate: sa.ColumnElement[bool], as_filter: bool = False
) -> tuple[int, int]:
    """Total and failing row counts in one statement.

    Default: one scan with SUM(CASE ...), cheapest for simple column predicates.
    `as_filter=True`: two scalar subqueries with the predicate in WHERE. Needed for
    subquery predicates (NOT EXISTS): inside a CASE the planner must run a correlated
    subplan per row, while in WHERE it can use a (parallel) hash anti-join.
    Measured on 1M rows / PostgreSQL 16: 1,991 ms -> 218 ms (see docs/adr/0003).
    """
    if as_filter:
        total = sa.select(sa.func.count()).select_from(ctx.t).scalar_subquery()
        failing = sa.select(sa.func.count()).select_from(ctx.t).where(predicate).scalar_subquery()
        row = ctx.conn.execute(sa.select(total.label("total"), failing.label("failing"))).one()
    else:
        stmt = sa.select(
            sa.func.count().label("total"),
            sa.func.sum(sa.case((predicate, 1), else_=0)).label("failing"),
        ).select_from(ctx.t)
        row = ctx.conn.execute(stmt).one()
    return _int(row.total), _int(row.failing)


def _row_predicate_check(
    ctx: Ctx, predicate: sa.ColumnElement[bool], label: str, as_filter: bool = False
) -> Outcome:
    total, failing = _count_failing(ctx, predicate, as_filter)
    ok = failing == 0
    return Outcome(
        status="pass" if ok else "fail",
        observed=float(failing),
        failing_rows=failing,
        total_rows=total,
        message=f"{failing} of {total} rows {label}",
        samples=[] if ok else ctx.samples(predicate),
    )


# --- individual check types -------------------------------------------------------


def run_not_null(ctx: Ctx) -> Outcome:
    c: s.NotNull = ctx.check
    return _row_predicate_check(ctx, ctx.col(c.column).is_(None), f"have NULL {c.column}")


def run_null_rate(ctx: Ctx) -> Outcome:
    c: s.NullRate = ctx.check
    pred = ctx.col(c.column).is_(None)
    total, failing = _count_failing(ctx, pred)
    rate = failing / total if total else 0.0
    ok = rate <= c.max
    return Outcome(
        status="pass" if ok else "fail",
        observed=rate,
        failing_rows=failing,
        total_rows=total,
        message=f"null rate {rate:.2%} (max {c.max:.2%})",
        details={"threshold": c.max},
        samples=[] if ok else ctx.samples(pred),
    )


def run_unique(ctx: Ctx) -> Outcome:
    c: s.Unique = ctx.check
    cols = [ctx.col(n) for n in c.columns]
    not_null = sa.and_(*[col.is_not(None) for col in cols])  # NULLs never collide (SQL std)
    dupes = (
        sa.select(*cols, sa.func.count().label("n"))
        .select_from(ctx.t)
        .where(not_null)
        .group_by(*cols)
        .having(sa.func.count() > 1)
        .subquery("dupes")
    )
    total_sq = sa.select(sa.func.count()).select_from(ctx.t).scalar_subquery()
    stmt = sa.select(
        total_sq.label("total"),
        sa.func.count().label("dup_keys"),
        sa.func.coalesce(sa.func.sum(dupes.c.n), 0).label("dup_rows"),
    ).select_from(dupes)
    row = ctx.conn.execute(stmt).one()
    dup_keys, dup_rows = _int(row.dup_keys), _int(row.dup_rows)
    samples: list[dict[str, Any]] = []
    if dup_keys:
        sample_stmt = sa.select(dupes).order_by(dupes.c.n.desc()).limit(ctx.sample_limit)
        samples = [
            mask_row(dict(r), ctx.suite.pii_columns)
            for r in ctx.conn.execute(sample_stmt).mappings().all()
        ]
    return Outcome(
        status="pass" if dup_keys == 0 else "fail",
        observed=float(dup_rows),
        failing_rows=dup_rows,
        total_rows=_int(row.total),
        message=f"{dup_keys} duplicated key(s) across {dup_rows} rows",
        details={"duplicate_keys": dup_keys},
        samples=samples,
    )


def run_referential(ctx: Ctx) -> Outcome:
    c: s.Referential = ctx.check
    ref = sa.table(c.ref_table).alias("ref")
    ref_col: sa.ColumnClause[Any] = sa.column(c.ref_column, _selectable=ref)
    child = ctx.col(c.column)
    # NOT EXISTS anti-join: planners turn this into a hash anti-join, and unlike
    # NOT IN it behaves correctly when the reference column contains NULLs.
    orphan = sa.and_(
        child.is_not(None),
        ~sa.exists(sa.select(sa.literal(1)).select_from(ref).where(ref_col == child)),
    )
    return _row_predicate_check(
        ctx, orphan, f"reference a missing {c.ref_table}.{c.ref_column}", as_filter=True
    )


def run_accepted_values(ctx: Ctx) -> Outcome:
    c: s.AcceptedValues = ctx.check
    col = ctx.col(c.column)
    pred = sa.and_(col.is_not(None), col.not_in(list(c.values)))
    out = _row_predicate_check(ctx, pred, f"have {c.column} outside the accepted set")
    out.details["accepted"] = list(c.values)
    return out


def run_range(ctx: Ctx) -> Outcome:
    c: s.Range = ctx.check
    col = ctx.col(c.column)
    parts = []
    if c.min is not None:
        parts.append(col < c.min)
    if c.max is not None:
        parts.append(col > c.max)
    pred = sa.and_(col.is_not(None), sa.or_(*parts))
    stats = ctx.conn.execute(
        sa.select(sa.func.min(col).label("lo"), sa.func.max(col).label("hi")).select_from(ctx.t)
    ).one()
    out = _row_predicate_check(ctx, pred, f"have {c.column} outside [{c.min}, {c.max}]")
    out.details.update(
        min=c.min,
        max=c.max,
        observed_min=float(stats.lo) if stats.lo is not None else None,
        observed_max=float(stats.hi) if stats.hi is not None else None,
    )
    return out


def _as_utc(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, str):  # SQLite returns ISO strings
        value = dt.datetime.fromisoformat(value)
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        value = dt.datetime.combine(value, dt.time())
    assert isinstance(value, dt.datetime)
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def run_freshness(ctx: Ctx) -> Outcome:
    c: s.Freshness = ctx.check
    latest = _as_utc(
        ctx.conn.execute(sa.select(sa.func.max(ctx.col(c.column))).select_from(ctx.t)).scalar()
    )
    if latest is None:
        return Outcome("fail", None, f"{c.column} has no values: table is empty or all NULL")
    lag_h = (ctx.now - latest).total_seconds() / 3600
    ok = lag_h <= c.max_lag_hours
    return Outcome(
        status="pass" if ok else "fail",
        observed=round(lag_h, 3),
        message=f"latest {c.column} is {lag_h:.1f}h old (max {c.max_lag_hours}h)",
        details={"latest": latest.isoformat(), "max_lag_hours": c.max_lag_hours},
    )


def run_row_count_anomaly(ctx: Ctx) -> Outcome:
    c: s.RowCountAnomaly = ctx.check
    stmt = sa.select(sa.func.count()).select_from(ctx.t)
    if c.recent_column:
        hi = ctx.now.replace(tzinfo=None)
        lo = hi - dt.timedelta(hours=c.recent_hours)
        rc = ctx.col(c.recent_column)
        stmt = stmt.where(rc >= lo, rc < hi)
    count = _int(ctx.conn.execute(stmt).scalar())
    hist = ctx.history.metric_history(ctx.check_id, c.window)
    details: dict[str, Any] = {"history_points": len(hist), "z_threshold": c.z_threshold}
    if len(hist) < c.min_history:
        return Outcome(
            "pass",
            float(count),
            f"{count} rows; collecting history ({len(hist)}/{c.min_history})",
            total_rows=count,
            details=details,
        )
    z = zscore(count, hist)
    assert z is not None
    mean = sum(hist) / len(hist)
    rel = (count - mean) / mean if mean else (math.inf if count else 0.0)
    ok = abs(z) <= c.z_threshold or abs(rel) <= c.min_relative_change
    details.update(
        z=None if math.isinf(z) else round(z, 3),
        mean=round(mean, 2),
        relative_change=None if math.isinf(rel) else round(rel, 4),
    )
    return Outcome(
        status="pass" if ok else "fail",
        observed=float(count),
        total_rows=count,
        message=f"{count} rows ({rel:+.0%} vs mean {mean:.0f}), z={z:.2f} over {len(hist)} runs"
        if not math.isinf(rel)
        else f"{count} rows vs mean 0",
        details=details,
    )


def _bin_counts(
    ctx: Ctx, column: str, edges: list[float], where: list[sa.ColumnElement[bool]]
) -> list[int]:
    col = ctx.col(column)
    conds: list[sa.ColumnElement[bool]] = [col < edges[0]]
    conds += [sa.and_(col >= lo, col < hi) for lo, hi in itertools.pairwise(edges)]
    conds.append(col >= edges[-1])
    stmt = sa.select(
        *[sa.func.sum(sa.case((cond, 1), else_=0)).label(f"b{i}") for i, cond in enumerate(conds)]
    ).select_from(ctx.t)
    if where:
        stmt = stmt.where(*where)
    return [_int(v) for v in ctx.conn.execute(stmt).one()]


def _quantile_edges(
    ctx: Ctx, column: str, bins: int, where: list[sa.ColumnElement[bool]]
) -> list[float] | None:
    """Equal-frequency bin edges via NTILE(), portable across PG / MySQL 8 / SQLite.

    Equal-width bins put ~1-2% of the mass in the tails of a bell curve, so a modest
    sample often has an *empty* tail bin and PSI explodes on pure noise. Quantile bins
    give every bin ~1/k of the baseline, which is the standard PSI construction."""
    col = ctx.col(column)
    tiled = (
        sa.select(col.label("v"), sa.func.ntile(bins).over(order_by=col).label("tile"))
        .select_from(ctx.t)
        .where(col.is_not(None), *where)
        .subquery()
    )
    rows = ctx.conn.execute(
        sa.select(tiled.c.tile, sa.func.min(tiled.c.v), sa.func.max(tiled.c.v))
        .group_by(tiled.c.tile)
        .order_by(tiled.c.tile)
    ).all()
    if not rows:
        return None
    return edges_from_tiles([(float(lo), float(hi)) for _, lo, hi in rows])


def run_drift(ctx: Ctx) -> Outcome:
    c: s.Drift = ctx.check
    ctx.col(c.column)
    segment = [ctx.col(k) == v for k, v in c.filter.items()]
    window = list(segment)
    if c.recent_column:
        cutoff = (ctx.now - dt.timedelta(days=c.recent_days)).replace(tzinfo=None)
        window.append(ctx.col(c.recent_column) >= cutoff)

    baseline = ctx.history.get_baseline(ctx.check_id)
    if baseline is None:
        edges = _quantile_edges(ctx, c.column, c.bins, segment)
        if edges is None:
            return Outcome("fail", None, f"cannot build baseline: {c.column} is empty")
        counts = _bin_counts(ctx, c.column, edges, segment)
        ctx.history.save_baseline(ctx.check_id, {"edges": edges, "counts": counts})
        return Outcome(
            "pass",
            0.0,
            "baseline established",
            details={"edges": edges, "baseline": counts, "current": counts},
        )

    edges, base_counts = baseline["edges"], baseline["counts"]
    current = _bin_counts(ctx, c.column, edges, window)
    n = sum(current)
    if n < c.min_rows:
        return Outcome(
            "pass",
            None,
            f"only {n} rows in window (< {c.min_rows}); not enough to judge drift",
            total_rows=n,
            details={"edges": edges, "baseline": base_counts, "current": current},
        )
    value = psi(base_counts, current)
    ok = value <= c.psi_threshold
    return Outcome(
        status="pass" if ok else "fail",
        observed=round(value, 4),
        message=f"PSI {value:.3f} (threshold {c.psi_threshold})",
        total_rows=sum(current),
        details={"edges": edges, "baseline": base_counts, "current": current},
    )


def run_custom_sql(ctx: Ctx) -> Outcome:
    c: s.CustomSQL = ctx.check
    failing = _int(ctx.conn.execute(sa.text(c.sql)).scalar())
    ok = failing <= c.max_failures
    return Outcome(
        status="pass" if ok else "fail",
        observed=float(failing),
        failing_rows=failing,
        message=f"{failing} failing rows (max {c.max_failures})",
    )


RUNNERS: dict[str, Callable[[Ctx], Outcome]] = {
    "not_null": run_not_null,
    "null_rate": run_null_rate,
    "unique": run_unique,
    "referential": run_referential,
    "accepted_values": run_accepted_values,
    "range": run_range,
    "freshness": run_freshness,
    "row_count_anomaly": run_row_count_anomaly,
    "drift": run_drift,
    "custom_sql": run_custom_sql,
}
