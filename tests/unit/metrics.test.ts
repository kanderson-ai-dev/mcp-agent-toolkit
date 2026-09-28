import type { AddressInfo } from "node:net";
import { afterAll, describe, expect, it } from "vitest";
import { createLogger } from "../../src/server/observability/logger.js";
import {
  createMetrics,
  exposeMetrics,
} from "../../src/server/observability/metrics.js";

describe("metrics", () => {
  const metrics = createMetrics();
  const server = exposeMetrics(metrics.registry, 0, createLogger("error"));

  afterAll(async () => {
    await new Promise<void>((resolve) => server.close(() => resolve()));
  });

  it("counts invocations by tool and status", async () => {
    metrics.recordInvocation("web_search", true, 12);
    metrics.recordInvocation("web_search", false, 30);
    metrics.recordRateLimited("db_query");

    const text = await metrics.registry.metrics();
    expect(text).toContain('mcp_tool_invocations_total{tool="web_search",status="ok"} 1');
    expect(text).toContain('mcp_tool_invocations_total{tool="web_search",status="error"} 1');
    expect(text).toContain('mcp_rate_limit_rejections_total{tool="db_query"} 1');
    expect(text).toContain("mcp_tool_invocation_duration_ms");
  });

  it("exposes /metrics and /healthz over HTTP", async () => {
    const port = (server.address() as AddressInfo).port;
    const health = await fetch(`http://127.0.0.1:${port}/healthz`);
    expect(health.status).toBe(200);
    const metricsRes = await fetch(`http://127.0.0.1:${port}/metrics`);
    expect(metricsRes.status).toBe(200);
    expect(await metricsRes.text()).toContain("mcp_tool_invocations_total");
    const nf = await fetch(`http://127.0.0.1:${port}/nope`);
    expect(nf.status).toBe(404);
  });
});
