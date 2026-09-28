import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Records a real agent run as an asciinema v2 cast at docs/demo.cast.
 *
 *   npm run demo:record            # uses the default demo question
 *   npm run demo:record -- "…"     # custom question
 *
 * Render to GIF (docs/demo.gif, referenced by the README) with the official
 * asciinema renderer — no local deps beyond Docker:
 *
 *   docker run --rm -v "$PWD/docs:/out" ghcr.io/asciinema/agg \
 *     --font-size 16 --theme monokai /out/demo.cast /out/demo.gif
 *
 * Only the agent's stdout event stream is captured — server JSON logs on
 * stderr stay out of the recording, exactly as a terminal user sees it.
 */

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const WIDTH = 104;
const HEIGHT = 34;
const MAX_DELAY_S = 1.6; // clamp idle gaps so the animation stays snappy

const question =
  process.argv.slice(2).join(" ").trim() ||
  "Which security and observability reports exist in the internal database? " +
    "Search the web for MCP security best practices, then save a cited summary to reports/demo-briefing.md";

interface CastHeader {
  version: 2;
  width: number;
  height: number;
  timestamp: number;
  env: Record<string, string>;
}
type CastEvent = [number, "o", string];

// tsx binary resolved directly — spawning .cmd shims needs shell on Windows.
const tsxCli = path.join(root, "node_modules", "tsx", "dist", "cli.mjs");

const commandLine = `npm run agent -- "${question.slice(0, 76)}…"`;

const child = spawn(process.execPath, [tsxCli, "src/client/index.ts", question], {
  cwd: root,
  env: process.env,
  stdio: ["ignore", "pipe", "inherit"], // stderr (server logs) → pass through, not recorded
});

const start = Date.now();
let lastStamp = 0;
const events: CastEvent[] = [[0, "o", `\x1b[1m$ ${commandLine}\x1b[0m\r\n\r\n`]];

child.stdout.on("data", (chunk: Buffer) => {
  const text = chunk.toString("utf8");
  process.stdout.write(text);
  const t = Math.min((Date.now() - start) / 1000, lastStamp + MAX_DELAY_S);
  events.push([Math.max(t - lastStamp, 0.02), "o", text.replace(/\n/g, "\r\n")]);
  lastStamp = t;
});

child.on("close", (code) => {
  events.push([0.8, "o", `\r\n\x1b[2m(exit ${code})\x1b[0m`]);
  const header: CastHeader = {
    version: 2,
    width: WIDTH,
    height: HEIGHT,
    timestamp: Math.floor(start / 1000),
    env: { TERM: "xterm-256color" },
  };
  const cast = [JSON.stringify(header), ...events.map((e) => JSON.stringify(e))].join("\n");

  const castPath = path.join(root, "docs", "demo.cast");
  fs.mkdirSync(path.dirname(castPath), { recursive: true });
  fs.writeFileSync(castPath, cast, "utf8");
  console.log(`\ncast saved → ${path.relative(root, castPath)}`);
  console.log(
    "render → docker run --rm -v \"$PWD/docs:/out\" ghcr.io/asciinema/agg" +
      " --font-size 16 --theme monokai /out/demo.cast /out/demo.gif",
  );
  process.exit(code ?? 1);
});
