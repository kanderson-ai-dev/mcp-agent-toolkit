import { describe, expect, it } from "vitest";
import type { ToolHandlerExtra } from "../../src/server/observability/request-context.js";
import { extractRequestId } from "../../src/server/observability/request-context.js";
import { createLogger } from "../../src/server/observability/logger.js";
import { connectedPair, outcomeData, testConfig } from "../helpers.js";

describe("server wiring over InMemoryTransport (real protocol)", () => {
  it("exposes exactly the four tools with zod-derived schemas", async () => {
    const { client, close } = await connectedPair(testConfig());
    try {
      const tools = client.listTools();
      expect(tools.map((t) => t.name).sort()).toEqual([
        "db_query",
        "read_file",
        "web_search",
        "write_file",
      ]);
      for (const t of tools) {
        expect(t.inputSchema.type).toBe("object");
        expect(t.description.length).toBeGreaterThan(10);
      }
      const openaiTools = client.toOpenAITools();
      expect(openaiTools).toHaveLength(4);
      expect(openaiTools[0]?.type).toBe("function");
      expect(openaiTools[0]?.function.name).toBe(tools[0]?.name);
    } finally {
      await close();
    }
  });

  it("invokes a tool end-to-end and returns the ok envelope", async () => {
    const { client, close } = await connectedPair(testConfig());
    try {
      const outcome = await client.call("web_search", { query: "mcp", max_results: 2 }, "req-1");
      expect(outcome.ok).toBe(true);
      const data = outcomeData<{ provider: string; results: unknown[] }>(outcome);
      expect(data.provider).toBe("none");
      expect(data.results).toHaveLength(2);
    } finally {
      await close();
    }
  });

  it("surfaces zod validation errors as isError results", async () => {
    const { client, close } = await connectedPair(testConfig());
    try {
      const outcome = await client.call("web_search", { query: "" }, "req-bad");
      expect(outcome.ok).toBe(false);
      expect(outcome.text).toMatch(/too small|invalid|expected/i);
    } finally {
      await close();
    }
  });

  it("reports unknown tools as unknown_tool", async () => {
    const { client, close } = await connectedPair(testConfig());
    try {
      const outcome = await client.call("nope_tool", {}, "req-x");
      expect(outcome.ok).toBe(false);
      expect(outcome.failure).toBe("unknown_tool");
    } finally {
      await close();
    }
  });

  it("enforces the per-tool rate limit", async () => {
    const { client, close } = await connectedPair(
      testConfig({ RATE_LIMIT_PER_MINUTE: "2" }),
    );
    try {
      expect((await client.call("web_search", { query: "a" }, "r1")).ok).toBe(true);
      expect((await client.call("web_search", { query: "b" }, "r2")).ok).toBe(true);
      const third = await client.call("web_search", { query: "c" }, "r3");
      expect(third.ok).toBe(false);
      expect(third.text).toContain("rate_limited");
    } finally {
      await close();
    }
  });
});

describe("extractRequestId", () => {
  const fakeExtra = (meta?: Record<string, unknown>): ToolHandlerExtra =>
    ({ requestId: 42, _meta: meta }) as unknown as ToolHandlerExtra;

  it("prefers the client-supplied _meta.requestId", () => {
    expect(extractRequestId(fakeExtra({ requestId: "uuid-123" }))).toBe("uuid-123");
  });

  it("falls back to the JSON-RPC request id", () => {
    expect(extractRequestId(fakeExtra())).toBe("42");
    expect(extractRequestId(fakeExtra({ requestId: "" }))).toBe("42");
    expect(extractRequestId(fakeExtra({ requestId: 7 }))).toBe("42");
  });
});

describe("logger", () => {
  it("builds a redacting JSON logger on stderr", () => {
    const logger = createLogger("error", "test-svc");
    expect(logger).toBeDefined();
    expect(typeof logger.info).toBe("function");
  });
});
