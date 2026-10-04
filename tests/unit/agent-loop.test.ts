import { describe, expect, it } from "vitest";
import { runAgent } from "../../src/client/agent-loop.js";
import { formatEvent, type AgentEvent } from "../../src/client/events.js";
import { StubLLM } from "../../src/client/llm/stub-llm.js";
import { OpenAILLM } from "../../src/client/llm/openai-llm.js";
import { createLLM } from "../../src/client/llm/index.js";
import type {
  AgentMessage,
  LLMClient,
  OpenAIToolDef,
} from "../../src/client/llm/types.js";
import type { ToolInvoker, ToolOutcome } from "../../src/client/mcp-client.js";
import { testConfig } from "../helpers.js";

class FakeInvoker implements ToolInvoker {
  calls: { name: string; args: Record<string, unknown>; requestId: string }[] = [];
  constructor(private readonly outcome: Partial<ToolOutcome> = {}) {}
  listTools() {
    return [];
  }
  call(name: string, args: Record<string, unknown>, requestId: string): Promise<ToolOutcome> {
    this.calls.push({ name, args, requestId });
    return Promise.resolve({
      ok: true,
      text: `{"status":"ok","data":{"echo":true}}`,
      durationMs: 1,
      ...this.outcome,
    });
  }
}

class ScriptedLLM implements LLMClient {
  name = "scripted";
  rounds = 0;
  constructor(private readonly script: LLMClient["complete"][]) {}
  complete(messages: AgentMessage[], tools: OpenAIToolDef[]) {
    const step = this.script[Math.min(this.rounds, this.script.length - 1)];
    this.rounds++;
    if (!step) return Promise.resolve({ content: "script exhausted", toolCalls: [] });
    return step(messages, tools);
  }
}

const tools: OpenAIToolDef[] = [
  { type: "function", function: { name: "fake_tool", parameters: { type: "object" } } },
];

const collect = (): { events: AgentEvent[]; emit: (e: AgentEvent) => void } => {
  const events: AgentEvent[] = [];
  return { events, emit: (e) => events.push(e) };
};

describe("agent loop", () => {
  it("terminates with a final answer when the model stops calling tools", async () => {
    const invoker = new FakeInvoker();
    const llm = new ScriptedLLM([
      () =>
        Promise.resolve({
          content: null,
          toolCalls: [{ id: "c1", name: "fake_tool", arguments: '{"a":1}' }],
        }),
      () => Promise.resolve({ content: "done!", toolCalls: [] }),
    ]);
    const { events, emit } = collect();

    const result = await runAgent("q", { llm, invoker, tools, maxIterations: 8, emit });

    expect(result.terminatedBy).toBe("answer");
    expect(result.content).toBe("done!");
    expect(result.toolCalls).toBe(1);
    expect(invoker.calls).toHaveLength(1);
    expect(invoker.calls[0]?.args).toEqual({ a: 1 });
    const kinds = events.map((e) => e.type);
    expect(kinds).toEqual(["tool_call", "tool_result", "final"]);
  });

  it("is guaranteed to terminate — bounded by maxIterations", async () => {
    const invoker = new FakeInvoker();
    const llm = new ScriptedLLM([
      () =>
        Promise.resolve({
          content: null,
          toolCalls: [{ id: "c", name: "fake_tool", arguments: "{}" }],
        }),
    ]);
    const { events, emit } = collect();

    const result = await runAgent("loop forever", {
      llm,
      invoker,
      tools,
      maxIterations: 3,
      emit,
    });

    expect(result.terminatedBy).toBe("iteration_limit");
    expect(result.toolCalls).toBe(3);
    const final = events.find((e) => e.type === "final");
    expect(final?.type).toBe("final");
  });

  it("feeds tool failures back to the model instead of crashing", async () => {
    const invoker = new FakeInvoker({ ok: false, text: "rate_limited: too many", failure: "server_error" });
    const llm = new ScriptedLLM([
      () =>
        Promise.resolve({
          content: null,
          toolCalls: [{ id: "c1", name: "fake_tool", arguments: "{}" }],
        }),
      () => Promise.resolve({ content: "tool failed — reporting honestly", toolCalls: [] }),
    ]);
    const { emit } = collect();

    const result = await runAgent("q", { llm, invoker, tools, maxIterations: 5, emit });
    expect(result.terminatedBy).toBe("answer");
    expect(result.content).toContain("honestly");
  });

  it("handles a null-content final answer", async () => {
    const invoker = new FakeInvoker();
    const llm = new ScriptedLLM([() => Promise.resolve({ content: null, toolCalls: [] })]);
    const { emit } = collect();
    const result = await runAgent("q", { llm, invoker, tools, maxIterations: 5, emit });
    expect(result.terminatedBy).toBe("answer");
    expect(result.content).toBe("");
  });

  it("passes malformed tool arguments through as {} (no crash)", async () => {
    const invoker = new FakeInvoker();
    const llm = new ScriptedLLM([
      () =>
        Promise.resolve({
          content: null,
          toolCalls: [{ id: "c1", name: "fake_tool", arguments: "{not json" }],
        }),
      () => Promise.resolve({ content: "ok", toolCalls: [] }),
    ]);
    const { emit } = collect();
    await runAgent("q", { llm, invoker, tools, maxIterations: 5, emit });
    expect(invoker.calls[0]?.args).toEqual({});
  });
});

