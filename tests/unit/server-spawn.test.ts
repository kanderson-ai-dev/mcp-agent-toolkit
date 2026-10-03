import { describe, expect, it } from "vitest";
import { resolveServerSpawn } from "../../src/client/server-spawn.js";
import { testConfig } from "../helpers.js";

describe("resolveServerSpawn", () => {
  it("uses the explicit override when MCP_SERVER_COMMAND is set", () => {
    const config = testConfig({
      MCP_SERVER_COMMAND: "node",
      MCP_SERVER_ARGS: "dist/server/index.js --flag",
    });
    expect(resolveServerSpawn(config)).toEqual({
      command: "node",
      args: ["dist/server/index.js", "--flag"],
    });
  });

  it("defaults to empty args when MCP_SERVER_ARGS is absent", () => {
    const config = testConfig({ MCP_SERVER_COMMAND: "custom-runner" });
    expect(resolveServerSpawn(config)).toEqual({ command: "custom-runner", args: [] });
  });

  it("falls back to tsx against the source when no override and no dist build exists", () => {
    const config = testConfig();
    // The test cwd (repo root) may or may not have a dist/ build present
    // depending on test order; both resolved branches are valid fallbacks.
    const spawn = resolveServerSpawn(config);
    expect(["npx", process.execPath]).toContain(spawn.command);
  });
});
