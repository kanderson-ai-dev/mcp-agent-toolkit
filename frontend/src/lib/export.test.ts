import { describe, expect, it } from "vitest";
import { runToJson, runToMarkdown } from "./export";
import type { RunRecord } from "../types";

const RUN: RunRecord = {
  id: "run-1",
  question: "List the reports",
  steps: [
    {
      kind: "tool",
      requestId: "req-ab12",
      iteration: 1,
      tool: "db_query",
      args: { query: "SELECT title FROM reports" },
      status: "ok",
      durationMs: 42,
      result: { status: "ok", data: { row_count: 3, rows: [{}, {}, {}] } },
    },
    { kind: "note", iteration: 1, content: "checking the catalog first" },
  ],
  final: {
    content: "Three reports found.",
    iterations: 2,
    toolCalls: 1,
    terminatedBy: "answer",
    durationMs: 2100,
    usage: { promptTokens: 900, completionTokens: 100, totalTokens: 1000 },
    promptVersion: { name: "agent_system", version: 3, sha256: "abc" },
  },
  elapsedMs: 2200,
};

describe("runToMarkdown", () => {
  it("produces a readable transcript with friendly wording", () => {
    const md = runToMarkdown(RUN);
    expect(md).toContain("# List the reports");
    expect(md).toContain("**Database query** — Success");
    expect(md).toContain("`SELECT title FROM reports`"); // code args stay code
    expect(md).toContain("3 rows returned");
    expect(md).toContain("> checking the catalog first");
    expect(md).toContain("## Final answer\n\nThree reports found.");
    expect(md).toContain("~1.0k tokens");
    expect(md).toContain("Prompt v3");
  });

  it("never leaks internal field names", () => {
    const md = runToMarkdown(RUN);
    for (const lit of [
      "durationMs",
      "requestId",
      "toolCalls",
      "terminatedBy",
      "db_query",
      "row_count",
      "req-ab12",
    ]) {
      expect(md).not.toContain(lit);
    }
  });

  it("exports an errored run honestly", () => {
    const md = runToMarkdown({ ...RUN, final: undefined, errorMessage: "down" });
    expect(md).toContain("## Error\n\ndown");
    expect(md).not.toContain("Final answer\n\n");
  });
});

describe("runToJson", () => {
  it("emits friendly-labelled structured data", () => {
    const doc = JSON.parse(runToJson(RUN)) as {
      question: string;
      steps: Array<Record<string, unknown>>;
      answer?: string;
      promptVersion?: number;
    };
    expect(doc.question).toBe("List the reports");
    expect(doc.steps).toMatchObject([
      {
        type: "step",
        name: "Database query",
        status: "Success",
        outcome: "3 rows returned",
      },
      { type: "note", content: "checking the catalog first" },
    ]);
    expect(doc.answer).toBe("Three reports found.");
    expect(doc.promptVersion).toBe(3);
  });

  it("keeps internal identifiers out of the payload", () => {
    const text = runToJson(RUN);
    for (const lit of ["durationMs", "requestId", "req-ab12", "db_query"]) {
      expect(text).not.toContain(lit);
    }
  });
});
