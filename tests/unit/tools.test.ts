import fs from "node:fs";
import path from "node:path";
import { z } from "zod";
import { beforeAll, describe, expect, it } from "vitest";
import { seedDemoDb } from "../../scripts/seed-db.js";
import { createDbQueryTool } from "../../src/server/tools/db-query.js";
import { createReadFileTool } from "../../src/server/tools/read-file.js";
import { createWriteFileTool } from "../../src/server/tools/write-file.js";
import { createWebSearchTool, StubSearchProvider } from "../../src/server/tools/web-search.js";
import type { AnyToolDefinition } from "../../src/server/tools/types.js";
import { errorResult } from "../../src/shared/result.js";
import { parseEnvelope, testConfig, tmpDir, type Env } from "../helpers.js";

const ctx = { requestId: "test-req", sessionId: "test-session" };

/**
 * Mirror of the server's per-invocation wrapper: zod-validate args
 * (applies defaults), run the handler, convert throws to error envelopes.
 */
async function call(tool: AnyToolDefinition, args: unknown): Promise<Env> {
  try {
    const parsed = z.object(tool.inputSchema).parse(args ?? {});
    const res = await tool.handler(parsed, ctx);
    return parseEnvelope(res.content[0]?.text ?? "");
  } catch (err) {
    return parseEnvelope(errorResult(err).content[0]?.text ?? "");
  }
}

describe("db_query tool", () => {
  const config = testConfig();
  const tool = createDbQueryTool(config.DB_PATH);

  beforeAll(() => seedDemoDb(config.DB_PATH));

  it("returns rows for a SELECT", async () => {
    const env = await call(tool, { query: "SELECT id, title FROM reports ORDER BY id LIMIT 3" });
    expect(env.status).toBe("ok");
    const data = env.data as { rows: { id: number; title: string }[]; row_count: number };
    expect(data.row_count).toBe(3);
    expect(data.rows[0]?.title).toContain("MCP Adoption");
  });

  it("rejects writes even though the statement parses", async () => {
    const env = await call(tool, { query: "DELETE FROM reports" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("forbidden");
  });

  it("maps SQL syntax errors to invalid_input", async () => {
    const env = await call(tool, { query: "SELECT FROM WHERE nothing" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("invalid_input");
  });

  it("maps missing tables to invalid_input", async () => {
    const env = await call(tool, { query: "SELECT * FROM ghosts" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("invalid_input");
    expect(env.message).toContain("ghosts");
  });

  it("reports a missing database file as not_found", async () => {
    const missing = createDbQueryTool(path.join(tmpDir(), "absent.db"));
    const env = await call(missing, { query: "SELECT 1" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("not_found");
    expect(env.message).toContain("seed:db");
  });
});

describe("read_file tool", () => {
  let sandbox: string;
  let tool: ReturnType<typeof createReadFileTool>;

  beforeAll(() => {
    sandbox = tmpDir("read-sandbox-");
    fs.writeFileSync(path.join(sandbox, "note.txt"), "sandboxed content");
    tool = createReadFileTool(sandbox, 1_024);
  });

  it("reads a file inside the sandbox", async () => {
    const env = await call(tool, { path: "note.txt" });
    expect(env.status).toBe("ok");
    expect((env.data as { content: string }).content).toBe("sandboxed content");
  });

  it("rejects traversal outside the sandbox", async () => {
    const env = await call(tool, { path: "../../secret.txt" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("forbidden");
  });

  it("reports missing files as not_found", async () => {
    const env = await call(tool, { path: "ghost.txt" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("not_found");
  });

  it("rejects oversized files", async () => {
    const big = createReadFileTool(sandbox, 4);
    const env = await call(big, { path: "note.txt" });
    expect(env.status).toBe("error");
    expect(env.message).toContain("too large");
  });

  it("rejects directories as not-a-regular-file", async () => {
    fs.mkdirSync(path.join(sandbox, "subdir"));
    const env = await call(tool, { path: "subdir" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("invalid_input");
  });
});

describe("write_file tool", () => {
  let sandbox: string;
  let tool: ReturnType<typeof createWriteFileTool>;

  beforeAll(() => {
    sandbox = tmpDir("write-sandbox-");
    tool = createWriteFileTool(sandbox, 8_192);
  });

  it("writes a file and creates parent dirs", async () => {
    const env = await call(tool, { path: "reports/r.md", content: "# report" });
    expect(env.status).toBe("ok");
    expect(fs.readFileSync(path.join(sandbox, "reports", "r.md"), "utf8")).toBe("# report");
  });

  it("rejects escapes and oversized content", async () => {
    const env = await call(tool, { path: "../evil.txt", content: "x" });
    expect(env.status).toBe("error");
    expect(env.code).toBe("forbidden");
    expect(fs.existsSync(path.join(path.dirname(sandbox), "evil.txt"))).toBe(false);

    const tiny = createWriteFileTool(sandbox, 3);
    const env2 = await call(tiny, { path: "ok.txt", content: "four+" });
    expect(env2.status).toBe("error");
    expect(env2.message).toContain("too large");
  });
});

describe("web_search tool", () => {
  it("returns stub hits offline", async () => {
    const tool = createWebSearchTool(new StubSearchProvider());
    const env = await call(tool, { query: "mcp", max_results: 2 });
    expect(env.status).toBe("ok");
    const data = env.data as { provider: string; results: unknown[] };
    expect(data.provider).toBe("none");
    expect(data.results).toHaveLength(2);
  });
});
