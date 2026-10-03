import fs from "node:fs";
import type { AppConfig } from "../shared/config.js";

export interface ServerSpawn {
  command: string;
  args: string[];
}

/**
 * Resolve how to spawn the MCP server subprocess: an explicit override
 * (`MCP_SERVER_COMMAND`/`MCP_SERVER_ARGS`), the compiled `dist/` artifact
 * when present, or `tsx` against the TypeScript source as a dev fallback.
 * Shared by every entrypoint that spawns the server (CLI agent, web
 * console) so the resolution logic lives in exactly one place.
 */
export function resolveServerSpawn(config: AppConfig): ServerSpawn {
  if (config.MCP_SERVER_COMMAND) {
    return {
      command: config.MCP_SERVER_COMMAND,
      args: config.MCP_SERVER_ARGS ? config.MCP_SERVER_ARGS.split(/\s+/).filter(Boolean) : [],
    };
  }
  if (fs.existsSync("dist/server/index.js")) {
    return { command: process.execPath, args: ["dist/server/index.js"] };
  }
  return { command: "npx", args: ["tsx", "src/server/index.ts"] };
}
