import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import type { AppConfig } from "../shared/config.js";
import { errorResult, ToolError, type CallToolResult } from "../shared/result.js";
import { RateLimiter } from "./guardrails/rate-limiter.js";
import { sanitizeToolOutput } from "./guardrails/sanitize.js";
import { createLogger } from "./observability/logger.js";
import { createMetrics, type ServerMetrics } from "./observability/metrics.js";
import { extractRequestId } from "./observability/request-context.js";
import { createTools } from "./tools/index.js";

export interface BuiltServer {
  server: McpServer;
  sessionId: string;
  metrics: ServerMetrics;
}

/**
 * Assemble the MCP server: tool registry + per-invocation guardrail
 * chain. Order is deliberate — rate limit first (cheap reject), then the
 * tool handler (Zod-validated args), then output sanitization (untrusted
 * data capped/normalized) — and every call is logged as structured JSON.
 */
export function buildServer(config: AppConfig): BuiltServer {
  const logger = createLogger(config.LOG_LEVEL);
  const limiter = new RateLimiter(config.RATE_LIMIT_PER_MINUTE);
  const metrics = createMetrics();
  const sessionId = crypto.randomUUID();

  const server = new McpServer(
    { name: "mcp-agent-toolkit", version: "0.1.0" },
    { capabilities: { logging: {} } },
  );

  for (const tool of createTools(config)) {
    server.registerTool(
      tool.name,
      {
        description: tool.description,
        inputSchema: tool.inputSchema,
      },
      async (args, extra) => {
        const started = performance.now();
        const requestId = extractRequestId(extra);
        const logFields = (): Record<string, unknown> => ({
          event: "tool_invocation",
          tool: tool.name,
          request_id: requestId,
          session_id: sessionId,
          duration_ms: Math.round(performance.now() - started),
        });
        try {
          limiter.check(tool.name);
        } catch (err) {
          metrics.recordRateLimited(tool.name);
          logger.warn(
            { ...logFields(), ok: false, error: err instanceof Error ? err.message : String(err) },
            "tool invocation rate-limited",
          );
          return errorResult(err);
        }
        try {
          const result = await tool.handler(args, { requestId, sessionId });
          const sanitized = sanitizeResult(result, config.MAX_TOOL_OUTPUT_CHARS);
          const ok = sanitized.isError !== true;
          metrics.recordInvocation(tool.name, ok, performance.now() - started);
          logger.info(
            {
              ...logFields(),
              ok,
              input: preview(args),
              output: preview(sanitized.content),
            },
            "tool invocation",
          );
          return sanitized;
        } catch (err) {
          metrics.recordInvocation(tool.name, false, performance.now() - started);
          logger.warn(
            {
              ...logFields(),
              ok: false,
              error: err instanceof Error ? err.message : String(err),
            },
            "tool invocation failed",
          );
          return errorResult(err instanceof ToolError ? err : new ToolError("internal", String(err)));
        }
      },
    );
  }

  return { server, sessionId, metrics };
}

/** Cap/normalize every text block — tool output is untrusted data. */
function sanitizeResult(result: CallToolResult, maxChars: number): CallToolResult {
  return {
    ...result,
    content: result.content.map((c) =>
      c.type === "text" ? { ...c, text: sanitizeToolOutput(c.text, maxChars).text } : c,
    ),
  };
}

/** Bounded single-line preview of tool args/output for the JSON log line. */
function preview(value: unknown, max = 500): string {
  try {
    const s = JSON.stringify(value);
    return s.length > max ? `${s.slice(0, max)}…` : s;
  } catch {
    return "[unserializable]";
  }
}

