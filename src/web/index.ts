import fs from "node:fs";
import path from "node:path";
import { createLLM } from "../client/llm/index.js";
import { McpAgentClient } from "../client/mcp-client.js";
import { resolveServerSpawn } from "../client/server-spawn.js";
import { getConfig, loadEnvFile } from "../shared/config.js";
import { createApp } from "./server.js";

/**
 * Web console entrypoint. Spawns the MCP server once (not per request),
 * discovers tools, builds the LLM (OpenAI or the offline `StubLLM`, same
 * selection as the CLI), and serves the Express API — plus the built
 * React console as static files once `frontend/dist/` exists (Phase 10).
 */
async function main(): Promise<void> {
  loadEnvFile();
  const config = getConfig();

  const { command, args } = resolveServerSpawn(config);
  const mcp = McpAgentClient.overStdio(command, args);
  const discoveredTools = await mcp.connect();
  const llm = createLLM(config);

  const app = createApp({
    invoker: mcp,
    tools: mcp.toOpenAITools(),
    discoveredTools,
    llm,
    maxIterations: config.MAX_TOOL_ITERATIONS,
    corsOrigin: config.WEB_CORS_ORIGIN,
  });

  const staticDir = path.resolve("frontend/dist");
  if (fs.existsSync(staticDir)) {
    const express = (await import("express")).default;
    app.use(express.static(staticDir));
    app.get("/{*splat}", (_req, res) => {
      res.sendFile(path.join(staticDir, "index.html"));
    });
  }

  const httpServer = app.listen(config.WEB_PORT, () => {
    console.log(
      `web console listening on http://localhost:${String(config.WEB_PORT)} ` +
        `(llm=${llm.name}, ${String(discoveredTools.length)} tools)`,
    );
  });

  const shutdown = async (signal: string): Promise<void> => {
    console.log(`web console shutting down (${signal})`);
    httpServer.close();
    await mcp.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err: unknown) => {
  console.error(`fatal: web console failed to start: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
