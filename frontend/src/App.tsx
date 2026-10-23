import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState } from "./components/EmptyState";
import { Header } from "./components/Header";
import { PromptComposer } from "./components/PromptComposer";
import { RegistryView } from "./components/registry/RegistryView";
import { RunView } from "./components/RunView";
import { ApiError, fetchTools, submitQuestion } from "./lib/api";
import { toolMeta } from "./lib/labels";
import { streamRun } from "./lib/sse";
import type {
  AgentEvent,
  ConsolePhase,
  RunRecord,
  TimelineStep,
  ToolsInfo,
} from "./types";

const EXAMPLES = __EXAMPLE_QUESTIONS__;

/** Merge a streamed AgentEvent into the run's timeline (immutable update). */
function applyEvent(steps: TimelineStep[], event: AgentEvent): TimelineStep[] {
  switch (event.type) {
    case "tool_call":
      return [
        ...steps,
        {
          kind: "tool",
          requestId: event.requestId,
          iteration: event.iteration,
          tool: event.tool,
          args: event.args,
          status: "running",
        },
      ];
    case "tool_result":
      return steps.map((s) =>
        s.kind === "tool" && s.requestId === event.requestId
          ? {
              ...s,
              status: event.ok ? "ok" : "error",
              durationMs: event.durationMs,
              preview: event.preview,
              result: event.result,
            }
          : s,
      );
    case "llm_message":
      return [
        ...steps,
        { kind: "note", iteration: event.iteration, content: event.content },
      ];
    default:
      return steps;
  }
}

export default function App() {
  const [toolsInfo, setToolsInfo] = useState<ToolsInfo | null>(null);
  const [apiDown, setApiDown] = useState(false);
  const [phase, setPhase] = useState<ConsolePhase>("idle");
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [announcement, setAnnouncement] = useState("");
  const [tab, setTab] = useState<"console" | "registry">("console");
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    fetchTools()
      .then(setToolsInfo)
      .catch(() => setApiDown(true));
  }, []);

  // Follow the stream as events arrive — smooth, no layout jumps.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [runs]);

  useEffect(() => () => cancelRef.current?.(), []);

  const patchLiveRun = useCallback((patch: (run: RunRecord) => RunRecord) => {
    setRuns((prev) =>
      prev.length === 0 ? prev : [...prev.slice(0, -1), patch(prev[prev.length - 1]!)],
    );
  }, []);

  const handleSubmit = useCallback(
    (question: string) => {
      if (phase === "streaming") return;
      const started = performance.now();
      setPhase("streaming");
      setAnnouncement("Run started");
      setRuns((prev) => [
        ...prev,
        { id: crypto.randomUUID(), question, steps: [] },
      ]);

      void (async () => {
        let runId: string;
        try {
          runId = await submitQuestion(question);
        } catch (err) {
          const message =
            err instanceof ApiError
              ? err.message
              : "Could not reach the server. Is it running?";
          patchLiveRun((run) => ({ ...run, errorMessage: message }));
          setPhase("error");
          return;
        }

        cancelRef.current = streamRun(runId, {
          onEvent: (event) => {
            if (event.type === "final") {
              setAnnouncement("Run complete. The final answer is ready.");
              patchLiveRun((run) => ({
                ...run,
                steps: applyEvent(run.steps, event),
                final: {
                  content: event.content,
                  iterations: event.iterations,
                  toolCalls: event.toolCalls,
                  terminatedBy: event.terminatedBy,
                  promptVersion: event.promptVersion,
                  usage: event.usage,
                  costUsd: event.costUsd,
                  durationMs: event.durationMs,
                },
                elapsedMs: Math.round(performance.now() - started),
              }));
            } else if (event.type === "error") {
              setAnnouncement(`The run failed: ${event.message}`);
              patchLiveRun((run) => ({ ...run, errorMessage: event.message }));
            } else {
              if (event.type === "tool_result") {
                setAnnouncement(
                  `${toolMeta(event.tool).label} finished: ${event.ok ? "success" : "error"}.`,
                );
              }
              patchLiveRun((run) => ({ ...run, steps: applyEvent(run.steps, event) }));
            }
          },
          onDone: (reason) => {
            if (reason === "connection_lost") {
              patchLiveRun((run) =>
                run.final || run.errorMessage
                  ? run
                  : {
                      ...run,
                      errorMessage:
                        "Connection to the server was lost before the run finished.",
                    },
              );
            }
            setPhase((p) => (p === "streaming" ? "done" : p));
          },
        });
      })();
    },
    [phase, patchLiveRun],
  );

  const liveIndex = phase === "streaming" ? runs.length - 1 : -1;

  return (
    <div className="flex min-h-dvh flex-col">
      {/* Screen-reader announcements: run start, each tool result, completion. */}
      <div role="status" aria-live="polite" className="sr-only">
        {announcement}
      </div>
      <Header toolsInfo={toolsInfo} connected={!apiDown} />

      <nav
        aria-label="Views"
        className="border-b border-line/60 bg-page/60"
      >
        <div className="mx-auto flex max-w-4xl gap-1 px-4 sm:px-6">
          {(
            [
              ["console", "Console"],
              ["registry", "Prompt Registry"],
            ] as const
          ).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              aria-current={tab === id ? "page" : undefined}
              className={`relative px-3 py-2.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
                tab === id ? "text-ink" : "text-muted hover:text-soft"
              }`}
            >
              {label}
              {tab === id && (
                <span
                  aria-hidden="true"
                  className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-accent"
                />
              )}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto w-full max-w-4xl flex-1 px-4 pb-6 pt-6 sm:px-6">
        {tab === "registry" ? (
          <RegistryView />
        ) : apiDown && runs.length === 0 ? (
          <div className="mt-10 rounded-xl bg-rose-500/10 p-5 text-center ring-1 ring-rose-500/30">
            <p className="text-sm font-semibold text-danger">
              Can't reach the agent.
            </p>
            <p className="mt-1 text-xs text-danger/70">
              Check that the server is running (
              <code className="font-mono">uv run mcp-toolkit-web</code>), then
              try again.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-3 rounded-lg bg-danger/15 px-4 py-2 text-xs font-medium text-danger ring-1 ring-danger/30 transition-colors hover:bg-danger/25 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger"
            >
              Try again
            </button>
          </div>
        ) : runs.length === 0 ? (
          <EmptyState examples={EXAMPLES} onPick={setDraft} />
        ) : (
          <div className="space-y-8">
            {runs.map((run, i) => (
              <RunView key={run.id} run={run} live={i === liveIndex} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      {tab === "console" && (
        <PromptComposer
          disabled={phase === "streaming" || apiDown}
          value={draft}
          onChange={setDraft}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
