from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from sentinel.alerts import Alerter
from sentinel.api import AppState, Settings, create_app
from sentinel.cli import app as cli
from sentinel.demo import simulate
from sentinel.runner import Runner
from sentinel.spec import AlertConfig, Project, load_suites
from sentinel.store import Store, init_db

CHECKS = Path(__file__).parent.parent / "checks"


@pytest.fixture
def env(tmp_path: Path, source: sa.Engine) -> Iterator[dict[str, Any]]:
    results = sa.create_engine(f"sqlite:///{tmp_path / 'res.db'}")
    init_db(results)
    store = Store(results)
    project = Project(
        datasources={"clinic": {"url": str(source.url)}},  # type: ignore[dict-item]
        results_url=str(results.url),
        suites_dir=str(CHECKS),
    )
    sent: list[dict[str, Any]] = []

    def hook(req: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(req.content))
        return httpx.Response(200)

    alerter = Alerter(
        AlertConfig(webhook_url="http://hook.test", cooldown_minutes=60 * 24 * 30),
        store,
        httpx.Client(transport=httpx.MockTransport(hook)),
    )
    runner = Runner(project, store, alerter, engines={"clinic": source})
    yield {"store": store, "runner": runner, "project": project, "sent": sent, "source": source}
    results.dispose()


def _timeline(store: Store) -> dict[str, str]:
    with store.engine.connect() as c:
        rows = c.execute(
            sa.text("SELECT check_id, status FROM check_results ORDER BY run_id")
        ).all()
    out: dict[str, str] = {}
    for cid, status in rows:
        out[cid.split(".", 2)[2]] = out.get(cid.split(".", 2)[2], "") + status[0]
    return out


def test_demo_detects_every_injected_defect(env: dict[str, Any]) -> None:
    """End-to-end: 10 simulated days, each injected defect is caught and nothing else."""
    suites = load_suites(CHECKS)
    runner: Runner = env["runner"]
    simulate(
        env["source"],
        lambda at: runner.run(suites, "demo", now=at),
        days=10,
        end=dt.datetime(2026, 5, 1, tzinfo=dt.UTC),
        history_days=30,
    )
    t = _timeline(env["store"])
    always_fail = [
        "accepted_values.species",
        "referential.owner_id",
        "null_rate.email",
        "range.dose_ml",
        "unique.external_ref",
    ]
    for k in always_fail:
        assert set(t[k]) == {"f"}, k
    for k in [
        "not_null.full_name",
        "unique.id",
        "referential.animal_id",
        "custom_sql.vaccinated_before_registration",
        "accepted_values.vaccine_code",
    ]:
        assert set(t[k]) == {"p"}, k
    assert (
        t["freshness.administered_at"].endswith("pf")
        and "f" not in t["freshness.administered_at"][:-1]
    )
    assert t["row_count_anomaly.daily_registrations"].endswith("ff")
    assert "f" not in t["row_count_anomaly.daily_registrations"][:-2]
    assert "f" in t["drift.cattle_weight"][-3:] and "f" not in t["drift.cattle_weight"][:-3]
    # dedup: 15 checks x 11 runs = 165 results, but alerts fire only on state changes:
    # initial failures, the volume spike, and stale data (drift is warning-level).
    assert len(env["sent"]) == 3
    assert env["sent"][0]["text"].startswith("sentinel-dq: 5 check(s)")


