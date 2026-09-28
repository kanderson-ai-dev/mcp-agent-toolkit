import fs from "node:fs/promises";
import path from "node:path";
import { z } from "zod";
import { ToolError, okResult } from "../../shared/result.js";
import { realpathInSandbox, resolveInSandbox } from "../guardrails/path-sandbox.js";
import type { ToolDefinition } from "./types.js";

/**
 * `write_file` — persist UTF-8 text (typically the agent's final report)
 * inside the sandbox root only. Same path-sandbox guarantees as
 * `read_file`, plus a per-write byte cap.
 */
export function createWriteFileTool(
  sandboxRoot: string,
  maxBytes: number,
): ToolDefinition<{ path: z.ZodString; content: z.ZodString }> {
  return {
    name: "write_file",
    description:
      "Write UTF-8 text to a file inside the sandboxed workspace directory " +
      "(creates parent directories; overwrites existing files). " +
      "Use it to persist the final report or working notes.",
    inputSchema: {
      path: z
        .string()
        .min(1)
        .max(500)
        .describe("Destination path relative to the sandbox, e.g. reports/out.md"),
      content: z.string().min(0).describe("UTF-8 text to write"),
    },
    handler: async (args) => {
      const bytes = Buffer.byteLength(args.content, "utf8");
      if (bytes > maxBytes) {
        throw new ToolError(
          "invalid_input",
          `Content too large: ${bytes} bytes (limit ${maxBytes})`,
        );
      }
      const resolved = resolveInSandbox(sandboxRoot, args.path);
      const canonical = realpathInSandbox(sandboxRoot, resolved);
      await fs.mkdir(path.dirname(canonical), { recursive: true });
      await fs.writeFile(canonical, args.content, "utf8");
      return okResult({ path: path.relative(sandboxRoot, canonical), bytes_written: bytes });
    },
  };
}
