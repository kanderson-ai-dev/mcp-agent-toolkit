import { ApiError } from "./api";
import type {
  AbEvent,
  AbResult,
  AbRunInfo,
  AuditResponse,
  PromptVersionDetail,
  PromptsListResponse,
} from "../types";

/** Registry API client — mirrors src/mcp_toolkit/web/prompts_routes.py.
 * Mutation bodies are snake_case (Pydantic field names, verbatim). */

interface ErrorBody {
  detail?: unknown;
  error?: unknown;
  code?: unknown;
  regressions?: unknown;
  mode?: unknown;
}

export class PromptApiError extends ApiError {
  constructor(
    message: string,
    status: number,
    readonly code: string,
    readonly regressions?: string[],
    readonly mode?: string,
  ) {
    super(message, status);
    this.name = "PromptApiError";
  }
}

async function readJsonOrThrow(res: Response): Promise<never> {
  let body: ErrorBody = {};
  try {
    body = (await res.json()) as ErrorBody;
  } catch {
    // fall through — generic status line below
  }
  const message =
    (typeof body.detail === "string" && body.detail) ||
    (typeof body.error === "string" && body.error) ||
    `The server returned an error (${res.status}).`;
  const code = typeof body.code === "string" ? body.code : "internal";
  const regressions = Array.isArray(body.regressions)
    ? body.regressions.filter((r): r is string => typeof r === "string")
    : undefined;
  const mode = typeof body.mode === "string" ? body.mode : undefined;
  throw new PromptApiError(message, res.status, code, regressions, mode);
}

async function get<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) await readJsonOrThrow(res);
  return (await res.json()) as T;
}

async function post<T>(url: string, payload: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) await readJsonOrThrow(res);
  return (await res.json()) as T;
}

export function fetchPrompts(): Promise<PromptsListResponse> {
  return get<PromptsListResponse>("/api/prompts");
}

export function fetchPromptVersion(
  name: string,
  version: number,
): Promise<PromptVersionDetail> {
  return get<PromptVersionDetail>(
    `/api/prompts/${encodeURIComponent(name)}/${version}`,
  );
}

export interface DiffResponse {
  name: string;
  from: number;
  to: number;
  diff: string;
}

export function fetchDiff(name: string, from: number, to: number): Promise<DiffResponse> {
  return get<DiffResponse>(
    `/api/prompts/${encodeURIComponent(name)}/diff?from=${from}&to=${to}`,
  );
}

export function fetchAudit(): Promise<AuditResponse> {
  return get<AuditResponse>("/api/prompts/audit");
}

export function createCandidate(
  name: string,
  body: {
    template: string;
    description: string;
    author: string;
  },
): Promise<PromptVersionDetail> {
  return post<PromptVersionDetail>(
    `/api/prompts/${encodeURIComponent(name)}/candidates`,
    body,
  );
}

export function startAbRun(
  name: string,
  body: {
    candidate_version: number;
    baseline_version?: number;
    offline?: boolean;
  },
): Promise<{ runId: string }> {
  return post<{ runId: string }>(
    `/api/prompts/${encodeURIComponent(name)}/ab-test`,
    body,
  );
}

export function promote(
  name: string,
  body: { version: number; ab_id?: string; override_reason?: string },
): Promise<PromptVersionDetail> {
  return post<PromptVersionDetail>(
    `/api/prompts/${encodeURIComponent(name)}/promote`,
    body,
  );
}

export function rollback(
  name: string,
  body: { to_version: number; reason: string },
): Promise<PromptVersionDetail> {
  return post<PromptVersionDetail>(
    `/api/prompts/${encodeURIComponent(name)}/rollback`,
    body,
  );
}

/** Newest persisted A/B evidence for a candidate (404 → PromptApiError). */
export function fetchAbResult(name: string, candidateVersion: number): Promise<AbResult> {
  return get<AbResult>(
    `/api/prompts/${encodeURIComponent(name)}/ab-result?candidate=${candidateVersion}`,
  );
}

export function fetchAbRun(runId: string): Promise<AbRunInfo> {
  return get<AbRunInfo>(`/api/prompts/ab/${encodeURIComponent(runId)}`);
}

export interface AbStreamHandlers {
  onEvent: (event: AbEvent) => void;
  onDone: (reason: "completed" | "connection_lost") => void;
}

const AB_EVENT_TYPES = ["progress", "result", "error"] as const;

/**
 * SSE stream for one A/B run — same close-on-terminal semantics as
 * `streamRun` (the run ends at `result`/`error`; reconnects are wrong).
 */
export function streamAbRun(runId: string, handlers: AbStreamHandlers): () => void {
  const source = new EventSource(
    `/api/prompts/ab/${encodeURIComponent(runId)}/stream`,
  );
  let finished = false;

  const finish = (reason: "completed" | "connection_lost"): void => {
    if (finished) return;
    finished = true;
    source.close();
    handlers.onDone(reason);
  };

  for (const type of AB_EVENT_TYPES) {
    source.addEventListener(type, (ev) => {
      try {
        // The event name IS the type — A/B payloads don't repeat it
        // (progress/error wrap a message; result is the AbResult wire).
        const event = { ...(JSON.parse(ev.data as string) as object), type } as AbEvent;
        handlers.onEvent(event);
        if (type === "result" || type === "error") finish("completed");
      } catch {
        // malformed frame — keep listening
      }
    });
  }

  source.onerror = () => finish("connection_lost");
  return () => finish("completed");
}
