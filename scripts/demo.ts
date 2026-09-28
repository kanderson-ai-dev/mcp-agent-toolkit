import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Recorded end-to-end demo: seeds the fixture DB, then runs one agent
 * question that exercises 2+ MCP tools over real stdio transport, teeing
 * the transcript to docs/demo-transcript.txt.
 *
 *   npm run demo                  # default question
 *   npm run demo -- "question"    # custom question
 *
 * With OPENAI_API_KEY set the run is a live OpenAI tool-calling loop;
 * without it the deterministic StubLLM replays the same tool plan.
 */

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const isWin = process.platform === "win32";
const npm = isWin ? "npm.cmd" : "npm";

const question =
  process.argv.slice(2).join(" ").trim() ||
  "Which security and observability reports exist in the internal database? " +
    "Search the web for MCP security best practices, then save a cited summary to reports/demo-briefing.md";

function run(cmd: string, args: string[]): { output: string; code: number } {
  const res = spawnSync(cmd, args, {
    cwd: root,
    encoding: "utf8",
    env: process.env,
    shell: isWin,
  });
  const output = `${res.stdout ?? ""}${res.stderr ?? ""}`;
  process.stdout.write(output);
  return { output, code: res.status ?? 1 };
}

const header = [
  "# Recorded demo — agent ↔ MCP server over stdio",
  `# ${new Date().toISOString()}`,
  `# question: ${question}`,
  "",
].join("\n");

let transcript = header;

const seed = run(npm, ["run", "seed:db"]);
transcript += `$ npm run seed:db\n${seed.output}\n`;
if (seed.code !== 0) {
  console.error(`demo aborted: seed failed (${seed.code})`);
  process.exit(seed.code);
}

const agent = run(npm, ["run", "agent", "--", question]);
transcript += `$ npm run agent -- "${question}"\n${agent.output}`;

const out = path.join(root, "docs", "demo-transcript.txt");
fs.mkdirSync(path.dirname(out), { recursive: true });
fs.writeFileSync(out, transcript, "utf8");
console.log(`\ntranscript saved → ${path.relative(root, out)}`);
process.exit(agent.code);
