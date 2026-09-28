import fs from "node:fs";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import {
  realpathInSandbox,
  resolveInSandbox,
} from "../../src/server/guardrails/path-sandbox.js";
import { ToolError } from "../../src/shared/result.js";
import { tmpDir } from "../helpers.js";

describe("path sandbox", () => {
  let root: string;
  let outside: string;

  beforeAll(() => {
    root = tmpDir("sandbox-");
    outside = tmpDir("outside-");
    fs.writeFileSync(path.join(root, "ok.txt"), "inside");
    fs.writeFileSync(path.join(outside, "secret.txt"), "outside");
  });

  afterAll(() => {
    fs.rmSync(root, { recursive: true, force: true });
    fs.rmSync(outside, { recursive: true, force: true });
  });

  it("resolves simple relative paths inside the root", () => {
    expect(resolveInSandbox(root, "ok.txt")).toBe(path.join(root, "ok.txt"));
    expect(resolveInSandbox(root, "a/b/c.txt")).toBe(
      path.join(root, "a", "b", "c.txt"),
    );
  });

  it("resolves absolute paths that stay inside the root", () => {
    const abs = path.join(root, "ok.txt");
    expect(resolveInSandbox(root, abs)).toBe(abs);
  });

  it.each([
    "../escape.txt",
    "../../etc/passwd",
    "..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "a/../../b.txt",
    ".",
    "..",
  ])("rejects traversal attempt: %s", (rel) => {
    // "." and ".." resolve to the root itself or above — both must fail
    // the strict containment check for `..` only; "." lands on the root.
    const resolved = resolveInSandboxSafe(rel);
    if (rel === ".") {
      expect(resolved).toBe(root);
    } else {
      expect(resolved).toBeInstanceOf(ToolError);
    }
  });

  function resolveInSandboxSafe(rel: string): string | ToolError {
    try {
      return resolveInSandbox(root, rel);
    } catch (err) {
      return err as ToolError;
    }
  }

  it.each([
    "/etc/passwd",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "\\\\evil-share\\secret.txt",
  ])("rejects absolute path outside the root: %s", (abs) => {
    // Foreign-style absolutes (POSIX on Windows, Windows on POSIX) must be
    // rejected on every platform — not just where they parse as absolute.
    expect(() => resolveInSandbox(root, abs)).toThrowError(ToolError);
    expect(() => resolveInSandbox(root, outside)).toThrowError(ToolError);
  });

  it("rejects NUL bytes", () => {
    expect(() => resolveInSandbox(root, "a\0b.txt")).toThrowError(ToolError);
  });

  it("canonicalizes existing files inside the root", () => {
    const real = realpathInSandbox(root, path.join(root, "ok.txt"));
    expect(fs.existsSync(real)).toBe(true);
  });

  it("allows writes to non-existent files inside the root", () => {
    const target = path.join(root, "new", "deep", "file.txt");
    const real = realpathInSandbox(root, target);
    expect(real.startsWith(fs.realpathSync(root))).toBe(true);
  });

  it("rejects symlink/junction escapes", () => {
    const link = path.join(root, "escape-link");
    const linkTarget = path.join(link, "secret.txt");
    try {
      // junction works on Windows without privileges; symlink elsewhere.
      fs.symlinkSync(outside, link, process.platform === "win32" ? "junction" : "dir");
    } catch (err) {
      console.warn(`skipping symlink test: ${(err as Error).message}`);
      return;
    }
    // Lexically inside the sandbox…
    expect(resolveInSandbox(root, path.join("escape-link", "secret.txt"))).toBe(linkTarget);
    // …but canonical resolution reveals the escape → rejected.
    expect(() => realpathInSandbox(root, linkTarget)).toThrowError(
      /escapes the sandbox root/,
    );
  });
});
