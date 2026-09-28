import { describe, expect, it } from "vitest";
import { RateLimiter } from "../../src/server/guardrails/rate-limiter.js";
import { ToolError } from "../../src/shared/result.js";

describe("rate limiter", () => {
  it("allows up to the per-tool limit then rejects", () => {
    const limiter = new RateLimiter(3);
    for (let i = 0; i < 3; i++) expect(() => limiter.check("web_search")).not.toThrow();
    expect(() => limiter.check("web_search")).toThrowError(ToolError);
    try {
      limiter.check("web_search");
      expect.unreachable();
    } catch (err) {
      expect((err as ToolError).code).toBe("rate_limited");
      expect((err as Error).message).toContain("3/min");
    }
  });

  it("tracks tools independently", () => {
    const limiter = new RateLimiter(1);
    limiter.check("web_search");
    expect(() => limiter.check("web_search")).toThrowError(ToolError);
    expect(() => limiter.check("db_query")).not.toThrow();
  });

  it("enforces the session bound across tools", () => {
    // per-tool 5 but session cap 3 → 4th call across any tools fails.
    const limiter = new RateLimiter(5, Date.now, 3);
    limiter.check("a");
    limiter.check("b");
    limiter.check("c");
    expect(() => limiter.check("d")).toThrowError(/Session rate limit/);
  });

  it("frees capacity as the window slides", () => {
    let t = 1_000_000;
    const limiter = new RateLimiter(2, () => t);
    limiter.check("x");
    limiter.check("x");
    expect(() => limiter.check("x")).toThrowError(ToolError);
    t += 61_000; // window elapsed
    expect(() => limiter.check("x")).not.toThrow();
  });
});
