"""Webhook alerting (Slack-compatible payload) with dedup and cooldown."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from typing import Any

import httpx
import structlog

from .spec import AlertConfig
from .store import CheckResult, Store

log = structlog.get_logger(__name__)
_RANK = {"warning": 1, "critical": 2}


class Alerter:
    def __init__(self, cfg: AlertConfig, store: Store, client: httpx.Client | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.client = client or httpx.Client(timeout=10)

    def select(self, results: Sequence[CheckResult], now: dt.datetime) -> list[CheckResult]:
        cooldown = dt.timedelta(minutes=self.cfg.cooldown_minutes)
        floor = _RANK[self.cfg.min_severity]
        return [
            r
            for r in results
            if _RANK.get(r.severity, 0) >= floor
            and self.store.should_alert(r.check_id, r.status, now, cooldown)
        ]

    @staticmethod
    def payload(items: Sequence[CheckResult]) -> dict[str, Any]:
        lines = []
        for r in items:
            icon = {"pass": ":white_check_mark:", "fail": ":red_circle:"}.get(r.status, ":warning:")
            verb = "recovered" if r.status == "pass" else r.status.upper()
            lines.append(f"{icon} *{r.check_id}* {verb}: {r.message}")
        return {"text": f"sentinel-dq: {len(items)} check(s) changed state\n" + "\n".join(lines)}

    def process(self, results: Sequence[CheckResult], now: dt.datetime) -> list[CheckResult]:
        items = self.select(results, now)
        if items and self.cfg.webhook_url:
            try:
                self.client.post(self.cfg.webhook_url, json=self.payload(items)).raise_for_status()
            except httpx.HTTPError as exc:  # alerting must never fail a run
                log.error("alert.failed", error=str(exc))
        return items
