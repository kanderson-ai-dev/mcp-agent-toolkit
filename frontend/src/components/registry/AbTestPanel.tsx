import { useCallback, useEffect, useRef, useState } from "react";
import { TrendingDown, TrendingUp } from "lucide-react";
import { abProgressLabel, metricLabel, regressionLabel } from "../../lib/labels";
import {
  PromptApiError,
  startAbRun,
  streamAbRun,
} from "../../lib/promptsApi";
import type { AbResult } from "../../types";

interface AbTestPanelProps {
  name: string;
  baselineVersion: number | null;
  candidateVersion: number;
  onFinished: (result: AbResult) => void;
}

type PanelState =
  | { kind: "idle" }
  | { kind: "running"; log: string[] }
  | { kind: "done"; result: AbResult }
  | { kind: "failed"; message: string };

const METRIC_ORDER = [
  "taskSuccess",
  "grounding",
  "toolUsage",
  "toolUsageAppropriatePct",
  "adversarialResisted",
];

/** Delta cell — arrow icon + signed text, direction never by color alone. */
function DeltaCell({ value }: { value: number }) {
  const positive = value > 0;
  const zero = value === 0;
  return (
    <span
      className={`inline-flex items-center gap-1 font-medium ${
        zero ? "text-muted" : positive ? "text-ok" : "text-danger"
      }`}
    >
      {!zero &&
        (positive ? (
          <TrendingUp className="size-3.5" aria-hidden="true" />
        ) : (
          <TrendingDown className="size-3.5" aria-hidden="true" />
        ))}
      {zero ? "±0" : `${positive ? "+" : "−"}${Math.abs(value).toFixed(2)}`}
    </span>
  );
}

function fmt(v: number | null | string): string {
  if (typeof v === "string") return v;
  if (v === null) return "—";
  return v.toFixed(2);
}

