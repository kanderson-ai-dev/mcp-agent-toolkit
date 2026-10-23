"""Background A/B runs for the prompt-registry API.

A/B evaluation is long-running (dataset × 2 sides × real LLM calls), so
the API returns a ``runId`` immediately and progress streams over SSE —
the same one-shot consumption and heartbeat pattern as ``/api/chat``.
Completed results persist to ``data/prompt-registry/ab/`` so a later
``promote`` call can verify measured evidence instead of trusting a
flag.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    # ``ab_test`` imports the top-level ``evaluation`` package, which
    # deployments may not ship — everything here only duck-types the
    # result, so the import is annotation-only.
    from mcp_toolkit.prompts.ab_test import ABResult

AB_RESULTS_DIR = Path("data/prompt-registry/ab")


@dataclass
class AbRun:
    """One tracked A/B run: identity, progress lines, final result."""

    id: str
    name: str
    status: Literal["running", "completed", "error"] = "running"
    progress: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    queue: asyncio.Queue[tuple[str, Any] | None] = field(default_factory=lambda: asyncio.Queue())
    task: asyncio.Task[None] | None = None


def ab_result_path(out_dir: Path, result: ABResult) -> Path:
    """Deterministic file name: name, versions, sha prefix, timestamp."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    return out_dir / (
        f"{result.name}-b{result.baseline_version}-c{result.candidate_version}-"
        f"{result.candidate_sha256[:8]}-{stamp}.json"
    )


def save_ab_result(result: ABResult, out_dir: Path | str | None = None) -> Path:
    """Persist a completed A/B result as promote-time evidence."""
    path = ab_result_path(Path(out_dir) if out_dir is not None else AB_RESULTS_DIR, result)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_wire(), indent=2), encoding="utf-8")
    return path


def latest_ab_result(
    name: str, candidate_version: int, out_dir: Path | str | None = None
) -> dict[str, Any] | None:
    """Newest persisted A/B result whose candidate is ``version``."""
    root = Path(out_dir) if out_dir is not None else AB_RESULTS_DIR
    if not root.exists():
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for f in root.glob(f"{name}-*-c{candidate_version}-*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("candidateVersion") != candidate_version:
            continue
        mtime = f.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, data)
    return best[1] if best else None


class AbRunManager:
    """Tracks background A/B tasks with per-run progress queues.

    In-memory and bounded — a single-user console runs one A/B at a
    time; a second ``start`` while one is running returns ``None`` so
    two promotions can never race.
    """

    def __init__(self, max_runs: int = 20) -> None:
        self._runs: dict[str, AbRun] = {}
        self._max = max_runs

    @property
    def running(self) -> AbRun | None:
        for run in self._runs.values():
            if run.status == "running":
                return run
        return None

    def get(self, run_id: str) -> AbRun | None:
        return self._runs.get(run_id)

    def start(
        self,
        name: str,
        work: Callable[[Callable[[str], None]], Awaitable[ABResult]],
        *,
        on_done: Callable[[AbRun], None] | None = None,
    ) -> AbRun | None:
        """Spawn ``work`` in the background; ``None`` when one is running."""
        if self.running is not None:
            return None
        if len(self._runs) >= self._max:
            oldest = min(self._runs.values(), key=lambda r: r.created_at)
            del self._runs[oldest.id]
        run = AbRun(id=str(uuid.uuid4()), name=name)
        self._runs[run.id] = run

        def progress(msg: str) -> None:
            run.progress.append(msg)
            run.queue.put_nowait(("progress", msg))

        async def _go() -> None:
            try:
                result = await work(progress)
            except Exception as exc:  # surfaced as an error event, never thrown at the client
                run.status = "error"
                run.error = str(exc)
                run.queue.put_nowait(("error", run.error))
            else:
                run.status = "completed"
                run.result = result.to_wire()
                run.queue.put_nowait(("result", run.result))
            finally:
                run.queue.put_nowait(None)
                if on_done is not None:
                    on_done(run)

        run.task = asyncio.get_running_loop().create_task(_go())
        return run
