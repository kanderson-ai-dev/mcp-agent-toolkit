import { ShieldCheck, ShieldX } from "lucide-react";
import { auditEventLabel, promptNameLabel } from "../../lib/labels";
import type { AuditEvent } from "../../types";

interface AuditTimelineProps {
  events: AuditEvent[];
  verified: boolean;
  firstTamperedLine?: number | null;
}

function eventLine(e: AuditEvent): string {
  const name = promptNameLabel(e.name);
  switch (e.type) {
    case "candidate_created":
      return `${name} — version ${e.version} by ${e.author ?? "unknown"}`;
    case "ab_test":
      return `${name} — candidate v${e.candidate_version ?? "?"}${
        e.mode === "offline-smoke" ? " · smoke check" : ""
      }${e.gate_passed === true ? " · gate passed" : e.gate_passed === false ? " · gate failed" : ""}`;
    case "promoted":
      return `${name} — version ${e.version}${
        e.previous_promoted != null ? ` (replaced v${e.previous_promoted})` : ""
      }`;
    case "promoted_override":
      return `${name} — version ${e.version}`;
    case "rolled_back":
      return `${name} — version ${e.version}${
        e.restored_version != null ? ` (restored v${e.restored_version})` : ""
      }`;
    default:
      return name;
  }
}

/**
 * Append-only audit chain — each line labelled in friendly terms and
 * the hash-chain verification surfaced as an explicit badge.
 */
export function AuditTimeline({ events, verified, firstTamperedLine }: AuditTimelineProps) {
  const rows = events;
  return (
    <div className="rounded-xl bg-raised/80 p-4 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">Audit log</h3>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${
            verified
              ? "bg-emerald-500/15 text-ok ring-emerald-500/30"
              : "bg-rose-500/15 text-danger ring-rose-500/30"
          }`}
        >
          {verified ? (
            <ShieldCheck className="size-3.5" aria-hidden="true" />
          ) : (
            <ShieldX className="size-3.5" aria-hidden="true" />
          )}
          {verified
            ? "Chain verified"
            : `Chain broken at entry ${firstTamperedLine ?? "?"}`}
        </span>
      </div>

      {rows.length === 0 ? (
        <p className="mt-3 text-xs text-muted">No audited events yet.</p>
      ) : (
        <ol className="mt-3 space-y-2" aria-label="Audit events">
          {[...rows].reverse().map((e, i) => (
            <li
              key={rows.length - i}
              className="flex items-start gap-2.5 text-xs"
            >
              <span
                className="mt-1 size-1.5 shrink-0 rounded-full bg-accent"
                aria-hidden="true"
              />
              <div>
                <p className="font-medium text-soft">{auditEventLabel(e.type)}</p>
                <p className="text-muted">{eventLine(e)}</p>
                {(e.reason ?? e.override_reason ?? e.description) && (
                  <p className="mt-0.5 text-muted/80 italic">
                    “{e.reason ?? e.override_reason ?? e.description}”
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
