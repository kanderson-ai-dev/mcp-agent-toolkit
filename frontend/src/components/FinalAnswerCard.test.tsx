import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FinalAnswerCard } from "./FinalAnswerCard";
import type { FinalResult } from "../types";

const FINAL: FinalResult = {
  content: "The **answer** is 42.",
  iterations: 3,
  toolCalls: 2,
  terminatedBy: "answer",
};

describe("FinalAnswerCard", () => {
  it("renders the answer as Markdown with a human footer", () => {
    render(<FinalAnswerCard final={FINAL} elapsedMs={65_000} />);
    expect(screen.getByText("Final answer")).toBeInTheDocument();
    expect(document.querySelector("strong")).toHaveTextContent("answer");
    expect(screen.getByText(/3 steps/)).toBeInTheDocument();
    expect(screen.getByText(/2 tool calls/)).toBeInTheDocument();
    expect(screen.getByText(/1 min 5 s/)).toBeInTheDocument();
    expect(screen.queryByText(/toolCalls/)).not.toBeInTheDocument();
  });

  it("warns when the run hit the step limit", () => {
    render(
      <FinalAnswerCard
        final={{ ...FINAL, terminatedBy: "iteration_limit" }}
      />,
    );
    expect(screen.getByText(/step limit reached/i)).toBeInTheDocument();
    expect(screen.queryByText(/iteration_limit/)).not.toBeInTheDocument();
  });

  it("never activates hostile markdown: script, javascript: link, img onerror", () => {
    const hostile = [
      '<script>window.__pwned = true</script>',
      '[click me](javascript:alert(1))',
      '<img src=x onerror="window.__pwned = true">',
    ].join("\n\n");
    const { container } = render(
      <FinalAnswerCard final={{ ...FINAL, content: hostile }} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    const link = container.querySelector("a");
    if (link) expect(link.href ?? "").not.toMatch(/^javascript:/i);
    expect(
      (window as unknown as Record<string, unknown>).__pwned,
    ).toBeUndefined();
  });

  it("renders citation links with rel=noreferrer and target=_blank", () => {
    render(
      <FinalAnswerCard
        final={{ ...FINAL, content: "[source](https://example.com/a)" }}
      />,
    );
    const link = screen.getByRole("link", { name: "source" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noreferrer");
  });
});
