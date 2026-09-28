import { describe, expect, it } from "vitest";
import { sanitizeToolOutput } from "../../src/server/guardrails/sanitize.js";

const ch = (n: number): string => String.fromCharCode(n);

describe("tool-output sanitization (OWASP LLM01)", () => {
  it("strips control characters but keeps whitespace and text", () => {
    const dirty =
      "line1\n" +
      ch(0x00) + // NUL
      ch(0x1b) + // ESC (ANSI)
      ch(0x07) + // BEL
      "evil text" +
      ch(0x202e) + // bidi override
      ch(0x200b); // zero-width space
    const { text, truncated } = sanitizeToolOutput(dirty, 10_000);
    expect(truncated).toBe(false);
    expect(text).toContain("line1");
    expect(text).toContain("evil text"); // instruction text stays — it's data
    expect(text).not.toContain(ch(0x00));
    expect(text).not.toContain(ch(0x1b));
    expect(text).not.toContain(ch(0x202e));
    expect(text).not.toContain(ch(0x200b));
    expect(text).toContain("\n");
  });

  it("truncates oversized output with an explicit marker", () => {
    const big = "x".repeat(50_000);
    const { text, truncated } = sanitizeToolOutput(big, 1_000);
    expect(truncated).toBe(true);
    expect(text.length).toBeLessThan(1_100);
    expect(text).toContain("truncated");
  });

  it("passes clean short text through untouched", () => {
    const { text, truncated } = sanitizeToolOutput("plain\nwith\nlines\n", 100);
    expect(truncated).toBe(false);
    expect(text).toBe("plain\nwith\nlines\n");
  });
});
