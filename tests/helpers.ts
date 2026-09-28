import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { getConfig, type AppConfig } from "../src/shared/config.js";
import { McpAgentClient } from "../src/client/mcp-client.js";
import { buildServer } from "../src/server/server.js";

export function tmpDir(prefix = "mcp-test-"): string {
  return fs.mkdtempSync(path.join(os.tmpdir(), prefix));
}

/** Deterministic offline config; every override goes through env parsing. */
export function testConfig(overrides: Record<string, string> = {}): AppConfig {
  return getConfig({
    SANDBOX_ROOT: path.join(tmpDir(), "sandbox"),
    DB_PATH: path.join(tmpDir(), "demo.db"),
    SEARCH_PROVIDER: "none",
    LOG_LEVEL: "error",
    ...overrides,
  });
}

export interface Env {
  status: string;
  data?: Record<string, unknown>;
  code?: string;
  message?: string;
}

/** Unwrap the { status, data|code,message } JSON envelope a tool returns. */
export function parseEnvelope(text: string): Env {
  return JSON.parse(text) as Env;
}

/**
 * Wire a real McpServer to a real McpAgentClient over the SDK's linked
 * in-memory transport pair — the genuine MCP protocol, no subprocess.
 */
export async function connectedPair(
  config: AppConfig,
): Promise<{ client: McpAgentClient; sessionId: string; close: () => Promise<void> }> {
  const [clientT, serverT] = InMemoryTransport.createLinkedPair();
  const { server, sessionId } = buildServer(config);
  await server.connect(serverT);
  const client = McpAgentClient.overTransport(clientT);
  await client.connect();
  return {
    client,
    sessionId,
    close: async () => {
      await client.close();
      await server.close();
    },
  };
}

/** Extract the parsed `data` payload from an ok tool outcome. */
export function outcomeData<T>(outcome: { ok: boolean; text: string }): T {
  const env = parseEnvelope(outcome.text);
  if (env.status !== "ok") throw new Error(`expected ok, got ${env.code}: ${env.message}`);
  return env.data as T;
}
