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
}

const TOOL_META: Record<string, ToolMeta> = {
  web_search: {
    label: "Web search",
    Icon: Globe,
    tileClass: "bg-cyan-500/15 text-cyan-300 ring-cyan-500/30",
  },
  db_query: {
    label: "Database query",
    Icon: Database,
    tileClass: "bg-violet-500/15 text-violet-300 ring-violet-500/30",
  },
  read_file: {
    label: "File read",
    Icon: FileText,
    tileClass: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  },
  write_file: {
    label: "File write",
    Icon: FilePenLine,
    tileClass: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  },
};

const FALLBACK_META: ToolMeta = {
  label: "",
  Icon: Wrench,
  tileClass: "bg-slate-500/15 text-slate-300 ring-slate-500/30",
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
      return str(args.path);
    case "write_file": {
      const p = str(args.path);
      const content = str(args.content);
      return content
        ? `${p} · ${(new TextEncoder().encode(content).length / 1024).toFixed(1)} KB of content`
        : p;
    }
    default:
      return "";
  }
}

/** Whether an argument summary renders as inline code (SQL, paths). */
export function argsAreCode(toolName: string): boolean {
  return toolName === "db_query" || toolName === "read_file" || toolName === "write_file";
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
