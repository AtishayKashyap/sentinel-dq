"""Export the dashboard's API responses as static JSON files.

Powers the GitHub Pages demo: the same React dashboard, served with no backend. Every
file has exactly the shape the live API returns (it reuses the API's own response
models), so the frontend needs only a different URL mapping, not different code.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from .api import ResultOut, RunOut
from .store import Store

_JSON: TypeAdapter[Any] = TypeAdapter(Any)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.+-]+$")


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pydantic's JSON serializer is what FastAPI uses for typed responses, so dates
    # serialise exactly like the live API (UTC as "Z").
    path.write_bytes(_JSON.dump_json(data))


def export_static(
    store: Store,
    out: str | Path,
    *,
    history_points: int = 60,
    heatmap_days: int = 14,
    runs: int = 50,
) -> dict[str, int]:
    root = Path(out)
    latest = store.latest_results()
    _write(root / "tables.json", store.table_health())
    _write(root / "latest.json", [ResultOut.of(r).model_dump(mode="json") for r in latest])
    _write(root / "heatmap.json", store.heatmap(heatmap_days))
    _write(
        root / "runs.json",
        [
            RunOut.model_validate(r, from_attributes=True).model_dump(mode="json")
            for r in store.runs(runs)
        ],
    )

    for r in latest:
        if not SAFE_NAME.match(r.check_id):  # check ids become file names
            raise ValueError(f"check id not safe as a file name: {r.check_id!r}")
        _write(
            root / "history" / f"{r.check_id}.json", store.check_history(r.check_id, history_points)
        )
        _write(root / "results" / f"{r.id}.json", ResultOut.of(r).model_dump(mode="json"))
        _write(root / "samples" / f"{r.id}.json", list(r.samples or []))

    _write(
        root / "meta.json",
        {"generated_at": dt.datetime.now(dt.UTC).isoformat(), "checks": len(latest)},
    )
    return {"checks": len(latest), "files": sum(1 for _ in root.rglob("*.json"))}
