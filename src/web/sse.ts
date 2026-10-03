import type { Response } from "express";
import type { AgentEvent } from "../client/events.js";

/** Open an SSE stream: headers + flush, no buffering by intermediaries. */
export function startSseStream(res: Response): void {
  res.status(200);
  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  // Disable response buffering on reverse proxies (e.g. nginx) if this is
  // ever deployed behind one — irrelevant for local use, cheap to set.
  res.setHeader("X-Accel-Buffering", "no");
  res.flushHeaders();
}

/**
 * Write one `AgentEvent` as a named SSE event — `event:` is the
 * discriminant (`tool_call`, `tool_result`, ...), `data:` is the full
 * JSON payload. The frontend subscribes per event type via
 * `EventSource.addEventListener`.
 */
export function writeSseEvent(res: Response, event: AgentEvent): void {
  if (res.writableEnded) return;
  res.write(`event: ${event.type}\n`);
  res.write(`data: ${JSON.stringify(event)}\n\n`);
}
