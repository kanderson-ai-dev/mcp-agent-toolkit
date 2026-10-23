import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { PromptApiError, rollback } from "../../lib/promptsApi";
import type { PromptVersionDetail } from "../../types";

export const MIN_REASON_CHARS = 10;

interface RollbackButtonProps {
  name: string;
  /** The rolled-back version to restore as active. */
  toVersion: number;
  onRolledBack: (artifact: PromptVersionDetail) => void;
}

/**
 * Restore a previously-rolled-back version to active — requires a
 * ≥10-character reason, recorded in the audit chain.
 */
export function RollbackButton({ name, toVersion, onRolledBack }: RollbackButtonProps) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const tooShort = reason.trim().length < MIN_REASON_CHARS;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const artifact = await rollback(name, {
        to_version: toVersion,
        reason: reason.trim(),
      });
      onRolledBack(artifact);
      setOpen(false);
      setReason("");
    } catch (err) {
      setError(
        err instanceof PromptApiError ? err.message : "Could not reach the server.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button
          type="button"
          className="rounded-lg px-3 py-1.5 text-xs font-medium text-soft ring-1 ring-line transition-colors hover:bg-overlay focus-visible:outline-2 focus-visible:outline-accent"
        >
          Roll back to v{toVersion}
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/50 backdrop-blur-sm" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,92vw)] -translate-x-1/2 -translate-y-1/2 rounded-2xl bg-raised p-5 shadow-2xl ring-1 ring-line-strong focus-visible:outline-none"
          aria-describedby="rollback-desc"
        >
          <div className="flex items-start justify-between gap-3">
            <Dialog.Title className="text-sm font-semibold text-ink">
              Roll back to version {toVersion}
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
          <p id="rollback-desc" className="mt-2 text-xs leading-relaxed text-soft">
            Version {toVersion} becomes active again; the current active version
            is marked rolled back.
          </p>
          <label className="mt-3 block">
            <span className="mb-1 block text-xs font-medium text-soft">
              Reason — at least {MIN_REASON_CHARS} characters, recorded in the audit log
            </span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              className="w-full rounded-lg bg-overlay/70 px-3 py-2 text-xs text-ink ring-1 ring-line focus-visible:outline-2 focus-visible:outline-accent"
            />
          </label>
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
            <button
              type="button"
              disabled={busy || tooShort}
              onClick={() => void submit()}
              className="rounded-lg bg-indigo-500/20 px-3 py-2 text-xs font-medium text-accent ring-1 ring-indigo-500/40 transition-colors hover:bg-indigo-500/30 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy ? "Rolling back…" : "Roll back"}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
