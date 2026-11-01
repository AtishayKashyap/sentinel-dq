"""Declarative check specifications.

Suites are YAML files; each file targets one table in one datasource and lists checks.
Pydantic's discriminated union gives precise validation errors per check type, so a
typo in a YAML file fails fast at load time rather than at 3am in the scheduler.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _ident(value: str) -> str:
    # Identifiers are quoted by SQLAlchemy anyway; this is defence in depth and gives
    # users a clear error instead of a confusing "column not found".
    if not IDENT.match(value):
        raise ValueError(f"invalid SQL identifier: {value!r}")
    return value


Severity = Literal["critical", "warning"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    severity: Severity = "critical"
    description: str | None = None

    @property
    def target(self) -> str:
        """Human-readable target used to build a stable check id."""
        return ""


class _ColumnCheck(_Base):
    column: str

    @field_validator("column")
    @classmethod
    def _column(cls, v: str) -> str:
        return _ident(v)

    @property
    def target(self) -> str:
        return self.column


class NotNull(_ColumnCheck):
    type: Literal["not_null"]


class NullRate(_ColumnCheck):
    type: Literal["null_rate"]
    max: float = Field(ge=0, le=1)


class Unique(_Base):
    type: Literal["unique"]
    columns: list[str] = Field(min_length=1)

    @field_validator("columns")
    @classmethod
    def _cols(cls, v: list[str]) -> list[str]:
        return [_ident(c) for c in v]

    @property
    def target(self) -> str:
        return "+".join(self.columns)


class Referential(_ColumnCheck):
    type: Literal["referential"]
    ref_table: str
    ref_column: str

    @field_validator("ref_table", "ref_column")
    @classmethod
    def _ref(cls, v: str) -> str:
        return _ident(v)


class AcceptedValues(_ColumnCheck):
    type: Literal["accepted_values"]
    values: list[str | int | float] = Field(min_length=1)


class Range(_ColumnCheck):
    type: Literal["range"]
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds(self) -> Range:
        if self.min is None and self.max is None:
            raise ValueError("range check needs at least one of min/max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min must be <= max")
        return self


class Freshness(_ColumnCheck):
    type: Literal["freshness"]
    max_lag_hours: float = Field(gt=0)


class RowCountAnomaly(_Base):
    type: Literal["row_count_anomaly"]
    z_threshold: float = Field(default=3.0, gt=0)
    window: int = Field(default=14, ge=3, le=365)
    min_history: int = Field(default=5, ge=2)
    # Both conditions must hold to fail: |z| > z_threshold AND the count moved by more
    # than this fraction of the mean. Stable tables have tiny std-devs, so z alone flags
    # a harmless 3% wobble as "5 sigma".
    min_relative_change: float = Field(default=0.2, ge=0)
    # Count only rows inside a trailing window (daily volume). Cumulative counts of an
    # append-only table always trend upward and would look anomalous on every run.
    recent_column: str | None = None
    recent_hours: float = Field(default=24, gt=0)

    @field_validator("recent_column")
    @classmethod
    def _recent(cls, v: str | None) -> str | None:
        return None if v is None else _ident(v)


class Drift(_ColumnCheck):
    type: Literal["drift"]
    bins: int = Field(default=10, ge=2, le=50)
    psi_threshold: float = Field(default=0.2, gt=0)
    # Compare only recent rows (e.g. the last 3 days) against the baseline. Without a
    # window, drift in new data is diluted by years of unchanged history.
    recent_column: str | None = None
    recent_days: int = Field(default=3, ge=1)
    # Equality filters (bound parameters, never raw SQL), e.g. {species: cattle}:
    # drift on a mixed population hides shifts inside one segment.
    filter: dict[str, str | int | float] = Field(default_factory=dict)
    # PSI on tiny samples is mostly noise (expected PSI ~ bins/n); don't judge below this.
    min_rows: int = Field(default=30, ge=1)

    @field_validator("filter")
    @classmethod
    def _filter(cls, v: dict[str, str | int | float]) -> dict[str, str | int | float]:
        for k in v:
            _ident(k)
        return v

    @field_validator("recent_column")
    @classmethod
    def _recent(cls, v: str | None) -> str | None:
        return None if v is None else _ident(v)


class CustomSQL(_Base):
    """Arbitrary SQL that must return a single integer: the number of failing rows."""

    type: Literal["custom_sql"]
    name: str  # required for custom checks: there is no column to derive an id from
    sql: str
    max_failures: int = Field(default=0, ge=0)

    @field_validator("sql")
    @classmethod
    def _read_only(cls, v: str) -> str:
        head = v.lstrip().split(None, 1)[0].lower() if v.strip() else ""
        if head not in {"select", "with"}:
            raise ValueError("custom_sql must be a read-only SELECT/WITH statement")
        if ";" in v.strip().rstrip(";"):
            raise ValueError("custom_sql must be a single statement")
        return v.strip().rstrip(";")


CheckSpec = Annotated[
    NotNull
    | NullRate
    | Unique
    | Referential
    | AcceptedValues
    | Range
    | Freshness
    | RowCountAnomaly
    | Drift
    | CustomSQL,
    Field(discriminator="type"),
]


class Suite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    datasource: str
    table: str
    pii_columns: list[str] = Field(default_factory=list)
    checks: list[CheckSpec] = Field(min_length=1)

    @field_validator("table")
    @classmethod
    def _table(cls, v: str) -> str:
        return _ident(v)

    @model_validator(mode="after")
    def _unique_ids(self) -> Suite:
        ids = [check_id(self, c) for c in self.checks]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate check ids (add a `name`): {sorted(dupes)}")
        return self


def check_id(suite: Suite, check: _Base) -> str:
    """Stable identifier so history survives YAML re-ordering."""
    suffix = check.name or check.target or check.type  # type: ignore[attr-defined]
    return f"{suite.datasource}.{suite.table}.{check.type}.{suffix}"  # type: ignore[attr-defined]


class Datasource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


class AlertConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    webhook_url: str | None = None
    cooldown_minutes: int = Field(default=60, ge=0)
    min_severity: Severity = "critical"


class Project(BaseModel):
    """Top-level `sentinel.yml`."""

    model_config = ConfigDict(extra="forbid")

    datasources: dict[str, Datasource]
    results_url: str
    suites_dir: str = "checks"
    schedule_cron: str | None = None
    alerts: AlertConfig = Field(default_factory=AlertConfig)


def expand_env(text: str) -> str:
    """Expand ${VAR} and ${VAR:-default} so secrets never live in YAML."""

    def repl(m: re.Match[str]) -> str:
        val = os.environ.get(m.group(1))
        if val is None:
            if m.group(2) is None:
                raise KeyError(f"environment variable {m.group(1)} is not set")
            return m.group(2)
        return val

    return ENV_VAR.sub(repl, text)


def load_project(path: str | Path) -> Project:
    p = Path(path)
    return Project.model_validate(yaml.safe_load(expand_env(p.read_text())))


def load_suites(directory: str | Path) -> list[Suite]:
    suites: list[Suite] = []
    for f in sorted(Path(directory).glob("*.y*ml")):
        try:
            suites.append(Suite.model_validate(yaml.safe_load(f.read_text())))
        except Exception as exc:  # re-raise with file context
            raise ValueError(f"{f}: {exc}") from exc
    return suites
