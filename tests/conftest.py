from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa

from sentinel.spec import Project
from sentinel.store import Store, init_db

NOW = dt.datetime(2026, 6, 1, 12, 0, tzinfo=dt.UTC)


def _engines() -> list[Any]:
    params: list[Any] = ["sqlite"]
    if os.getenv("SENTINEL_TEST_PG_URL"):
        params.append(pytest.param("postgresql", marks=pytest.mark.postgres))
    if os.getenv("SENTINEL_TEST_MYSQL_URL"):
        params.append(pytest.param("mysql", marks=pytest.mark.mysql))
    return params


@pytest.fixture(params=_engines())
def source(request: pytest.FixtureRequest, tmp_path: Any) -> Iterator[sa.Engine]:
    """A source database. SQLite always; Postgres/MySQL when their URL env vars are set
    (CI provides them as service containers), so dialect differences are tested for real."""
    if request.param == "sqlite":
        eng = sa.create_engine(f"sqlite:///{tmp_path / 'src.db'}")
    elif request.param == "postgresql":
        eng = sa.create_engine(os.environ["SENTINEL_TEST_PG_URL"])
    else:
        eng = sa.create_engine(os.environ["SENTINEL_TEST_MYSQL_URL"])
    md = sa.MetaData()
    md.reflect(eng)
    md.drop_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def store(tmp_path: Any) -> Iterator[Store]:
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'results.db'}")
    init_db(eng)
    yield Store(eng)
    eng.dispose()


@pytest.fixture
def project(tmp_path: Any) -> Project:
    return Project(
        datasources={"src": {"url": "sqlite://"}},  # type: ignore[dict-item]
        results_url="sqlite://",
        suites_dir=str(tmp_path / "checks"),
    )


class FakeHistory:
    def __init__(self, values: list[float] | None = None) -> None:
        self.values = values or []
        self.baselines: dict[str, dict[str, Any]] = {}

    def metric_history(self, check_id: str, limit: int) -> list[float]:
        return self.values[:limit]

    def get_baseline(self, check_id: str) -> dict[str, Any] | None:
        return self.baselines.get(check_id)

    def save_baseline(self, check_id: str, baseline: dict[str, Any]) -> None:
        self.baselines[check_id] = baseline
