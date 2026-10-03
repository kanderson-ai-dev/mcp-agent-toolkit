import { Plug, Sparkles } from "lucide-react";
import type { ToolsInfo } from "../types";

interface HeaderProps {
  toolsInfo: ToolsInfo | null;
  connected: boolean;
}

/** Product header: name + live capability badges (tools, LLM provider). */
export function Header({ toolsInfo, connected }: HeaderProps) {
  const provider = toolsInfo?.llm.provider;
  const isStub = provider === "stub";
  return (
    <header className="border-b border-slate-800/80 bg-slate-950/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-4xl items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-500/15 text-indigo-300 ring-1 ring-indigo-500/30">
            <Plug className="size-4.5" />
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-slate-100">
              MCP Agent Console
            </h1>
            <p className="text-xs text-slate-400">
              Herramientas reales, por protocolo estándar
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {toolsInfo && (
            <span className="hidden rounded-full bg-slate-800/80 px-2.5 py-1 text-xs font-medium text-slate-300 ring-1 ring-slate-700 sm:inline">
              {toolsInfo.tools.length} herramientas
            </span>
          )}
          {provider && (
            <span
              className={
                isStub
                  ? "inline-flex items-center gap-1.5 rounded-full bg-amber-500/15 px-2.5 py-1 text-xs font-medium text-amber-300 ring-1 ring-amber-500/30"
                  : "inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-300 ring-1 ring-emerald-500/30"
              }
            >
              <Sparkles className="size-3.5" />
              {isStub ? "Modo demo" : "OpenAI"}
            </span>
          )}
          <span className="flex items-center gap-1.5 text-xs text-slate-400">
            <span
              className={
                connected
                  ? "size-2 rounded-full bg-emerald-400"
                  : "size-2 rounded-full bg-rose-400"
              }
            />
            {connected ? "En línea" : "Sin conexión"}
          </span>
        </div>
      </div>
    </header>
  );
}
