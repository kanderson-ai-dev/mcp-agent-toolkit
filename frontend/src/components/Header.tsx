import { Plug, Sparkles } from "lucide-react";
import type { ToolsInfo } from "../types";
import { ThemeToggle } from "./ThemeToggle";

interface HeaderProps {
  toolsInfo: ToolsInfo | null;
  connected: boolean;
}

/** Product header: name + live capability badges (tools, LLM provider). */
export function Header({ toolsInfo, connected }: HeaderProps) {
  const provider = toolsInfo?.llm.provider;
  const isStub = provider === "stub";
  return (
    <header className="border-b border-line/80 bg-page/80 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-4xl items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex size-8 items-center justify-center rounded-lg bg-indigo-500/15 text-accent ring-1 ring-indigo-500/30">
            <Plug className="size-4.5" />
          </div>
          <div>
            <h1 className="text-sm font-semibold tracking-tight text-ink">
              MCP Agent Console
            </h1>
            <p className="text-xs text-muted">
              Real tools, over a standard protocol
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {toolsInfo && (
            <span className="hidden rounded-full bg-overlay/80 px-2.5 py-1 text-xs font-medium text-soft ring-1 ring-line-strong sm:inline">
              {toolsInfo.tools.length} tools
            </span>
          )}
          {provider && (
            <span
              className={
                isStub
                  ? "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-amber-500/15 px-2.5 py-1 text-xs font-medium text-warn ring-1 ring-amber-500/30"
                  : "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-ok ring-1 ring-emerald-500/30"
              }
            >
              <Sparkles className="size-3.5" />
              {isStub ? (
                <>
                  Demo mode
                  <span className="hidden sm:inline">&nbsp;(no live LLM)</span>
                </>
              ) : (
                "OpenAI"
              )}
            </span>
          )}
          <span className="flex items-center gap-1.5 text-xs text-muted">
            <span
              className={
                connected
                  ? "size-2 rounded-full bg-ok"
                  : "size-2 rounded-full bg-danger"
              }
            />
            {connected ? "Online" : "Offline"}
          </span>
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
