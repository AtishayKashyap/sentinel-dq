"""Blocking cron scheduler for always-on deployments."""

from __future__ import annotations

import structlog
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .alerts import Alerter
from .runner import Runner
from .spec import load_project, load_suites
from .store import Store, init_db, make_engine

log = structlog.get_logger(__name__)


def start(config: str) -> None:
    project = load_project(config)
    if not project.schedule_cron:
        raise SystemExit("schedule_cron is not set in sentinel.yml")
    engine = make_engine(project.results_url)
    init_db(engine)
    store = Store(engine)
    runner = Runner(project, store, Alerter(project.alerts, store))

    def job() -> None:
        runner.run(load_suites(project.suites_dir), trigger="schedule")

    sched = BlockingScheduler(timezone="UTC")
    # max_instances=1 + coalesce: a slow run is never overlapped by the next tick.
    sched.add_job(
        job, CronTrigger.from_crontab(project.schedule_cron), max_instances=1, coalesce=True
    )
    log.info("scheduler.started", cron=project.schedule_cron)
    sched.start()
