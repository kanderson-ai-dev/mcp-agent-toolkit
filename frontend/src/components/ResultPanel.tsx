import { useState } from "react";
import { AlertTriangle, ChevronDown, ExternalLink, ShieldAlert } from "lucide-react";
import clsx from "clsx";
import { humanizeBytes, humanizeDate } from "../lib/format";
import { columnLabel, errorCodeLabel, providerLabel } from "../lib/labels";
import type { ToolStep } from "../types";

interface ResultPanelProps {
  step: ToolStep;
}

interface SearchHit {
  title?: unknown;
  url?: unknown;
  snippet?: unknown;
}

function rec(v: unknown): Record<string, unknown> {
  return typeof v === "object" && v !== null ? (v as Record<string, unknown>) : {};
}

function text(v: unknown): string {
  return typeof v === "string" ? v : "";
}

/**
 * Structured, per-tool result rendering. The `tool_result` event carries
 * the parsed `{ status, data | code, message }` envelope; each tool's
 * `data` shape is rendered in human terms (result lists, tables, file
 * notes) — raw JSON stays behind "View raw JSON" on the card itself.
 */
export function ResultPanel({ step }: ResultPanelProps) {
  const result = step.result;
  if (!result) {
    return step.preview ? (
      <p className="mt-2 line-clamp-3 text-xs text-muted">{step.preview}</p>
    ) : null;
  }

  if (result.status === "error") {
    const guardrail = result.code === "forbidden" || result.code === "invalid_input";
    return (
      <div className="mt-3 flex items-start gap-2.5 rounded-lg bg-rose-500/10 p-3 ring-1 ring-rose-500/25">
        {guardrail ? (
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-danger" />
        ) : (
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" />
        )}
        <div className="min-w-0">
          <p className="text-xs font-semibold text-danger">
            {errorCodeLabel(result.code)}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-danger/80">
            {result.message}
          </p>
        </div>
      </div>
    );
  }

  const data = rec(result.data);
  switch (step.tool) {
    case "web_search":
      return <WebSearchResult data={data} />;
    case "db_query":
      return <DbQueryResult data={data} />;
    case "read_file":
      return <ReadFileResult data={data} />;
    case "write_file":
      return <WriteFileResult data={data} />;
    default:
      return null;
  }
}

function WebSearchResult({ data }: { data: Record<string, unknown> }) {
  const hits = Array.isArray(data.results) ? (data.results as SearchHit[]) : [];
  return (
    <div className="mt-3">
      <p className="text-xs text-muted">
        Source: <span className="font-medium text-soft">{providerLabel(data.provider)}</span>
        {" · "}
        {hits.length} {hits.length === 1 ? "result" : "results"}
      </p>
      {hits.length === 0 && (
        <p className="mt-2 rounded-lg bg-page/60 p-3 text-xs text-muted ring-1 ring-line">
          No results for this query — try rephrasing or a different source.
        </p>
      )}
      <ul className="mt-2 space-y-2">
        {hits.map((hit, i) => {
          const url = text(hit.url);
          return (
            <li
              key={i}
              className="rounded-lg bg-page/60 p-3 ring-1 ring-line transition-colors hover:ring-line-strong"
            >
              <a
                href={url || undefined}
                target="_blank"
                rel="noopener noreferrer"
                className="group inline-flex items-center gap-1.5 text-sm font-medium text-cyan hover:text-cyan"
              >
                <span className="line-clamp-1">{text(hit.title) || url}</span>
                <ExternalLink className="size-3.5 shrink-0 opacity-60 group-hover:opacity-100" />
              </a>
              {text(hit.snippet) && (
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted">
                  {text(hit.snippet)}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const MAX_TABLE_ROWS = 10;

function DbQueryResult({ data }: { data: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false);
  const rows = Array.isArray(data.rows) ? data.rows.map(rec) : [];
  const rowCount = typeof data.row_count === "number" ? data.row_count : rows.length;
  const truncated = data.truncated === true;
  const columns = [...new Set(rows.flatMap((r) => Object.keys(r)))];
  const visible = expanded ? rows : rows.slice(0, MAX_TABLE_ROWS);

  return (
    <div className="mt-3">
      <p className="text-xs text-muted">
        {rowCount} {rowCount === 1 ? "row returned" : "rows returned"}
        {truncated && (
          <span className="ml-2 inline-flex items-center gap-1 text-warn">
            <AlertTriangle className="size-3" />
            results truncated
          </span>
        )}
      </p>
      {rows.length > 0 && (
        <div className="mt-2 overflow-x-auto rounded-lg ring-1 ring-line">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="bg-overlay/60">
                {columns.map((col) => (
                  <th
                    key={col}
                    className="whitespace-nowrap px-3 py-2 font-semibold text-soft"
                  >
                    {columnLabel(col)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line bg-page/60">
              {visible.map((row, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="max-w-72 truncate px-3 py-2 align-top text-soft"
                      title={formatCell(row[col])}
                    >
                      {formatCell(row[col])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > MAX_TABLE_ROWS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-muted transition-colors hover:text-soft max-sm:min-h-11"
        >
          {expanded ? "Show less" : `Show all ${rows.length} rows`}
          <ChevronDown className={clsx("size-3.5 transition-transform", expanded && "rotate-180")} />
        </button>
      )}
    </div>
  );
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return ISO_DATE.test(v) ? humanizeDate(v) : v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

const CONTENT_PREVIEW_CHARS = 600;

function ReadFileResult({ data }: { data: Record<string, unknown> }) {
  const content = text(data.content);
  const clipped = content.length > CONTENT_PREVIEW_CHARS;
  return (
    <div className="mt-3">
      <p className="text-xs text-muted">
        <span className="font-medium text-soft">{text(data.path)}</span>
        {" · "}
        {typeof data.bytes === "number" ? humanizeBytes(data.bytes) : ""}
      </p>
      <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-page/60 p-3 text-xs leading-relaxed text-soft ring-1 ring-line">
        {clipped ? `${content.slice(0, CONTENT_PREVIEW_CHARS)}…` : content}
      </pre>
      {clipped && (
        <p className="mt-1 text-xs text-muted">
          Preview — the file continues for{" "}
          {(content.length - CONTENT_PREVIEW_CHARS).toLocaleString("en-US")} more
          characters
        </p>
      )}
    </div>
  );
}

function WriteFileResult({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="mt-3 flex items-center gap-2.5 rounded-lg bg-emerald-500/10 p-3 ring-1 ring-emerald-500/25">
      <p className="text-xs text-ok">
        File saved: <span className="font-medium">{text(data.path)}</span>
        {typeof data.bytes_written === "number" && (
          <span className="text-ok/70">
            {" · "}
            {humanizeBytes(data.bytes_written)} written
          </span>
        )}
      </p>
    </div>
  );
}
