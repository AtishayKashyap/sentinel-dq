"""`sentinel` command-line interface."""

from __future__ import annotations

import json
import os
from typing import Annotated

import typer

from .alerts import Alerter
from .logging import configure
from .runner import Runner
from .spec import load_project, load_suites
from .store import Store, init_db, make_engine

app = typer.Typer(help="Declarative SQL data-quality monitoring", no_args_is_help=True)
ConfigOpt = Annotated[str, typer.Option("--config", "-c", envvar="SENTINEL_CONFIG")]


def _store(cfg: str) -> tuple[Store, object]:
    project = load_project(cfg)
    engine = make_engine(project.results_url)
    init_db(engine)
    return Store(engine), project


@app.command()
def validate(config: ConfigOpt = "sentinel.yml") -> None:
    """Validate sentinel.yml and every suite without touching any database."""
    project = load_project(config)
    suites = load_suites(project.suites_dir)
    n = sum(len(s.checks) for s in suites)
    for s in suites:
        if s.datasource not in project.datasources:
            raise typer.BadParameter(f"suite {s.table} uses unknown datasource {s.datasource!r}")
    typer.echo(f"OK: {len(suites)} suites, {n} checks")


@app.command()
def run(
    config: ConfigOpt = "sentinel.yml",
    table: Annotated[str | None, typer.Option(help="Only run suites for this table")] = None,
    fail_on_error: Annotated[bool, typer.Option(help="Exit 1 if any check fails (for CI)")] = True,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Execute all suites once and store the results."""
    configure(json=os.getenv("SENTINEL_LOG_FORMAT", "console") == "json")
    project = load_project(config)
    engine = make_engine(project.results_url)
    init_db(engine)
    store = Store(engine)
    runner = Runner(project, store, Alerter(project.alerts, store))
    summary = runner.run(load_suites(project.suites_dir), trigger="cli", only_table=table)
    if as_json:
        typer.echo(json.dumps({**summary.__dict__, "ok": summary.ok}))
    else:
        typer.echo(
            f"run #{summary.run_id}: {summary.passed} passed, {summary.failed} failed, "
            f"{summary.errored} errored"
        )
    if fail_on_error and not summary.ok:
        raise typer.Exit(1)


@app.command("reset-baseline")
def reset_baseline(check_id: str, config: ConfigOpt = "sentinel.yml") -> None:
    """Forget a drift baseline so the next run re-learns it (after an intended change)."""
    project = load_project(config)
    store = Store(make_engine(project.results_url))
    typer.echo("reset" if store.reset_baseline(check_id) else "no baseline found")


@app.command()
def demo(
    config: ConfigOpt = "sentinel.yml",
    days: Annotated[int, typer.Option(min=3, max=60)] = 14,
    datasource: str = "clinic",
) -> None:
    """Rebuild the demo clinic DB with injected defects and simulate N days of runs."""
    import datetime as dt

    from .demo import simulate

    configure(json=False)
    project = load_project(config)
    engine = make_engine(project.results_url)
    init_db(engine)
    store = Store(engine)
    runner = Runner(project, store)  # no alerts during simulation
    suites = load_suites(project.suites_dir)

    def once(at: dt.datetime) -> None:
        s = runner.run(suites, trigger="demo", now=at)
        typer.echo(f"{at:%Y-%m-%d %H:%M} run #{s.run_id}: {s.passed} pass / {s.failed} fail")

    simulate(runner.engine(datasource), once, days=days)


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:  # noqa: S104
    """Run the API server."""
    import uvicorn

    configure()
    uvicorn.run("sentinel.api:app", host=host, port=port, proxy_headers=True)


@app.command()
def schedule(config: ConfigOpt = "sentinel.yml") -> None:
    """Run suites on the cron in sentinel.yml (for always-on hosts; on free tiers use the
    GitHub Actions workflow instead)."""
    from .scheduler import start

    configure()
    start(config)


if __name__ == "__main__":  # pragma: no cover
    app()
