import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RunView } from "./RunView";
import type { RunRecord } from "../types";

const BASE: RunRecord = {
  id: "r1",
  question: "What is MCP?",
  steps: [],
};

describe("RunView streaming answer", () => {
  it("renders the provisional card while deltas accumulate", () => {
    render(<RunView run={{ ...BASE, streamingAnswer: "Hello wor" }} />);
    expect(
      screen.getByRole("heading", { name: "Final answer" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Streaming")).toBeInTheDocument();
    expect(screen.getByText("Hello wor")).toBeInTheDocument();
    // No telemetry footer yet — that belongs to the real final card.
    expect(screen.queryByText(/tokens/)).not.toBeInTheDocument();
  });

  it("the real final card replaces the provisional one", () => {
    render(
      <RunView
        run={{
          ...BASE,
          streamingAnswer: undefined,
          final: {
            content: "Hello world",
            iterations: 1,
            toolCalls: 0,
            terminatedBy: "answer",
          },
        }}
      />,
    );
    expect(screen.getByText("Hello world")).toBeInTheDocument();
    expect(screen.queryByText("Streaming")).not.toBeInTheDocument();
    expect(screen.getByText(/no tool calls/)).toBeInTheDocument();
  });

  it("markdown in the provisional card stays sanitized", () => {
    render(
      <RunView
        run={{
          ...BASE,
          streamingAnswer: "ok <script>alert(1)</script>",
        }}
      />,
    );
    expect(document.querySelector("script")).toBeNull();
  });
});

describe("RunView export controls", () => {
  it("shows export buttons once a run finished", () => {
    render(
      <RunView
        run={{
          ...BASE,
          final: {
            content: "done",
            iterations: 1,
            toolCalls: 0,
            terminatedBy: "answer",
          },
        }}
      />,
    );
    expect(
      screen.getByRole("button", { name: /markdown/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /json/i })).toBeInTheDocument();
  });

  it("hides export while the run is in flight", () => {
    render(<RunView run={{ ...BASE, streamingAnswer: "partial" }} />);
    expect(
      screen.queryByRole("button", { name: /markdown/i }),
    ).not.toBeInTheDocument();
  });
});
