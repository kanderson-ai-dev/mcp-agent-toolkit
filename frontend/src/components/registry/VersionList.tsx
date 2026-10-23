import { CircleCheck } from "lucide-react";
import { humanizeDate } from "../../lib/format";
import { promptStatusLabel } from "../../lib/labels";
import type { PromptStatus, PromptVersionInfo } from "../../types";

const STATUS_CLASS: Record<PromptStatus, string> = {
  draft: "bg-overlay/80 text-soft ring-line-strong",
  candidate: "bg-amber-500/15 text-warn ring-amber-500/30",
  promoted: "bg-emerald-500/15 text-ok ring-emerald-500/30",
  rolled_back: "bg-rose-500/15 text-danger ring-rose-500/30",
};

interface VersionListProps {
  versions: PromptVersionInfo[];
  selectedVersion: number | null;
  onSelect: (version: number) => void;
}

/**
 * Version history for one prompt — newest first. The promoted row gets
 * both an "Active" word and a distinct tint so status never rides on
 * color alone.
 */
export function VersionList({ versions, selectedVersion, onSelect }: VersionListProps) {
  const sorted = [...versions].sort((a, b) => b.version - a.version);
  return (
    <ul className="space-y-2" aria-label="Versions">
      {sorted.map((v) => {
        const status = v.status;
        const isActive = status === "promoted";
        const isSelected = v.version === selectedVersion;
        return (
          <li key={v.version}>
            <button
              type="button"
              onClick={() => onSelect(v.version)}
              aria-current={isSelected ? "true" : undefined}
              className={`w-full rounded-xl p-3 text-left ring-1 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${
                isSelected
                  ? "bg-indigo-500/[0.08] ring-indigo-500/40"
                  : "bg-overlay/60 ring-line hover:bg-overlay"
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-ink">
                  Version {v.version}
                </span>
                <span
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ${STATUS_CLASS[status] ?? STATUS_CLASS.draft}`}
                >
                  {isActive && <CircleCheck className="size-3" aria-hidden="true" />}
                  {promptStatusLabel(status)}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
                <span>{v.author}</span>
                <span aria-hidden="true">·</span>
                <span>{humanizeDate(v.createdAt)}</span>
              </div>
              {v.description && (
                <p className="mt-1 truncate text-xs text-muted/80">{v.description}</p>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
