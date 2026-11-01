# ADR 0003: Referential checks as a WHERE-clause anti-join

**Status:** accepted

## Problem
A benchmark of 15 checks over 1.7M rows (PostgreSQL 16) showed `referential` taking 1.9 s, which was half the run.

The first version computed everything in one scan:

```sql
SELECT count(*),
       sum(CASE WHEN fk IS NOT NULL AND NOT EXISTS (SELECT 1 FROM parent p WHERE p.id = fk)
                THEN 1 ELSE 0 END)
FROM child;
```

`EXPLAIN ANALYZE` showed the cost. Because `NOT EXISTS` sits inside a `CASE`, the planner cannot transform it into a join. It runs a **correlated SubPlan once per row**: 1,000,000 index probes taking 1,991 ms.

## Decision
For subquery predicates, put the predicate in `WHERE` and return two scalar subqueries:

```sql
SELECT (SELECT count(*) FROM child) AS total,
       (SELECT count(*) FROM child WHERE fk IS NOT NULL AND NOT EXISTS (...)) AS failing;
```

The planner now picks a **Parallel Hash Anti Join**: **218 ms, 9.1× faster**. The whole suite went from about 4.2 s to about 1.8 s.

`NOT EXISTS` was chosen over `NOT IN` because `NOT IN` returns no rows at all if the parent column contains a single NULL, which is a classic silent bug.

Simple column predicates (`IS NULL`, ranges) keep the single-scan `SUM(CASE ...)` form, because for those one pass is cheaper than two.
