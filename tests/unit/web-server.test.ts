import { EventEmitter } from "node:events";
import type { Request, Response } from "express";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import request from "supertest";
import type { Express } from "express";
import { StubLLM } from "../../src/client/llm/stub-llm.js";
import type { AgentMessage, LLMClient, LLMResponse, OpenAIToolDef } from "../../src/client/llm/types.js";
import type { ToolInvoker, ToolOutcome } from "../../src/client/mcp-client.js";
import { createApp, handleStream } from "../../src/web/server.js";
import { RunStore } from "../../src/web/run-store.js";
import { connectedPair, testConfig } from "../helpers.js";

/** An LLM that always fails — exercises the stream's error-SSE branch. */
class ThrowingLLM implements LLMClient {
  readonly name = "throwing";
  complete(_messages: AgentMessage[], _tools: OpenAIToolDef[]): Promise<LLMResponse> {
    return Promise.reject(new Error("upstream LLM exploded"));
  }
}

/** A no-op invoker for tests that never actually call a tool. */
const noopInvoker: ToolInvoker = {
  listTools: () => [],
  call: (): Promise<ToolOutcome> => {
    throw new Error("not expected to be called in this test");
  },
};

interface SseEvent {
  event: string;
  data: Record<string, unknown>;
}

/** Narrow a supertest JSON body (typed `any`) without `unsafe-*` lint noise. */
function jsonBody(body: unknown): Record<string, unknown> {
  return body as Record<string, unknown>;
}

/** Parse a raw SSE response body into `{event, data}` records. */
function parseSse(raw: string): SseEvent[] {
  return raw
    .split("\n\n")
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      const eventLine = block.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = block.split("\n").find((l) => l.startsWith("data: "));
      return {
        event: eventLine?.slice("event: ".length) ?? "",
        data: JSON.parse(dataLine?.slice("data: ".length) ?? "{}") as Record<string, unknown>,
      };
    });
}

describe("web server (Express adapter)", () => {
  let app: Express;
  let close: () => Promise<void>;

  beforeAll(async () => {
    const config = testConfig();
    const pair = await connectedPair(config);
    close = pair.close;
    app = createApp({
      invoker: pair.client,
      tools: pair.client.toOpenAITools(),
      discoveredTools: pair.client.listTools(),
      llm: new StubLLM(),
      maxIterations: config.MAX_TOOL_ITERATIONS,
    });
  });

  afterAll(async () => {
    await close();
  });

  it("GET /api/health responds ok", async () => {
    const res = await request(app).get("/api/health");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ status: "ok" });
  });

  it("GET /api/tools lists the discovered tools and llm provider", async () => {
    const res = await request(app).get("/api/tools");
    expect(res.status).toBe(200);
    const body = jsonBody(res.body);
    const names = (body.tools as { name: string }[]).map((t) => t.name).sort();
    expect(names).toEqual(["db_query", "read_file", "web_search", "write_file"]);
    expect(body.llm).toEqual({ provider: "stub" });
  });

  it("POST /api/chat rejects an empty question", async () => {
    const res = await request(app).post("/api/chat").send({ question: "" });
    expect(res.status).toBe(400);
    expect(jsonBody(res.body).error).toBeTruthy();
  });

  it("POST /api/chat rejects an oversized question", async () => {
    const res = await request(app)
      .post("/api/chat")
      .send({ question: "x".repeat(2001) });
    expect(res.status).toBe(400);
  });

  it("POST /api/chat rejects a missing field", async () => {
    const res = await request(app).post("/api/chat").send({});
    expect(res.status).toBe(400);
  });

  it("POST /api/chat accepts a valid question and returns a run id", async () => {
    const res = await request(app).post("/api/chat").send({ question: "What reports exist?" });
    expect(res.status).toBe(201);
    const runId = jsonBody(res.body).runId;
    expect(typeof runId).toBe("string");
    expect((runId as string).length).toBeGreaterThan(0);
  });

  it("GET /api/chat/stream streams the full agent run as SSE events", async () => {
    const created = await request(app)
      .post("/api/chat")
      .send({ question: "Which reports exist in the database?" });
    const runId = jsonBody(created.body).runId as string;

    const res = await request(app).get("/api/chat/stream").query({ runId });
    expect(res.status).toBe(200);
    expect(res.headers["content-type"]).toContain("text/event-stream");

    const events = parseSse(res.text);
    const types = events.map((e) => e.event);
    expect(types).toContain("tool_call");
    expect(types).toContain("tool_result");
    expect(types).toContain("final");

    const final = events.find((e) => e.event === "final");
    expect(typeof final?.data.content).toBe("string");
    expect((final?.data.content as string).length).toBeGreaterThan(0);
  });

  it("GET /api/chat/stream 404s on an unknown run id", async () => {
    const res = await request(app).get("/api/chat/stream").query({ runId: "nonexistent" });
    expect(res.status).toBe(404);
  });

  it("GET /api/chat/stream emits an error SSE event when the agent loop throws", async () => {
    const failingApp = createApp({
      invoker: noopInvoker,
      tools: [],
      discoveredTools: [],
      llm: new ThrowingLLM(),
      maxIterations: 8,
    });
    const created = await request(failingApp).post("/api/chat").send({ question: "boom?" });
    const runId = jsonBody(created.body).runId as string;

    const res = await request(failingApp).get("/api/chat/stream").query({ runId });
    expect(res.status).toBe(200);
    const events = parseSse(res.text);
    expect(events).toEqual([{ event: "error", data: { type: "error", message: "upstream LLM exploded" } }]);
  });

  it("sets Access-Control-Allow-Origin only for the configured dev origin", async () => {
    const corsApp = createApp({
      invoker: noopInvoker,
      tools: [],
      discoveredTools: [],
      llm: new StubLLM(),
      maxIterations: 8,
      corsOrigin: "http://localhost:5173",
    });

    const allowed = await request(corsApp)
      .get("/api/health")
      .set("Origin", "http://localhost:5173");
    expect(allowed.headers["access-control-allow-origin"]).toBe("http://localhost:5173");

    const other = await request(corsApp).get("/api/health").set("Origin", "https://evil.example");
    expect(other.headers["access-control-allow-origin"]).toBeUndefined();
  });

  it("stops emitting once the client disconnects mid-stream", async () => {
    const runs = new RunStore();
    const runId = runs.create("anything");
    const req = Object.assign(new EventEmitter(), { query: { runId } });
    const writes: string[] = [];
    const fakeRes = {
      writableEnded: false,
      status: () => undefined,
      setHeader: () => undefined,
      flushHeaders: () => undefined,
      write: (chunk: string) => {
        writes.push(chunk);
      },
      end: () => undefined,
    };

    const pending = handleStream(
      req as unknown as Request,
      fakeRes as unknown as Response,
      { invoker: noopInvoker, tools: [], discoveredTools: [], llm: new StubLLM(), maxIterations: 8 },
      runs,
    );
    // The "close" listener is registered synchronously before the agent
    // loop's first await — emitting it here lands before any SSE write.
    req.emit("close");
    await pending;

    expect(writes).toEqual([]);
  });

  it("GET /api/chat/stream run ids are single-use", async () => {
    const created = await request(app).post("/api/chat").send({ question: "Single use test?" });
    const runId = jsonBody(created.body).runId as string;

    const first = await request(app).get("/api/chat/stream").query({ runId });
    expect(first.status).toBe(200);

    const second = await request(app).get("/api/chat/stream").query({ runId });
    expect(second.status).toBe(404);
  });
});
