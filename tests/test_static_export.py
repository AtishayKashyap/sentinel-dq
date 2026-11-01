from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from sentinel.api import AppState, Settings, create_app
from sentinel.cli import app as cli
from sentinel.demo import simulate
from sentinel.runner import Runner
from sentinel.spec import Project, load_suites
from sentinel.static_export import export_static
from sentinel.store import Store, init_db

CHECKS = Path(__file__).parent.parent / "checks"


def test_static_files_match_live_api(tmp_path: Path) -> None:
    source = sa.create_engine(f"sqlite:///{tmp_path / 'src.db'}")
    results = sa.create_engine(f"sqlite:///{tmp_path / 'res.db'}")
    init_db(results)
    store = Store(results)
    project = Project(
        datasources={"clinic": {"url": str(source.url)}},
        results_url=str(results.url),
        suites_dir=str(CHECKS),
    )  # type: ignore[dict-item]
    runner = Runner(project, store, engines={"clinic": source})
    simulate(
        source, lambda at: runner.run(load_suites(CHECKS), "demo", now=at), days=4, history_days=5
    )

    out = tmp_path / "data"
    stats = export_static(store, out)
    assert stats["checks"] == 15
    client = TestClient(create_app(AppState(project, store, runner, Settings())))

    def same(file: str, url: str) -> None:
        assert json.loads((out / file).read_text()) == client.get(url).json(), file

    same("tables.json", "/api/tables")
    same("latest.json", "/api/results/latest")
    same("heatmap.json", "/api/heatmap?days=14")
    same("runs.json", "/api/runs?limit=50")
    first = json.loads((out / "latest.json").read_text())[0]
    same(f"results/{first['id']}.json", f"/api/results/{first['id']}")
    same(f"samples/{first['id']}.json", f"/api/results/{first['id']}/samples")
    same(f"history/{first['check_id']}.json", f"/api/checks/{first['check_id']}/history?limit=60")
    assert json.loads((out / "meta.json").read_text())["checks"] == 15
    results.dispose()
    source.dispose()


def test_export_static_cli(tmp_path: Path) -> None:
    cfg = tmp_path / "sentinel.yml"
    cfg.write_text(
        f"datasources: {{clinic: {{url: 'sqlite:///{tmp_path / 'src.db'}'}}}}\n"
        f"results_url: 'sqlite:///{tmp_path / 'r.db'}'\nsuites_dir: '{CHECKS}'\n"
    )
    r = CliRunner()
    assert r.invoke(cli, ["demo", "-c", str(cfg), "--days", "3"]).exit_code == 0
    res = r.invoke(cli, ["export-static", "-c", str(cfg), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert "exported 15 checks" in res.output and (tmp_path / "out" / "tables.json").exists()
