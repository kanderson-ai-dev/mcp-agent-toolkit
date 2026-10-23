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
      promptVersion?: { name: string; version: number; sha256: string };
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
  /** Prompt-registry provenance for the answer, when the run reports it. */
  promptVersion?: { name: string; version: number; sha256: string };
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

/* ── Prompt Registry ──────────────────────────────────────────────
 * Wire mirrors of src/mcp_toolkit/web/prompts_routes.py responses.
 * Field names stay camelCase on the wire; friendly labels live in
 * lib/labels.ts (raw ids never render).
 */

export type PromptStatus = "draft" | "candidate" | "promoted" | "rolled_back";

export interface PromptVersionInfo {
  name: string;
  version: number;
  status: PromptStatus;
  author: string;
  createdAt: string;
  description: string;
  sha256: string;
}

export interface PromptVersionDetail extends PromptVersionInfo {
  modelHint: string | null;
  template: string;
}

export interface PromptsListResponse {
  prompts: Record<string, PromptVersionInfo[]>;
  adminEnabled: boolean;
}

export interface AuditEvent {
  type: string;
  name: string;
  version?: number;
  baseline_version?: number;
  candidate_version?: number;
  previous_promoted?: number | null;
  restored_version?: number;
  author?: string;
  reason?: string | null;
  override_reason?: string;
  description?: string;
  mode?: string;
  gate_passed?: boolean;
}

export interface AuditResponse {
  events: AuditEvent[];
  verified: boolean;
  firstTamperedLine?: number | null;
}

/** One side's aggregate summary — `adversarialResisted` is "n/m" text. */
export interface AbSideSummary {
  taskSuccessAvg: number | null;
  groundingAvg: number | null;
  toolUsageAvg: number | null;
  toolUsageAppropriatePct: number;
  adversarialResisted: string;
  passed: boolean;
}

export interface AbResult {
  name: string;
  baselineVersion: number;
  candidateVersion: number;
  baselineSha256: string;
  candidateSha256: string;
  mode: "live" | "offline-smoke";
  judged: boolean;
  baseline: AbSideSummary;
  candidate: AbSideSummary;
  deltas: Record<string, number>;
  regressions: string[];
  gatePassed: boolean;
  promotionAllowed: boolean;
}

export interface AbProgress {
  phase: "baseline" | "candidate" | "done";
  done: number;
  total: number;
}

export type AbRunStatus = "running" | "completed" | "error";

export interface AbRunInfo {
  runId: string;
  name: string;
  status: AbRunStatus;
  progress: string[];
  result?: AbResult | null;
  error?: string | null;
}

/**
 * SSE payloads: `progress`/`error` wrap a message; `result` carries the
 * AbResult wire object directly (prompts_routes._sse sends it as-is).
 */
export type AbEvent =
  | ({ type: "progress" } & { message: string })
  | ({ type: "result" } & AbResult)
  | ({ type: "error" } & { message?: string });
