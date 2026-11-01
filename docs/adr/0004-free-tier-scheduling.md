# ADR 0004: Schedule from GitHub Actions, not inside the API

**Status:** accepted

## Context
The project must be deployable at zero cost. Free web services (Render, for example) sleep after about 15 minutes without traffic, so an in-process scheduler would silently stop firing.

## Decision
- The API is stateless: it reads results and accepts authenticated `POST /api/runs`.
- Scheduled runs happen in `.github/workflows/scheduled-checks.yml` on a cron, which runs `sentinel run` against the hosted databases. Its minute is offset (`:17`) to avoid the top-of-hour congestion on shared runners.
- `sentinel schedule` (APScheduler, `max_instances=1`, `coalesce=True`) remains available for always-on hosts.

## Consequences
Free scheduled runs, with full logs in the Actions UI. Scheduling is accurate to within a few minutes, which is fine for data-quality checks.
