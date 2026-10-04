import type { AgentMessage, LLMClient, LLMResponse, OpenAIToolDef } from "./types.js";

let seq = 0;
const nextId = (): string => `stubcall_${++seq}`;

/**
 * Deterministic stand-in for a real LLM. Replays a fixed tool-call plan —
 * web_search + db_query in parallel, then write_file, then a final
 * answer — so the whole agent loop is exercisable offline, in tests, and
 * in CI with zero secrets.
 *
 * Input-aware branches keep determinism while exercising the guardrail
 * and error-recovery paths end-to-end: questions containing a path
 * traversal or `DROP TABLE` deliberately attempt the dangerous call so
 * the server's typed `forbidden` error renders in the UI; questions
 * asking for a "citation" count trigger a nonexistent-column error and
 * then a corrected retry.
 */
export class StubLLM implements LLMClient {
  readonly name = "stub";

  complete(messages: AgentMessage[], tools: OpenAIToolDef[]): Promise<LLMResponse> {
    const toolRounds = messages.filter((m) => m.role === "tool").length;
    const has = (name: string): boolean => tools.some((t) => t.function.name === name);
    const userQuestion =
      [...messages].reverse().find((m) => m.role === "user" && "content" in m)?.content ??
      "the user's question";
    const question = String(userQuestion);
    const adversarial =
      /\.\.[\\/]|etc\/passwd/i.test(question) || /\bDROP\s+TABLE\b/i.test(question);
    const toolCallsBy = (name: string): number =>
      messages.filter((m) => m.role === "tool" && m.name === name).length;

    if (toolRounds === 0) {
      // Adversarial inputs: attempt the dangerous call — the guardrail
      // must reject it (asserted in e2e + security suites).
      if (/\.\.[\\/]|etc\/passwd/i.test(question) && has("read_file")) {
        return Promise.resolve({
          content: null,
          toolCalls: [
            {
              id: nextId(),
              name: "read_file",
              arguments: JSON.stringify({ path: "../../etc/passwd" }),
            },
          ],
        });
      }
      if (/\bDROP\s+TABLE\b/i.test(question) && has("db_query")) {
        return Promise.resolve({
          content: null,
          toolCalls: [
            {
              id: nextId(),
              name: "db_query",
              arguments: JSON.stringify({ query: "DROP TABLE reports" }),
            },
          ],
        });
      }
      // Error-recovery input: query a column that does not exist, then
      // correct itself on the next round (see below).
      if (/citation/i.test(question) && has("db_query")) {
        return Promise.resolve({
          content: null,
          toolCalls: [
            {
              id: nextId(),
              name: "db_query",
              arguments: JSON.stringify({
                query: "SELECT title, citations FROM reports ORDER BY published_at DESC",
              }),
            },
          ],
        });
      }
      const calls = [];
      if (has("web_search")) {
        calls.push({
          id: nextId(),
          name: "web_search",
          arguments: JSON.stringify({ query: question.slice(0, 120), max_results: 3 }),
        });
      }
      if (has("db_query")) {
        calls.push({
          id: nextId(),
          name: "db_query",
          arguments: JSON.stringify({
            query: "SELECT id, title, topic, author, published_at FROM reports ORDER BY published_at DESC LIMIT 5",
          }),
        });
      }
      if (calls.length > 0) {
        return Promise.resolve({ content: null, toolCalls: calls });
      }
    }

    // Error-recovery: retry once with real columns after the bad query.
    if (
      toolRounds === 1 &&
      /citation/i.test(question) &&
      toolCallsBy("db_query") === 1 &&
      has("db_query")
    ) {
      return Promise.resolve({
        content: null,
        toolCalls: [
          {
            id: nextId(),
            name: "db_query",
            arguments: JSON.stringify({
              query: "SELECT title, author, published_at FROM reports ORDER BY published_at DESC LIMIT 5",
            }),
          },
        ],
      });
    }

    if (toolRounds > 0 && has("write_file") && !alreadyWrote(messages) && !adversarial) {
      return Promise.resolve({
        content: null,
        toolCalls: [
          {
            id: nextId(),
            name: "write_file",
            arguments: JSON.stringify({
              path: "reports/research-summary.md",
              content: buildStubReport(messages),
            }),
          },
        ],
      });
    }

    return Promise.resolve({
      content: adversarial
        ? "Stub answer: the requested operation was rejected by the server's " +
            "guardrails; nothing unsafe was executed. (Run with OPENAI_API_KEY " +
            "set for a live model.)"
        : "Stub answer: gathered evidence via MCP tools and persisted a report " +
            "to reports/research-summary.md inside the sandbox. (Run with " +
            "OPENAI_API_KEY set for a live model.)",
      toolCalls: [],
    });
  }
}

function alreadyWrote(messages: AgentMessage[]): boolean {
  return messages.some((m) => m.role === "tool" && m.name === "write_file");
}

function buildStubReport(messages: AgentMessage[]): string {
  const collected = messages
    .filter((m) => m.role === "tool")
    .map((m) => `## ${m.name}\n\n${m.content.slice(0, 800)}\n`)
    .join("\n");
  return [
    "# Research summary (stub run)",
    "",
    "Generated by the deterministic StubLLM — no OPENAI_API_KEY present.",
    "Each section below is raw output collected over the MCP protocol.",
    "",
    collected,
  ].join("\n");
}
