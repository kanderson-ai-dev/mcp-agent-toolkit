import { useRef } from "react";
import { SendHorizonal } from "lucide-react";
import clsx from "clsx";

const MAX_CHARS = 2000;

interface PromptComposerProps {
  disabled: boolean;
  /** A run is streaming — show "working" in place of the hint. */
  busy?: boolean;
  /** Controlled draft — lifted so example chips can fill (not submit). */
  value: string;
  onChange: (value: string) => void;
  onSubmit: (question: string) => void;
}

/**
 * Sticky bottom composer — Enter sends, Shift+Enter inserts a newline.
 * Disabled while a run is streaming (one run at a time).
 */
export function PromptComposer({
  disabled,
  busy = false,
  value,
  onChange,
  onSubmit,
}: PromptComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const remaining = MAX_CHARS - value.length;
  const tooLong = remaining < 0;
  const tooShort = value.trim().length > 0 && value.trim().length < 3;
  const canSend = !disabled && value.trim().length >= 3 && !tooLong;
  // A human reason for why send is unavailable — the counter alone is
  // not a validation message.
  const validation = tooLong
    ? `Keep it under ${MAX_CHARS.toLocaleString("en-US").replace(",", "\u2009")} characters.`
    : tooShort
      ? "Questions need at least 3 characters."
      : null;

  const send = (): void => {
    if (!canSend) return;
    onSubmit(value.trim());
    onChange("");
    textareaRef.current?.focus();
  };

  return (
    <div className="sticky bottom-0 border-t border-line/80 bg-page/90 pb-4 pt-3 backdrop-blur">
      <div className="mx-auto max-w-4xl px-4 sm:px-6">
        <div
          className={clsx(
            "flex items-end gap-3 rounded-2xl bg-raised/80 p-3 ring-1 ring-line-strong/70 transition-shadow focus-within:ring-2 focus-within:ring-indigo-500/60",
            disabled && "opacity-70",
          )}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            rows={2}
            disabled={disabled}
            placeholder="Ask the agent a question…"
            aria-label="Question for the agent"
            className="max-h-40 min-h-11 flex-1 resize-none bg-transparent px-1.5 py-1.5 text-sm leading-relaxed text-ink placeholder:text-muted focus:outline-none disabled:cursor-not-allowed"
          />
          <button
            type="button"
            onClick={send}
            disabled={!canSend}
            aria-label="Send question"
            className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-indigo-500 text-white shadow-lg shadow-indigo-500/25 transition-all hover:bg-indigo-400 disabled:cursor-not-allowed disabled:bg-overlay disabled:shadow-none"
          >
            <SendHorizonal className="size-4.5" />
          </button>
        </div>
        <div className="mt-1.5 flex items-center justify-between gap-3 px-1 text-xs">
          <span
            className={clsx(
              "inline-flex items-center gap-2",
              validation ? "font-medium text-warn" : "text-muted",
            )}
          >
            {busy ? (
              <>
                <span className="relative flex size-2">
                  <span className="absolute inline-flex size-full animate-ping rounded-full bg-indigo-400 opacity-60" />
                  <span className="relative inline-flex size-2 rounded-full bg-indigo-400" />
                </span>
                The agent is working…
              </>
            ) : (
              (validation ?? "Enter to send · Shift+Enter for a new line")
            )}
          </span>
          <span className={remaining < 200 ? "text-warn" : "text-muted"}>
            {value.length}/{MAX_CHARS}
          </span>
        </div>
      </div>
    </div>
  );
}
