import { AlertTriangle, User } from "lucide-react";
import type { RunRecord } from "../types";
import { FinalAnswerCard } from "./FinalAnswerCard";
import { ToolStepCard } from "./ToolStepCard";

/**
 * One console run: the user's question, the live timeline of steps
 * (tool cards + assistant notes), then the final answer or the error.
 */
export function RunView({ run, live }: { run: RunRecord; live: boolean }) {
  return (
    <section className="space-y-3">
      <div className="flex items-start gap-3">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-slate-800 text-slate-300 ring-1 ring-slate-700">
          <User className="size-4" />
        </div>
        <div className="min-w-0 flex-1 rounded-xl rounded-tl-sm bg-slate-800/70 px-4 py-3 ring-1 ring-slate-700/60">
          <p className="text-sm leading-relaxed text-slate-100">{run.question}</p>
        </div>
      </div>

      <div className="ml-5 space-y-3 border-l border-slate-800 pl-6 sm:ml-6">
        {run.steps.map((step, i) =>
          step.kind === "tool" ? (
            <ToolStepCard key={step.requestId} step={step} />
          ) : (
            <div
              key={`note-${i}`}
              className="animate-fade-up rounded-lg border border-dashed border-slate-700/70 px-3 py-2 text-xs italic leading-relaxed text-slate-400"
            >
              {step.content}
            </div>
          ),
        )}

        {live && !run.final && !run.errorMessage && (
          <div className="flex items-center gap-2.5 rounded-xl bg-slate-900/50 px-4 py-3 ring-1 ring-slate-800/70">
            <span className="relative flex size-2.5">
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-indigo-400 opacity-60" />
              <span className="relative inline-flex size-2.5 rounded-full bg-indigo-400" />
            </span>
            <p className="text-xs font-medium text-slate-300">
              The agent is working…
            </p>
          </div>
        )}

        {run.errorMessage && (
          <div className="animate-fade-up flex items-start gap-2.5 rounded-xl bg-rose-500/10 p-4 ring-1 ring-rose-500/30">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-rose-300" />
            <div>
              <p className="text-sm font-semibold text-rose-200">
                The request could not be completed
              </p>
              <p className="mt-1 text-xs leading-relaxed text-rose-200/80">
                {run.errorMessage}
              </p>
            </div>
          </div>
        )}

        {run.final && <FinalAnswerCard final={run.final} elapsedMs={run.elapsedMs} />}
      </div>
    </section>
  );
}
