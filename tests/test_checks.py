from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import sqlalchemy as sa

from sentinel.checks import RUNNERS, Ctx, Outcome
from sentinel.spec import Suite, check_id

from .conftest import NOW, FakeHistory

md = sa.MetaData()
owners = sa.Table(
    "owners",
    md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("email", sa.String(100)),
)
pets = sa.Table(
    "pets",
    md,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
    sa.Column("owner_id", sa.Integer),
    sa.Column("species", sa.String(20)),
    sa.Column("weight", sa.Float),
    sa.Column("ref", sa.String(20)),
    sa.Column("seen_at", sa.DateTime),
)


@pytest.fixture
def db(source: sa.Engine) -> sa.Engine:
    md.create_all(source)
    t = NOW.replace(tzinfo=None)
    with source.begin() as c:
        c.execute(
            owners.insert(),
            [
                {"id": 1, "email": "a@x.com"},
                {"id": 2, "email": None},
                {"id": 3, "email": "c@x.com"},
                {"id": 4, "email": "d@x.com"},
            ],
        )
        c.execute(
            pets.insert(),
            [
                {
                    "id": 1,
                    "owner_id": 1,
                    "species": "dog",
                    "weight": 20.0,
                    "ref": "A",
                    "seen_at": t - dt.timedelta(hours=5),
                },
                {
                    "id": 2,
                    "owner_id": 2,
                    "species": "cat",
                    "weight": 4.0,
                    "ref": "B",
                    "seen_at": t - dt.timedelta(hours=3),
                },
                {
                    "id": 3,
                    "owner_id": 99,
                    "species": "dgo",
                    "weight": -1.0,
                    "ref": "B",
                    "seen_at": t - dt.timedelta(hours=2),
                },
                {
                    "id": 4,
                    "owner_id": None,
                    "species": None,
                    "weight": None,
                    "ref": None,
                    "seen_at": t - dt.timedelta(hours=1),
                },
            ],
        )
    return source


def run(
    db: sa.Engine,
    table: str,
    check: dict[str, Any],
    history: FakeHistory | None = None,
    pii: list[str] | None = None,
) -> Outcome:
    suite = Suite.model_validate(
        {"datasource": "src", "table": table, "pii_columns": pii or [], "checks": [check]}
    )
    c = suite.checks[0]
    with db.connect() as conn:
        return RUNNERS[c.type](
            Ctx(conn, suite, c, check_id(suite, c), history or FakeHistory(), NOW)
        )


def test_not_null_counts_and_samples(db: sa.Engine) -> None:
    out = run(db, "owners", {"type": "not_null", "column": "email"})
    assert (out.status, out.failing_rows, out.total_rows) == ("fail", 1, 4)
    assert out.samples == [{"id": 2, "email": None}]


def test_null_rate_threshold(db: sa.Engine) -> None:
    assert run(db, "owners", {"type": "null_rate", "column": "email", "max": 0.25}).status == "pass"
    out = run(db, "owners", {"type": "null_rate", "column": "email", "max": 0.1})
    assert out.status == "fail" and out.observed == pytest.approx(0.25)


def test_unique_ignores_nulls_and_reports_dupes(db: sa.Engine) -> None:
    out = run(db, "pets", {"type": "unique", "columns": ["ref"]})
    assert out.status == "fail"
    assert out.details["duplicate_keys"] == 1 and out.failing_rows == 2
    assert out.samples == [{"ref": "B", "n": 2}]
    assert run(db, "pets", {"type": "unique", "columns": ["id"]}).status == "pass"


def test_composite_unique(db: sa.Engine) -> None:
    assert run(db, "pets", {"type": "unique", "columns": ["ref", "species"]}).status == "pass"


def test_referential_anti_join(db: sa.Engine) -> None:
    out = run(
        db,
        "pets",
        {"type": "referential", "column": "owner_id", "ref_table": "owners", "ref_column": "id"},
    )
    assert out.failing_rows == 1  # 99 is orphaned; NULL owner is not an orphan
    assert out.samples[0]["owner_id"] == 99


def test_accepted_values(db: sa.Engine) -> None:
    out = run(
        db, "pets", {"type": "accepted_values", "column": "species", "values": ["dog", "cat"]}
    )
    assert out.failing_rows == 1 and out.samples[0]["species"] == "dgo"


