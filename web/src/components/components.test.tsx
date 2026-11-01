import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CheckResult, HeatCell } from "../lib/api";
import { driftBins, thresholdFor } from "../pages/CheckPage";
import { buildMatrix, Heatmap, heatColor } from "./Heatmap";
import { StatusPill } from "./ui";

const cells: HeatCell[] = [
  { datasource: "db", table: "b", day: "2026-01-02", checks: 4, pass_rate: 0.5 },
  { datasource: "db", table: "a", day: "2026-01-01", checks: 4, pass_rate: 1 },
];

describe("heatmap", () => {
  it("builds a sorted, sparse matrix", () => {
    const m = buildMatrix(cells);
    expect(m.days).toEqual(["2026-01-01", "2026-01-02"]);
    expect(m.tables).toEqual(["db.a", "db.b"]);
    expect(m.lookup.get("db.a|2026-01-02")).toBeUndefined();
  });

  it("colours by pass rate", () => {
    expect(heatColor(1)).toBe("var(--good)");
    expect(heatColor(0.9)).toContain("--good");
    expect(heatColor(0.5)).toBe("var(--warn)");
    expect(heatColor(-3)).toBe("var(--bad)");
  });

  it("renders cells with accessible titles and empty days", () => {
    render(<Heatmap cells={cells} />);
    expect(screen.getByTitle(/db.b · 2026-01-02: 50% of 4/)).toBeTruthy();
    expect(screen.getByTitle("db.a · 2026-01-02: no run")).toBeTruthy();
  });

  it("shows an empty state", () => {
    render(<Heatmap cells={[]} />);
    expect(screen.getByText(/No runs/)).toBeTruthy();
  });
});

describe("check page helpers", () => {
  it("computes drift bin proportions and labels", () => {
    const bins = driftBins({ edges: [10, 20], baseline: [1, 2, 1], current: [0, 0, 4] });
    expect(bins.map((b) => b.bin)).toEqual(["< 10.0", "10.0–20.0", "≥ 20.0"]);
    expect(bins[1]).toMatchObject({ baseline: 0.5, current: 0 });
    expect(bins[2]?.current).toBe(1);
  });

  it("extracts thresholds per type", () => {
    const base = { details: { threshold: 0.02, max_lag_hours: 30 } } as unknown as CheckResult;
    expect(thresholdFor({ ...base, check_type: "null_rate" })).toBe(0.02);
    expect(thresholdFor({ ...base, check_type: "freshness" })).toBe(30);
    expect(thresholdFor({ ...base, check_type: "unique" })).toBeNull();
  });
});

describe("StatusPill", () => {
  it("labels statuses", () => {
    render(<StatusPill status="fail" />);
    expect(screen.getByText("Failing").className).toContain("fail");
  });
});
