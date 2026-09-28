import http from "node:http";
import {
  collectDefaultMetrics,
  Counter,
  Histogram,
  Registry,
} from "@prometheus-io/client";
import type { Logger } from "pino";

/**
 * Prometheus metrics for the MCP server. Registered on a dedicated
 * registry (not the global one) so tests don't leak state between runs.
 */
export interface ServerMetrics {
  readonly registry: Registry;
  recordInvocation(tool: string, ok: boolean, durationMs: number): void;
  recordRateLimited(tool: string): void;
}

export function createMetrics(): ServerMetrics {
  const registry = new Registry();
  collectDefaultMetrics({ register: registry });

  const invocations = new Counter({
    name: "mcp_tool_invocations_total",
    help: "Tool invocations by tool name and outcome",
    labelNames: ["tool", "status"],
    registers: [registry],
  });
  const duration = new Histogram({
    name: "mcp_tool_invocation_duration_ms",
    help: "Tool invocation latency in milliseconds",
    labelNames: ["tool"],
    buckets: [5, 25, 100, 500, 1_000, 5_000, 15_000, 30_000],
    registers: [registry],
  });
  const rateLimited = new Counter({
    name: "mcp_rate_limit_rejections_total",
    help: "Tool calls rejected by the rate limiter",
    labelNames: ["tool"],
    registers: [registry],
  });

  return {
    registry,
    recordInvocation(tool, ok, durationMs) {
      invocations.inc({ tool, status: ok ? "ok" : "error" });
      duration.observe({ tool }, durationMs);
    },
    recordRateLimited(tool) {
      rateLimited.inc({ tool });
    },
  };
}

/**
 * Optional `/metrics` + `/healthz` HTTP exposition. Opt-in via
 * METRICS_PORT — the canonical transport is stdio, which carries no HTTP.
 */
export function exposeMetrics(registry: Registry, port: number, logger: Logger): http.Server {
  const server = http.createServer((req, res) => {
    if (req.url === "/metrics" && req.method === "GET") {
      void registry.metrics().then((body) => {
        res.writeHead(200, { "content-type": registry.contentType });
        res.end(body);
      });
      return;
    }
    if (req.url === "/healthz" && req.method === "GET") {
      res.writeHead(200, { "content-type": "text/plain" });
      res.end("ok");
      return;
    }
    res.writeHead(404);
    res.end();
  });
  server.listen(port, () => {
    logger.info({ event: "metrics_listen", port }, "metrics endpoint listening");
  });
  return server;
}
