import { execSync } from "node:child_process";

/**
 * Integration tests spawn `dist/server/index.js` — build once so they
 * exercise the compiled artifact, not tsx-transpiled source.
 */
export default function setup(): void {
  execSync(`${process.execPath} node_modules/typescript/bin/tsc -p tsconfig.build.json`, {
    stdio: "inherit",
  });
}
