import { describe, expect, it } from "vitest";
import {
  STATUS_LABEL,
  argsAreCode,
  argsSummary,
  errorCodeLabel,
  providerLabel,
  resultSummary,
  stepLabel,
  toolMeta,
  traceLabel,
} from "./labels";
import { humanizeBytes, humanizeDuration } from "./format";

describe("toolMeta", () => {
  it("covers every backend tool with a friendly label and icon", () => {
    const labels: Record<string, string> = {
      web_search: "Web search",
      db_query: "Database query",
      read_file: "File read",
      write_file: "File write",
    };
    for (const [name, label] of Object.entries(labels)) {
      const meta = toolMeta(name);
      expect(meta.label).toBe(label);
      expect(meta.Icon).toBeDefined();
      expect(meta.tileClass).toBeTruthy();
    }
  });

  it("humanizes unknown tools instead of showing the raw id", () => {
    expect(toolMeta("custom_lookup").label).toBe("Custom lookup");
    expect(toolMeta("snake_and_camelCase").label).toBe(
      "Snake and camel Case",
    );
    expect(toolMeta("").label).toBe("Tool");
  });
});

describe("STATUS_LABEL", () => {
  it("maps every status to sentence-case text — never raw flags", () => {
    expect(STATUS_LABEL).toEqual({
      running: "Running",
      ok: "Success",
      error: "Error",
    });
  });
});

describe("errorCodeLabel", () => {
  it("covers every backend error code", () => {
    expect(errorCodeLabel("invalid_input")).toBe("Invalid input");
    expect(errorCodeLabel("forbidden")).toBe("Blocked by security policy");
    expect(errorCodeLabel("not_found")).toBe("Not found");
    expect(errorCodeLabel("rate_limited")).toBe("Rate limit reached");
    expect(errorCodeLabel("upstream")).toBe("External service unavailable");
    expect(errorCodeLabel("internal")).toBe("Unexpected error");
  });

  it("never returns the raw code for unknown values", () => {
    expect(errorCodeLabel("some_new_code")).toBe("Unexpected error");
  });
});

describe("providerLabel", () => {
  it("maps provider ids to readable names", () => {
    expect(providerLabel("tavily")).toBe("Tavily");
    expect(providerLabel("duckduckgo")).toBe("DuckDuckGo");
    expect(providerLabel("none")).toBe("offline mode");
    expect(providerLabel("something_else")).toBe("unknown");
    expect(providerLabel(42)).toBe("unknown");
  });
});

describe("stepLabel / traceLabel", () => {
  it("renders 'Step N' and an 8-char trace abbreviation", () => {
    expect(stepLabel(3)).toBe("Step 3");
    expect(traceLabel("abcdef12-3456-7890")).toBe("abcdef12");
  });
});

describe("argsSummary", () => {
  it("summarizes each tool's args in prose — never a JSON dump", () => {
    expect(
      argsSummary("web_search", { query: "mcp spec", max_results: 5 }),
    ).toBe('"mcp spec" · up to 5 results');
    expect(argsSummary("web_search", { query: "mcp spec" })).toBe(
      '"mcp spec"',
    );
    expect(argsSummary("db_query", { query: "SELECT 1" })).toBe("SELECT 1");
    expect(argsSummary("read_file", { path: "notes/a.md" })).toBe(
      "notes/a.md",
    );
    expect(
      argsSummary("write_file", { path: "a.md", content: "hi" }),
    ).toBe("a.md · 0.0 KB of content");
    expect(argsSummary("unknown_tool", { x: 1 })).toBe("");
  });
});

describe("argsAreCode", () => {
  it("flags SQL and file tools as code-styled summaries", () => {
    expect(argsAreCode("db_query")).toBe(true);
    expect(argsAreCode("read_file")).toBe(true);
    expect(argsAreCode("write_file")).toBe(true);
    expect(argsAreCode("web_search")).toBe(false);
  });
});

describe("resultSummary", () => {
  it("summarizes each tool's result without raw field names", () => {
    expect(
      resultSummary({
        tool: "web_search",
        status: "ok",
        result: { status: "ok", data: { results: [{}, {}, {}] } },
      }),
    ).toBe("3 results");
    expect(
      resultSummary({
        tool: "db_query",
        status: "ok",
        result: { status: "ok", data: { row_count: 1, rows: [{}] } },
      }),
    ).toBe("1 row returned");
    expect(
      resultSummary({
        tool: "read_file",
        status: "ok",
        result: { status: "ok", data: { bytes: 1900 } },
      }),
    ).toBe("1.9 KB");
    expect(
      resultSummary({
        tool: "write_file",
        status: "ok",
        result: { status: "ok", data: { bytes_written: 2048 } },
      }),
    ).toBe("2.0 KB written");
  });

  it("maps error envelopes to friendly labels", () => {
    expect(
      resultSummary({
        tool: "db_query",
        status: "error",
        result: { status: "error", code: "forbidden", message: "no" },
      }),
    ).toBe("Blocked by security policy");
    expect(
      resultSummary({ tool: "db_query", status: "error" }),
    ).toBe("Failed");
  });
});

describe("humanize helpers", () => {
  it("formats durations", () => {
    expect(humanizeDuration(340)).toBe("340 ms");
    expect(humanizeDuration(1200)).toBe("1.2 s");
    expect(humanizeDuration(12_000)).toBe("12 s");
    expect(humanizeDuration(95_000)).toBe("1 min 35 s");
    expect(humanizeDuration(-1)).toBe("—");
  });

  it("formats byte sizes", () => {
    expect(humanizeBytes(300)).toBe("300 B");
    expect(humanizeBytes(1900)).toBe("1.9 KB");
    expect(humanizeBytes(2_500_000)).toBe("2.4 MB");
  });
});
