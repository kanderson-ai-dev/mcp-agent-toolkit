import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { promote, PromptApiError } from "../../lib/promptsApi";
import { regressionLabel } from "../../lib/labels";
import type { AbResult, PromptVersionDetail } from "../../types";

interface PromoteDialogProps {
  name: string;
  version: number;
  /** Latest persisted/live evidence for this candidate, if any. */
  evidence: AbResult | null;
  onPromoted: (artifact: PromptVersionDetail) => void;
}

interface BlockedState {
  message: string;
  regressions: string[];
  mode?: string;
}

/**
 * Promotion gate dialog — tries a clean, evidence-based promote first;
 * when the gate blocks (no evidence / gate failed), the regressions are
 * shown and only then an override reason field appears.
 */
export function PromoteDialog({
  name,
  version,
  evidence,
  onPromoted,
}: PromoteDialogProps) {
  const [open, setOpen] = useState(false);
  const [blocked, setBlocked] = useState<BlockedState | null>(null);
  const [overrideReason, setOverrideReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const close = () => {
    setOpen(false);
    setBlocked(null);
    setOverrideReason("");
    setError(null);
  };

  const attempt = async (override: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const artifact = await promote(name, {
        version,
        ...(override ? { override_reason: overrideReason.trim() } : {}),
      });
      onPromoted(artifact);
      close();
    } catch (err) {
      if (err instanceof PromptApiError && err.status === 409) {
        // Gate blocked — show why; the override path appears only here.
        setBlocked({
          message: err.message,
          regressions: err.regressions ?? [],
          mode: err.mode,
        });
      } else {
        setError(
          err instanceof PromptApiError ? err.message : "Could not reach the server.",
        );
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        if (!o) close();
        else setOpen(true);
      }}
    >
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="rounded-lg bg-emerald-500/20 px-3 py-1.5 text-xs font-medium text-ok ring-1 ring-emerald-500/40 transition-colors hover:bg-emerald-500/30 focus-visible:outline-2 focus-visible:outline-accent"
        >
          Promote to active
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(28rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-raised p-5 shadow-2xl ring-1 ring-line-strong focus-visible:outline-none"
          aria-describedby="promote-desc"
        >
          <div className="flex items-start justify-between gap-3">
            <Dialog.Title className="text-sm font-semibold text-ink">
              Promote version {version} to active
            </Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                aria-label="Close"
                className="rounded-md p-1 text-muted transition-colors hover:bg-overlay hover:text-ink focus-visible:outline-2 focus-visible:outline-accent"
              >
                <X className="size-4" />
              </button>
            </Dialog.Close>
          </div>

          <p id="promote-desc" className="mt-2 text-xs leading-relaxed text-soft">
            The currently active version is rolled back automatically — exactly
            one active version per prompt is kept at all times.
          </p>

          {evidence && (
            <p className="mt-2 text-xs text-muted">
              Latest A/B result:{" "}
              {evidence.gatePassed ? "gate passed" : "gate failed"} ·{" "}
              {evidence.mode === "offline-smoke" ? "smoke check" : "live measurement"}
            </p>
          )}

          {blocked && (
            <div className="mt-3 space-y-2 rounded-lg bg-rose-500/10 p-3 ring-1 ring-rose-500/30">
              <p className="text-xs font-semibold text-danger">{blocked.message}</p>
              {blocked.mode === "offline-smoke" && (
                <p className="text-xs text-danger/90">
                  The A/B run was a smoke check — offline runs exercise the
                  harness only and are never promotion evidence.
                </p>
              )}
              {blocked.regressions.length > 0 && (
                <ul className="list-inside list-disc space-y-0.5 text-xs text-danger/90">
                  {blocked.regressions.map((r, i) => (
                    <li key={i}>{regressionLabel(r)}</li>
                  ))}
                </ul>
              )}
              <label className="block pt-1">
                <span className="mb-1 block text-xs font-medium text-danger">
                  Override reason — required, written to the audit log
                </span>
                <textarea
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                  rows={3}
                  placeholder="Why promote despite the blocked gate?"
                  className="w-full rounded-lg bg-overlay/70 px-3 py-2 text-xs text-ink ring-1 ring-rose-500/30 placeholder:text-faint focus-visible:outline-2 focus-visible:outline-accent"
                />
              </label>
            </div>
          )}

          {error && (
            <p role="alert" className="mt-3 rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-danger ring-1 ring-rose-500/30">
              {error}
            </p>
          )}

          <div className="mt-4 flex items-center justify-end gap-2">
            <Dialog.Close asChild>
              <button
                type="button"
                className="rounded-lg px-3 py-2 text-xs font-medium text-soft ring-1 ring-line transition-colors hover:bg-overlay focus-visible:outline-2 focus-visible:outline-accent"
              >
                Cancel
              </button>
            </Dialog.Close>
            {blocked ? (
              <button
                type="button"
                disabled={busy || overrideReason.trim().length === 0}
                onClick={() => void attempt(true)}
                className="rounded-lg bg-amber-500/20 px-3 py-2 text-xs font-medium text-warn ring-1 ring-amber-500/40 transition-colors hover:bg-amber-500/30 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? "Promoting…" : "Override and promote"}
              </button>
            ) : (
              <button
                type="button"
                disabled={busy}
                onClick={() => void attempt(false)}
                className="rounded-lg bg-emerald-500/20 px-3 py-2 text-xs font-medium text-ok ring-1 ring-emerald-500/40 transition-colors hover:bg-emerald-500/30 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? "Promoting…" : "Promote"}
              </button>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
