import Database from "better-sqlite3";
import { z } from "zod";
import { ToolError, okResult } from "../../shared/result.js";
import { assertReadOnlySelect } from "../guardrails/sql-guard.js";
import type { ToolDefinition } from "./types.js";

const MAX_ROWS = 500;

/**
 * `db_query` — read-only SQL against a seeded SQLite fixture.
 * Defense in depth: the statement guard rejects anything that isn't a
 * single SELECT, and the connection itself is opened `readonly`.
 */
export function createDbQueryTool(
  dbPath: string,
): ToolDefinition<{ query: z.ZodString }> {
  let db: Database.Database | undefined;
  const getDb = (): Database.Database => {
    db ??= new Database(dbPath, { readonly: true, fileMustExist: true });
    return db;
  };

  return {
    name: "db_query",
    description:
      "Run a single read-only SELECT statement against the internal SQLite " +
      "fixture database. Schema: table `reports` (id, title, topic, author, " +
      "published_at, summary); `topic` values are English keywords such as " +
      "security, protocols, reliability, observability, retrieval. " +
      "Returns JSON rows. Only SELECT/WITH statements are accepted — " +
      "everything else is rejected.",
    inputSchema: {
      query: z
        .string()
        .min(1)
        .max(4_000)
        .describe("A single SELECT statement, e.g. SELECT title, author FROM reports"),
    },
    handler: (args) => {
      assertReadOnlySelect(args.query);
      let rows: unknown[];
      try {
        rows = getDb().prepare(args.query).all();
      } catch (err) {
        if (err instanceof ToolError) throw err;
        const message = err instanceof Error ? err.message : String(err);
        if (/unable to open database/i.test(message)) {
          throw new ToolError(
            "not_found",
            `Database not found at ${dbPath} — run \`npm run seed:db\` first`,
          );
        }
        if (/syntax error|no such table|no such column/i.test(message)) {
          throw new ToolError("invalid_input", `SQLite rejected the query: ${message}`);
        }
        throw err;
      }
      const truncated = rows.length > MAX_ROWS;
      return Promise.resolve(
        okResult({
          rows: truncated ? rows.slice(0, MAX_ROWS) : rows,
          row_count: Math.min(rows.length, MAX_ROWS),
          truncated,
        }),
      );
    },
  };
}
