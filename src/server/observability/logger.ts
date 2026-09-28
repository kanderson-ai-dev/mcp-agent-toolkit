import { pino, type Logger } from "pino";

/**
 * Structured JSON logging on **stderr** — stdout is reserved for the MCP
 * stdio transport and must stay clean. Secret-shaped fields are redacted
 * unconditionally (defense-in-depth: never log config or env objects).
 */
export function createLogger(level: string, service = "mcp-server"): Logger {
  return pino(
    {
      level,
      base: { service },
      timestamp: pino.stdTimeFunctions.isoTime,
      redact: {
        paths: [
          "*.key",
          "*.apiKey",
          "*.api_key",
          "*.token",
          "*.secret",
          "*.password",
          "*.authorization",
          "env",
          "env.*",
          "config.OPENAI_API_KEY",
          "config.SEARCH_API_KEY",
        ],
        censor: "[redacted]",
      },
    },
    process.stderr,
  );
}
