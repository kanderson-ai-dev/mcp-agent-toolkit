/**
 * Mirror of the backend `AgentEvent` union (src/client/events.ts) plus the
 * UI-side timeline model. The SSE stream carries the union verbatim as
 * named events; the timeline merges `tool_call` + `tool_result` into one
 * `ToolStep` per `requestId`.
 */

export type ToolResultEnvelope =
  | { status: "ok"; data: unknown }
  | { status: "error"; code: string; message: string };

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
      result?: ToolResultEnvelope;
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

export type ToolStepStatus = "running" | "ok" | "error";

export interface ToolStep {
  kind: "tool";
  requestId: string;
  iteration: number;
  tool: string;
  args: Record<string, unknown>;
  status: ToolStepStatus;
  durationMs?: number;
  preview?: string;
  result?: ToolResultEnvelope;
}

/** Intermediate assistant note (streamed reasoning text), if any. */
export interface NoteStep {
  kind: "note";
  iteration: number;
  content: string;
}

export type TimelineStep = ToolStep | NoteStep;

export interface FinalResult {
  content: string;
  iterations: number;
  toolCalls: number;
  terminatedBy: "answer" | "iteration_limit";
}

/** One console run — a submitted question and everything it produced. */
export interface RunRecord {
  id: string;
  question: string;
  steps: TimelineStep[];
  final?: FinalResult;
  errorMessage?: string;
  /** Wall-clock run time, filled when the stream finishes. */
  elapsedMs?: number;
}

export type ConsolePhase = "idle" | "streaming" | "done" | "error";

export interface ToolsInfo {
  tools: { name: string; description: string }[];
  llm: { provider: string };
}
