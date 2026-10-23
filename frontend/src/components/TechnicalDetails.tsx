import { useState } from "react";
import { Braces, Check, ChevronDown, Copy } from "lucide-react";
import clsx from "clsx";

/**
 * "View raw JSON" — the opt-in escape hatch for raw payloads. Hidden
 * by default; the primary experience never shows raw JSON or raw field
 * names. Expanding is a deliberate user action. The copy button copies
 * the exact JSON text shown in the panel.
 */
export function TechnicalDetails({ payload }: { payload: unknown }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(payload, null, 2);

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(json);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable (non-secure context) — the JSON stays visible
    }
  };

  return (
    <div className="mt-3 border-t border-line pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted transition-colors hover:text-soft max-sm:min-h-11"
      >
        <Braces className="size-3.5" />
        View raw JSON
        <ChevronDown
          className={clsx("size-3.5 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <div className="relative mt-2">
          <pre className="max-h-64 overflow-auto rounded-lg bg-page/80 p-3 text-xs leading-relaxed text-soft ring-1 ring-line">
            {json}
          </pre>
          <button
            type="button"
            onClick={() => void copy()}
            title="Copy JSON"
            aria-label="Copy JSON"
            className="absolute right-2 top-2 flex items-center justify-center rounded-md bg-overlay p-1.5 text-muted ring-1 ring-line transition-colors hover:text-soft max-sm:size-11"
          >
            {copied ? (
              <Check className="size-3.5 text-ok" />
            ) : (
              <Copy className="size-3.5" />
            )}
          </button>
        </div>
      )}
    </div>
  );
}
