# Contributing

1. Branch from `main`: `feat/<topic>` or `fix/<topic>`.
2. Use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `perf:`, `docs:`, `test:`, `refactor:`, `ci:`).
3. Before pushing, `make lint typecheck test` must pass.
4. Open a PR using the template. CI must be green and one approving review is required.

### Adding a check type
1. Add a Pydantic model to `spec.py` and include it in the `CheckSpec` union.
2. Write `run_<type>(ctx) -> Outcome` in `checks.py` and register it in `RUNNERS`.
3. Add tests to `tests/test_checks.py`. They run automatically on every configured dialect.
4. If it's useful in the demo, inject a matching defect in `demo.py` and extend the end-to-end assertion.

### Recommended branch protection on `main`
Require a PR, one approval, passing `backend`, `frontend` and `docker` checks, and linear history.
