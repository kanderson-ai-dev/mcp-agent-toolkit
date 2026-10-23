import { useCallback, useEffect, useRef, useState } from "react";
import { EmptyState } from "./components/EmptyState";
import { Header } from "./components/Header";
import { PromptComposer } from "./components/PromptComposer";
import { RegistryView } from "./components/registry/RegistryView";
import { RunView } from "./components/RunView";
import { ApiError, fetchStatus, fetchTools, submitQuestion } from "./lib/api";
import { modeLabel, toolMeta } from "./lib/labels";
import { streamRun } from "./lib/sse";
import type {
  AgentEvent,
  ConsolePhase,
  HistoryTurn,
  RunRecord,
  StatusInfo,
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
  const [status, setStatus] = useState<StatusInfo | null>(null);
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
    fetchStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  // Follow the stream only while the reader sits near the bottom —
  // scrolling up detaches; submitting a new question re-engages.
  const followRef = useRef(true);
  useEffect(() => {
    const onScroll = () => {
      const gap =
        document.documentElement.scrollHeight - window.scrollY - window.innerHeight;
      followRef.current = gap < 96;
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);
  useEffect(() => {
    if (followRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [runs]);

  useEffect(() => () => cancelRef.current?.(), []);

  const patchLiveRun = useCallback((patch: (run: RunRecord) => RunRecord) => {
    setRuns((prev) =>
      prev.length === 0 ? prev : [...prev.slice(0, -1), patch(prev[prev.length - 1]!)],
    );
  }, []);

  // Follow-up context: completed runs become user/assistant turns —
  // bounded server-side, so the client sends everything it has.
  const historyRef = useRef<HistoryTurn[]>([]);

  const handleSubmit = useCallback(
    (question: string) => {
      if (phase === "streaming") return;
      const started = performance.now();
      const history = historyRef.current;
      followRef.current = true;
      setPhase("streaming");
      setAnnouncement("Run started");
      setRuns((prev) => [
        ...prev,
        { id: crypto.randomUUID(), question, steps: [] },
      ]);

      void (async () => {
        let runId: string;
        try {
          runId = await submitQuestion(question, history);
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
            if (event.type === "answer_delta") {
              // Token-level streaming — accumulate into the provisional
              // card; the `final` event replaces it with the full text.
              patchLiveRun((run) => ({
                ...run,
                streamingAnswer: (run.streamingAnswer ?? "") + event.content,
              }));
            } else if (event.type === "final") {
              setAnnouncement("Run complete. The final answer is ready.");
              historyRef.current = [
                ...historyRef.current,
                { role: "user", content: question },
                { role: "assistant", content: event.content },
              ];
              patchLiveRun((run) => ({
                ...run,
                steps: applyEvent(run.steps, event),
                streamingAnswer: undefined,
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
              patchLiveRun((run) => ({
                ...run,
                streamingAnswer: undefined,
                errorMessage: event.message,
              }));
            } else {
              if (event.type === "tool_result") {
                setAnnouncement(
                  `${toolMeta(event.tool).label} finished: ${event.ok ? "success" : "error"}.`,
                );
              }
              patchLiveRun((run) => {
                let steps = applyEvent(run.steps, event);
                let streamingAnswer = run.streamingAnswer;
                // A model that narrates before calling tools: the partial
                // text is real intermediate output — fold it into a note
                // above the new tool step rather than dropping it.
                if (streamingAnswer && event.type === "tool_call") {
                  steps = [
                    ...steps.slice(0, -1),
                    {
                      kind: "note",
                      iteration: event.iteration,
                      content: streamingAnswer,
                    },
                    steps[steps.length - 1]!,
                  ];
                  streamingAnswer = undefined;
                }
                return { ...run, steps, streamingAnswer };
              });
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

  const startNewConversation = useCallback(() => {
    if (phase === "streaming") return;
    historyRef.current = [];
    setRuns([]);
    setPhase("idle");
    setAnnouncement("Started a new conversation.");
  }, [phase]);

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
              className={`relative flex items-center px-3 py-2.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent max-sm:min-h-11 ${
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
              className="mt-3 inline-flex items-center rounded-lg bg-danger/15 px-4 py-2 text-xs font-medium text-danger ring-1 ring-danger/30 transition-colors hover:bg-danger/25 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger max-sm:min-h-11"
            >
              Try again
            </button>
          </div>
        ) : runs.length === 0 ? (
          <EmptyState examples={EXAMPLES} onPick={setDraft} />
        ) : (
          <div className="space-y-8">
            <div className="flex justify-end">
              <button
                type="button"
                onClick={startNewConversation}
                disabled={phase === "streaming"}
                className="rounded-lg px-3 py-1.5 text-xs font-medium text-muted ring-1 ring-line transition-colors hover:text-soft hover:ring-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50 max-sm:min-h-11"
              >
                New conversation
              </button>
            </div>
            {runs.map((run) => (
              <RunView
                key={run.id}
                run={run}
                onRetry={
                  run.errorMessage ? () => handleSubmit(run.question) : undefined
                }
                retryDisabled={phase === "streaming"}
              />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      {status && (
        <footer className="border-t border-line/60 px-4 py-3 text-center text-[11px] text-muted sm:px-6">
          {[
            modeLabel(status.mode),
            status.prompts.agentSystem
              ? `Agent prompt v${status.prompts.agentSystem.version}`
              : null,
            status.lastEvaluation
              ? `Last evaluation: ${status.lastEvaluation.passed ? "passed" : "failed"}`
              : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </footer>
      )}

      {tab === "console" && (
        <PromptComposer
          disabled={phase === "streaming" || apiDown}
          busy={phase === "streaming"}
          value={draft}
          onChange={setDraft}
          onSubmit={handleSubmit}
        />
      )}
    </div>
  );
}
