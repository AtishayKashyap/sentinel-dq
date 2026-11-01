# ADR 0002: Statistical checks that don't cry wolf

**Status:** accepted

Alert fatigue kills monitoring tools: after a few false pages, people mute the channel. Each statistical choice below was driven by a false positive that appeared in the demo simulation and was then fixed.

## Drift: quantile bins, not equal-width bins
PSI was first computed over 10 equal-width bins. On a bell-shaped weight distribution the tail bins hold about 1–2% of the mass, so a 55-row daily sample often has an **empty** tail bin. The epsilon floor then turns that into a PSI near 0.9 on pure noise.

**Fix:** equal-frequency bins from `NTILE(k) OVER (ORDER BY col)`, which is portable across PG, MySQL 8 and SQLite. Edges sit midway between adjacent tiles. On no-drift days PSI now stays at or below 0.17, while the injected +45% shift scores 1.4 to 5.7.

**Also:** `filter:` (for example `{species: cattle}`) measures drift within one segment, because drift in a mixed population hides inside the mix. `min_rows` refuses to judge tiny samples, since expected PSI noise grows roughly as bins/n.

## Volume anomalies: z-score AND a relative-change floor
On a stable table the standard deviation of daily volume is tiny, so a harmless 3% wobble scored z = 5. A failure now requires **both** `|z| > z_threshold` and `|Δ| / mean > min_relative_change` (default 20%).

## Baselines learn only from healthy observations
After a 6× spike, the spike stayed in the 14-run history, inflated the standard deviation, and the next day's **drop to zero rows looked normal**. `metric_history` now returns only results whose status was `pass`, so incidents never become part of "normal".

## Volume is measured per window
Cumulative `COUNT(*)` on an append-only table rises on every run, so it would always look anomalous. `recent_column` and `recent_hours` count rows in a trailing window instead.
