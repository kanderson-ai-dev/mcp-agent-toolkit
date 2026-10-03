import type { AgentEvent } from "../types";

const EVENT_TYPES = ["tool_call", "tool_result", "llm_message", "final", "error"] as const;

export interface StreamHandlers {
  onEvent: (event: AgentEvent) => void;
  /** Called once when the stream ends — cleanly (`final`/`error` event)
   * or because the connection dropped (`reason` describes which). */
  onDone: (reason: "completed" | "connection_lost") => void;
}

/**
 * Open the SSE stream for a one-time runId and dispatch typed events.
 * `EventSource` auto-reconnects on drop — undesirable here (the runId is
 * single-use), so the source is closed as soon as a terminal event or a
 * network error arrives. Returns a cancel function.
 */
export function streamRun(runId: string, handlers: StreamHandlers): () => void {
  const source = new EventSource(`/api/chat/stream?runId=${encodeURIComponent(runId)}`);
  let finished = false;

  const finish = (reason: "completed" | "connection_lost"): void => {
    if (finished) return;
    finished = true;
    source.close();
    handlers.onDone(reason);
  };

  for (const type of EVENT_TYPES) {
    source.addEventListener(type, (ev) => {
      try {
        const parsed: unknown = JSON.parse(ev.data as string);
        const event = parsed as AgentEvent;
        handlers.onEvent(event);
        if (event.type === "final" || event.type === "error") finish("completed");
      } catch {
        // A malformed SSE payload is ignored — the stream may still deliver
        // the remaining events; nothing user-visible is fabricated here.
      }
    });
  }

  source.onerror = () => {
    // Also fires when the server closes the stream after `final` — the
    // `finished` flag makes that a no-op.
    finish("connection_lost");
  };

  return () => finish("completed");
}
