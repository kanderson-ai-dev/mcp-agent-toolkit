import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ToolStepCard } from "./ToolStepCard";
import type { ToolStep } from "../types";

const BASE: ToolStep = {
  kind: "tool",
  requestId: "req-1234-abcd",
  iteration: 2,
  tool: "db_query",
  args: { query: "SELECT title FROM reports" },
  status: "running",
};

describe("ToolStepCard states", () => {
  it("running: friendly name, annotation hints, Running badge, no result control", () => {
    render(<ToolStepCard step={BASE} />);
    expect(screen.getByText("Database query")).toBeInTheDocument();
    // MCP annotation → human chip; the raw hint names never render.
    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.queryByText(/readOnlyHint/)).not.toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("Step 2")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /^result/i }),
    ).not.toBeInTheDocument();
    // The SQL summary renders as code; the raw requestId never appears.
    expect(screen.getByText("SELECT title FROM reports")).toBeInTheDocument();
    expect(screen.queryByText("req-1234-abcd")).not.toBeInTheDocument();
  });

  it("ok: Success badge, humanized duration, result summary", () => {
    render(
      <ToolStepCard
        step={{
          ...BASE,
          status: "ok",
          durationMs: 1234,
          result: {
            status: "ok",
            data: { row_count: 2, rows: [{}, {}] },
          },
        }}
      />,
    );
    expect(screen.getByText("Success")).toBeInTheDocument();
    expect(screen.getByText("1.2 s")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /result: 2 rows returned/i }),
    ).toBeInTheDocument();
    // Raw field names stay hidden.
    expect(screen.queryByText(/row_count/)).not.toBeInTheDocument();
    expect(screen.queryByText(/durationMs/)).not.toBeInTheDocument();
  });

  it("error: Error badge plus the friendly error-code label", () => {
    render(
      <ToolStepCard
        step={{
          ...BASE,
          status: "error",
          durationMs: 12,
          result: {
            status: "error",
            code: "forbidden",
            message: "statement rejected",
          },
        }}
      />,
    );
    expect(screen.getByText("Error")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: /result: blocked by security policy/i,
      }),
    ).toBeInTheDocument();
  });

  it("expands the result panel on demand", async () => {
    const user = userEvent.setup();
    render(
      <ToolStepCard
        step={{
          ...BASE,
          status: "ok",
          result: {
            status: "ok",
            data: { row_count: 1, rows: [{ title: "Q3 report" }] },
          },
        }}
      />,
    );
    await user.click(screen.getByRole("button", { name: /result/i }));
    expect(screen.getByText("Q3 report")).toBeInTheDocument();
  });

  it("shows each tool's hint vocabulary, not raw annotation keys", () => {
    const { unmount } = render(
      <ToolStepCard step={{ ...BASE, tool: "web_search" }} />,
    );
    expect(screen.getByText("Read-only")).toBeInTheDocument();
    expect(screen.getByText("Reaches the internet")).toBeInTheDocument();
    unmount();

    render(<ToolStepCard step={{ ...BASE, tool: "write_file" }} />);
    expect(screen.getByText("Writes files")).toBeInTheDocument();
    expect(screen.getByText("Overwrites")).toBeInTheDocument();
    // Write tools are not read-only — no stale chip.
    expect(screen.queryByText("Read-only")).not.toBeInTheDocument();
  });
});
