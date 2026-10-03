import { useState } from "react";
import { AlertTriangle, ChevronDown, ExternalLink, ShieldAlert } from "lucide-react";
import clsx from "clsx";
import { humanizeBytes } from "../lib/format";
import { errorCodeLabel, providerLabel } from "../lib/labels";
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
 * notes) — raw JSON stays behind "Ver JSON técnico" on the card itself.
 */
export function ResultPanel({ step }: ResultPanelProps) {
  const result = step.result;
  if (!result) {
    return step.preview ? (
      <p className="mt-2 line-clamp-3 text-xs text-slate-400">{step.preview}</p>
    ) : null;
  }

  if (result.status === "error") {
    const guardrail = result.code === "forbidden" || result.code === "invalid_input";
    return (
      <div className="mt-3 flex items-start gap-2.5 rounded-lg bg-rose-500/10 p-3 ring-1 ring-rose-500/25">
        {guardrail ? (
          <ShieldAlert className="mt-0.5 size-4 shrink-0 text-rose-300" />
        ) : (
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-rose-300" />
        )}
        <div className="min-w-0">
          <p className="text-xs font-semibold text-rose-200">
            {errorCodeLabel(result.code)}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-rose-200/80">
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
      <p className="text-xs text-slate-400">
        Fuente: <span className="font-medium text-slate-300">{providerLabel(data.provider)}</span>
        {" · "}
        {hits.length} {hits.length === 1 ? "resultado" : "resultados"}
      </p>
      <ul className="mt-2 space-y-2">
        {hits.map((hit, i) => {
          const url = text(hit.url);
          return (
            <li
              key={i}
              className="rounded-lg bg-slate-950/60 p-3 ring-1 ring-slate-800 transition-colors hover:ring-slate-700"
            >
              <a
                href={url || undefined}
                target="_blank"
                rel="noreferrer"
                className="group inline-flex items-center gap-1.5 text-sm font-medium text-cyan-300 hover:text-cyan-200"
              >
                <span className="line-clamp-1">{text(hit.title) || url}</span>
                <ExternalLink className="size-3.5 shrink-0 opacity-60 group-hover:opacity-100" />
              </a>
              {text(hit.snippet) && (
                <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">
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
      <p className="text-xs text-slate-400">
        {rowCount} {rowCount === 1 ? "fila devuelta" : "filas devueltas"}
        {truncated && (
          <span className="ml-2 inline-flex items-center gap-1 text-amber-300">
            <AlertTriangle className="size-3" />
            resultados recortados
          </span>
        )}
      </p>
      {rows.length > 0 && (
        <div className="mt-2 overflow-x-auto rounded-lg ring-1 ring-slate-800">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="bg-slate-800/60">
                {columns.map((col) => (
                  <th
                    key={col}
                    className="whitespace-nowrap px-3 py-2 font-semibold text-slate-300"
                  >
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-950/60">
              {visible.map((row, i) => (
                <tr key={i}>
                  {columns.map((col) => (
                    <td
                      key={col}
                      className="max-w-72 truncate px-3 py-2 align-top text-slate-300"
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
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-slate-400 transition-colors hover:text-slate-200"
        >
          {expanded ? "Mostrar menos" : `Ver las ${rows.length} filas`}
          <ChevronDown className={clsx("size-3.5 transition-transform", expanded && "rotate-180")} />
        </button>
      )}
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

const CONTENT_PREVIEW_CHARS = 600;

function ReadFileResult({ data }: { data: Record<string, unknown> }) {
  const content = text(data.content);
  const clipped = content.length > CONTENT_PREVIEW_CHARS;
  return (
    <div className="mt-3">
      <p className="text-xs text-slate-400">
        <span className="font-medium text-slate-300">{text(data.path)}</span>
        {" · "}
        {typeof data.bytes === "number" ? humanizeBytes(data.bytes) : ""}
      </p>
      <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950/60 p-3 text-xs leading-relaxed text-slate-300 ring-1 ring-slate-800">
        {clipped ? `${content.slice(0, CONTENT_PREVIEW_CHARS)}…` : content}
      </pre>
      {clipped && (
        <p className="mt-1 text-xs text-slate-400">Vista previa del contenido</p>
      )}
    </div>
  );
}

function WriteFileResult({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="mt-3 flex items-center gap-2.5 rounded-lg bg-emerald-500/10 p-3 ring-1 ring-emerald-500/25">
      <p className="text-xs text-emerald-200">
        Archivo guardado: <span className="font-medium">{text(data.path)}</span>
        {typeof data.bytes_written === "number" && (
          <span className="text-emerald-200/70">
            {" · "}
            {humanizeBytes(data.bytes_written)} escritos
          </span>
        )}
      </p>
    </div>
  );
}
