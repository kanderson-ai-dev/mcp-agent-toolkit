/**
 * Structured agent event stream. Emitted on stdout so the transcript is
 * machine-readable (`OUTPUT_FORMAT=json` → one JSON object per line) or
 * human-readable (`pretty`). SSE can sit on top of the same union later.
 */

export type AgentEvent =
  | {
      type: "tool_call";
      iteration: number;
      requestId: string;
      tool: string;
      args: Record<string, unknown>;
    }
  | {
      type: "tool_result";
      iteration: number;
      requestId: string;
      tool: string;
      ok: boolean;
      durationMs: number;
      preview: string;
    }
  | { type: "llm_message"; iteration: number; content: string }
  | {
      type: "final";
      content: string;
      iterations: number;
      toolCalls: number;
      terminatedBy: "answer" | "iteration_limit";
    }
  | { type: "error"; message: string };

export function formatEvent(event: AgentEvent, format: "pretty" | "json"): string {
  if (format === "json") return JSON.stringify(event);
  switch (event.type) {
    case "tool_call":
      return `[iter ${event.iteration}] → tool_call ${event.tool} ${truncate(JSON.stringify(event.args), 160)}`;
    case "tool_result":
      return `[iter ${event.iteration}] ← ${event.ok ? "ok" : "ERROR"} ${event.tool} (${event.durationMs}ms) ${truncate(event.preview, 140)}`;
    case "llm_message":
      return `[iter ${event.iteration}] llm: ${truncate(event.content, 140)}`;
    case "final":
      return `\n=== FINAL (${event.iterations} iterations, ${event.toolCalls} tool calls, ${event.terminatedBy}) ===\n${event.content}`;
    case "error":
      return `error: ${event.message}`;
  }
}

function truncate(s: string, n: number): string {
  return s.length > n ? `${s.slice(0, n - 1)}…` : s;
}
