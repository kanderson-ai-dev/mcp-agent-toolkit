import type { LucideIcon } from "lucide-react";
import {
  Database,
  FilePenLine,
  FileText,
  Globe,
  Wrench,
} from "lucide-react";
import { humanizeBytes } from "./format";
import type { ToolResultEnvelope } from "../types";

/**
 * UI label dictionary — the single source of truth for everything the
 * user reads. Internal field names (`tool_call`, `requestId`,
 * `durationMs`, `ok`, `row_count`, `bytes_written`, `provider`,
 * `truncated`, `terminatedBy`, `session_id`...) are translated here and
 * never interpolated raw into JSX anywhere else.
 */

export interface ToolMeta {
  label: string;
  Icon: LucideIcon;
  /** Tailwind classes for the icon tile — one accent per tool. */
  tileClass: string;
  /** Capability chips mirroring the server's MCP ToolAnnotations. */
  hints: string[];
}

const TOOL_META: Record<string, ToolMeta> = {
  web_search: {
    label: "Web search",
    Icon: Globe,
    tileClass: "bg-cyan-500/15 text-cyan ring-cyan-500/30",
    hints: ["Read-only", "Reaches the internet"],
  },
  db_query: {
    label: "Database query",
    Icon: Database,
    tileClass: "bg-violet-500/15 text-violet ring-violet-500/30",
    hints: ["Read-only"],
  },
  read_file: {
    label: "File read",
    Icon: FileText,
    tileClass: "bg-amber-500/15 text-warn ring-amber-500/30",
    hints: ["Read-only"],
  },
  write_file: {
    label: "File write",
    Icon: FilePenLine,
    tileClass: "bg-emerald-500/15 text-ok ring-emerald-500/30",
    hints: ["Writes files", "Overwrites"],
  },
};

const FALLBACK_META: ToolMeta = {
  label: "",
  Icon: Wrench,
  tileClass: "bg-faint/15 text-muted ring-faint/30",
  hints: [],
};

/** `snake_case`/`camelCase` → "Readable words" (fallback only). */
function humanizeName(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  return words.length === 0 ? "Tool" : words[0]!.toUpperCase() + words.slice(1);
}

/** Friendly tool name — never the raw identifier. */
export function toolMeta(toolName: string): ToolMeta {
  const meta = TOOL_META[toolName];
  if (meta) return meta;
  return { ...FALLBACK_META, label: humanizeName(toolName) };
}

/** Status badges — `ok`, `isError`, `running` never reach the UI. */
export const STATUS_LABEL = {
  running: "Running",
  ok: "Success",
  error: "Error",
} as const;

/** Search provider ids → "Source: …" label. */
export function providerLabel(provider: unknown): string {
  switch (provider) {
    case "tavily":
      return "Tavily";
    case "duckduckgo":
      return "DuckDuckGo";
    case "none":
      return "offline mode";
    default:
      return "unknown";
  }
}

/** Tool error codes → friendly explanation (never `invalid_input` raw). */
export function errorCodeLabel(code: string): string {
  switch (code) {
    case "invalid_input":
      return "Invalid input";
    case "forbidden":
      return "Blocked by security policy";
    case "not_found":
      return "Not found";
    case "rate_limited":
      return "Rate limit reached";
    case "too_large":
      return "Result too large";
    case "upstream":
      return "External service unavailable";
    default:
      return "Unexpected error";
  }
}

/** `iteration` → "Step N" inside the timeline. */
export function stepLabel(iteration: number): string {
  return `Step ${iteration}`;
}

/** Run-telemetry copy — `usage`, `costUsd`, `durationMs` never render raw. */
export const TELEMETRY_LABEL = {
  time: "Time",
  tokens: "Tokens",
  cost: "Estimated cost",
} as const;

/** DB column names → human headers ("published_at" → "Published at").
 * Unknown camelCase columns get the same treatment so raw identifiers
 * never render as visible text. */
export function columnLabel(column: string): string {
  const words = column
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (words.length === 0) return "Column";
  const known: Record<string, string> = { id: "ID", url: "URL", sql: "SQL" };
  return words
    .map((w) => known[w.toLowerCase()] ?? w[0]!.toUpperCase() + w.slice(1))
    .join(" ");
}

/** `requestId` → abbreviated trace label (copy button shows the full id). */
export function traceLabel(requestId: string): string {
  return requestId.slice(0, 8);
}

/**
 * One-line prose summary of a call's arguments — a short sentence, not a
 * JSON dump. Raw argument payloads live behind "View raw JSON".
 */
export function argsSummary(toolName: string, args: Record<string, unknown>): string {
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const num = (v: unknown): number | undefined =>
    typeof v === "number" && Number.isFinite(v) ? v : undefined;

  switch (toolName) {
    case "web_search": {
      const q = str(args.query);
      const max = num(args.max_results);
      return max !== undefined ? `"${q}" · up to ${max} results` : `"${q}"`;
    }
    case "db_query":
      return str(args.query);
    case "read_file":
      return pathSummary(str(args.path));
    case "write_file": {
      const p = pathSummary(str(args.path));
      const content = str(args.content);
      return content
        ? `${p} · ${(new TextEncoder().encode(content).length / 1024).toFixed(1)} KB of content`
        : p;
    }
    default:
      return "";
  }
}

