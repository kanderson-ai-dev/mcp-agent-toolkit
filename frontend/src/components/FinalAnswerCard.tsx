import { AlertTriangle, MessageSquareText } from "lucide-react";
import { humanizeDuration, humanizeTokens, humanizeUsd } from "../lib/format";
import { TELEMETRY_LABEL } from "../lib/labels";
import type { FinalResult } from "../types";
import { Markdown } from "./Markdown";

/**
 * The agent's final answer — rendered Markdown (GFM) with clickable
 * citations. Footer reports steps/tool calls/time/tokens/estimated cost
 * in human terms — each telemetry piece is hidden when the run didn't
 * report it (unknown price ⇒ no cost figure, never a fabricated $0).
 * `terminatedBy` limits become explicit warnings.
 */
export function FinalAnswerCard({
  final,
  elapsedMs,
}: {
  final: FinalResult;
  elapsedMs?: number;
}) {
  const limitWarning =
    final.terminatedBy === "iteration_limit"
      ? "Step limit reached — the answer may be incomplete."
      : final.terminatedBy === "budget_limit"
        ? "Stopped: budget reached — the answer may be incomplete."
        : null;
  // Prefer the backend's wall-clock measurement; fall back to the
  // client-side timer (e.g. replays that lack durationMs).
  const elapsed = final.durationMs ?? elapsedMs;
  return (
    <div className="animate-fade-up rounded-xl bg-indigo-500/[0.07] p-4 ring-1 ring-indigo-500/25 sm:p-5">
      <div className="flex items-center gap-2">
        <MessageSquareText className="size-4 text-accent" />
        <h3 className="text-sm font-semibold text-accent">Final answer</h3>
        {final.promptVersion && (
          <span
            className="ml-auto rounded-full bg-indigo-500/15 px-2 py-0.5 text-[11px] font-medium text-accent/80 ring-1 ring-indigo-500/30"
            title="Instructions version used for this answer"
          >
            Prompt v{final.promptVersion.version}
          </span>
        )}
      </div>

      {limitWarning && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-warn ring-1 ring-amber-500/25">
          <AlertTriangle className="size-3.5 shrink-0" />
          {limitWarning}
        </div>
      )}

      {final.toolCalls === 0 && (
        <p className="mt-2 text-xs italic text-accent/60">
          Answered without tools
        </p>
      )}

      <div className="markdown mt-3">
        <Markdown>{final.content}</Markdown>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-indigo-500/15 pt-3 text-xs text-accent/60">
        <span>{final.iterations} {final.iterations === 1 ? "step" : "steps"}</span>
        <span aria-hidden="true">·</span>
        <span>
          {final.toolCalls === 0
            ? "no tool calls"
            : `${final.toolCalls} ${final.toolCalls === 1 ? "tool call" : "tool calls"}`}
        </span>
        {elapsed !== undefined && (
          <>
            <span aria-hidden="true">·</span>
            <span>
              {TELEMETRY_LABEL.time} {humanizeDuration(elapsed)}
            </span>
          </>
        )}
        {final.usage && (
          <>
            <span aria-hidden="true">·</span>
            <span title="Provider-reported prompt + completion">
              {humanizeTokens(final.usage.totalTokens)} tokens
            </span>
          </>
        )}
        {final.costUsd != null && (
          <>
            <span aria-hidden="true">·</span>
            <span title={`${TELEMETRY_LABEL.cost} — computed from the committed price table`}>
              {humanizeUsd(final.costUsd)}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
