import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { streamRun } from "./sse";
import type { AgentEvent } from "../types";

/** Minimal EventSource stand-in: captures listeners, manual emit/error. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  closed = false;
  onerror: (() => void) | null = null;
  private listeners = new Map<string, ((ev: { data: string }) => void)[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(
    type: string,
    fn: (ev: { data: string }) => void,
  ): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }

  emit(type: string, payload: unknown): void {
    for (const fn of this.listeners.get(type) ?? []) {
      fn({ data: JSON.stringify(payload) });
    }
  }

  emitRaw(type: string, data: string): void {
    for (const fn of this.listeners.get(type) ?? []) fn({ data });
  }

  close(): void {
    this.closed = true;
  }
}

const TOOL_CALL = {
  type: "tool_call",
  iteration: 1,
  requestId: "r1",
  tool: "db_query",
  args: { query: "SELECT 1" },
};

const FINAL = {
  type: "final",
  content: "done",
  iterations: 1,
  toolCalls: 1,
  terminatedBy: "answer",
};

describe("streamRun", () => {
  beforeEach(() => {
    FakeEventSource.instances = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function open() {
    const events: AgentEvent[] = [];
    const done = vi.fn();
    const cancel = streamRun("run-1", {
      onEvent: (e) => events.push(e),
      onDone: done,
    });
    const source = FakeEventSource.instances[0]!;
    return { events, done, cancel, source };
  }

  it("opens the one-time stream URL and dispatches typed events", () => {
    const { events, source } = open();
    expect(source.url).toBe("/api/chat/stream?runId=run-1");
    source.emit("tool_call", TOOL_CALL);
    expect(events).toEqual([TOOL_CALL]);
  });

  it("URL-encodes the runId", () => {
    const noop = vi.fn();
    streamRun("a b&c", { onEvent: noop, onDone: noop });
    expect(FakeEventSource.instances[0]!.url).toBe(
      "/api/chat/stream?runId=a%20b%26c",
    );
  });

  it("closes on a terminal event and reports 'completed' once", () => {
    const { done, source } = open();
    source.emit("final", FINAL);
    expect(done).toHaveBeenCalledTimes(1);
    expect(done).toHaveBeenCalledWith("completed");
    expect(source.closed).toBe(true);
    // The fake still delivers emitted events after close(), but the
    // `finished` flag guarantees onDone is never called twice.
    source.emit("tool_call", TOOL_CALL);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("reports 'connection_lost' when the source errors mid-stream", () => {
    const { done, source } = open();
    source.onerror?.();
    expect(done).toHaveBeenCalledWith("connection_lost");
    expect(source.closed).toBe(true);
    source.onerror?.(); // second error is a no-op
    expect(done).toHaveBeenCalledTimes(1);
  });

  it("ignores malformed payloads and keeps listening", () => {
    const { events, done, source } = open();
    source.emitRaw("tool_call", "{not json");
    expect(events).toEqual([]);
    source.emit("final", FINAL);
    expect(events).toEqual([FINAL]);
    expect(done).toHaveBeenCalledWith("completed");
  });

  it("cancel closes the source and finishes 'completed'", () => {
    const { done, source, cancel } = open();
    cancel();
    expect(source.closed).toBe(true);
    expect(done).toHaveBeenCalledWith("completed");
    cancel(); // idempotent
    expect(done).toHaveBeenCalledTimes(1);
  });
});
