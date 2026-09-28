/**
 * Tool-output sanitization — OWASP LLM01 hygiene on the server side.
 * Everything a tool returns is *untrusted third-party data*: web results
 * can carry embedded instructions ("ignore previous instructions…"),
 * files can hold exploit text, DB rows can be adversarial.
 *
 * Server-side defense is intentionally conservative — we normalize and
 * cap rather than rewrite, because evidence integrity matters to the
 * caller. The *framing* defense (treating content as data, never as
 * instructions) lives in the client's system prompt + `tool` role.
 */

// C0/C1 control chars except \t \n \r (NUL, ESC/ANSI, DEL…), plus
// zero-width and bidi-override format chars (Trojan-Source class). Keeps
// transcripts and logs safe to view. Built via RegExp so this source
// file stays plain ASCII.
const CONTROL_CHARS = new RegExp(
  "[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F-\\u009F" +
    "\\u200B-\\u200F\\u202A-\\u202E\\u2060-\\u2064\\uFEFF]",
  "g",
);

export interface SanitizedText {
  text: string;
  truncated: boolean;
}

export function sanitizeToolOutput(text: string, maxChars: number): SanitizedText {
  const cleaned = text.replace(CONTROL_CHARS, "");
  if (cleaned.length <= maxChars) return { text: cleaned, truncated: false };
  return {
    text: `${cleaned.slice(0, maxChars)}\n[truncated: tool output exceeded ${maxChars} chars]`,
    truncated: true,
  };
}
