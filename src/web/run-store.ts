/**
 * Single-use pending-run tokens bridging `POST /api/chat` (validates and
 * stashes the question) to `GET /api/chat/stream` (consumes it and starts
 * streaming). Avoids putting a long, user-authored question in a URL
 * query string while keeping the streaming endpoint a plain `GET` (what
 * `EventSource` requires).
 *
 * In-memory, bounded, and TTL-expired — this is a local single-user
 * console, not a durable job queue.
 */
export class RunStore {
  private readonly pending = new Map<string, { question: string; createdAt: number }>();

  constructor(
    private readonly ttlMs = 5 * 60_000,
    private readonly maxPending = 50,
    private readonly now: () => number = Date.now,
  ) {}

  private sweep(): void {
    const cutoff = this.now() - this.ttlMs;
    for (const [id, run] of this.pending) {
      if (run.createdAt < cutoff) this.pending.delete(id);
    }
  }

  /** Stash a validated question; returns a one-time run id. */
  create(question: string): string {
    this.sweep();
    if (this.pending.size >= this.maxPending) {
      // Drop the oldest entry rather than growing unbounded — a local
      // console abandoning streams faster than it starts them is misuse,
      // not a case worth an error path.
      const oldest = this.pending.keys().next().value;
      if (oldest !== undefined) this.pending.delete(oldest);
    }
    const runId = crypto.randomUUID();
    this.pending.set(runId, { question, createdAt: this.now() });
    return runId;
  }

  /** Consume (pop) a run id — single use, never replayable. */
  consume(runId: string): string | undefined {
    this.sweep();
    const run = this.pending.get(runId);
    if (!run) return undefined;
    this.pending.delete(runId);
    return run.question;
  }

  /** Current pending count — exposed for tests only. */
  get size(): number {
    return this.pending.size;
  }
}
