import { ToolError } from "../../shared/result.js";

/**
 * Sliding-window rate limiter, in-memory. Two keys are checked per
 * invocation: `tool:<name>` (per-tool bound) and `session` (global
 * bound for the whole client session — one stdio process = one session).
 * `now` is injectable for deterministic tests.
 */
export class RateLimiter {
  private readonly hits = new Map<string, number[]>();
  private readonly windowMs = 60_000;
  private readonly sessionLimit: number;

  constructor(
    private readonly perToolLimit: number,
    private readonly now: () => number = Date.now,
    sessionLimit?: number,
  ) {
    // A session legitimately touches several tools per question.
    this.sessionLimit = sessionLimit ?? perToolLimit * 4;
  }

  private countInWindow(key: string): number {
    const cutoff = this.now() - this.windowMs;
    const kept = (this.hits.get(key) ?? []).filter((t) => t > cutoff);
    this.hits.set(key, kept);
    return kept.length;
  }

  private record(key: string): void {
    const arr = this.hits.get(key);
    if (arr) arr.push(this.now());
    else this.hits.set(key, [this.now()]);
  }

  /** Throws ToolError("rate_limited") when either bound is exceeded. */
  check(toolName: string): void {
    if (this.countInWindow(`tool:${toolName}`) >= this.perToolLimit) {
      throw new ToolError(
        "rate_limited",
        `Rate limit exceeded for tool "${toolName}" (${this.perToolLimit}/min)`,
      );
    }
    if (this.countInWindow("session") >= this.sessionLimit) {
      throw new ToolError(
        "rate_limited",
        `Session rate limit exceeded (${this.sessionLimit} calls/min)`,
      );
    }
    this.record(`tool:${toolName}`);
    this.record("session");
  }
}
