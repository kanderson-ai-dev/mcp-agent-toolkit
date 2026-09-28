import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { getConfig, loadEnvFile } from "../../src/shared/config.js";
import { tmpDir } from "../helpers.js";

describe("config", () => {
  it("applies safe defaults with an empty env", () => {
    const c = getConfig({});
    expect(c.OPENAI_API_KEY).toBeUndefined();
    expect(c.SEARCH_API_KEY).toBeUndefined();
    expect(c.SEARCH_PROVIDER).toBe("auto");
    expect(c.LLM_PROVIDER).toBe("auto");
    expect(c.CHAT_MODEL_NAME).toBe("gpt-4o-mini");
    expect(c.RATE_LIMIT_PER_MINUTE).toBe(60);
    expect(c.MAX_TOOL_ITERATIONS).toBe(8);
    expect(c.METRICS_PORT).toBeUndefined();
    expect(c.OUTPUT_FORMAT).toBe("pretty");
  });

  it("treats empty-string secrets as absent", () => {
    const c = getConfig({ OPENAI_API_KEY: "", SEARCH_API_KEY: "   " });
    expect(c.OPENAI_API_KEY).toBeUndefined();
    expect(c.SEARCH_API_KEY).toBeUndefined();
  });

  it("keeps real secrets when present", () => {
    const c = getConfig({ OPENAI_API_KEY: "sk-test" });
    expect(c.OPENAI_API_KEY).toBe("sk-test");
  });

  it("coerces numeric knobs", () => {
    const c = getConfig({ RATE_LIMIT_PER_MINUTE: "12", MAX_TOOL_ITERATIONS: "3" });
    expect(c.RATE_LIMIT_PER_MINUTE).toBe(12);
    expect(c.MAX_TOOL_ITERATIONS).toBe(3);
  });

  it("parses METRICS_PORT only when set", () => {
    expect(getConfig({ METRICS_PORT: "" }).METRICS_PORT).toBeUndefined();
    expect(getConfig({ METRICS_PORT: "9108" }).METRICS_PORT).toBe(9108);
  });

  it("resolves SANDBOX_ROOT and DB_PATH to absolute paths", () => {
    const c = getConfig({ SANDBOX_ROOT: "./rel/sb", DB_PATH: "./rel/db.sqlite" });
    expect(path.isAbsolute(c.SANDBOX_ROOT)).toBe(true);
    expect(path.isAbsolute(c.DB_PATH)).toBe(true);
  });

  it("rejects invalid enum values", () => {
    expect(() => getConfig({ SEARCH_PROVIDER: "bing" })).toThrow();
    expect(() => getConfig({ LLM_PROVIDER: "nope" })).toThrow();
  });
});

describe("loadEnvFile", () => {
  const KEY = "MCP_TOOLKIT_TEST_VAR";
  afterEach(() => {
    delete process.env[KEY];
  });

  it("does nothing when the file is absent", () => {
    expect(() => loadEnvFile(path.join(tmpDir(), "no.env"))).not.toThrow();
  });

  it("loads values, and real env vars win over the file", () => {
    const file = path.join(tmpDir(), "x.env");
    fs.writeFileSync(file, `${KEY}=from-file\nOTHER=file-val\n`);
    process.env[KEY] = "from-env";
    loadEnvFile(file);
    expect(process.env[KEY]).toBe("from-env"); // env > .env
    expect(process.env.OTHER).toBe("file-val");
    delete process.env.OTHER;
  });
});
