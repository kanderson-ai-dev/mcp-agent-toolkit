import type { RunRecord, TimelineStep } from "../types";
import {
  argsAreCode,
  argsSummary,
  resultSummary,
  toolMeta,
  STATUS_LABEL,
  TELEMETRY_LABEL,
} from "./labels";
import { humanizeDuration, humanizeTokens, humanizeUsd } from "./format";

/**
 * Client-side run export — the run already lives fully in memory, so no
 * server round-trip is needed. Both serializers emit the same friendly
 * wording the UI uses (labels.ts) — internal field names (`durationMs`,
 * `tool_call`, `isError`, request ids) never appear in either format.
 */

function toolSteps(run: RunRecord): Extract<TimelineStep, { kind: "tool" }>[] {
  return run.steps.filter(
    (s): s is Extract<TimelineStep, { kind: "tool" }> => s.kind === "tool",
  );
}

function telemetryLine(run: RunRecord): string {
  const parts: string[] = [];
  const elapsed = run.final?.durationMs ?? run.elapsedMs;
  if (elapsed !== undefined) {
    parts.push(`${TELEMETRY_LABEL.time} ${humanizeDuration(elapsed)}`);
  }
  if (run.final?.usage) {
    parts.push(`${humanizeTokens(run.final.usage.totalTokens)} tokens`);
  }
  if (run.final?.costUsd != null) {
    parts.push(
      `${TELEMETRY_LABEL.cost}: ${humanizeUsd(run.final.costUsd)}`,
    );
  }
  if (run.final?.promptVersion) {
    parts.push(`Prompt v${run.final.promptVersion.version}`);
  }
  return parts.length ? `_${parts.join(" · ")}_` : "";
}

function mdEscape(text: string): string {
  return text.replace(/</g, "\\<");
}

/** Human-readable Markdown transcript: question, timeline, answer. */
export function runToMarkdown(run: RunRecord): string {
  const lines: string[] = [`# ${mdEscape(run.question)}`, ""];

  const steps = toolSteps(run);
  if (steps.length > 0) {
    lines.push("## Steps", "");
    for (const step of steps) {
      const meta = toolMeta(step.tool);
      const status =
        step.status === "running"
          ? STATUS_LABEL.running
          : step.result?.status === "error"
            ? STATUS_LABEL.error
            : STATUS_LABEL.ok;
      const timing =
        step.durationMs !== undefined
          ? ` — ${humanizeDuration(step.durationMs)}`
          : "";
      lines.push(`- **${meta.label}** — ${status}${timing}`);
      const summary = argsSummary(step.tool, step.args);
      if (summary) {
        lines.push(
          `  - Arguments: ${argsAreCode(step.tool) ? `\`${summary.replace(/`/g, "'")}\`` : summary}`,
        );
      }
      const outcome = resultSummary(step);
      if (outcome) lines.push(`  - ${outcome}`);
      lines.push("");
    }
  }

  const notes = run.steps.filter(
    (s): s is Extract<TimelineStep, { kind: "note" }> => s.kind === "note",
  );
  for (const note of notes) {
    lines.push(`> ${mdEscape(note.content)}`, "");
  }

  if (run.final) {
    lines.push("## Final answer", "", run.final.content, "");
    const telemetry = telemetryLine(run);
    if (telemetry) lines.push(telemetry, "");
  } else if (run.errorMessage) {
    lines.push("## Error", "", run.errorMessage, "");
  }

  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd() + "\n";
}

/** Structured JSON export — friendly labels, never raw field names. */
export function runToJson(run: RunRecord): string {
  const steps = run.steps.map((step) =>
    step.kind === "note"
      ? { type: "note", step: step.iteration, content: step.content }
      : {
          type: "step",
          step: step.iteration,
          name: toolMeta(step.tool).label,
          status:
            step.status === "running"
              ? STATUS_LABEL.running
              : step.result?.status === "error"
                ? STATUS_LABEL.error
                : STATUS_LABEL.ok,
          arguments: argsSummary(step.tool, step.args),
          time:
            step.durationMs !== undefined
              ? humanizeDuration(step.durationMs)
              : undefined,
          outcome: resultSummary(step) || undefined,
        },
  );
  const doc = {
    question: run.question,
    steps,
    answer: run.final?.content,
    error: run.errorMessage,
    time:
      run.final?.durationMs !== undefined
        ? humanizeDuration(run.final.durationMs)
        : run.elapsedMs !== undefined
          ? humanizeDuration(run.elapsedMs)
          : undefined,
    tokens:
      run.final?.usage != null
        ? humanizeTokens(run.final.usage.totalTokens)
        : undefined,
    estimatedCost:
      run.final?.costUsd != null ? humanizeUsd(run.final.costUsd) : undefined,
    promptVersion: run.final?.promptVersion?.version,
    stoppedEarly:
      run.final?.terminatedBy === "iteration_limit"
        ? "Step limit reached"
        : run.final?.terminatedBy === "budget_limit"
          ? "Budget limit reached"
          : undefined,
  };
  return `${JSON.stringify(doc, null, 2)}\n`;
}

function slugify(question: string): string {
  const slug = question
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  return slug || "run";
}

/** Serialize the run and trigger a browser download. */
export function downloadRun(run: RunRecord, format: "markdown" | "json"): void {
  const body = format === "markdown" ? runToMarkdown(run) : runToJson(run);
  const mime = format === "markdown" ? "text/markdown" : "application/json";
  const ext = format === "markdown" ? "md" : "json";
  const blob = new Blob([body], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `run-${slugify(run.question)}.${ext}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
