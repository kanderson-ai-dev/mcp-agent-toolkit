import { defineConfig } from "@playwright/test";

/**
 * Console E2E — drives the production frontend + Express adapter with a
 * fully deterministic backend: StubLLM + SEARCH_PROVIDER=none, zero
 * credentials, zero network. The webServer block spawns the real
 * `npm run web` process (which in turn spawns the MCP server over stdio),
 * so the test exercises the same path a user hits.
 *
 * `video: "on"` records every run — the multi-tool test's .webm is the
 * reproducible source for docs/web-demo.gif (scripts/make-demo-gif.ts).
 */
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1, // one MCP child process; the console runs one question at a time
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    video: "on",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run web",
    url: "http://127.0.0.1:3100/api/health",
    timeout: 90_000,
    reuseExistingServer: !process.env.CI,
    env: {
      WEB_PORT: "3100",
      LLM_PROVIDER: "stub",
      SEARCH_PROVIDER: "none",
      OPENAI_API_KEY: "",
      LOG_LEVEL: "error",
    },
  },
});