def test_range_reports_observed_bounds(db: sa.Engine) -> None:
    out = run(db, "pets", {"type": "range", "column": "weight", "min": 0, "max": 100})
    assert out.failing_rows == 1
    assert out.details["observed_min"] == -1.0 and out.details["observed_max"] == 20.0
    assert run(db, "pets", {"type": "range", "column": "weight", "max": 50}).status == "pass"


def test_freshness(db: sa.Engine) -> None:
    out = run(db, "pets", {"type": "freshness", "column": "seen_at", "max_lag_hours": 2})
    assert out.status == "pass" and out.observed == pytest.approx(1.0)
    assert (
        run(db, "pets", {"type": "freshness", "column": "seen_at", "max_lag_hours": 0.5}).status
        == "fail"
    )


def test_freshness_empty_table(db: sa.Engine) -> None:
    with db.begin() as c:
        c.execute(pets.delete())
    assert (
        run(db, "pets", {"type": "freshness", "column": "seen_at", "max_lag_hours": 1}).status
        == "fail"
    )


def test_row_count_anomaly(db: sa.Engine) -> None:
    check = {"type": "row_count_anomaly", "min_history": 3}
    warmup = run(db, "pets", check, FakeHistory([4]))
    assert warmup.status == "pass" and "collecting history" in warmup.message
    assert run(db, "pets", check, FakeHistory([4, 5, 4, 3])).status == "pass"
    drop = run(db, "pets", check, FakeHistory([100, 101, 99, 100]))
    assert drop.status == "fail" and drop.details["z"] < -3
    # statistically "significant" but tiny in relative terms -> not an incident
    assert run(db, "pets", check, FakeHistory([4.1, 4.11, 4.09, 4.1])).status == "pass"
    assert run(db, "pets", check, FakeHistory([0, 0, 0])).status == "fail"


def test_row_count_window(db: sa.Engine) -> None:
    check = {"type": "row_count_anomaly", "recent_column": "seen_at", "recent_hours": 2.5}
    assert run(db, "pets", check).observed == 2


def test_drift_baseline_then_shift(db: sa.Engine) -> None:
    h = FakeHistory()
    check = {"type": "drift", "column": "weight", "bins": 4, "min_rows": 1}
    first = run(db, "pets", check, h)
    assert first.message == "baseline established" and len(h.baselines) == 1
    assert run(db, "pets", check, h).observed == 0.0
    with db.begin() as c:
        c.execute(
            pets.insert(),
            [
                {"id": 10 + i, "weight": 19.5, "seen_at": NOW.replace(tzinfo=None)}
                for i in range(30)
            ],
        )
    shifted = run(db, "pets", check, h)
    assert shifted.status == "fail" and shifted.observed > 0.25


def test_drift_segment_and_min_rows(db: sa.Engine) -> None:
    h = FakeHistory()
    check = {"type": "drift", "column": "weight", "filter": {"species": "dog"}, "min_rows": 5}
    run(db, "pets", check, h)
    out = run(db, "pets", check, h)
    assert out.status == "pass" and "not enough" in out.message and out.total_rows == 1


def test_drift_empty_column(db: sa.Engine) -> None:
    with db.begin() as c:
        c.execute(pets.delete())
    assert run(db, "pets", {"type": "drift", "column": "weight"}).status == "fail"


def test_custom_sql(db: sa.Engine) -> None:
    check = {
        "type": "custom_sql",
        "name": "neg",
        "sql": "SELECT COUNT(*) FROM pets WHERE weight < 0;",
    }
    assert run(db, "pets", check).failing_rows == 1
    assert run(db, "pets", {**check, "max_failures": 1}).status == "pass"


def test_pii_columns_masked_in_samples(db: sa.Engine) -> None:
    with db.begin() as c:
        c.execute(
            owners.insert(),
            [
                {"id": 5, "email": "bad-email@clinic.com"},
                {"id": 6, "email": "bad-email@clinic.com"},
            ],
        )
    out = run(db, "owners", {"type": "unique", "columns": ["email"]})
    assert out.samples[0]["email"] == "b***@c***.com"
