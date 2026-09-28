import fs from "node:fs";
import { getConfig, loadEnvFile } from "../shared/config.js";
import { runAgent } from "./agent-loop.js";
import { formatEvent } from "./events.js";
import { createLLM } from "./llm/index.js";
import { McpAgentClient } from "./mcp-client.js";

const USAGE = `Usage: npm run agent -- "<question>" [--json] [--stub]

The agent spawns the MCP server over stdio, discovers its tools, and
answers the question through an LLM tool-calling loop.`;

/** Server spawn default: compiled dist when present, else tsx source. */
function serverSpawn(config: ReturnType<typeof getConfig>): { command: string; args: string[] } {
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

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const jsonFlag = argv.includes("--json");
  const stubFlag = argv.includes("--stub");
  const prompt = argv.filter((a) => !a.startsWith("--")).join(" ").trim();
  if (!prompt) {
    console.log(USAGE);
    process.exit(2);
  }

  loadEnvFile();
  const config = getConfig();
  if (stubFlag) config.LLM_PROVIDER = "stub";
  const format = jsonFlag ? "json" : config.OUTPUT_FORMAT;

  const { command, args } = serverSpawn(config);
  const mcp = McpAgentClient.overStdio(command, args);
  const emit = (e: Parameters<typeof formatEvent>[0]): void => {
    console.log(formatEvent(e, format));
  };

  try {
    const tools = await mcp.connect();
    const llm = createLLM(config);
    emit({ type: "llm_message", iteration: 0, content: `connected — ${tools.length} tools discovered (${tools.map((t) => t.name).join(", ")}), llm=${llm.name}` });

    const result = await runAgent(prompt, {
      llm,
      invoker: mcp,
      tools: mcp.toOpenAITools(),
      maxIterations: config.MAX_TOOL_ITERATIONS,
      emit,
    });
    process.exitCode = result.terminatedBy === "answer" ? 0 : 1;
  } finally {
    await mcp.close();
  }
}

main().catch((err: unknown) => {
  console.error(`fatal: ${err instanceof Error ? err.message : String(err)}`);
  process.exit(1);
});
