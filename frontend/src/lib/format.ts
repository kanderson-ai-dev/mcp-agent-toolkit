/** Human-readable formatting helpers — the UI never shows raw units. */

/** `340 ms` → "340 ms", `1200` → "1.2 s", `95_000` → "1 min 35 s". */
export function humanizeDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) {
    const s = ms / 1000;
    return `${s >= 10 ? Math.round(s) : s.toFixed(1)} s`;
  }
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return seconds > 0 ? `${minutes} min ${seconds} s` : `${minutes} min`;
}

/** `300` → "300 B", `1900` → "1.9 KB", `2_500_000` → "2.4 MB". */
export function humanizeBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
