import { describe, expect, it, vi } from "vitest";
import type { Response } from "express";
import { writeSseEvent } from "../../src/web/sse.js";

describe("writeSseEvent", () => {
  it("writes the event type and JSON payload as SSE lines", () => {
    const write = vi.fn();
    const res = { writableEnded: false, write } as unknown as Response;
    writeSseEvent(res, { type: "llm_message", iteration: 1, content: "hi" });
    expect(write).toHaveBeenCalledWith("event: llm_message\n");
    expect(write).toHaveBeenCalledWith(
      `data: ${JSON.stringify({ type: "llm_message", iteration: 1, content: "hi" })}\n\n`,
    );
  });

  it("is a no-op once the response has already ended", () => {
    const write = vi.fn();
    const res = { writableEnded: true, write } as unknown as Response;
    writeSseEvent(res, { type: "error", message: "too late" });
    expect(write).not.toHaveBeenCalled();
  });
});
