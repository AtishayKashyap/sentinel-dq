"""PII masking for failing-row samples.

Samples are the most useful debugging artefact a DQ tool produces, and also the most
dangerous: they copy production rows into a second database and a web UI. Every sample
value goes through `mask_row` before it is persisted.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

# Column names that are masked even if a suite forgets to list them.
PII_NAME_PATTERN = re.compile(
    r"(e_?mail|phone|mobile|contact|ssn|aadhaar|pan_?no|passport|address|owner_name|"
    r"first_?name|last_?name|full_?name|dob|date_of_birth)",
    re.IGNORECASE,
)


def is_pii(column: str, declared: Iterable[str]) -> bool:
    return column in set(declared) or bool(PII_NAME_PATTERN.search(column))


def mask_value(value: Any) -> Any:
    if value is None:
        return None
    s = str(value)
    if "@" in s:
        local, _, domain = s.partition("@")
        dom, _, tld = domain.rpartition(".")
        return f"{local[:1]}***@{(dom or domain)[:1]}***{'.' + tld if tld and dom else ''}"
    if len(s) <= 2:
        return "**"
    return f"{s[:1]}{'*' * min(len(s) - 1, 6)}"


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID | bytes | bytearray | memoryview):
        return str(value) if isinstance(value, uuid.UUID) else "<binary>"
    return value


def mask_row(row: Mapping[str, Any], declared_pii: Iterable[str]) -> dict[str, Any]:
    declared = list(declared_pii)
    return {k: mask_value(v) if is_pii(k, declared) else to_jsonable(v) for k, v in row.items()}
