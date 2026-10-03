import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { chromium, type Page } from "@playwright/test";

/**
 * Phase-10 visual QA: drives the real console (backend + production
 * frontend on WEB_PORT) through the dataset's `uiExample` questions,
 * captures every UI state into docs/ui-qa/, and asserts the hard rule —
 * no raw internal field names in visible text (technical JSON stays
 * collapsed throughout).
 *
 * Requires the web server running (`npm run web`). Not part of vitest.
 */

const BASE = process.env.UI_QA_BASE ?? "http://localhost:3000";
const OUT_DIR = path.resolve("docs/ui-qa");
const DATASET_PATH = path.resolve("evaluation/dataset.json");
const RUN_TIMEOUT_MS = 120_000;

/** Internal field names that must never appear as visible UI text. */
const FORBIDDEN_LITERALS = [
  "tool_call",
  "tool_result",
  "llm_message",
  "request_id",
  "requestId",
  "session_id",
  "durationMs",
  "terminatedBy",
  "iteration_limit",
  "max_results",
  "row_count",
  "bytes_written",
  "isError",
  "toolCalls",
];

interface DatasetQuestion {
  id: string;
  uiExample?: boolean;
  prompt: string;
}

function loadExampleQuestions(): DatasetQuestion[] {
  const raw = JSON.parse(fs.readFileSync(DATASET_PATH, "utf8")) as {
    questions: DatasetQuestion[];
  };
  return raw.questions.filter((q) => q.uiExample);
}

async function visibleText(page: Page): Promise<string> {
  return page.locator("body").innerText();
}

async function scanForbidden(page: Page, context: string): Promise<string[]> {
  const text = await visibleText(page);
  const hits = FORBIDDEN_LITERALS.filter((lit) => text.includes(lit));
  if (hits.length > 0) {
    console.error(`  ✗ forbidden literals visible after ${context}: ${hits.join(", ")}`);
  }
  return hits;
}

/**
 * The composer is disabled while a run streams and re-enabled exactly
 * when the stream closes — the most reliable end-of-run signal (works
 * for success, error and connection-loss alike).
 */
async function waitForRunEnd(page: Page): Promise<void> {
  const composer = page.getByLabel("Pregunta para el agente");
  // The click returns before React flips `disabled` — wait for the
  // disable first (instant API errors skip it) then for re-enable.
  await composer
    .and(page.locator(":disabled"))
    .waitFor({ state: "visible", timeout: 10_000 })
    .catch(() => undefined);
  await composer
    .and(page.locator(":enabled"))
    .waitFor({ state: "visible", timeout: RUN_TIMEOUT_MS });
}

async function submitQuestion(page: Page, question: string): Promise<void> {
  await page.getByLabel("Pregunta para el agente").fill(question);
  await page.getByLabel("Enviar pregunta").click();
}

async function main(): Promise<void> {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const questions = loadExampleQuestions();
  if (questions.length === 0) {
    console.error("No uiExample questions found in evaluation/dataset.json");
    process.exit(1);
  }
  const browser = await chromium.launch();
  const violations: string[] = [];

  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

    // ── Empty state ────────────────────────────────────────────────
    await page.goto(BASE, { waitUntil: "networkidle" });
    await page.screenshot({ path: path.join(OUT_DIR, "01-empty-state.png"), fullPage: true });
    violations.push(...(await scanForbidden(page, "empty state")));
    console.log("✓ 01-empty-state.png");

    // ── Streaming state — capture mid-run on the first question ────
    const first = questions[0];
    if (!first) throw new Error("unreachable — checked above");
    await submitQuestion(page, first.prompt);
    await page
      .locator("text=En curso")
      .first()
      .waitFor({ state: "visible", timeout: 30_000 });
    await page.screenshot({ path: path.join(OUT_DIR, "02-streaming.png"), fullPage: true });
    console.log("✓ 02-streaming.png");
    await waitForRunEnd(page);
    violations.push(...(await scanForbidden(page, `${first.id} complete`)));
    await page.screenshot({
      path: path.join(OUT_DIR, `run-${first.id}.png`),
      fullPage: true,
    });
    console.log(`✓ run-${first.id}.png (first question done)`);

    // ── Remaining example questions ────────────────────────────────
    for (const q of questions.slice(1)) {
      await submitQuestion(page, q.prompt);
      await waitForRunEnd(page);
      violations.push(...(await scanForbidden(page, `${q.id} complete`)));
      await page.screenshot({
        path: path.join(OUT_DIR, `run-${q.id}.png`),
        fullPage: true,
      });
      console.log(`✓ run-${q.id}.png`);
    }

    // ── Expanded result panel (a database card, if present) ────────
    const resultado = page.locator("text=Resultado").last();
    if (await resultado.isVisible().catch(() => false)) {
      await resultado.click();
      await page.waitForTimeout(400);
      await page.screenshot({
        path: path.join(OUT_DIR, "03-result-expanded.png"),
        fullPage: true,
      });
      violations.push(...(await scanForbidden(page, "result panel expanded")));
      console.log("✓ 03-result-expanded.png");
    }

    // ── Technical JSON toggle (deliberately shows raw payload) ─────
    const toggle = page.locator("text=Ver JSON técnico").first();
    if (await toggle.isVisible().catch(() => false)) {
      await toggle.click();
      await page.waitForTimeout(300);
      await page.screenshot({
        path: path.join(OUT_DIR, "04-technical-json.png"),
        fullPage: true,
      });
      console.log("✓ 04-technical-json.png (raw JSON behind explicit toggle)");
      await toggle.click(); // collapse again
    }

    // ── Responsive viewports ───────────────────────────────────────
    for (const [name, viewport] of [
      ["05-mobile-375", { width: 375, height: 720 }],
      ["06-tablet-768", { width: 768, height: 900 }],
    ] as const) {
      const p = await browser.newPage({ viewport });
      await p.goto(BASE, { waitUntil: "networkidle" });
      await p.screenshot({ path: path.join(OUT_DIR, `${name}.png`), fullPage: true });
      violations.push(...(await scanForbidden(p, name)));
      console.log(`✓ ${name}.png`);
      await p.close();
    }
  } finally {
    await browser.close();
  }

  if (violations.length > 0) {
    console.error(`\nUI QA FAILED — forbidden literals found (${violations.length}).`);
    process.exit(1);
  }
  console.log(`\nUI QA passed — screenshots in ${OUT_DIR}`);
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;
if (isMain) {
  main().catch((err: unknown) => {
    console.error(err instanceof Error ? err.message : String(err));
    process.exit(1);
  });
}