/* ── Prompt Registry labels ──────────────────────────────────────
 * Registry raw ids (`agent_system`, `rolled_back`, `task_success`…)
 * never render — these maps are the only source of visible copy.
 */

const PROMPT_NAME_LABEL: Record<string, string> = {
  agent_system: "Agent system prompt",
  judge_rubric: "Judge rubric",
};

/** Prompt registry name → friendly title (fallback humanizes). */
export function promptNameLabel(name: string): string {
  return PROMPT_NAME_LABEL[name] ?? humanizeName(name);
}

/** Registry lifecycle status → badge copy. */
export const PROMPT_STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  candidate: "Candidate",
  promoted: "Active",
  rolled_back: "Rolled back",
};

export function promptStatusLabel(status: string): string {
  return PROMPT_STATUS_LABEL[status] ?? humanizeName(status);
}

const METRIC_LABEL: Record<string, string> = {
  taskSuccess: "Task success",
  grounding: "Grounding",
  toolUsage: "Tool use",
  toolUsageAppropriatePct: "Appropriate tool use",
  adversarialResisted: "Adversarial resistance",
  avgTotalTokens: "Avg tokens",
  avgCostUsd: "Avg cost",
  avgDurationMs: "Avg time",
};

/** A/B metric id → "Task success" etc. */
export function metricLabel(metric: string): string {
  return METRIC_LABEL[metric] ?? humanizeName(metric);
}

/**
 * Server regression strings start with a raw metric id
 * (`taskSuccess: 4.50 → 4.20 (regression +0.30)`). The leading token is
 * swapped for its friendly label; anything unrecognized passes through.
 */
export function regressionLabel(text: string): string {
  const m = /^([A-Za-z]+):\s*(.*)$/.exec(text);
  if (!m) return text;
  const label = METRIC_LABEL[m[1]!];
  return label ? `${label}: ${m[2]}` : text;
}

/** A/B progress phases → human wording. */
export function abPhaseLabel(phase: string): string {
  switch (phase) {
    case "baseline":
      return "Active version";
    case "candidate":
      return "Candidate";
    case "done":
      return "Done";
    default:
      return humanizeName(phase);
  }
}

/**
 * Server progress lines embed raw ids (`agent_system@v1`). Rewrites
 * `name@vN` → friendly title + version; everything else passes through.
 */
export function abProgressLabel(text: string): string {
  return text.replace(/(\w+)@v(\d+)/g, (_m, name: string, ver: string) =>
    `${promptNameLabel(name)} v${ver}`,
  );
}

const AUDIT_EVENT_LABEL: Record<string, string> = {
  candidate_created: "Candidate created",
  ab_test: "A/B test run",
  promoted: "Promoted",
  promoted_override: "Promoted with override",
  rolled_back: "Rolled back",
};

export function auditEventLabel(event: string): string {
  return AUDIT_EVENT_LABEL[event] ?? humanizeName(event);
}

/** Provider mode → footer wording ("Demo mode", never a raw name). */
export function modeLabel(mode: string): string {
  switch (mode) {
    case "stub":
      return "Demo mode";
    case "openai":
      return "Live mode";
    default:
      return humanizeName(mode);
  }
}

/** Whether an argument summary renders as inline code (SQL, paths). */
export function argsAreCode(toolName: string): boolean {
  return toolName === "db_query" || toolName === "read_file" || toolName === "write_file";
}

/** Paths that escape the workspace (absolute drive/UNC/root, `..`
 * segments) render as a fixed phrase — the machine path stays in the
 * collapsed raw JSON, never as visible UI text. */
const OUTSIDE_PATH = /^([a-zA-Z]:[\\/]|\\\\|\/)/;
export function pathSummary(path: string): string {
  if (!path) return path;
  if (OUTSIDE_PATH.test(path) || path.split(/[\\/]/).includes("..")) {
    return "a path outside the workspace";
  }
  return path;
}

interface ResultLikeStep {
  tool: string;
  status: "running" | "ok" | "error";
  preview?: string;
  result?: ToolResultEnvelope;
}

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {};
}

/**
 * One-line human summary of a finished tool result — "5 results",
 * "3 rows returned", "1.9 KB written" — shown next to the collapsed
 * "Result" disclosure. Raw keys (`row_count`, `bytes_written`) never
 * reach the string.
 */
export function resultSummary(step: ResultLikeStep): string {
  const result = step.result;
  if (!result) return step.status === "error" ? "Failed" : "";
  if (result.status === "error") return errorCodeLabel(result.code);
  const data = rec(result.data);
  switch (step.tool) {
    case "web_search": {
      const hits = Array.isArray(data.results) ? data.results.length : 0;
      return `${hits} ${hits === 1 ? "result" : "results"}`;
    }
    case "db_query": {
      const n = typeof data.row_count === "number" ? data.row_count : 0;
      return `${n} ${n === 1 ? "row returned" : "rows returned"}`;
    }
    case "read_file":
      return typeof data.bytes === "number" ? humanizeBytes(data.bytes) : "Content read";
    case "write_file":
      return typeof data.bytes_written === "number"
        ? `${humanizeBytes(data.bytes_written)} written`
        : "File saved";
    default:
      return "";
  }
}
