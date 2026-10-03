import { getConfig, loadEnvFile } from "../shared/config.js";
import { runAgent } from "./agent-loop.js";
import { formatEvent } from "./events.js";
import { createLLM } from "./llm/index.js";
import { McpAgentClient } from "./mcp-client.js";
import { resolveServerSpawn } from "./server-spawn.js";

const USAGE = `Usage: npm run agent -- "<question>" [--json] [--stub]

The agent spawns the MCP server over stdio, discovers its tools, and
answers the question through an LLM tool-calling loop.`;

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

  const { command, args } = resolveServerSpawn(config);
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
