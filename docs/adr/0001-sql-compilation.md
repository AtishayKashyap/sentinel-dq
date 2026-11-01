# ADR 0001: Compile checks to SQLAlchemy Core, one aggregated query per check

**Status:** accepted

## Context
A data-quality tool must run against tables far larger than memory, on several SQL dialects, using rule values (column names, accepted values, thresholds) that come from user-edited YAML.

## Options
1. Pull rows into pandas and check them in Python. This is simple, but O(table) network transfer and memory, and unusable past a few million rows.
2. Jinja-templated SQL strings (dbt-style). This is flexible, but every dialect quirk becomes a template branch, and values must be escaped by hand, which is an injection risk.
3. **The SQLAlchemy Core expression language.** Statements are composed as objects and rendered per dialect, with bound parameters.

## Decision
Option 3. Each check type is a small function that builds one aggregated `SELECT` (counts, sums of `CASE`, `MIN`/`MAX`, `NTILE`) and returns an `Outcome`.

## Consequences
- The same code runs on PostgreSQL, MySQL and SQLite, and CI proves this by running every check test on all three.
- Values from YAML are always bind parameters. Identifiers are validated against `^[A-Za-z_][A-Za-z0-9_]*$` at load time *and* quoted by the dialect.
- `custom_sql` is the deliberate escape hatch. It is restricted to a single `SELECT`/`WITH` statement and runs inside a read-only transaction with a statement timeout.
