import { describe, expect, it } from "vitest";
import { RunStore } from "../../src/web/run-store.js";

describe("RunStore", () => {
  it("round-trips a question through create/consume", () => {
    const store = new RunStore();
    const runId = store.create("hello?");
    expect(store.consume(runId)).toBe("hello?");
  });

  it("is single-use — consuming twice returns undefined the second time", () => {
    const store = new RunStore();
    const runId = store.create("once only");
    expect(store.consume(runId)).toBe("once only");
    expect(store.consume(runId)).toBeUndefined();
  });

  it("returns undefined for an unknown run id", () => {
    const store = new RunStore();
    expect(store.consume("nope")).toBeUndefined();
  });

  it("expires entries past the TTL", () => {
    let now = 1_000;
    const store = new RunStore(1_000, 50, () => now);
    const runId = store.create("will expire");
    now += 2_000; // past the 1s TTL
    expect(store.consume(runId)).toBeUndefined();
  });

  it("evicts the oldest entry once at capacity", () => {
    const store = new RunStore(5 * 60_000, 2);
    const first = store.create("first");
    store.create("second");
    expect(store.size).toBe(2);
    store.create("third"); // should evict "first"
    expect(store.size).toBe(2);
    expect(store.consume(first)).toBeUndefined();
  });
});
