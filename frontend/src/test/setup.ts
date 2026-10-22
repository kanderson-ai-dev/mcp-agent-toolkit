import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// globals:false — register cleanup explicitly so renders don't accumulate.
afterEach(cleanup);

// `__EXAMPLE_QUESTIONS__` is injected at build time by vite.config.ts;
// component tests get an empty set so they never touch the real dataset.
(globalThis as Record<string, unknown>).__EXAMPLE_QUESTIONS__ = [];
