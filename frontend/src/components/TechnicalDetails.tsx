import { useState } from "react";
import { Braces, ChevronDown } from "lucide-react";
import clsx from "clsx";

/**
 * "Ver JSON técnico" — the opt-in escape hatch for raw payloads. Hidden
 * by default; the primary experience never shows raw JSON or raw field
 * names. Expanding is a deliberate user action.
 */
export function TechnicalDetails({ payload }: { payload: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-3 border-t border-slate-800 pt-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-slate-400 transition-colors hover:text-slate-200"
      >
        <Braces className="size-3.5" />
        Ver JSON técnico
        <ChevronDown
          className={clsx("size-3.5 transition-transform", open && "rotate-180")}
        />
      </button>
      {open && (
        <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-slate-950/80 p-3 text-xs leading-relaxed text-slate-300 ring-1 ring-slate-800">
          {JSON.stringify(payload, null, 2)}
        </pre>
      )}
    </div>
  );
}
