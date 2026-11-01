"""Structured JSON logging (structlog) with contextvars for run/request ids."""

from __future__ import annotations

import logging
import os
import sys

import structlog


def configure(json: bool | None = None) -> None:
    json = json if json is not None else os.getenv("SENTINEL_LOG_FORMAT", "json") == "json"
    level = getattr(logging, os.getenv("SENTINEL_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(stream=sys.stderr, level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Resolve sys.stderr at call time (not import time) so redirected/captured
        # streams in tests and process managers are honoured.
        logger_factory=lambda *_: structlog.PrintLogger(sys.stderr),
        cache_logger_on_first_use=False,
    )
