import fs from "node:fs";
import path from "node:path";
import { ToolError } from "../../shared/result.js";

/**
 * Filesystem sandbox. Two complementary checks:
 *  1. `resolveInSandbox` — lexical containment after resolve() catches
 *     `..` traversal and absolute paths outside the root.
 *  2. `realpathInSandbox` — canonical-path containment catches symlink /
 *     junction escapes (a lexical check alone is fooled by them).
 */

const inside = (root: string, p: string): boolean => {
  const rel = path.relative(root, p);
  return rel === "" || (!rel.startsWith("..") && !path.isAbsolute(rel));
};

// Windows filesystems are case-insensitive; normalize for comparison.
const norm = (p: string): string =>
  process.platform === "win32" ? p.toLowerCase() : p;

/**
 * Windows-style absolute paths (drive letter or UNC share). Rejected
 * unconditionally: on POSIX they'd slip through resolve() as inert
 * filenames, but the same code runs on Windows where they're escapes.
 */
const FOREIGN_ABSOLUTE = /^(?:[a-zA-Z]:[\\/]|\\\\)/;

/** Resolve `rel` against `root`, rejecting anything that escapes it. */
export function resolveInSandbox(root: string, rel: string): string {
  if (rel.includes("\0")) {
    throw new ToolError("invalid_input", "Path contains a NUL byte");
  }
  const rootResolved = path.resolve(root);
  const resolved = path.resolve(rootResolved, rel);
  // Reject foreign-style absolutes the host parser treats as relative
  // (e.g. "C:\Windows\…" on POSIX) — inert filenames here, escapes on the
  // OS where they're absolute. Host-native absolutes are still allowed
  // when they resolve inside the root.
  const foreignAbsolute = FOREIGN_ABSOLUTE.test(rel) && !path.isAbsolute(rel);
  if (foreignAbsolute || !inside(norm(rootResolved), norm(resolved))) {
    throw new ToolError("forbidden", `Path escapes the sandbox root: ${rel}`);
  }
  return resolved;
}

/**
 * Canonicalize `candidate` (which may not exist yet — e.g. a write target)
 * by walking up to the deepest existing ancestor, and verify the canonical
 * path stays inside `root`. Returns the canonical path to operate on.
 */
export function realpathInSandbox(root: string, candidate: string): string {
  const realRoot = fs.realpathSync(root);
  const missing: string[] = [];
  let p = candidate;
  while (!fs.existsSync(p)) {
    const parent = path.dirname(p);
    if (parent === p) break;
    missing.push(path.basename(p));
    p = parent;
  }
  if (!fs.existsSync(p)) {
    throw new ToolError("forbidden", `Path escapes the sandbox root: ${candidate}`);
  }
  const realAncestor = fs.realpathSync(p);
  if (!inside(norm(realRoot), norm(realAncestor))) {
    throw new ToolError("forbidden", "Path escapes the sandbox root via a link");
  }
  return path.join(realAncestor, ...missing.reverse());
}
