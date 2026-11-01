from pathlib import Path

import pytest
from pydantic import ValidationError

from sentinel.masking import mask_value, to_jsonable
from sentinel.spec import Suite, expand_env, load_project, load_suites


def suite(*checks: dict, table: str = "t") -> Suite:
    return Suite.model_validate({"datasource": "d", "table": table, "checks": list(checks)})


def test_discriminated_union_parses() -> None:
    s = suite({"type": "not_null", "column": "a"}, {"type": "range", "column": "b", "min": 0})
    assert [c.type for c in s.checks] == ["not_null", "range"]


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "not_null", "column": "a; DROP TABLE x"},
        {"type": "range", "column": "a"},
        {"type": "range", "column": "a", "min": 5, "max": 1},
        {"type": "null_rate", "column": "a", "max": 2},
        {"type": "nope", "column": "a"},
        {"type": "not_null", "column": "a", "extra": 1},
        {"type": "custom_sql", "name": "x", "sql": "DELETE FROM t"},
        {"type": "custom_sql", "name": "x", "sql": "SELECT 1; DROP TABLE t"},
        {"type": "drift", "column": "a", "filter": {"x y": 1}},
        {"type": "unique", "columns": ["ok", "1bad"]},
        {"type": "referential", "column": "a", "ref_table": "t t", "ref_column": "id"},
    ],
)
def test_invalid_specs_rejected(bad: dict) -> None:
    with pytest.raises(ValidationError):
        suite(bad)


def test_bad_table_and_duplicate_ids() -> None:
    with pytest.raises(ValidationError):
        suite({"type": "not_null", "column": "a"}, table="bad-name")
    with pytest.raises(ValidationError, match="duplicate check ids"):
        suite({"type": "not_null", "column": "a"}, {"type": "not_null", "column": "a"})
    ok = suite(
        {"type": "not_null", "column": "a"}, {"type": "not_null", "column": "a", "name": "again"}
    )
    assert len(ok.checks) == 2


def test_env_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_URL", "sqlite://")
    assert expand_env("${DB_URL} ${MISSING:-x}") == "sqlite:// x"
    with pytest.raises(KeyError):
        expand_env("${DEFINITELY_MISSING}")


def test_load_project_and_suites(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("R_URL", "sqlite://")
    (tmp_path / "sentinel.yml").write_text(
        "datasources: {a: {url: 'sqlite://'}}\nresults_url: ${R_URL}\n"
    )
    assert load_project(tmp_path / "sentinel.yml").results_url == "sqlite://"
    d = tmp_path / "checks"
    d.mkdir()
    (d / "good.yml").write_text("datasource: a\ntable: t\nchecks: [{type: not_null, column: c}]\n")
    assert len(load_suites(d)) == 1
    (d / "zbad.yml").write_text("datasource: a\ntable: t\nchecks: []\n")
    with pytest.raises(ValueError, match=r"zbad\.yml"):
        load_suites(d)


def test_repo_suites_are_valid() -> None:
    assert len(load_suites(Path(__file__).parent.parent / "checks")) == 3


def test_masking() -> None:
    assert mask_value("john@vet.co.in") == "j***@v***.in"
    assert mask_value("+919876543210") == "+******"
    assert mask_value("ab") == "**"
    assert mask_value(None) is None
    import datetime as dt
    import decimal
    import uuid

    assert to_jsonable(decimal.Decimal("1.5")) == 1.5
    assert to_jsonable(dt.date(2026, 1, 2)) == "2026-01-02"
    assert to_jsonable(b"x") == "<binary>"
    u = uuid.uuid4()
    assert to_jsonable(u) == str(u)
