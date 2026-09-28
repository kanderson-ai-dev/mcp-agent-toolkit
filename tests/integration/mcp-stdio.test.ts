import fs from "node:fs";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { seedDemoDb } from "../../scripts/seed-db.js";
import { McpAgentClient } from "../../src/client/mcp-client.js";
import { outcomeData, tmpDir } from "../helpers.js";

/**
 * Integration over the REAL stdio transport: the server runs as a spawned
 * child process (the compiled `dist/` artifact), the client speaks MCP
 * JSON-RPC over pipes — nothing mocked.
 */
describe("client ↔ server over real stdio transport", () => {
  let client: McpAgentClient;
  let sandbox: string;
  let dbPath: string;
  let stderrBuf = "";
  const savedEnv: Record<string, string | undefined> = {};

  const setEnv = (kv: Record<string, string>): void => {
    for (const [k, v] of Object.entries(kv)) {
      savedEnv[k] ??= process.env[k];
      process.env[k] = v;
    }
  };
  const restoreEnv = (): void => {
    for (const [k, v] of Object.entries(savedEnv)) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  };

  beforeAll(async () => {
    sandbox = tmpDir("it-sandbox-");
    fs.writeFileSync(path.join(sandbox, "note.txt"), "stdio fixture");
    dbPath = path.join(tmpDir("it-db-"), "demo.db");
    seedDemoDb(dbPath);
    setEnv({
      SEARCH_PROVIDER: "none",
      SANDBOX_ROOT: sandbox,
      DB_PATH: dbPath,
      LOG_LEVEL: "info",
    });
    client = McpAgentClient.overStdio(process.execPath, ["dist/server/index.js"], "pipe");
    client.stderrStream?.on("data", (c: Buffer) => {
      stderrBuf += c.toString();
    });
    await client.connect();
  }, 30_000);

  afterAll(async () => {
    await client.close();
    restoreEnv();
  });

  it("discovers all four tools via tools/list", () => {
    expect(client.listTools().map((t) => t.name).sort()).toEqual([
      "db_query",
      "read_file",
      "web_search",
      "write_file",
    ]);
  });

  it("invokes web_search → stub provider (fully offline)", async () => {
    const outcome = await client.call("web_search", { query: "mcp", max_results: 1 }, "it-1");
    expect(outcome.ok).toBe(true);
    expect(outcomeData<{ provider: string }>(outcome).provider).toBe("none");
  });

  it("invokes db_query against the spawned server's SQLite", async () => {
    const outcome = await client.call(
      "db_query",
      { query: "SELECT title FROM reports WHERE topic = 'security' ORDER BY id" },
      "it-2",
    );
    expect(outcome.ok).toBe(true);
    const data = outcomeData<{ rows: { title: string }[] }>(outcome);
    expect(data.rows.length).toBe(3);
    expect(data.rows[0]?.title).toContain("Prompt Injection");
  });

  it("read_file + write_file round-trip inside the sandbox", async () => {
    const w = await client.call(
      "write_file",
      { path: "it/out.txt", content: "roundtrip" },
      "it-3",
    );
    expect(w.ok).toBe(true);
    const r = await client.call("read_file", { path: "it/out.txt" }, "it-4");
    expect(outcomeData<{ content: string }>(r).content).toBe("roundtrip");
    expect(fs.existsSync(path.join(sandbox, "it", "out.txt"))).toBe(true);
  });

  it("correlates client requestId into server JSON logs via _meta", async () => {
    const requestId = "it-corr-" + crypto.randomUUID();
    const outcome = await client.call("read_file", { path: "note.txt" }, requestId);
    expect(outcome.ok).toBe(true);
    // Wait briefly for the stderr flush, then assert correlation.
    await new Promise((r) => setTimeout(r, 300));
    const line = stderrBuf
      .split("\n")
      .filter(Boolean)
      .map((l) => {
        try {
          return JSON.parse(l) as Record<string, unknown>;
        } catch {
          return {};
        }
      })
      .find((e) => e.request_id === requestId);
    expect(line, "server log line carrying our requestId").toBeDefined();
    expect(line?.tool).toBe("read_file");
    expect(line?.ok).toBe(true);
  });

  it("enforces the per-tool rate limit over the wire", async () => {
    restoreEnv();
    setEnv({ SEARCH_PROVIDER: "none", RATE_LIMIT_PER_MINUTE: "2" });
    const limited = McpAgentClient.overStdio(process.execPath, ["dist/server/index.js"], "pipe");
    await limited.connect();
    try {
      expect((await limited.call("web_search", { query: "a" }, "rl-1")).ok).toBe(true);
      expect((await limited.call("web_search", { query: "b" }, "rl-2")).ok).toBe(true);
      const third = await limited.call("web_search", { query: "c" }, "rl-3");
      expect(third.ok).toBe(false);
      expect(third.text).toContain("rate_limited");
    } finally {
      await limited.close();
    }
  }, 30_000);
});
