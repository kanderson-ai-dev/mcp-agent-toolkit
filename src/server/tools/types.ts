import type { z } from "zod";
import type { CallToolResult } from "../../shared/result.js";

/** Per-invocation context propagated through the guardrail chain. */
export interface ToolContext {
  /** Request ID: client-supplied via `params._meta.requestId`, else the
   *  JSON-RPC request ID assigned by the SDK. */
  requestId: string;
  /** Server process instance ID — correlates a whole client↔server session. */
  sessionId: string;
}

export interface ToolDefinition<S extends z.ZodRawShape = z.ZodRawShape> {
  name: string;
  description: string;
  /** Zod raw shape — the SDK validates incoming arguments against it. */
  inputSchema: S;
  handler(
    args: z.infer<z.ZodObject<S>>,
    ctx: ToolContext,
  ): Promise<CallToolResult>;
}

/** Registry-facing type — `handler` stays a method so per-tool arg types
 *  remain assignable under strictFunctionTypes. */
export type AnyToolDefinition = ToolDefinition<z.ZodRawShape>;
