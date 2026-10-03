import type { ToolsInfo } from "../types";

/** HTTP client for the Express adapter (src/web/). Same-origin always —
 * Vite proxies `/api` in dev; Express serves the build in production. */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function readErrorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { error?: unknown };
    if (typeof body.error === "string" && body.error) return body.error;
  } catch {
    // fall through to the generic status line
  }
  return `El servidor respondió con un error (${res.status}).`;
}

export async function fetchTools(): Promise<ToolsInfo> {
  const res = await fetch("/api/tools");
  if (!res.ok) throw new ApiError(await readErrorMessage(res), res.status);
  return (await res.json()) as ToolsInfo;
}

/** POST the question; returns the one-time runId for the SSE stream. */
export async function submitQuestion(question: string): Promise<string> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new ApiError(await readErrorMessage(res), res.status);
  const body = (await res.json()) as { runId?: unknown };
  if (typeof body.runId !== "string" || !body.runId) {
    throw new ApiError("Respuesta inesperada del servidor.", res.status);
  }
  return body.runId;
}