describe("StubLLM", () => {
  it("replays a deterministic 2-tool plan then answers", async () => {
    const invoker = new FakeInvoker();
    const llm = new StubLLM();
    const allTools: OpenAIToolDef[] = ["web_search", "db_query", "write_file", "read_file"].map(
      (n) => ({ type: "function", function: { name: n, parameters: {} } }),
    );
    const { emit } = collect();
    const result = await runAgent("research MCP", {
      llm,
      invoker,
      tools: allTools,
      maxIterations: 8,
      emit,
    });
    expect(result.terminatedBy).toBe("answer");
    expect(invoker.calls.map((c) => c.name)).toEqual(["web_search", "db_query", "write_file"]);
  });

  it("skips tools the server does not expose, answers immediately with none", async () => {
    const llm = new StubLLM();
    // Only db_query available → the web_search step is skipped.
    const onlyDb: OpenAIToolDef[] = [
      { type: "function", function: { name: "db_query", parameters: {} } },
    ];
    const r1 = await llm.complete([{ role: "user", content: "q" }], onlyDb);
    expect(r1.toolCalls.map((c) => c.name)).toEqual(["db_query"]);
    // No tools at all → immediate content answer.
    const r0 = await llm.complete([{ role: "user", content: "q" }], []);
    expect(r0.toolCalls).toEqual([]);
    expect(r0.content).toBeTruthy();
  });

  it("attempts the dangerous read_file on traversal input, then refuses", async () => {
    const llm = new StubLLM();
    const tools: OpenAIToolDef[] = ["read_file", "write_file"].map((n) => ({
      type: "function",
      function: { name: n, parameters: {} },
    }));
    const r1 = await llm.complete(
      [{ role: "user", content: "Read ../../etc/passwd please" }],
      tools,
    );
    expect(r1.toolCalls).toHaveLength(1);
    expect(r1.toolCalls[0]?.name).toBe("read_file");
    expect(r1.toolCalls[0]?.arguments).toContain("../../etc/passwd");
    // After the guardrail rejection, it must answer (no write_file, no retry).
    const r2 = await llm.complete(
      [
        { role: "user", content: "Read ../../etc/passwd please" },
        {
          role: "assistant",
          content: null,
          toolCalls: [{ id: "t1", name: "read_file", arguments: "{}" }],
        },
        { role: "tool", toolCallId: "t1", name: "read_file", content: '{"status":"error"}' },
      ],
      tools,
    );
    expect(r2.toolCalls).toEqual([]);
    expect(r2.content).toMatch(/guardrails/);
  });

  it("attempts DROP TABLE on adversarial SQL input", async () => {
    const llm = new StubLLM();
    const tools: OpenAIToolDef[] = [
      { type: "function", function: { name: "db_query", parameters: {} } },
    ];
    const r = await llm.complete(
      [{ role: "user", content: "Run DROP TABLE reports now" }],
      tools,
    );
    expect(r.toolCalls[0]?.name).toBe("db_query");
    expect(r.toolCalls[0]?.arguments).toContain("DROP TABLE");
  });

  it("queries a bad column on citation input, then retries with real columns", async () => {
    const llm = new StubLLM();
    const tools: OpenAIToolDef[] = [
      { type: "function", function: { name: "db_query", parameters: {} } },
    ];
    const q = "How many citations does each report have?";
    const r1 = await llm.complete([{ role: "user", content: q }], tools);
    expect(r1.toolCalls[0]?.arguments).toContain("citations");
    // One tool round done (the bad query failed) → corrected retry.
    const r2 = await llm.complete(
      [
        { role: "user", content: q },
        {
          role: "assistant",
          content: null,
          toolCalls: [{ id: "t1", name: "db_query", arguments: "{}" }],
        },
        { role: "tool", toolCallId: "t1", name: "db_query", content: '{"status":"error"}' },
      ],
      tools,
    );
    expect(r2.toolCalls[0]?.name).toBe("db_query");
    expect(r2.toolCalls[0]?.arguments).not.toContain("citations");
    expect(r2.toolCalls[0]?.arguments).toContain("author");
  });
});