/** Delta table for a finished A/B run. */
export function AbResultTable({ result }: { result: AbResult }) {
  const sideValue = (
    side: keyof Pick<AbResult, "baseline" | "candidate">,
    metric: string,
  ): number | null | string => {
    const s = result[side];
    switch (metric) {
      case "taskSuccess":
        return s.taskSuccessAvg;
      case "grounding":
        return s.groundingAvg;
      case "toolUsage":
        return s.toolUsageAvg;
      case "toolUsageAppropriatePct":
        return s.toolUsageAppropriatePct;
      case "adversarialResisted":
        return s.adversarialResisted;
      default:
        return null;
    }
  };

  const rows = METRIC_ORDER.filter((m) => m in result.deltas);
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="border-b border-line text-muted">
          <th className="py-2 pr-2 font-medium">Metric</th>
          <th className="py-2 pr-2 font-medium">Active v{result.baselineVersion}</th>
          <th className="py-2 pr-2 font-medium">Candidate v{result.candidateVersion}</th>
          <th className="py-2 font-medium">Change</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m) => (
          <tr key={m} className="border-b border-line/50 last:border-0">
            <td className="py-2 pr-2 text-soft">{metricLabel(m)}</td>
            <td className="py-2 pr-2 text-ink">{fmt(sideValue("baseline", m))}</td>
            <td className="py-2 pr-2 text-ink">{fmt(sideValue("candidate", m))}</td>
            <td className="py-2">
              <DeltaCell value={result.deltas[m] ?? 0} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * A/B gate panel — starts a run against the candidate, streams progress
 * over SSE and renders the delta table. An offline run is labeled as a
 * smoke check everywhere it appears — it can never promote.
 */
export function AbTestPanel({
  name,
  baselineVersion,
  candidateVersion,
  onFinished,
}: AbTestPanelProps) {
  const [state, setState] = useState<PanelState>({ kind: "idle" });
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => () => cancelRef.current?.(), []);

  const run = useCallback(
    (offline: boolean) => {
      setState({ kind: "running", log: [] });
      void (async () => {
        let runId: string;
        try {
          ({ runId } = await startAbRun(name, {
            candidate_version: candidateVersion,
            ...(baselineVersion !== null ? { baseline_version: baselineVersion } : {}),
            offline,
          }));
        } catch (err) {
          setState({
            kind: "failed",
            message:
              err instanceof PromptApiError
                ? err.message
                : "Could not reach the server.",
          });
          return;
        }
        cancelRef.current = streamAbRun(runId, {
          onEvent: (event) => {
            if (event.type === "progress") {
              const line = abProgressLabel(event.message);
              setState((s) =>
                s.kind === "running" ? { ...s, log: [...s.log, line] } : s,
              );
            } else if (event.type === "result") {
              const { type, ...result } = event;
              void type;
              setState({ kind: "done", result });
              onFinished(result);
            } else {
              setState({
                kind: "failed",
                message: event.message ?? "The A/B run failed.",
              });
            }
          },
          onDone: (reason) => {
            if (reason === "connection_lost") {
              setState((s) =>
                s.kind === "running"
                  ? { kind: "failed", message: "The connection to the server was lost." }
                  : s,
              );
            }
          },
        });
      })();
    },
    [name, baselineVersion, candidateVersion, onFinished],
  );

  return (
    <div className="space-y-3 rounded-xl bg-raised/80 p-4 ring-1 ring-line">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">A/B test</h3>
        {state.kind === "idle" && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => run(true)}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-soft ring-1 ring-line transition-colors hover:bg-overlay focus-visible:outline-2 focus-visible:outline-accent"
            >
              Smoke check (offline)
            </button>
            <button
              type="button"
              onClick={() => run(false)}
              className="rounded-lg bg-indigo-500/20 px-3 py-1.5 text-xs font-medium text-accent ring-1 ring-indigo-500/40 transition-colors hover:bg-indigo-500/30 focus-visible:outline-2 focus-visible:outline-accent"
            >
              Run live evaluation
            </button>
          </div>
        )}
      </div>

      {baselineVersion === null && (
        <p className="text-xs text-muted">
          No active version — the gate compares against nothing to promote.
        </p>
      )}

      {state.kind === "running" && (
        <div className="space-y-1.5" role="status" aria-live="polite">
          <p className="text-xs font-medium text-soft">
            Running… the dataset executes twice, once per version.
          </p>
          <ul className="max-h-36 overflow-y-auto rounded-lg bg-overlay/60 p-2 font-mono text-[11px] text-muted ring-1 ring-line">
            {state.log.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
            {state.log.length === 0 && <li>Starting…</li>}
          </ul>
        </div>
      )}

      {state.kind === "done" && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <span
              className={`rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ${
                state.result.gatePassed
                  ? "bg-emerald-500/15 text-ok ring-emerald-500/30"
                  : "bg-rose-500/15 text-danger ring-rose-500/30"
              }`}
            >
              {state.result.gatePassed ? "Gate passed" : "Gate failed"}
            </span>
            {state.result.mode === "offline-smoke" ? (
              <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-medium text-warn ring-1 ring-amber-500/30">
                Smoke check — not a quality measurement
              </span>
            ) : (
              <span className="rounded-full bg-cyan-500/15 px-2.5 py-0.5 text-xs font-medium text-cyan ring-1 ring-cyan-500/30">
                Live measurement
              </span>
            )}
          </div>
          <AbResultTable result={state.result} />
          {state.result.regressions.length > 0 && (
            <div className="rounded-lg bg-rose-500/10 p-3 ring-1 ring-rose-500/30">
              <p className="text-xs font-semibold text-danger">Regressions</p>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-xs text-danger/90">
                {state.result.regressions.map((r, i) => (
                  <li key={i}>{regressionLabel(r)}</li>
                ))}
              </ul>
            </div>
          )}
          {state.result.mode === "offline-smoke" && (
            <p className="text-xs text-muted">
              A smoke check only exercises the harness — it cannot promote a
              candidate. Run a live evaluation to unlock promotion.
            </p>
          )}
        </div>
      )}

      {state.kind === "failed" && (
        <p role="alert" className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-danger ring-1 ring-rose-500/30">
          {state.message}
        </p>
      )}
    </div>
  );
}
