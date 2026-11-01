# sentinel-dq: interview walkthrough

Read this until you can explain every section without notes. Interviewers probe **why**, not what.

## 30-second pitch
> "I built a data-quality monitoring engine. You describe rules in YAML (no nulls here, every foreign key resolves, this distribution shouldn't drift), and it compiles each rule into a single aggregated SQL query, runs it against Postgres, MySQL or SQLite, stores results, alerts on state changes and shows trends in a React dashboard. Two things I'm proud of: I found and fixed a 9× slow query using EXPLAIN ANALYZE, and I eliminated every false positive in a simulated two-week dataset by fixing the statistics: quantile bins for drift, and not learning from failed observations."

## Resume bullets (numbers are measured; see the ADRs)
- Built **sentinel-dq**, a declarative SQL data-quality engine (Python, FastAPI, SQLAlchemy, React/TypeScript) with 10 check types, including referential-integrity anti-joins, z-score volume anomalies and PSI distribution drift, running unchanged on **PostgreSQL, MySQL and SQLite**.
- Cut referential-check latency **9.1× (1,991 → 218 ms on 1M rows)** by restructuring a correlated subquery into a hash anti-join after profiling with `EXPLAIN ANALYZE`. The full 15-check suite over 1.7M rows dropped from 4.2 s to 1.8 s.
- Eliminated false-positive alerts by replacing equal-width PSI bins with `NTILE` quantile bins and excluding failed runs from anomaly baselines. An end-to-end test verifies that all 8 injected defects are detected with **zero false positives** across 165 check executions.
- Shipped with **89 tests at 98% coverage** across three database engines, property-based tests (Hypothesis), mypy `--strict`, structured JSON logging, Prometheus metrics, PII masking, and a CI pipeline with Trivy image scanning. It is deployable entirely on free tiers.

## Walk through a request
**`sentinel run`:**
1. `load_project` reads `sentinel.yml` and expands `${ENV}` vars, so secrets never live in YAML.
2. `load_suites` validates each YAML file through a Pydantic **discriminated union** on `type`, so each check type gets precise validation.
3. `Runner.run` creates a `Run` row, then for each check:
   - opens a connection in a transaction marked **read-only** with a **statement timeout** (`_guard`),
   - calls `RUNNERS[type](Ctx)`, which builds one SQL statement and returns an `Outcome`,
   - catches **any** exception and converts it into an `error` result, so one broken check never kills the run.
4. Results are saved in bulk, and Prometheus counters and histograms are updated.
5. `Alerter` asks `Store.should_alert` per check. It alerts on a state change, repeats an unchanged failure only after the cooldown, and reports recovery once.

## Questions you will get, and good answers

**Why not load the data into pandas?**
Memory and network. A 50M-row table can't be shipped to Python every hour. Aggregating in the database moves one row back per check, and the database's optimizer, indexes and parallel workers do the work.

**How do you prevent SQL injection when the rules come from YAML?**
Values are always bind parameters (SQLAlchemy Core). Identifiers can't be parameters in SQL, so they're validated against a strict regex at load time and quoted by the dialect when rendered. `custom_sql` is intentionally raw, but it's restricted to a single `SELECT`, and the connection is read-only with a timeout. That is defence in depth: even if validation were bypassed, the transaction can't write.

**Why `NOT EXISTS` and not `NOT IN` or `LEFT JOIN ... IS NULL`?**
`NOT IN` silently returns nothing if the subquery contains a NULL, because of three-valued logic. `LEFT JOIN / IS NULL` and `NOT EXISTS` usually get the same anti-join plan in Postgres, but `NOT EXISTS` states the intent and never duplicates rows. The bigger lesson was *where* the predicate sits: inside a `CASE` the planner can't convert it to a join at all (ADR 0003).

**Explain PSI.**
PSI = Σ (actualᵢ − expectedᵢ) · ln(actualᵢ / expectedᵢ) over bins. It's symmetric, zero when the distributions match, and above 0.2 by convention means a significant shift. I floor empty bins at 1e-4 so it stays finite. Quantile bins matter because they make every expected bin about 1/k, so a reasonably sized sample almost never has an empty bin by chance.

**Why exclude failed runs from the history?**
Otherwise an incident becomes the new normal. A 6× spike inflates the standard deviation, and the next real anomaly (a drop to zero) gets z ≈ −1 and passes. I found this in the simulation, not in theory.

**How does the dashboard get "latest result per check" efficiently?**
With `ROW_NUMBER() OVER (PARTITION BY check_id ORDER BY executed_at DESC)` and a filter on `rn = 1`, which is one query instead of N. It's backed by a composite index on `(check_id, executed_at)`. The history endpoint uses `LAG()` for run-over-run deltas and `AVG(...) OVER (ROWS 6 PRECEDING)` for a rolling pass rate.

**How would you scale it to 1,000 tables?**
- Run checks concurrently with a bounded thread pool per datasource (connection-pool sized).
- Group checks on the same table into one scan, since many are `SUM(CASE ...)` aggregates that can share a `SELECT`.
- Sample huge tables with `TABLESAMPLE` for drift.
- Partition `check_results` by month.

**What would you change for production?**
- Alembic migrations instead of `create_all`.
- Per-user auth (OIDC) on the dashboard.
- Secrets in a vault.
- An outbox for alerts, so a crash between saving and sending doesn't drop a page.

**Why GitHub Actions for scheduling?**
The constraint was zero cost, and free web hosts sleep. Keeping the API stateless and scheduling externally is also the cleaner design. Kubernetes CronJobs or Airflow follow the same pattern (ADR 0004).

**What was the hardest bug?**
Pick one of the three statistical false positives in ADR 0002, or the FastAPI one: with `from __future__ import annotations`, a dependency alias defined inside a function couldn't be resolved, so FastAPI silently treated it as a query parameter and returned 422.

## Things to be able to do live
- Add a new check type end to end (see CONTRIBUTING.md).
- Run `EXPLAIN ANALYZE` on the referential query and read the plan aloud.
- Explain every line of `run_unique` (NULL semantics, the `HAVING` clause, why it's a subquery).