def test_api(env: dict[str, Any]) -> None:
    store: Store = env["store"]
    st = AppState(env["project"], store, env["runner"], Settings(api_key="s3cret"))
    client = TestClient(create_app(st))
    simulate(
        env["source"],
        lambda at: env["runner"].run(load_suites(CHECKS), "demo", now=at),
        days=3,
        history_days=5,
    )

    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 200
    tables = client.get("/api/tables").json()
    assert {t["table"] for t in tables} == {"owners", "animals", "vaccinations"}
    assert all(0 <= t["score"] <= 100 for t in tables)

    latest = client.get("/api/results/latest", params={"table": "owners"}).json()
    assert len(latest) == 3
    failing = next(r for r in latest if r["status"] == "fail")
    samples = client.get(f"/api/results/{failing['id']}/samples").json()
    assert samples and all(s["email"] is None for s in samples)  # null_rate samples
    assert all(s["phone"].endswith("*") for s in samples)
    assert client.get(f"/api/results/{failing['id']}").json()["check_id"] == failing["check_id"]

    hist = client.get(f"/api/checks/{failing['check_id']}/history").json()
    assert len(hist) == 4 and hist[0]["delta"] is None and "pass_rate_7" in hist[-1]
    assert client.get("/api/heatmap", params={"days": 30}).json()
    runs = client.get("/api/runs").json()
    assert len(runs) == 4
    detail = client.get(f"/api/runs/{runs[0]['id']}").json()
    assert len(detail["results"]) == 15

    assert client.get("/api/runs/999").status_code == 404
    assert client.get("/api/results/999").status_code == 404
    assert client.get("/api/results/999/samples").status_code == 404
    assert client.post("/api/runs").status_code == 401
    assert client.post("/api/runs", headers={"x-api-key": "wrong"}).status_code == 401
    r = client.post("/api/runs", headers={"x-api-key": "s3cret"}, json={"table": "owners"})
    assert r.status_code == 202 and r.json()["total"] == 3
    m = client.get("/metrics").text
    assert "sentinel_check_results_total" in m and "sentinel_http_request_duration_seconds" in m
    assert client.get("/healthz").headers["x-request-id"]


def test_check_error_is_isolated(env: dict[str, Any]) -> None:
    from sentinel.spec import Suite

    bad = Suite.model_validate(
        {
            "datasource": "clinic",
            "table": "no_such_table",
            "checks": [{"type": "not_null", "column": "x"}],
        }
    )
    s = env["runner"].run([bad], "cli")
    assert (s.errored, s.ok) == (1, False)


def test_alert_dedup_and_recovery(store: Store) -> None:
    now = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    cd = dt.timedelta(minutes=60)
    assert store.should_alert("c", "pass", now, cd) is False  # healthy from the start: silent
    assert store.should_alert("c2", "fail", now, cd) is True
    assert store.should_alert("c2", "fail", now + dt.timedelta(minutes=10), cd) is False
    assert store.should_alert("c2", "fail", now + dt.timedelta(minutes=61), cd) is True
    assert store.should_alert("c2", "pass", now + dt.timedelta(minutes=62), cd) is True  # recovery
    assert store.should_alert("c2", "pass", now + dt.timedelta(minutes=200), cd) is False


def test_alert_webhook_failure_does_not_raise(store: Store) -> None:
    from sentinel.store import CheckResult

    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    a = Alerter(AlertConfig(webhook_url="http://x", min_severity="warning"), store, client)
    r = CheckResult(check_id="x", severity="warning", status="error", message="boom")
    assert a.process([r], dt.datetime.now(dt.UTC)) == [r]
    assert "x" in Alerter.payload([r])["text"]


def test_baseline_reset(store: Store) -> None:
    store.save_baseline("c", {"edges": [1], "counts": [1, 2]})
    assert store.get_baseline("c") == {"edges": [1], "counts": [1, 2]}
    assert store.reset_baseline("c") is True
    assert store.get_baseline("c") is None


def test_cli(tmp_path: Path, source: sa.Engine) -> None:
    cfg = tmp_path / "sentinel.yml"
    cfg.write_text(
        f"datasources: {{clinic: {{url: '{source.url.render_as_string(hide_password=False)}'}}}}\n"
        f"results_url: 'sqlite:///{tmp_path / 'r.db'}'\nsuites_dir: '{CHECKS}'\n"
    )
    r = CliRunner()
    assert "15 checks" in r.invoke(cli, ["validate", "-c", str(cfg)]).output
    demo = r.invoke(cli, ["demo", "-c", str(cfg), "--days", "3"])
    assert demo.exit_code == 0, demo.output
    res = r.invoke(cli, ["run", "-c", str(cfg), "--json", "--table", "owners"])
    assert res.exit_code == 1 and '"total": 3' in res.output  # owners has a failing check
    ok = r.invoke(cli, ["run", "-c", str(cfg), "--no-fail-on-error"])
    assert ok.exit_code == 0 and "passed" in ok.output
    assert (
        "reset"
        in r.invoke(
            cli, ["reset-baseline", "clinic.animals.drift.cattle_weight", "-c", str(cfg)]
        ).output
    )
