import { describe, expect, it } from "vitest";
import { formatAxis, formatObserved, parseCheckId, relativeTime, scoreTone } from "./format";

describe("format helpers", () => {
  it("formats observed values per check type", () => {
    expect(formatObserved(0.0234, "null_rate")).toBe("2.34%");
    expect(formatObserved(47.01, "freshness")).toBe("47.0h");
    expect(formatObserved(1.38823, "drift")).toBe("PSI 1.388");
    expect(formatObserved(null, "unique")).toBe("—");
    expect(formatObserved(1.5, "range")).toBe("1.500");
  });

  it("formats compact axis ticks", () => {
    expect(formatAxis(0.025, "null_rate")).toBe("2.5%");
    expect(formatAxis(12500, "unique")).toBe("12.5k");
    expect(formatAxis(7.4717, "drift")).toBe("7.47");
  });

  it("parses check ids whose target contains dots", () => {
    expect(parseCheckId("clinic.animals.unique.a.b")).toEqual({
      datasource: "clinic", table: "animals", type: "unique", target: "a.b",
    });
  });

  it("renders relative times", () => {
    const now = new Date("2026-01-02T00:00:00Z");
    expect(relativeTime("2026-01-01T00:00:00Z", now)).toBe("1d ago");
    expect(relativeTime("2026-01-01T22:30:00Z", now)).toBe("1h ago");
    expect(relativeTime("2026-01-01T23:59:30Z", now)).toBe("just now");
    expect(relativeTime("2026-01-02T00:05:00Z", now)).toBe("in 5m");
  });

  it("maps scores to tones", () => {
    expect([scoreTone(95), scoreTone(75), scoreTone(10)]).toEqual(["good", "warn", "bad"]);
  });
});

describe("static demo mode", () => {
  it("maps calls to exported files and filters like the API", async () => {
    const { staticApi } = await import("./api");
    const calls: string[] = [];
    const rows = [{ table: "owners", id: 1 }, { table: "animals", id: 2 }];
    globalThis.fetch = (async (url: string) => {
      calls.push(url);
      const body = url.endsWith("latest.json") ? rows : url.endsWith("runs.json") ? [1, 2, 3] : url.includes("history") ? [1, 2, 3, 4] : {};
      return new Response(JSON.stringify(body), { status: 200 });
    }) as typeof fetch;
    expect(await staticApi.latest("animals")).toEqual([{ table: "animals", id: 2 }]);
    expect(await staticApi.runs(2)).toEqual([1, 2]);
    expect(await staticApi.history("clinic.a.unique.id", 2)).toEqual([3, 4]);
    await staticApi.result(7);
    await staticApi.samples(7);
    expect(calls).toEqual([
      "/data/latest.json", "/data/runs.json", "/data/history/clinic.a.unique.id.json",
      "/data/results/7.json", "/data/samples/7.json",
    ]);
  });
});
