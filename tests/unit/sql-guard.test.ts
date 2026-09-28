import { describe, expect, it } from "vitest";
import { assertReadOnlySelect } from "../../src/server/guardrails/sql-guard.js";
import { ToolError } from "../../src/shared/result.js";

const ACCEPT = [
  "SELECT 1",
  "  select id, title from reports  ",
  "SELECT * FROM reports;",
  "SELECT * FROM reports;; ;",
  "WITH recent AS (SELECT * FROM reports) SELECT title FROM recent",
  "SELECT count(*) AS n -- count them\nFROM reports",
  "/* preface */ SELECT title FROM reports",
];

const REJECT = [
  "DROP TABLE reports",
  "DELETE FROM reports WHERE id = 1",
  "UPDATE reports SET title = 'x'",
  "INSERT INTO reports (title) VALUES ('x')",
  "REPLACE INTO reports (title) VALUES ('x')",
  "SELECT 1; DROP TABLE reports",
  "SELECT * FROM reports; SELECT * FROM reports",
  "PRAGMA table_info(reports)",
  "ATTACH DATABASE 'evil.db' AS e",
  "VACUUM",
  "BEGIN TRANSACTION",
  "SELECT load_extension('x')",
  "CREATE TABLE x (a)",
  "ALTER TABLE reports ADD COLUMN x TEXT",
];

describe("sql-guard", () => {
  it.each(ACCEPT)("accepts read-only statement: %s", (sql) => {
    expect(() => assertReadOnlySelect(sql)).not.toThrow();
  });

  it.each(REJECT)("rejects non-SELECT or multi-statement: %s", (sql) => {
    expect(() => assertReadOnlySelect(sql)).toThrowError(ToolError);
    try {
      assertReadOnlySelect(sql);
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(ToolError);
      expect((err as ToolError).code).toBe("forbidden");
    }
  });

  it("rejects empty statements as invalid_input", () => {
    try {
      assertReadOnlySelect("   \n  ");
      expect.unreachable();
    } catch (err) {
      expect((err as ToolError).code).toBe("invalid_input");
    }
  });

  it("cannot be bypassed by comment smuggling", () => {
    expect(() => assertReadOnlySelect("SEL/**/ECT * FROM reports")).toThrowError(ToolError);
    expect(() => assertReadOnlySelect("-- sneaky\nDROP TABLE reports")).toThrowError(ToolError);
  });
});
