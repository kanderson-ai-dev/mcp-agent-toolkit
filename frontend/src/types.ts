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
      /** Token-level chunk of the assistant's answer as it streams —
       * additive; the `final` event still carries the full text. */
      type: "answer_delta";
      iteration: number;
      content: string;
    }
  | {
      type: "final";
      content: string;
      iterations: number;
      toolCalls: number;
      terminatedBy: "answer" | "iteration_limit" | "budget_limit";
      durationMs?: number;
      usage?: UsageBreakdown | null;
      costUsd?: number | null;
      promptVersion?: { name: string; version: number; sha256: string };
    }
  | { type: "error"; message: string };

/** Provider-reported token accounting for a run (camelCase on the wire). */
export interface UsageBreakdown {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

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
  terminatedBy: "answer" | "iteration_limit" | "budget_limit";
  /** Wall-clock run time reported by the backend. */
  durationMs?: number;
  /** Provider-reported tokens — absent for uninstrumented adapters. */
  usage?: UsageBreakdown | null;
  /** Estimated USD cost — null when the model's price is unknown. */
  costUsd?: number | null;
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
  /** Token-streamed answer text still in flight — cleared once `final`
   * arrives (which carries the authoritative full content). */
  streamingAnswer?: string;
}

/** A prior conversation turn sent with a follow-up question. */
export interface HistoryTurn {
  role: "user" | "assistant";
  content: string;
}

export type ConsolePhase = "idle" | "streaming" | "done" | "error";

export interface ToolsInfo {
  tools: {
    name: string;
    description: string;
    /** Server-advertised MCP ToolAnnotations (camelCase wire keys). */
    annotations?: {
      title?: string;
      readOnlyHint?: boolean;
      destructiveHint?: boolean;
      idempotentHint?: boolean;
      openWorldHint?: boolean;
    } | null;
    /** Published outputSchema for structuredContent, when advertised. */
    outputSchema?: Record<string, unknown> | null;
  }[];
  llm: { provider: string };
}

/** `GET /api/status` — public-safe deployment summary for the footer. */
export interface StatusInfo {
  mode: string;
  toolCount: number;
  prompts: Record<string, { version: number }>;
  lastEvaluation: {
    ranAt: string;
    passed: boolean;
    mode: string | null;
  } | null;
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
  /** Mean tokens per question — null when the provider reports no usage. */
  avgTotalTokens?: number | null;
  /** Mean USD cost per question — null when the model's price is unknown. */
  avgCostUsd?: number | null;
  /** Mean wall-clock time per question, in milliseconds. */
  avgDurationMs?: number | null;
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
