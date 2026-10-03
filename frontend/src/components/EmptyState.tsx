import { ArrowRight, ShieldCheck, Workflow, Zap } from "lucide-react";

interface EmptyStateProps {
  examples: string[];
  onPick: (question: string) => void;
}

const HIGHLIGHTS = [
  { Icon: Workflow, text: "Descubre herramientas en vivo vía MCP" },
  { Icon: ShieldCheck, text: "Guardrails activos en cada llamada" },
  { Icon: Zap, text: "Eventos transmitidos en tiempo real" },
];

/**
 * Hero empty state — explains the product in user terms and offers the
 * dataset's example questions as one-click chips.
 */
export function EmptyState({ examples, onPick }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center px-2 py-10 text-center sm:py-14">
      <div className="rounded-2xl bg-gradient-to-b from-indigo-500/20 to-transparent p-[1px]">
        <div className="rounded-2xl bg-slate-950 px-6 py-5">
          <h2 className="bg-gradient-to-r from-indigo-200 via-slate-100 to-cyan-200 bg-clip-text text-2xl font-bold tracking-tight text-transparent sm:text-3xl">
            Pregunta. Observa cómo trabaja el agente.
          </h2>
        </div>
      </div>
      <p className="mt-4 max-w-xl text-sm leading-relaxed text-slate-400">
        El agente decide qué herramientas necesita — búsqueda web, base de datos
        interna o archivos del workspace — las invoca por el protocolo MCP y te
        muestra cada paso en vivo, con las fuentes que usó.
      </p>

      <div className="mt-5 flex flex-wrap items-center justify-center gap-x-5 gap-y-2">
        {HIGHLIGHTS.map(({ Icon, text }) => (
          <span key={text} className="inline-flex items-center gap-1.5 text-xs text-slate-400">
            <Icon className="size-3.5 text-indigo-300" />
            {text}
          </span>
        ))}
      </div>

      {examples.length > 0 && (
        <div className="mt-8 w-full max-w-2xl">
          <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
            Prueba con una de estas preguntas
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {examples.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => onPick(q)}
                className="group flex items-start justify-between gap-3 rounded-xl bg-slate-900/70 p-3.5 text-left text-sm leading-snug text-slate-300 ring-1 ring-slate-800 transition-all hover:bg-slate-900 hover:text-slate-100 hover:ring-indigo-500/40"
              >
                <span className="line-clamp-3">{q}</span>
                <ArrowRight className="mt-0.5 size-4 shrink-0 text-slate-500 transition-transform group-hover:translate-x-0.5 group-hover:text-indigo-300" />
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
