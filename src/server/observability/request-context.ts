import type { RequestHandlerExtra } from "@modelcontextprotocol/sdk/shared/protocol.js";
import type { ServerNotification, ServerRequest } from "@modelcontextprotocol/sdk/types.js";

export type ToolHandlerExtra = RequestHandlerExtra<ServerRequest, ServerNotification>;

/**
 * Request-ID correlation. The agent client stamps a UUID into
 * `params._meta.requestId` on every `tools/call`; fall back to the
 * JSON-RPC request ID the SDK assigns (always present).
 */
export function extractRequestId(extra: ToolHandlerExtra): string {
  const value = extra._meta?.requestId;
  return typeof value === "string" && value !== "" ? value : String(extra.requestId);
}
