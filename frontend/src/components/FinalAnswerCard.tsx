import { AlertTriangle, MessageSquareText } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { humanizeDuration } from "../lib/format";
import type { FinalResult } from "../types";

/**
 * The agent's final answer — rendered Markdown (GFM) with clickable
 * citations. Footer reports steps/tool calls/elapsed in human terms;
 * `terminatedBy: "iteration_limit"` becomes an explicit warning.
 */
export function FinalAnswerCard({
  final,
  elapsedMs,
}: {
  final: FinalResult;
  elapsedMs?: number;
}) {
  return (
    <div className="animate-fade-up rounded-xl bg-indigo-500/[0.07] p-4 ring-1 ring-indigo-500/25 sm:p-5">
      <div className="flex items-center gap-2">
        <MessageSquareText className="size-4 text-indigo-300" />
        <h3 className="text-sm font-semibold text-indigo-200">Final answer</h3>
      </div>

      {final.terminatedBy === "iteration_limit" && (
        <div className="mt-3 flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-200 ring-1 ring-amber-500/25">
          <AlertTriangle className="size-3.5 shrink-0" />
          Step limit reached — the answer may be incomplete.
        </div>
      )}

      <div className="markdown mt-3">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={href} target="_blank" rel="noreferrer">
                {children}
              </a>
            ),
          }}
        >
          {final.content}
        </ReactMarkdown>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-indigo-500/15 pt-3 text-xs text-indigo-200/60">
        <span>{final.iterations} {final.iterations === 1 ? "step" : "steps"}</span>
        <span aria-hidden="true">·</span>
        <span>
          {final.toolCalls === 0
            ? "no tool calls"
            : `${final.toolCalls} ${final.toolCalls === 1 ? "tool call" : "tool calls"}`}
        </span>
        {elapsedMs !== undefined && (
          <>
            <span aria-hidden="true">·</span>
            <span>{humanizeDuration(elapsedMs)}</span>
          </>
        )}
      </div>
    </div>
  );
}
