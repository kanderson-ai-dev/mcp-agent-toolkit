import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { chromium, type Locator } from "@playwright/test";

/**
 * docs/web-demo.gif — recorded from a REAL Playwright run of the console
 * (the same mechanism the e2e suite uses), so the README hero is the
 * product, not a mockup. Usage:
 *
 *   npm run demo:gif          # uses OPENAI_API_KEY if set, else StubLLM
 *
 * Steps: spawn `src/web` on a scratch port → drive Chromium through the
 * multi-tool dataset question with recordVideo → ffmpeg (ffmpeg-static)
 * converts the .webm to an optimized GIF.
 */

const require = createRequire(import.meta.url);
const FFMPEG = require("ffmpeg-static") as string;

const PORT = 3101;
const BASE = `http://127.0.0.1:${PORT}`;
const OUT_GIF = path.resolve("docs/web-demo.gif");
const DEMO_QUESTION =
  "What security reports do we have in the database, and what does the web " +
  "say about security in MCP? Save me a combined briefing in `reports/security-briefing.md`.";
const SETTLE_MS = 1_600;
const INTRO_MS = 3_500;
const TYPE_DELAY_MS = 14;
const RUN_TIMEOUT_MS = 120_000;

async function waitForHealth(timeoutMs = 60_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/api/health`);
      if (res.ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("web server did not become healthy");
}

function killTree(child: ChildProcess): void {
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(child.pid), "/t", "/f"], { stdio: "ignore" });
  } else {
    child.kill("SIGTERM");
  }
}

async function main(): Promise<void> {
  const tsxCli = path.resolve("node_modules/tsx/dist/cli.mjs");
  const server = spawn(process.execPath, [tsxCli, "src/web/index.ts"], {
    env: { ...process.env, WEB_PORT: String(PORT) },
    stdio: "ignore",
  });

  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "mcp-demo-"));
  let browser;
  try {
    await waitForHealth();
    browser = await chromium.launch();
    const context = await browser.newContext({
      viewport: { width: 1280, height: 860 },
      recordVideo: { dir: tmpDir, size: { width: 1280, height: 860 } },
    });
    const page = await context.newPage();
    await page.goto(BASE, { waitUntil: "networkidle" });

    // Hold on the empty console long enough for a viewer to read it,
    // then type the question visibly rather than pasting it instantly.
    await page.waitForTimeout(INTRO_MS);
    const composer = page.getByLabel("Question for the agent");
    await composer.click();
    await composer.pressSequentially(DEMO_QUESTION, { delay: TYPE_DELAY_MS });
    await page.waitForTimeout(600);
    await page.getByLabel("Send question").click();
    await waitForRunEnd(composer, RUN_TIMEOUT_MS);

    // Let the final card paint, then land the frame on it.
    await page.getByRole("heading", { name: "Final answer" }).scrollIntoViewIfNeeded();
    await page.waitForTimeout(SETTLE_MS);
    const video = page.video();
    await context.close(); // finalizes the .webm
    await browser.close();

    const webmPath = video ? await video.path() : null;
    if (!webmPath || !fs.existsSync(webmPath)) {
      throw new Error("Playwright produced no video file");
    }

    // webm → gif: 8 fps, 960px wide, single-pass palette for quality.
    const args = [
      "-y",
      "-i", webmPath,
      "-vf",
      "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=4",
      "-loop", "0",
      OUT_GIF,
    ];
    const ff = spawnSync(FFMPEG, args, { stdio: "inherit" });
    if (ff.status !== 0) throw new Error(`ffmpeg exited with ${String(ff.status)}`);

    const kb = Math.round(fs.statSync(OUT_GIF).size / 1024);
    console.log(`✓ ${OUT_GIF} (${kb} KB)`);
  } finally {
    killTree(server);
    if (browser) await browser.close().catch(() => undefined);
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
}

/** The composer disables while a run streams and re-enables when it ends —
 * the same end-of-run signal the e2e suite relies on. */
async function waitForRunEnd(composer: Locator, timeout: number): Promise<void> {
  const graceMs = 10_000;
  const d0 = Date.now();
  while (Date.now() - d0 < graceMs) {
    if (await composer.isDisabled().catch(() => false)) break;
    await new Promise((r) => setTimeout(r, 200));
  }
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await composer.isEnabled()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("run did not finish in time");
}

const isMain = process.argv[1] && import.meta.url.endsWith(process.argv[1].replace(/\\/g, "/"));
if (isMain) {
  main().catch((err: unknown) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