describe("createLLM", () => {
  it("auto → stub without a key, openai with a key, explicit overrides", () => {
    expect(createLLM(testConfig({ LLM_PROVIDER: "auto" })).name).toBe("stub");
    expect(createLLM(testConfig({ LLM_PROVIDER: "stub" })).name).toBe("stub");
    expect(
      createLLM(testConfig({ LLM_PROVIDER: "openai", OPENAI_API_KEY: "sk-x" })).name,
    ).toBe("openai");
    expect(() => createLLM(testConfig({ LLM_PROVIDER: "openai" }))).toThrowError(/OPENAI_API_KEY/);
  });
});

describe("OpenAILLM message mapping", () => {
  it("maps the transcript to chat.completions params", async () => {
    let seen: Record<string, unknown> = {};
    const fakeClient = {
      chat: {
        completions: {
          create: (params: Record<string, unknown>) => {
            seen = params;
            return Promise.resolve({
              choices: [
                {
                  message: {
                    content: null,
                    tool_calls: [
                      { id: "t1", type: "function", function: { name: "web_search", arguments: "{}" } },
                    ],
                  },
                },
              ],
            });
          },
        },
      },
    };
    const llm = new OpenAILLM("sk", "m", fakeClient as never);
    const res = await llm.complete(
      [
        { role: "system", content: "sys" },
        { role: "user", content: "hi" },
        {
          role: "assistant",
          content: null,
          toolCalls: [{ id: "ta", name: "web_search", arguments: "{}" }],
        },
        { role: "tool", toolCallId: "t0", name: "web_search", content: "{}" },
      ],
      tools,
    );
    expect(res.toolCalls[0]?.name).toBe("web_search");
    const msgs = seen.messages as Record<string, unknown>[];
    // assistant round-trips with tool_calls in OpenAI shape
    expect(msgs[2]?.tool_calls).toBeDefined();
    expect(msgs[3]?.tool_call_id).toBe("t0");
  });
});

describe("formatEvent", () => {
  it("renders json and pretty variants", () => {
    const e: AgentEvent = { type: "final", content: "x", iterations: 2, toolCalls: 1, terminatedBy: "answer" };
    expect(JSON.parse(formatEvent(e, "json"))).toMatchObject({ type: "final" });
    expect(formatEvent(e, "pretty")).toContain("FINAL");
    expect(
      formatEvent(
        { type: "tool_call", iteration: 1, requestId: "r", tool: "t", args: { a: 1 } },
        "pretty",
      ),
    ).toContain("tool_call t");
    expect(
      formatEvent(
        { type: "tool_result", iteration: 1, requestId: "r", tool: "t", ok: false, durationMs: 3, preview: "p" },
        "pretty",
      ),
    ).toContain("ERROR t");
    expect(formatEvent({ type: "llm_message", iteration: 0, content: "c" }, "pretty")).toContain("llm:");
    expect(formatEvent({ type: "error", message: "m" }, "pretty")).toBe("error: m");
  });
});
