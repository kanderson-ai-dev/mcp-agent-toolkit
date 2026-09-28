import fs from "node:fs";
import path from "node:path";
import Database from "better-sqlite3";

/**
 * Seed the fixture database `db_query` reads from. Deterministic — same
 * rows every run — so demos and tests are reproducible. The dataset is a
 * fictional "internal reports" catalogue, sized to make the researcher
 * agent's evidence lookups meaningful.
 */

export interface ReportRow {
  id: number;
  title: string;
  topic: string;
  author: string;
  published_at: string;
  summary: string;
}

const REPORTS: ReportRow[] = [
  {
    id: 1,
    title: "MCP Adoption in Enterprise Tooling",
    topic: "protocols",
    author: "K. Anderson",
    published_at: "2026-03-14",
    summary:
      "Survey of how platform teams expose internal capabilities through the Model Context Protocol instead of bespoke tool bindings; highlights stdio vs HTTP transports and auth models.",
  },
  {
    id: 2,
    title: "Guardrails for Indirect Prompt Injection",
    topic: "security",
    author: "K. Anderson",
    published_at: "2026-04-02",
    summary:
      "Threat model and mitigations for OWASP LLM01 in tool-using agents: treat all tool output as untrusted data, cap sizes, and never interpolate into system prompts.",
  },
  {
    id: 3,
    title: "Read-Only SQL Interfaces for Agents",
    topic: "security",
    author: "J. Marlowe",
    published_at: "2026-05-21",
    summary:
      "Defense-in-depth pattern for LLM-facing databases: statement-level SELECT/WITH guards plus readonly connection flags, with an analysis of bypass attempts.",
  },
  {
    id: 4,
    title: "Filesystem Sandboxing for Coding Agents",
    topic: "security",
    author: "L. Okafor",
    published_at: "2026-06-09",
    summary:
      "Lexical vs canonical path containment, symlink and junction escape vectors on Windows and POSIX, and size caps as an abuse control.",
  },
  {
    id: 5,
    title: "Rate Limiting AI Tool Surfaces",
    topic: "reliability",
    author: "J. Marlowe",
    published_at: "2026-07-11",
    summary:
      "Token-bucket rate limiting applied per tool and per session to bound cost and prevent runaway agent loops; includes percentile latency impact measurements.",
  },
  {
    id: 6,
    title: "Structured Logging for Agent Tool Calls",
    topic: "observability",
    author: "L. Okafor",
    published_at: "2026-08-03",
    summary:
      "JSON log schema for tool invocations with request-ID correlation across client, MCP server and tool handler; secret-field redaction rules.",
  },
  {
    id: 7,
    title: "Vector Databases vs. Hybrid Retrieval",
    topic: "retrieval",
    author: "K. Anderson",
    published_at: "2026-01-28",
    summary:
      "Comparison of pure vector search against BM25+vector hybrids for B2B knowledge bases; relevance and latency trade-offs on a 40k-document corpus.",
  },
  {
    id: 8,
    title: "Streaming Tool Events to Operator Consoles",
    topic: "observability",
    author: "J. Marlowe",
    published_at: "2026-08-19",
    summary:
      "Design notes for surfacing intermediate agent actions — tool calls, retries, guardrail rejections — as a structured event stream for live supervision.",
  },
];

export function seedDemoDb(dbPath: string): void {
  fs.mkdirSync(path.dirname(dbPath), { recursive: true });
  const db = new Database(dbPath);
  try {
    db.pragma("journal_mode = WAL");
    db.exec(`
      DROP TABLE IF EXISTS reports;
      CREATE TABLE reports (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        topic TEXT NOT NULL,
        author TEXT NOT NULL,
        published_at TEXT NOT NULL,
        summary TEXT NOT NULL
      );
      CREATE INDEX idx_reports_topic ON reports(topic);
    `);
    const insert = db.prepare(
      `INSERT INTO reports (id, title, topic, author, published_at, summary)
       VALUES (@id, @title, @topic, @author, @published_at, @summary)`,
    );
    const insertAll = db.transaction((rows: ReportRow[]) => {
      for (const row of rows) insert.run(row);
    });
    insertAll(REPORTS);
  } finally {
    db.close();
  }
}

// Run as a script: `npm run seed:db`
if (process.argv[1] && import.meta.url === `file:///${process.argv[1].replace(/\\/g, "/")}`) {
  const dbPath = path.resolve(process.env.DB_PATH ?? "./data/demo.db");
  seedDemoDb(dbPath);
  console.log(`Seeded ${REPORTS.length} rows into ${dbPath}`);
}
