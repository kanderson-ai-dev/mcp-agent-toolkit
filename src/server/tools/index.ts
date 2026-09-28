import type { AppConfig } from "../../shared/config.js";
import { createDbQueryTool } from "./db-query.js";
import { createReadFileTool } from "./read-file.js";
import { createSearchProvider, createWebSearchTool } from "./web-search.js";
import { createWriteFileTool } from "./write-file.js";
import type { AnyToolDefinition } from "./types.js";

/** Build the four-tool registry for the MCP server. */
export function createTools(config: AppConfig): AnyToolDefinition[] {
  return [
    createWebSearchTool(createSearchProvider(config)),
    createDbQueryTool(config.DB_PATH),
    createReadFileTool(config.SANDBOX_ROOT, config.READ_FILE_MAX_BYTES),
    createWriteFileTool(config.SANDBOX_ROOT, config.WRITE_FILE_MAX_BYTES),
  ];
}
