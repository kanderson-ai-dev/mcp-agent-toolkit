import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

/**
 * Console E2E — Phase 12. Drives the real UI through dataset questions
 * (8: all `uiExample` entries plus the path-traversal adversarial case)
 * against a deterministic backend (StubLLM + SEARCH_PROVIDER=none).
 *
 * StubLLM replays a fixed plan, so "expected tools" below mean the
 * scripted plan for that input — the suite verifies the UI pipeline
 * (question echo → tool cards in order → status badges → final answer),
 * not the model's judgment. Guardrail coverage is deterministic too:
 * the adversarial question makes the stub attempt a sandbox escape that
 * the server must reject with a typed `forbidden` error.
 */

interface DatasetQuestion {
  id: string;
  category: "multi_tool" | "single_tool" | "error_recovery" | "adversarial" | "ambiguous";
  uiExample?: boolean;
  prompt: string;
}

const DATASET_PATH = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../evaluation/dataset.json",
);

function loadQuestions(): DatasetQuestion[] {
  const raw = JSON.parse(fs.readFileSync(DATASET_PATH, "utf8")) as {
    questions: DatasetQuestion[];
  };
  const examples = raw.questions.filter((q) => q.uiExample);
  const traversal = raw.questions.find((q) => q.id === "ad-02");
  if (!traversal) throw new Error("dataset is missing the ad-02 traversal case");
  return [...examples, traversal];
}

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

interface ExpectedStep {
  label: string;
  status: "Success" | "Error";
}

/** The deterministic StubLLM plan per input class. */
function expectedSteps(q: DatasetQuestion): ExpectedStep[] {
  if (q.id === "ad-02") {
    return [{ label: "File read", status: "Error" }];
  }
  if (q.category === "error_recovery") {
    return [
      { label: "Database query", status: "Error" },
      { label: "Database query", status: "Success" },
      { label: "File write", status: "Success" },
    ];
  }
  return [
    { label: "Web search", status: "Success" },
    { label: "Database query", status: "Success" },
    { label: "File write", status: "Success" },
  ];
}

/** Submit a question and wait until the run fully settles (composer re-enables). */
async function askQuestion(page: Page, prompt: string): Promise<void> {
  const composer = page.getByLabel("Question for the agent");
  await composer.fill(prompt);
  await page.getByLabel("Send question").click();
  await expect(composer).toBeDisabled({ timeout: 10_000 }).catch(() => undefined);
  await expect(composer).toBeEnabled({ timeout: 100_000 });
}

async function scanForbidden(page: Page): Promise<string[]> {
  const text = await page.locator("body").innerText();
  return FORBIDDEN_LITERALS.filter((lit) => text.includes(lit));
}

test.describe("agent console — deterministic e2e", () => {
  const questions = loadQuestions();
  const violations: { context: string; hits: string[] }[] = [];

  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "networkidle" });
  });

  test.afterAll(() => {
    if (violations.length > 0) {
      const detail = violations
        .map((v) => `${v.context}: ${v.hits.join(", ")}`)
        .join("; ");
      throw new Error(`forbidden literals visible — ${detail}`);
    }
  });

  for (const q of questions) {
    test(`${q.id} (${q.category}): question, tool cards, badges, final answer`, async ({
      page,
    }) => {
      await askQuestion(page, q.prompt);
      const run = page.locator("section").last();

      // The question is echoed verbatim in the run history (exact match:
      // the web_search args summary may quote the same prompt inline).
      await expect(run.getByText(q.prompt, { exact: true })).toBeVisible();

      // Tool cards render friendly names in the scripted order, with the
      // matching status badge sequence — never raw tool ids or flags.
      const expected = expectedSteps(q);
      const cardTitles = await run.locator("h3").allTextContents();
      expect(cardTitles.slice(0, expected.length)).toEqual(expected.map((s) => s.label));
      const badges = await run.getByText(/^(Success|Error|Running)$/).allTextContents();
      expect(badges).toEqual(expected.map((s) => s.status));

      // Humanized details are present: step numbers, trace ids, results.
      await expect(run.getByText(/Step \d+/).first()).toBeVisible();
      await expect(run.getByText("Trace ID:").first()).toBeVisible();
      await expect(run.getByText(/^Result/).first()).toBeVisible();

      // The final answer renders as markdown inside its card.
      await expect(run.getByRole("heading", { name: "Final answer" })).toBeVisible();
      await expect(run.locator(".markdown").last()).toBeVisible();

      // Category-specific assertions.
      if (q.id === "ad-02") {
        // The guardrail block is explicit: error badge + friendly label —
        // the refusal is visible, not swallowed.
        await expect(run.getByText("Blocked by security policy")).toBeVisible();
        expect(await run.getByText(/^Success$/).count()).toBe(0);
      }
      if (q.category === "error_recovery") {
        // The failed call is labelled for humans, then the run recovers.
        await expect(run.getByText("Invalid input")).toBeVisible();
      }

      const hits = await scanForbidden(page);
      if (hits.length > 0) violations.push({ context: q.id, hits });
    });
  }

  test("page never exposes raw internal field names", async ({ page }) => {
    // Also exercised per-question above; this scan covers the untouched
    // empty state (example chips included).
    const hits = await scanForbidden(page);
    expect(hits).toEqual([]);
    await expect(page.getByText("Trace ID:")).toHaveCount(0); // nothing yet
    await expect(page.getByLabel("Question for the agent")).toBeEnabled();
  });
});
