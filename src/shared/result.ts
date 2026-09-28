/**
 * Uniform tool-result envelope. Every tool returns JSON text with either
 * `{ status: "ok", data }` or `{ status: "error", code, message }`.
 * Errors are surfaced as `isError: true` MCP results (never thrown
 * across the protocol) so the calling model can read and react to them.
 */

export type ToolErrorCode =
  | "invalid_input"
  | "forbidden"
  | "not_found"
  | "rate_limited"
  | "upstream"
  | "internal";

export class ToolError extends Error {
  readonly code: ToolErrorCode;

  constructor(code: ToolErrorCode, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "ToolError";
    this.code = code;
  }
}

export interface TextContent {
  type: "text";
  text: string;
}

/** Shape compatible with the SDK's CallToolResult (text content only). */
export interface CallToolResult {
  [key: string]: unknown;
  content: TextContent[];
  isError?: boolean;
}

export function okResult(data: unknown): CallToolResult {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify({ status: "ok", data }, null, 2),
      },
    ],
  };
}

export function errorResult(err: unknown): CallToolResult {
  const code: ToolErrorCode = err instanceof ToolError ? err.code : "internal";
  const message = err instanceof Error ? err.message : String(err);
  return {
    isError: true,
    content: [
      {
        type: "text",
        text: JSON.stringify({ status: "error", code, message }, null, 2),
      },
    ],
  };
}
