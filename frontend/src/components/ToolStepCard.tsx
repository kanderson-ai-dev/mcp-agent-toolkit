import { useState } from "react";
import { Check, ChevronDown, Copy } from "lucide-react";
import clsx from "clsx";
import { humanizeDuration } from "../lib/format";
import {
  argsAreCode,
  argsSummary,
  resultSummary,
  stepLabel,
  toolMeta,
  traceLabel,
} from "../lib/labels";
import type { ToolStep } from "../types";
import { ResultPanel } from "./ResultPanel";
import { StatusBadge } from "./StatusBadge";
import { TechnicalDetails } from "./TechnicalDetails";

/**
 * One tool invocation in the timeline: friendly name + icon, args as a
 * short sentence, status badge, humanized duration, collapsible result
 * panel, abbreviated trace id with copy — zero raw field names.
 */
export function ToolStepCard({ step }: { step: ToolStep }) {
  const [resultOpen, setResultOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = toolMeta(step.tool);
  const summary = argsSummary(step.tool, step.args);
  const finished = step.status !== "running";
  const hasResult = finished && (step.result !== undefined || Boolean(step.preview));

  const copyTrace = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(step.requestId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable (non-secure context) — the id stays visible
    }
  };

  return (
    <div className="animate-fade-up rounded-xl bg-raised/70 p-4 ring-1 ring-line transition-colors">
      <div className="flex items-start gap-3">
        <div
          className={clsx(
            "flex size-9 shrink-0 items-center justify-center rounded-lg ring-1 ring-inset",
            meta.tileClass,
          )}
        >
          <meta.Icon className="size-4.5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <h3 className="text-sm font-semibold text-ink">{meta.label}</h3>
            <StatusBadge status={step.status} />
            {step.durationMs !== undefined && (
              <span className="text-xs text-muted">
                {humanizeDuration(step.durationMs)}
              </span>
            )}
            <span className="ml-auto text-xs text-muted">
              {stepLabel(step.iteration)}
            </span>
          </div>
          {summary &&
            (argsAreCode(step.tool) ? (
              <code className="mt-2 block max-w-full truncate rounded-md bg-page/70 px-2.5 py-1.5 text-xs text-soft ring-1 ring-line">
                {summary}
              </code>
            ) : (
              <p className="mt-1.5 text-sm leading-relaxed text-soft">
                {summary}
              </p>
            ))}

          {hasResult && (
            <div className="mt-2">
              <button
                type="button"
                onClick={() => setResultOpen((v) => !v)}
                aria-expanded={resultOpen}
                className="inline-flex items-center gap-1.5 text-xs font-medium text-muted transition-colors hover:text-soft"
              >
                Result{!resultOpen && resultSummary(step) && `: ${resultSummary(step)}`}
                <ChevronDown
                  className={clsx(
                    "size-3.5 transition-transform",
                    resultOpen && "rotate-180",
                  )}
                />
              </button>
              {resultOpen && <ResultPanel step={step} />}
            </div>
          )}

          <div className="mt-2.5 flex items-center gap-1.5 text-xs text-muted">
            <span>
              Trace ID: <span className="font-mono text-soft">{traceLabel(step.requestId)}</span>
            </span>
            <button
              type="button"
              onClick={() => void copyTrace()}
              title="Copy full trace ID"
              aria-label="Copy full trace ID"
              className="rounded p-0.5 text-muted transition-colors hover:bg-overlay hover:text-soft"
            >
              {copied ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
            </button>
          </div>

          <TechnicalDetails
            payload={{
              tool: step.tool,
              args: step.args,
              result: step.result ?? step.preview ?? null,
            }}
          />
        </div>
      </div>
    </div>
  );
}
