"""``write_file`` — persist UTF-8 text inside the sandbox root only.

Same path-sandbox guarantees as ``read_file``, plus a per-write byte cap.
Used by the agent to persist the final report or working notes.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from mcp_toolkit.result import ToolError
from mcp_toolkit.server.guardrails.path_sandbox import (
    realpath_in_sandbox,
    resolve_in_sandbox,
)
from mcp_toolkit.server.tools.types import ToolDef

DESCRIPTION = (
    "Write UTF-8 text to a file inside the sandboxed workspace directory "
    "(creates parent directories; overwrites existing files). "
    "Use it to persist the final report or working notes."
)


def create_write_file_tool(sandbox_root: str | Path, max_bytes: int) -> ToolDef:
    root = Path(sandbox_root)

    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    async def handler(path: str, content: str, **_ignored: Any) -> dict[str, Any]:
        n_bytes = len(content.encode("utf-8"))
        if n_bytes > max_bytes:
            raise ToolError(
                "invalid_input",
                f"Content too large: {n_bytes} bytes (limit {max_bytes})",
                guardrail="file_size_limit",
            )
        resolved = resolve_in_sandbox(root, path)
        canonical = realpath_in_sandbox(root, resolved)
        await asyncio.to_thread(_write, canonical, content)
        return {
            "path": str(os.path.relpath(canonical, root)),  # noqa: ASYNC240
            "bytes_written": n_bytes,
        }

    return ToolDef(name="write_file", description=DESCRIPTION, handler=handler)
