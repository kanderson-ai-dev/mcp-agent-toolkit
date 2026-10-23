import { AlertTriangle, Download, MessageSquareText, User } from "lucide-react";
import { downloadRun } from "../lib/export";
import type { RunRecord } from "../types";
import { FinalAnswerCard } from "./FinalAnswerCard";
import { Markdown } from "./Markdown";
import { ToolStepCard } from "./ToolStepCard";

/**
 * One console run: the user's question, the live timeline of steps
 * (tool cards + assistant notes), then the final answer or the error.
 */
export function RunView({
  run,
  onRetry,
  retryDisabled,
}: {
  run: RunRecord;
  onRetry?: () => void;
  retryDisabled?: boolean;
}) {
  return (
    <section className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-overlay text-soft ring-1 ring-line-strong">
          <User className="size-4" />
        </div>
        <div className="min-w-0 flex-1 rounded-xl rounded-tl-sm bg-overlay/70 px-4 py-3 ring-1 ring-line-strong/60">
          <p className="text-sm leading-relaxed text-ink">{run.question}</p>
        </div>
      </div>

      <div className="ml-5 space-y-3 border-l border-line pl-6 sm:ml-6">
        {run.steps.map((step, i) =>
          step.kind === "tool" ? (
            <ToolStepCard key={step.requestId} step={step} />
          ) : (
            <div
              key={`note-${i}`}
              className="animate-fade-up rounded-lg border border-dashed border-line-strong/70 px-3 py-2 text-xs italic leading-relaxed text-muted"
            >
              {step.content}
            </div>
          ),
        )}

        {run.errorMessage && (
          <div className="animate-fade-up flex items-start gap-2.5 rounded-xl bg-rose-500/10 p-4 ring-1 ring-rose-500/30">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
            <div>
              <p className="text-sm font-semibold text-danger">
                The request could not be completed
              </p>
              <p className="mt-1 text-xs leading-relaxed text-danger/80">
                {run.errorMessage}
              </p>
              {onRetry && (
                <button
                  type="button"
                  onClick={onRetry}
                  disabled={retryDisabled}
                  className="mt-2 inline-flex items-center rounded-lg bg-danger/15 px-3 py-1.5 text-xs font-medium text-danger ring-1 ring-danger/30 transition-colors hover:bg-danger/25 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11"
                >
                  Try again
                </button>
              )}
            </div>
          </div>
        )}

        {!run.final && run.streamingAnswer && (
          <div
            aria-label="Answer in progress"
            className="animate-fade-up rounded-xl bg-indigo-500/[0.07] p-4 ring-1 ring-indigo-500/25 sm:p-5"
          >
            <div className="flex items-center gap-2">
              <MessageSquareText className="size-4 text-accent" />
              <h3 className="text-sm font-semibold text-accent">
                Final answer
              </h3>
              <span className="ml-auto rounded-full bg-indigo-500/15 px-2 py-0.5 text-[11px] font-medium text-accent/80 ring-1 ring-indigo-500/30">
                Streaming
              </span>
            </div>
            <div className="markdown mt-3">
              <Markdown>{run.streamingAnswer}</Markdown>
            </div>
          </div>
        )}

        {run.final && <FinalAnswerCard final={run.final} elapsedMs={run.elapsedMs} />}

        {(run.final !== undefined || run.errorMessage !== undefined) && (
          <div className="flex items-center justify-end gap-1.5">
            <span className="text-[11px] text-muted">Export</span>
            <button
              type="button"
              onClick={() => downloadRun(run, "markdown")}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-muted ring-1 ring-line transition-colors hover:text-soft hover:ring-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-sm:min-h-11"
            >
              <Download className="size-3" />
              Markdown
            </button>
            <button
              type="button"
              onClick={() => downloadRun(run, "json")}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] font-medium text-muted ring-1 ring-line transition-colors hover:text-soft hover:ring-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent max-sm:min-h-11"
            >
              <Download className="size-3" />
              JSON
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
