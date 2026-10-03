import express, { type Express, type Request, type Response } from "express";
import { z } from "zod";
import { runAgent } from "../client/agent-loop.js";
import type { AgentEvent } from "../client/events.js";
import type { DiscoveredTool, ToolInvoker } from "../client/mcp-client.js";
import type { LLMClient, OpenAIToolDef } from "../client/llm/types.js";
import { RunStore } from "./run-store.js";
import { startSseStream, writeSseEvent } from "./sse.js";

export interface WebAppDeps {
  invoker: ToolInvoker;
  tools: OpenAIToolDef[];
  discoveredTools: DiscoveredTool[];
  llm: LLMClient;
  maxIterations: number;
  /** Origin allowed to call this API cross-origin (Vite dev server). */
  corsOrigin?: string;
}

const ChatRequestSchema = z.object({
  question: z
    .string()
    .trim()
    .min(3, "Question must be at least 3 characters.")
    .max(2000, "Question must be at most 2000 characters."),
});

/**
 * Express adapter over the existing agent loop — no agent logic lives
 * here, only the HTTP/SSE surface. `POST /api/chat` validates and stashes
 * the question; `GET /api/chat/stream` consumes it and streams the real
 * `AgentEvent` union as named SSE events.
 */
export function createApp(deps: WebAppDeps): Express {
  const app = express();
  const runs = new RunStore();

  app.disable("x-powered-by");
  app.use(express.json({ limit: "32kb" }));

  const corsOrigin = deps.corsOrigin;
  if (corsOrigin) {
    app.use((req, res, next) => {
      if (req.headers.origin === corsOrigin) {
        res.setHeader("Access-Control-Allow-Origin", corsOrigin);
        res.setHeader("Vary", "Origin");
      }
      next();
    });
  }

  app.get("/api/health", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.get("/api/tools", (_req, res) => {
    res.json({
      tools: deps.discoveredTools.map((t) => ({ name: t.name, description: t.description })),
      llm: { provider: deps.llm.name },
    });
  });

  app.post("/api/chat", (req, res) => {
    const parsed = ChatRequestSchema.safeParse(req.body);
    if (!parsed.success) {
      res.status(400).json({ error: parsed.error.issues[0]?.message ?? "Invalid request." });
      return;
    }
    const runId = runs.create(parsed.data.question);
    res.status(201).json({ runId });
  });

  app.get("/api/chat/stream", (req, res) => {
    void handleStream(req, res, deps, runs);
  });

  return app;
}

/** Exported for direct unit testing of the disconnect-mid-stream branch. */
export async function handleStream(
  req: Request,
  res: Response,
  deps: WebAppDeps,
  runs: RunStore,
): Promise<void> {
  const runId = typeof req.query.runId === "string" ? req.query.runId : undefined;
  const question = runId ? runs.consume(runId) : undefined;
  if (!question) {
    res.status(404).json({ error: "Unknown or expired run. Submit a new question first." });
    return;
  }

  startSseStream(res);
  let closed = false;
  req.on("close", () => {
    closed = true;
  });
  const emit = (event: AgentEvent): void => {
    if (!closed) writeSseEvent(res, event);
  };

  try {
    await runAgent(question, {
      llm: deps.llm,
      invoker: deps.invoker,
      tools: deps.tools,
      maxIterations: deps.maxIterations,
      emit,
    });
  } catch (err) {
    if (!closed) {
      writeSseEvent(res, {
        type: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  } finally {
    if (!res.writableEnded) res.end();
  }
}
