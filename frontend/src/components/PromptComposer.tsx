import { useRef, useState } from "react";
import { SendHorizonal } from "lucide-react";
import clsx from "clsx";

const MAX_CHARS = 2000;

interface PromptComposerProps {
  disabled: boolean;
  onSubmit: (question: string) => void;
}

/**
 * Sticky bottom composer — Enter sends, Shift+Enter inserts a newline.
 * Disabled while a run is streaming (one run at a time).
 */
export function PromptComposer({ disabled, onSubmit }: PromptComposerProps) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const remaining = MAX_CHARS - value.length;
  const canSend = !disabled && value.trim().length >= 3 && remaining >= 0;

  const send = (): void => {
    if (!canSend) return;
    onSubmit(value.trim());
    setValue("");
    textareaRef.current?.focus();
  };

  return (
    <div className="sticky bottom-0 border-t border-slate-800/80 bg-slate-950/90 pb-4 pt-3 backdrop-blur">
      <div className="mx-auto max-w-4xl px-4 sm:px-6">
        <div
          className={clsx(
            "flex items-end gap-3 rounded-2xl bg-slate-900/80 p-3 ring-1 ring-slate-700/70 transition-shadow focus-within:ring-2 focus-within:ring-indigo-500/60",
            disabled && "opacity-70",
          )}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            maxLength={MAX_CHARS}
            disabled={disabled}
            placeholder="Ask the agent a question…"
            aria-label="Question for the agent"
            className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm leading-relaxed text-slate-100 placeholder:text-slate-400 focus:outline-none disabled:cursor-not-allowed"
          />
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            aria-label="Send question"
            className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-indigo-500 text-white shadow-lg shadow-indigo-500/25 transition-all hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:shadow-none"
          >
            <SendHorizonal className="size-4.5" />
          </button>
        </div>
        <div className="mt-1.5 flex items-center justify-between px-1 text-xs text-slate-400">
          <span>Enter to send · Shift+Enter for a new line</span>
          <span className={remaining < 200 ? "text-amber-300" : ""}>
            {value.length}/{MAX_CHARS}
          </span>
        </div>
      </div>
    </div>
  );
}
