import fs from "node:fs";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { seedDemoDb } from "../../scripts/seed-db.js";
import type { McpAgentClient } from "../../src/client/mcp-client.js";
import { connectedPair, parseEnvelope, testConfig, tmpDir } from "../helpers.js";

/**
 * Every attack below must fail loudly — an `isError` MCP result with a
 * typed code, never a silent success, a crash, or an unhandled rejection.
 */
describe("guardrails under attack", () => {
  let client: McpAgentClient;
  let close: () => Promise<void>;
  let sandbox: string;
  let outside: string;

  beforeAll(async () => {
    sandbox = tmpDir("sb-sec-");
    outside = tmpDir("sb-out-");
    fs.writeFileSync(path.join(sandbox, "note.txt"), "safe");
    fs.writeFileSync(path.join(outside, "secret.txt"), "TOP SECRET");
    const dbPath = path.join(tmpDir("db-"), "demo.db");
    seedDemoDb(dbPath);
    const config = testConfig({ SANDBOX_ROOT: sandbox, DB_PATH: dbPath });
    ({ client, close } = await connectedPair(config));
  });

  afterAll(async () => {
    await close();
    fs.rmSync(sandbox, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  });

  const err = async (tool: string, args: Record<string, unknown>) => {
    const outcome = await client.call(tool, args, "sec-test");
    expect(outcome.ok).toBe(false);
    return parseEnvelope(outcome.text);
  };

  it("path traversal via ../ is rejected", async () => {
    const e = await err("read_file", { path: "../../outside-0/secret.txt" });
    expect(e.code).toBe("forbidden");
  });

  it("absolute path outside the sandbox is rejected", async () => {
    const e = await err("read_file", {
      path: path.join(outside, "secret.txt"),
    });
    expect(e.code).toBe("forbidden");
  });

  it("write_file cannot escape the sandbox", async () => {
    const e = await err("write_file", { path: "../pwned.txt", content: "x" });
    expect(e.code).toBe("forbidden");
    expect(fs.existsSync(path.join(path.dirname(sandbox), "pwned.txt"))).toBe(false);
  });

  it("junction/symlink escapes are rejected", async () => {
    const link = path.join(sandbox, "linked-out");
    try {
      fs.symlinkSync(outside, link, process.platform === "win32" ? "junction" : "dir");
    } catch {
      console.warn("symlink unavailable — skipping");
      return;
    }
    const e = await err("read_file", { path: "linked-out/secret.txt" });
    expect(e.code).toBe("forbidden");
    expect(e.message).not.toContain("TOP SECRET");
  });

  it("non-SELECT SQL is rejected before touching the DB", async () => {
    for (const query of [
      "DROP TABLE reports",
      "DELETE FROM reports",
      "UPDATE reports SET title='x'",
      "PRAGMA writable_schema=1",
      "SELECT * FROM reports; DROP TABLE reports",
      "ATTACH DATABASE 'evil.db' AS e",
    ]) {
      const e = await err("db_query", { query });
      expect(e.code, `expected ${query} to be forbidden`).toBe("forbidden");
    }
    // And the table is still intact.
    const ok = await client.call("db_query", { query: "SELECT count(*) AS n FROM reports" }, "sec-ok");
    expect(ok.ok).toBe(true);
    expect(ok.text).toContain('"n": 8');
  });

  it("oversized write_file payloads are rejected", async () => {
    const e = await err("write_file", {
      path: "big.bin",
      content: "y".repeat(1_100_000),
    });
    expect(e.code).toBe("invalid_input");
  });

  it("control characters in file output are stripped server-side", async () => {
    fs.writeFileSync(
      path.join(sandbox, "dirty.txt"),
      `safe${String.fromCharCode(0x1b)}[2J${String.fromCharCode(0x07)} text`,
    );
    const outcome = await client.call("read_file", { path: "dirty.txt" }, "sec-ctl");
    expect(outcome.ok).toBe(true);
    expect(outcome.text).toContain("safe");
    expect(outcome.text).not.toContain(String.fromCharCode(0x1b));
    expect(outcome.text).not.toContain(String.fromCharCode(0x07));
  });

  it("rejects malformed arguments without crashing the server", async () => {
    const outcome = await client.call("read_file", { path: 12345 }, "sec-malformed");
    expect(outcome.ok).toBe(false);
    // Server still healthy afterwards:
    const ping = await client.call("db_query", { query: "SELECT 1 AS ok" }, "sec-ping");
    expect(ping.ok).toBe(true);
  });
});
