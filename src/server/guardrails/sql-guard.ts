import { ToolError } from "../../shared/result.js";

/**
 * Read-only SQL guard — the first of two defense layers for `db_query`
 * (the second is opening the SQLite connection itself with `readonly`).
 *
 * Rules: a single statement, starting with SELECT or WITH (CTEs are
 * read-only), no forbidden write/DDL/administrative keywords anywhere,
 * comments stripped before inspection.
 */

const FORBIDDEN_KEYWORDS =
  /\b(insert|update|delete|drop|alter|create|pragma|attach|detach|vacuum|replace|grant|revoke|truncate|begin|commit|rollback|savepoint|release|reindex|analyze|load_extension)\b/i;

function stripComments(sql: string): string {
  return sql
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/--[^\n]*/g, " ");
}

/**
 * Throws ToolError("forbidden") unless `sql` is a single read-only SELECT.
 * Intentionally strict: a `;` inside a string literal or an identifier
 * colliding with a denied keyword is also rejected — `db_query` is for
 * evidence lookups, not arbitrary SQL, so false positives are acceptable
 * and documented.
 */
export function assertReadOnlySelect(sql: string): void {
  const cleaned = stripComments(sql)
    .trim()
    .replace(/[\s;]+$/, ""); // tolerate trailing semicolons/whitespace
  if (cleaned === "") {
    throw new ToolError("invalid_input", "Empty SQL statement");
  }
  if (cleaned.includes(";")) {
    throw new ToolError("forbidden", "Only a single SELECT statement is allowed");
  }
  if (!/^(select|with)\b/i.test(cleaned)) {
    throw new ToolError("forbidden", "Only SELECT statements are allowed");
  }
  const hit = FORBIDDEN_KEYWORDS.exec(cleaned);
  if (hit) {
    throw new ToolError(
      "forbidden",
      `Statement contains a forbidden keyword: ${hit[1]?.toUpperCase()}`,
    );
  }
}
