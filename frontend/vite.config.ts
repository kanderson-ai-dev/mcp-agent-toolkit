/// <reference types="vitest/config" />
import fs from "node:fs";
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Example chips come straight from the versioned evaluation dataset —
// the UI never defines its own sample questions (single source of truth).
interface DatasetQuestion {
  id: string;
  uiExample: boolean;
  prompt: string;
}

function loadExampleQuestions(): string[] {
  const datasetPath = path.resolve(import.meta.dirname, "../evaluation/dataset.json");
  try {
    const raw = JSON.parse(fs.readFileSync(datasetPath, "utf8")) as {
      questions?: DatasetQuestion[];
    };
    return (raw.questions ?? []).filter((q) => q.uiExample).map((q) => q.prompt);
  } catch {
    return [];
  }
}

// Dev-only proxy to the FastAPI/SSE backend (src/mcp_toolkit/web/) —
// avoids CORS entirely in development; the production build is served
// same-origin by that same backend process (src/mcp_toolkit/web/app.py).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __EXAMPLE_QUESTIONS__: JSON.stringify(loadExampleQuestions()),
  },
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:3000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
