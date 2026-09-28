import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import {
  getDefaultEnvironment,
  StdioClientTransport,
} from "@modelcontextprotocol/sdk/client/stdio.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import { McpError, ErrorCode } from "@modelcontextprotocol/sdk/types.js";
import type { Readable } from "node:stream";
import type { OpenAIToolDef } from "./llm/types.js";

/** A tool as discovered via MCP `tools/list`. */
export interface DiscoveredTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

/** Normalized outcome of one `tools/call` — never throws. */
export interface ToolOutcome {
  ok: boolean;
  text: string;
  /** Machine reason when ok=false: timeout | unknown_tool | malformed | server_error */
  failure?: "timeout" | "unknown_tool" | "malformed" | "server_error";
  durationMs: number;
}

/** Minimal invoker surface — the agent loop depends on this, not on the SDK. */
export interface ToolInvoker {
  listTools(): DiscoveredTool[];
  call(name: string, args: Record<string, unknown>, requestId: string): Promise<ToolOutcome>;
}

const CALL_TIMEOUT_MS = 30_000;

/**
 * Env vars the spawned server is allowed to see. The SDK's default
 * environment deliberately filters everything else — notably, the
 * client's `OPENAI_API_KEY` is never forwarded to the server process.
 */
const SERVER_ENV_KEYS = [
  "SEARCH_PROVIDER",
  "SEARCH_API_KEY",
  "SANDBOX_ROOT",
  "DB_PATH",
  "RATE_LIMIT_PER_MINUTE",
  "MAX_TOOL_OUTPUT_CHARS",
  "READ_FILE_MAX_BYTES",
  "WRITE_FILE_MAX_BYTES",
  "LOG_LEVEL",
  "METRICS_PORT",
] as const;

/**
 * Thin wrapper around the official MCP client. Canonical usage spawns
 * the server over stdio (`overStdio`); tests can inject any MCP transport
 * (`overTransport`, e.g. the SDK's InMemoryTransport) to exercise the
 * real protocol in-process.
 */
export interface McpClientOptions {
  /** tools/call timeout; injectable for tests. Default 30_000 ms. */
  callTimeoutMs?: number;
}

export class McpAgentClient implements ToolInvoker {
  private readonly client: Client;
  private readonly transport: Transport;
  private readonly callTimeoutMs: number;
  private tools: DiscoveredTool[] = [];

  private constructor(transport: Transport, opts: McpClientOptions = {}) {
    this.transport = transport;
    this.callTimeoutMs = opts.callTimeoutMs ?? CALL_TIMEOUT_MS;
    this.client = new Client(
      { name: "mcp-agent-toolkit-client", version: "0.1.0" },
      { capabilities: {} },
    );
  }

  /**
   * Spawn the server as a child process over stdio. `stderr` — "inherit"
   * shows server JSON logs to the operator (CLI default); "pipe" lets
   * tests capture them via `stderrStream`.
   */
  static overStdio(
    command: string,
    args: string[],
    stderr: "inherit" | "pipe" = "inherit",
    opts: McpClientOptions = {},
  ): McpAgentClient {
    const env: Record<string, string> = { ...getDefaultEnvironment() };
    for (const key of SERVER_ENV_KEYS) {
      const v = process.env[key];
      if (v !== undefined && v !== "") env[key] = v;
    }
    return new McpAgentClient(new StdioClientTransport({ command, args, env, stderr }), opts);
  }

  /** Wrap an already-created MCP transport (e.g. InMemoryTransport in tests). */
  static overTransport(transport: Transport, opts: McpClientOptions = {}): McpAgentClient {
    return new McpAgentClient(transport, opts);
  }

  /** Server-side JSON log stream (only for stderr="pipe" stdio clients). */
  get stderrStream(): Readable | null {
    return this.transport instanceof StdioClientTransport
      ? (this.transport.stderr as Readable | null)
      : null;
  }

  async connect(): Promise<DiscoveredTool[]> {
    await this.client.connect(this.transport);
    const { tools } = await this.client.listTools();
    this.tools = tools.map((t) => ({
      name: t.name,
      description: t.description ?? "",
      inputSchema: t.inputSchema ?? {},
    }));
    return this.tools;
  }

  listTools(): DiscoveredTool[] {
    return this.tools;
  }

  /** Convert discovered MCP tools into OpenAI function-calling defs. */
  toOpenAITools(): OpenAIToolDef[] {
    return this.tools.map((t) => ({
      type: "function",
      function: {
        name: t.name,
        description: t.description,
        parameters: t.inputSchema,
      },
    }));
  }

  /**
   * Invoke a tool over the real MCP transport. `requestId` rides in
   * `params._meta` so server-side logs correlate with this client's
   * events. Timeouts, unknown tools and malformed payloads are normalized
   * into typed outcomes instead of throwing.
   */
  async call(
    name: string,
    args: Record<string, unknown>,
    requestId: string,
  ): Promise<ToolOutcome> {
    const started = performance.now();
    const durationMs = (): number => Math.round(performance.now() - started);
    try {
      const result = await this.client.callTool(
        { name, arguments: args, _meta: { requestId } },
        undefined,
        { timeout: this.callTimeoutMs },
      );
      const text = extractText(result);
      if (result.isError === true) {
        // The SDK reports unregistered tools as an isError result
        // ("MCP error -32602: Tool X not found"), not a thrown McpError.
        const notFound = /-3260[12]|tool .+ not found/i.test(text);
        return {
          ok: false,
          text,
          failure: notFound ? "unknown_tool" : "server_error",
          durationMs: durationMs(),
        };
      }
      if (text === "") {
        return {
          ok: false,
          text: "(empty tool response)",
          failure: "malformed",
          durationMs: durationMs(),
        };
      }
      return { ok: true, text, durationMs: durationMs() };
    } catch (err) {
      if (err instanceof McpError) {
        if (err.code === Number(ErrorCode.RequestTimeout)) {
          return {
            ok: false,
            text: `tool call timed out after ${this.callTimeoutMs}ms`,
            failure: "timeout",
            durationMs: durationMs(),
          };
        }
        if (err.code === Number(ErrorCode.MethodNotFound)) {
          return {
            ok: false,
            text: `unknown tool: ${name}`,
            failure: "unknown_tool",
            durationMs: durationMs(),
          };
        }
      }
      const message = err instanceof Error ? err.message : String(err);
      return {
        ok: false,
        text: `tool call failed: ${message}`,
        failure: "server_error",
        durationMs: durationMs(),
      };
    }
  }

  async close(): Promise<void> {
    await this.client.close();
  }
}

function extractText(result: unknown): string {
  const content = (result as { content?: unknown }).content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((c): c is { type: "text"; text: string } => {
      const rec = c as Record<string, unknown>;
      return rec.type === "text" && typeof rec.text === "string";
    })
    .map((c) => c.text)
    .join("\n");
}
