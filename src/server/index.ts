import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import type http from "node:http";
import { getConfig, loadEnvFile } from "../shared/config.js";
import { createLogger } from "./observability/logger.js";
import { exposeMetrics } from "./observability/metrics.js";
import { buildServer } from "./server.js";

/**
 * MCP server entrypoint — stdio transport. The agent client spawns this
 * process and speaks JSON-RPC over stdin/stdout; all logs go to stderr.
 */
async function main(): Promise<void> {
  loadEnvFile();
  const config = getConfig();
  const logger = createLogger(config.LOG_LEVEL);

  const { server, sessionId, metrics } = buildServer(config);
  await server.connect(new StdioServerTransport());
  logger.info({ event: "server_ready", session_id: sessionId }, "MCP server listening on stdio");

  let metricsServer: http.Server | undefined;
  if (config.METRICS_PORT !== undefined) {
    metricsServer = exposeMetrics(metrics.registry, config.METRICS_PORT, logger);
  }

  const shutdown = async (signal: string): Promise<void> => {
    logger.info({ event: "server_shutdown", signal }, "shutting down");
    metricsServer?.close();
    await server.close();
    process.exit(0);
  };
  process.on("SIGINT", () => void shutdown("SIGINT"));
  process.on("SIGTERM", () => void shutdown("SIGTERM"));
}

main().catch((err: unknown) => {
  createLogger("error").error(
    { event: "server_fatal", error: err instanceof Error ? err.message : String(err) },
    "fatal: MCP server failed to start",
  );
  process.exit(1);
});
