import { useState } from "react";
import { createCandidate, PromptApiError } from "../../lib/promptsApi";
import type { PromptVersionDetail } from "../../types";

export const MAX_TEMPLATE_CHARS = 8000;

interface CandidateEditorProps {
  name: string;
  /** The version this candidate is based on — shown for context. */
  baseTemplate: string;
  onCreated: (created: PromptVersionDetail) => void;
  onCancel: () => void;
}

/**
 * Editor for a new candidate version — prefilled with the template it
 * derives from, live character counter and client-side validation that
 * mirrors the API limits (server remains authoritative).
 */
export function CandidateEditor({
  name,
  baseTemplate,
  onCreated,
  onCancel,
}: CandidateEditorProps) {
  const [template, setTemplate] = useState(baseTemplate);
  const [author, setAuthor] = useState("console");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const chars = template.length;
  const overLimit = chars > MAX_TEMPLATE_CHARS;
  const templateEmpty = template.trim().length === 0;
  const descriptionEmpty = description.trim().length === 0;
  const authorInvalid = !/^[A-Za-z0-9_@.-]{1,64}$/.test(author);
  const invalid = overLimit || templateEmpty || descriptionEmpty || authorInvalid;

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const created = await createCandidate(name, {
        template,
        author,
        description: description.trim(),
      });
      onCreated(created);
    } catch (err) {
      setError(
        err instanceof PromptApiError
          ? err.message
          : "Could not reach the server.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 rounded-xl bg-raised/80 p-4 ring-1 ring-line">
      <h3 className="text-sm font-semibold text-ink">New candidate</h3>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-soft">Author</span>
        <input
          type="text"
          value={author}
          onChange={(e) => setAuthor(e.target.value)}
          className="w-full rounded-lg bg-overlay/70 px-3 py-2 text-sm text-ink ring-1 ring-line focus-visible:outline-2 focus-visible:outline-accent"
        />
        {authorInvalid && (
          <span className="mt-1 block text-xs text-danger">
            Letters, digits, dots, dashes, underscores or @ — up to 64 characters.
          </span>
        )}
      </label>

      <label className="block">
        <span className="mb-1 block text-xs font-medium text-soft">Description</span>
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What this candidate changes — recorded in the audit log"
          className="w-full rounded-lg bg-overlay/70 px-3 py-2 text-sm text-ink ring-1 ring-line placeholder:text-faint focus-visible:outline-2 focus-visible:outline-accent"
        />
        {descriptionEmpty && (
          <span className="mt-1 block text-xs text-danger">
            A short description is required.
          </span>
        )}
      </label>

      <label className="block">
        <span className="mb-1 flex items-baseline justify-between text-xs font-medium text-soft">
          <span>Template</span>
          <span
            className={overLimit ? "font-semibold text-danger" : "text-muted"}
            aria-live="polite"
          >
            {chars.toLocaleString()} / {MAX_TEMPLATE_CHARS.toLocaleString()}
          </span>
        </span>
        <textarea
          value={template}
          onChange={(e) => setTemplate(e.target.value)}
          rows={14}
          spellCheck={false}
          className="w-full rounded-lg bg-overlay/70 px-3 py-2 font-mono text-xs leading-relaxed text-ink ring-1 ring-line focus-visible:outline-2 focus-visible:outline-accent"
        />
        {templateEmpty && (
          <span className="mt-1 block text-xs text-danger">
            The template cannot be empty.
          </span>
        )}
        {overLimit && (
          <span className="mt-1 block text-xs text-danger">
            The template is over the size limit — shorten it before creating the candidate.
          </span>
        )}
      </label>

      {error && (
        <p role="alert" className="rounded-lg bg-rose-500/10 px-3 py-2 text-xs text-danger ring-1 ring-rose-500/30">
          {error}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-3 py-2 text-xs font-medium text-soft ring-1 ring-line transition-colors hover:bg-overlay focus-visible:outline-2 focus-visible:outline-accent"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={invalid || busy}
          className="rounded-lg bg-indigo-500/20 px-3 py-2 text-xs font-medium text-accent ring-1 ring-indigo-500/40 transition-colors hover:bg-indigo-500/30 focus-visible:outline-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Creating…" : "Create candidate"}
        </button>
      </div>
    </div>
  );
}
