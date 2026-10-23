import { useCallback, useEffect, useState } from "react";
import { Lock } from "lucide-react";
import { promptNameLabel } from "../../lib/labels";
import {
  fetchAudit,
  fetchDiff,
  fetchPrompts,
  fetchPromptVersion,
  fetchAbResult,
  PromptApiError,
} from "../../lib/promptsApi";
import type {
  AbResult,
  AuditResponse,
  PromptVersionDetail,
  PromptVersionInfo,
} from "../../types";
import { AbTestPanel } from "./AbTestPanel";
import { AuditTimeline } from "./AuditTimeline";
import { CandidateEditor } from "./CandidateEditor";
import { DiffView } from "./DiffView";
import { PromoteDialog } from "./PromoteDialog";
import { RollbackButton } from "./RollbackButton";
import { VersionList } from "./VersionList";

type DetailPanel =
  | { kind: "idle" }
  | { kind: "diff"; from: number; to: number; text: string }
  | { kind: "editor" };

/**
 * Prompt Registry tab — version list on the left, detail + actions on
 * the right. Read-only when the server has admin mutations disabled;
 * the API 403 remains the enforcement, the UI just doesn't offer them.
 */
export function RegistryView() {
  const [names, setNames] = useState<string[]>([]);
  const [prompts, setPrompts] = useState<Record<string, PromptVersionInfo[]>>({});
  const [adminEnabled, setAdminEnabled] = useState(false);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [detail, setDetail] = useState<PromptVersionDetail | null>(null);
  const [panel, setPanel] = useState<DetailPanel>({ kind: "idle" });
  const [evidence, setEvidence] = useState<AbResult | null>(null);
  const [audit, setAudit] = useState<AuditResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  /** Re-fetch everything — called from event handlers after mutations. */
  const refresh = useCallback(() => setRefreshTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchPrompts(), fetchAudit()])
      .then(([list, auditRes]) => {
        if (cancelled) return;
        setPrompts(list.prompts);
        setAdminEnabled(list.adminEnabled);
        setNames(Object.keys(list.prompts));
        setAudit(auditRes);
        setLoadError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(
          err instanceof PromptApiError
            ? err.message
            : "Could not load the registry.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  // The selected name defaults to the first prompt — derived, no state.
  const effectiveName = selectedName ?? names[0] ?? null;

  const versions = effectiveName ? (prompts[effectiveName] ?? []) : [];
  const activeVersion = versions.find((v) => v.status === "promoted")?.version ?? null;
  const selected = versions.find((v) => v.version === selectedVersion) ?? null;

  // Load full detail for the selected version — the render side guards
  // on name/version so a stale fetch never shows against a new selection.
  useEffect(() => {
    if (!effectiveName || selectedVersion === null) return;
    let cancelled = false;
    fetchPromptVersion(effectiveName, selectedVersion)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [effectiveName, selectedVersion]);

  // Load the latest persisted A/B evidence for a selected candidate.
  useEffect(() => {
    if (!effectiveName || selected?.status !== "candidate") return;
    let cancelled = false;
    fetchAbResult(effectiveName, selected.version)
      .then((r) => {
        if (!cancelled) setEvidence(r);
      })
      .catch(() => {
        if (!cancelled) setEvidence(null);
      });
    return () => {
      cancelled = true;
    };
  }, [effectiveName, selected]);

  const showDiff = useCallback(async () => {
    if (!effectiveName || !selected || activeVersion === null) return;
    try {
      const d = await fetchDiff(effectiveName, activeVersion, selected.version);
      setPanel({ kind: "diff", from: activeVersion, to: selected.version, text: d.diff });
    } catch {
      setPanel({ kind: "idle" });
    }
  }, [effectiveName, selected, activeVersion]);

  const pickName = (name: string) => {
    setSelectedName(name);
    setSelectedVersion(null);
    setPanel({ kind: "idle" });
  };

  const pickVersion = (version: number) => {
    setSelectedVersion(version);
    setPanel((p) => (p.kind === "editor" ? p : { kind: "idle" }));
  };

  if (loadError) {
    return (
      <div className="mt-10 rounded-xl bg-rose-500/10 p-5 text-center ring-1 ring-rose-500/30">
        <p className="text-sm font-semibold text-danger">{loadError}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Prompt-name switcher */}
      <div role="tablist" aria-label="Prompt" className="flex gap-1 rounded-xl bg-overlay/60 p-1 ring-1 ring-line">
        {names.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            aria-selected={name === effectiveName}
            onClick={() => pickName(name)}
            className={`flex-1 rounded-lg px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent ${
              name === effectiveName
                ? "bg-raised text-ink ring-1 ring-line-strong"
                : "text-muted hover:text-soft"
            }`}
          >
            {promptNameLabel(name)}
          </button>
        ))}
      </div>

      {!adminEnabled && (
        <div className="flex items-start gap-2 rounded-xl bg-amber-500/10 p-3 text-xs text-warn ring-1 ring-amber-500/30">
          <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
          <p>
            <span className="font-semibold">Read-only.</span> Prompt mutations
            are disabled on this server — set{" "}
            <code className="font-mono">ENABLE_PROMPT_ADMIN=1</code> and
            restart to create candidates, run A/B tests, promote or roll back.
          </p>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        {/* Version list */}
        <div>
          {adminEnabled && (
            <button
              type="button"
              onClick={() => setPanel({ kind: "editor" })}
              className="mb-3 w-full rounded-lg bg-indigo-500/20 px-3 py-2 text-xs font-medium text-accent ring-1 ring-indigo-500/40 transition-colors hover:bg-indigo-500/30 focus-visible:outline-2 focus-visible:outline-accent"
            >
              New candidate from active
            </button>
          )}
          <VersionList
            versions={versions}
            selectedVersion={selectedVersion}
            onSelect={pickVersion}
          />
        </div>

        {/* Detail + actions */}
        <div className="space-y-4">
          {panel.kind === "editor" && effectiveName ? (
            <CandidateLoader
              name={effectiveName}
              baseVersion={activeVersion}
              onCancel={() => setPanel({ kind: "idle" })}
              onCreated={(created) => {
                setPanel({ kind: "idle" });
                setSelectedVersion(created.version);
                refresh();
              }}
            />
          ) : null}

          {selected && (
            <div className="rounded-xl bg-raised/80 p-4 ring-1 ring-line">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-ink">
                  {promptNameLabel(selected.name)} — version {selected.version}
                </h3>
                <div className="flex items-center gap-2">
                  {selected.status !== "promoted" && activeVersion !== null && (
                    <button
                      type="button"
                      onClick={() => void showDiff()}
                      className="rounded-lg px-3 py-1.5 text-xs font-medium text-soft ring-1 ring-line transition-colors hover:bg-overlay focus-visible:outline-2 focus-visible:outline-accent"
                    >
                      Compare with active
                    </button>
                  )}
                  {adminEnabled && selected.status === "candidate" && (
                    <PromoteDialog
                      name={selected.name}
                      version={selected.version}
                      evidence={
                        evidence?.name === selected.name &&
                        evidence?.candidateVersion === selected.version
                          ? evidence
                          : null
                      }
                      onPromoted={() => refresh()}
                    />
                  )}
                  {adminEnabled && selected.status === "rolled_back" && (
                    <RollbackButton
                      name={selected.name}
                      toVersion={selected.version}
                      onRolledBack={() => refresh()}
                    />
                  )}
                </div>
              </div>
              {detail?.name === selected.name &&
                detail?.version === selected.version && (
                  <pre className="mt-3 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg bg-overlay/60 p-3 font-mono text-[11px] leading-relaxed text-soft ring-1 ring-line">
                    {detail.template}
                  </pre>
                )}
            </div>
          )}

          {panel.kind === "diff" && <DiffView diff={panel.text} fromVersion={panel.from} toVersion={panel.to} />}

          {adminEnabled && selected?.status === "candidate" && effectiveName && (
            <AbTestPanel
              name={effectiveName}
              baselineVersion={activeVersion}
              candidateVersion={selected.version}
              onFinished={(r) => {
                setEvidence(r);
                refresh();
              }}
            />
          )}

          {audit && (
            <AuditTimeline
              events={audit.events}
              verified={audit.verified}
              firstTamperedLine={audit.firstTamperedLine}
            />
          )}
        </div>
      </div>
    </div>
  );
}

/** Fetches the active template to prefill the editor, then mounts it. */
function CandidateLoader({
  name,
  baseVersion,
  onCreated,
  onCancel,
}: {
  name: string;
  baseVersion: number | null;
  onCreated: (created: PromptVersionDetail) => void;
  onCancel: () => void;
}) {
  const [base, setBase] = useState<string | null>(null);
  useEffect(() => {
    if (baseVersion === null) return;
    let cancelled = false;
    fetchPromptVersion(name, baseVersion)
      .then((d) => {
        if (!cancelled) setBase(d.template);
      })
      .catch(() => {
        if (!cancelled) setBase("");
      });
    return () => {
      cancelled = true;
    };
  }, [name, baseVersion]);

  if (baseVersion === null || base === "") {
    return (
      <CandidateEditor
        name={name}
        baseTemplate=""
        onCreated={onCreated}
        onCancel={onCancel}
      />
    );
  }
  if (base === null) {
    return <p className="text-xs text-muted">Loading template…</p>;
  }
  return (
    <CandidateEditor
      name={name}
      baseTemplate={base}
      onCreated={onCreated}
      onCancel={onCancel}
    />
  );
}
