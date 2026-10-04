import fs from "node:fs";
import path from "node:path";
import { z } from "zod";

/**
 * Typed access to the versioned evaluation dataset (`dataset.json`).
 * The same file drives three consumers: this eval harness, the console's
 * example chips (injected at build time) and the e2e suite — one source
 * of truth so a question is never redefined in two places.
 */

const QuestionSchema = z.object({
  id: z.string().min(1),
  category: z.enum([
    "multi_tool",
    "single_tool",
    "error_recovery",
    "adversarial",
    "ambiguous",
  ]),
  uiExample: z.boolean().default(false),
  prompt: z.string().min(1),
  /** Optional gold answer — ground truth the judge can verify claims
   * against when the correct output is deterministic (e.g. seeded rows). */
  referenceAnswer: z.string().optional(),
  expect: z.object({
    tools: z.array(z.string()).default([]),
    forbiddenSuccessfulTools: z.array(z.string()).default([]),
    notes: z.string().default(""),
  }),
});

const DatasetSchema = z.object({
  version: z.string().min(1),
  description: z.string().default(""),
  categories: z.record(z.string(), z.string()).default({}),
  questions: z.array(QuestionSchema).min(1),
});

export type EvalQuestion = z.infer<typeof QuestionSchema>;
export type EvalCategory = EvalQuestion["category"];

export interface EvalDataset {
  version: string;
  description: string;
  categories: Record<string, string>;
  questions: EvalQuestion[];
}

const DATASET_PATH = path.resolve(import.meta.dirname, "dataset.json");

export function loadDataset(filePath = DATASET_PATH): EvalDataset {
  const raw: unknown = JSON.parse(fs.readFileSync(filePath, "utf8"));
  return DatasetSchema.parse(raw);
}
