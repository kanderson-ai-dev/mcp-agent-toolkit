import { describe, expect, it } from "vitest";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import type { JSONRPCMessage } from "@modelcontextprotocol/sdk/types.js";
import { McpAgentClient } from "../../src/client/mcp-client.js";

/**
 * A minimal fake MCP transport: completes the initialize handshake
 * correctly, then replies to `tools/call` with a scripted result (or
 * never — to trigger the client-side timeout).
 */
interface JsonRpcRequestLike {
  method?: string;
  id?: string | number;
}

class FakeTransport implements Transport {
  onmessage?: (message: JSONRPCMessage) => void;
  onclose?: () => void;
  onerror?: (error: Error) => void;
  sessionId = "fake-session";

  constructor(private readonly responder: (msg: JsonRpcRequestLike) => unknown) {}

  start(): Promise<void> {
    return Promise.resolve();
  }
  close(): Promise<void> {
    return Promise.resolve();
  }
  send(message: JSONRPCMessage): Promise<void> {
    const req = message as JsonRpcRequestLike;
    if (req.method === undefined || req.method === "notifications/initialized") {
      return Promise.resolve();
    }
    const result = this.responder(req);
    if (result !== undefined && req.id !== undefined) {
      const id = req.id;
      // A responder may return { error: {...} } to emit a real JSON-RPC
      // error response — the SDK surfaces it as an McpError.
      const isError = typeof result === "object" && result !== null && "error" in result;
      queueMicrotask(() => {
        this.onmessage?.(
          (isError ? { jsonrpc: "2.0", id, error: result.error } : { jsonrpc: "2.0", id, result }) as JSONRPCMessage,
        );
      });
    }
    return Promise.resolve();
  }
}

const initResult = {
  protocolVersion: "2025-06-18",
  capabilities: {},
  serverInfo: { name: "fake", version: "0" },
};

function clientOver(responder: (msg: JsonRpcRequestLike) => unknown, timeoutMs = 500): McpAgentClient {
  return McpAgentClient.overTransport(
    new FakeTransport(responder),
    { callTimeoutMs: timeoutMs },
  );
}

describe("McpAgentClient failure normalization", () => {
  it("maps empty/malformed tool results to failure=malformed", async () => {
    const client = clientOver((msg) =>
      msg.method === "initialize"
        ? initResult
        : msg.method === "tools/list"
          ? { tools: [] }
          : { content: [] }, // schema-valid, but carries no text data
    );
    await client.connect();
    const outcome = await client.call("anything", {}, "r-mal");
    expect(outcome.ok).toBe(false);
    expect(outcome.failure).toBe("malformed");
  });

  it("maps request timeouts to failure=timeout", async () => {
    const client = clientOver((msg) =>
      msg.method === "initialize" ? initResult : msg.method === "tools/list" ? { tools: [] } : undefined,
      150,
    );
    await client.connect();
    const outcome = await client.call("slow_tool", {}, "r-timeout");
    expect(outcome.ok).toBe(false);
    expect(outcome.failure).toBe("timeout");
    expect(outcome.text).toContain("timed out");
  });

  it("maps thrown JSON-RPC MethodNotFound to failure=unknown_tool", async () => {
    const client = clientOver((msg) => {
      if (msg.method === "initialize") return initResult;
      if (msg.method === "tools/list") return { tools: [] };
      return { error: { code: -32601, message: "Method not found" } };
    });
    await client.connect();
    const outcome = await client.call("ghost_tool", {}, "r-404");
    expect(outcome.ok).toBe(false);
    expect(outcome.failure).toBe("unknown_tool");
  });

  it("maps JSON-RPC error responses to failure=server_error", async () => {
    const client = clientOver((msg) => {
      if (msg.method === "initialize") return initResult;
      if (msg.method === "tools/list") return { tools: [] };
      return { error: { code: -32000, message: "connection dropped" } };
    });
    await client.connect();
    const outcome = await client.call("x", {}, "r-err");
    expect(outcome.ok).toBe(false);
    expect(outcome.failure).toBe("server_error");
    expect(outcome.text).toContain("tool call failed");
  });
});
