import { useState } from "react";
import { Moon, Sun } from "lucide-react";

/**
 * Light/dark toggle — flips `data-theme` on <html> (the CSS variables
 * follow automatically) and persists the choice in localStorage. The
 * initial theme is resolved by an inline script in index.html before
 * first paint, so the button only mirrors that state.
 */
export function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    document.documentElement.dataset.theme === "light" ? "light" : "dark",
  );
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => {
        document.documentElement.dataset.theme = next;
        localStorage.setItem("theme", next);
        setTheme(next);
      }}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="flex size-8 items-center justify-center rounded-lg text-muted ring-1 ring-line transition-colors hover:bg-overlay hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      {theme === "dark" ? (
        <Sun className="size-4" aria-hidden="true" />
      ) : (
        <Moon className="size-4" aria-hidden="true" />
      )}
    </button>
  );
}
