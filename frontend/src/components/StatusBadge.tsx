import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import clsx from "clsx";
import { STATUS_LABEL } from "../lib/labels";
import type { ToolStepStatus } from "../types";

const STYLES: Record<ToolStepStatus, string> = {
  running: "bg-sky-500/15 text-sky-300 ring-sky-500/30",
  ok: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  error: "bg-rose-500/15 text-rose-300 ring-rose-500/30",
};

/** Human status badge — "Running" / "Success" / "Error", never raw flags. */
export function StatusBadge({ status }: { status: ToolStepStatus }) {
  const Icon =
    status === "running" ? Loader2 : status === "ok" ? CheckCircle2 : XCircle;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset",
        STYLES[status],
      )}
    >
      <Icon className={clsx("size-3.5", status === "running" && "animate-spin")} />
      {STATUS_LABEL[status]}
    </span>
  );
}
