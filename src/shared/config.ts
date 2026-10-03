import fs from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";
import { z } from "zod";

/**
 * Load .env. Real environment variables take precedence over the file
 * (`??=` never clobbers). An absent file is fine — the whole system
 * degrades cleanly with zero secrets.
 */
export function loadEnvFile(filePath = ".env"): void {
  let src: string;
  try {
    src = fs.readFileSync(filePath, "utf8");
  } catch {
    return; // .env is optional.
  }
  for (const [key, value] of Object.entries(parseEnv(src))) {
    process.env[key] ??= value;
  }
}

const emptyToUndefined = (v: unknown): unknown =>
  v === undefined || (typeof v === "string" && v.trim() === "") ? undefined : v;

/** Secrets: empty string is treated as absent so CI never reads "" as a key. */
const optSecret = z.preprocess(emptyToUndefined, z.string().min(1).optional());

const envSchema = z.object({
  // LLM (agent client)
  OPENAI_API_KEY: optSecret,
  CHAT_MODEL_NAME: z.string().min(1).default("gpt-4o-mini"),
  LLM_PROVIDER: z.enum(["auto", "openai", "stub"]).default("auto"),
  // web_search tool
  SEARCH_PROVIDER: z.enum(["auto", "tavily", "duckduckgo", "none"]).default("auto"),
  SEARCH_API_KEY: optSecret,
  // MCP server spawn (agent client)
  MCP_SERVER_COMMAND: z.preprocess(emptyToUndefined, z.string().min(1).optional()),
  MCP_SERVER_ARGS: z.preprocess(emptyToUndefined, z.string().optional()),
  // Server resources
  SANDBOX_ROOT: z.string().min(1).default("./data/sandbox"),
  DB_PATH: z.string().min(1).default("./data/demo.db"),
  // Guardrails
  RATE_LIMIT_PER_MINUTE: z.coerce.number().int().positive().default(60),
  MAX_TOOL_ITERATIONS: z.coerce.number().int().positive().max(50).default(8),
  MAX_TOOL_OUTPUT_CHARS: z.coerce.number().int().positive().default(12_000),
  READ_FILE_MAX_BYTES: z.coerce.number().int().positive().default(262_144),
  WRITE_FILE_MAX_BYTES: z.coerce.number().int().positive().default(1_048_576),
  // Observability
  LOG_LEVEL: z.string().min(1).default("info"),
  METRICS_PORT: z.preprocess(
    emptyToUndefined,
    z.coerce.number().int().min(1).max(65535).optional(),
  ),
  OUTPUT_FORMAT: z.enum(["pretty", "json"]).default("pretty"),
  // Evaluation / LangSmith (npm run eval) — all optional; absent → the
  // harness runs locally only, no tracing upload.
  LANGCHAIN_TRACING_V2: z.preprocess(emptyToUndefined, z.enum(["true", "false"]).optional()),
  LANGCHAIN_API_KEY: optSecret,
  LANGCHAIN_PROJECT: z.string().min(1).default("mcp-agent-toolkit"),
  // Web console (PLAN-2.md)
  WEB_PORT: z.coerce.number().int().min(1).max(65535).default(3000),
  // Allowed origin for the Vite dev server when the frontend runs
  // unproxied against this API directly. Harmless in production — the
  // frontend is served same-origin by this process, so no CORS header is
  // ever needed there.
  WEB_CORS_ORIGIN: z.string().min(1).default("http://localhost:5173"),
});

export type AppConfig = Omit<z.infer<typeof envSchema>, "SANDBOX_ROOT" | "DB_PATH"> & {
  /** Absolute sandbox root — the only filesystem prefix tools may touch. */
  SANDBOX_ROOT: string;
  /** Absolute path to the SQLite fixture DB. */
  DB_PATH: string;
};

export function getConfig(env: NodeJS.ProcessEnv = process.env): AppConfig {
  const parsed = envSchema.parse(env);
  return {
    ...parsed,
    SANDBOX_ROOT: path.resolve(parsed.SANDBOX_ROOT),
    DB_PATH: path.resolve(parsed.DB_PATH),
  };
}
