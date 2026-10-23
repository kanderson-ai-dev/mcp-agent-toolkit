import { Minus, Plus } from "lucide-react";
import { promptNameLabel } from "../../lib/labels";

interface DiffViewProps {
  diff: string;
  fromVersion: number;
  toVersion: number;
}

interface DiffLine {
  kind: "add" | "remove" | "context" | "meta";
  text: string;
}

/** `--- agent_system/v1` → `Agent system prompt · version 1` (raw ids
 * never render — the prompt name is humanized, the version kept). */
function humanizeHeader(line: string): string {
  const m = /^[+-]{3}\s*([A-Za-z0-9_-]+)\/v(\d+)/.exec(line);
  if (m) return `${promptNameLabel(m[1]!)} · version ${m[2]}`;
  return line;
}

function parseDiff(diff: string): DiffLine[] {
  return diff.split("\n").map((line) => {
    if (line.startsWith("+++") || line.startsWith("---"))
      return { kind: "meta", text: humanizeHeader(line) };
    if (line.startsWith("+")) return { kind: "add", text: line.slice(1) };
    if (line.startsWith("-")) return { kind: "remove", text: line.slice(1) };
    if (line.startsWith("@@")) return { kind: "meta", text: line };
    return { kind: "context", text: line.startsWith(" ") ? line.slice(1) : line };
  });
}

const LINE_CLASS = {
  add: "bg-emerald-500/[0.08] text-ink",
  remove: "bg-rose-500/[0.08] text-ink",
  context: "text-soft",
  meta: "text-muted/70",
} as const;

/**
 * Hand-rolled unified-diff renderer: each line is prefixed by a visible
 * +/− marker (icon + glyph) so added/removed lines never depend on
 * color alone.
 */
export function DiffView({ diff, fromVersion, toVersion }: DiffViewProps) {
  const lines = parseDiff(diff);
  if (lines.every((l) => l.kind === "context" || l.kind === "meta")) {
    return (
      <p className="rounded-lg bg-overlay/60 p-3 text-xs text-muted ring-1 ring-line">
        Version {fromVersion} and version {toVersion} are identical.
      </p>
    );
  }
  return (
    <div
      className="overflow-x-auto rounded-xl bg-raised/80 font-mono text-xs leading-relaxed ring-1 ring-line"
      role="region"
      aria-label={`Changes from version ${fromVersion} to version ${toVersion}`}
    >
      <div className="border-b border-line px-3 py-2 text-[11px] font-sans text-muted">
        Changes from version {fromVersion} to version {toVersion}
      </div>
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, i) => (
            <tr key={i} className={LINE_CLASS[line.kind]}>
              <td className="w-8 select-none px-2 py-0 text-center align-top" aria-hidden="true">
                {line.kind === "add" && <Plus className="inline size-3.5 text-ok" />}
                {line.kind === "remove" && <Minus className="inline size-3.5 text-danger" />}
              </td>
              <td className="whitespace-pre-wrap break-all px-1 py-0">
                {line.kind === "add" && <span className="sr-only">Added: </span>}
                {line.kind === "remove" && <span className="sr-only">Removed: </span>}
                {/* literal template text may name tools verbatim — <code> */}
                <code className="font-mono">{line.text === "" ? " " : line.text}</code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
