"""Single-use pending-run tokens bridging ``POST /api/chat`` (validates
and stashes the question) to ``GET /api/chat/stream`` (consumes it and
starts streaming). Avoids putting a long, user-authored question in a
URL query string while keeping the streaming endpoint a plain ``GET``.

In-memory, bounded and TTL-expired — this is a local single-user
console, not a durable job queue.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable


class RunStore:
    def __init__(
        self,
        ttl_s: float = 5 * 60,
        max_pending: int = 50,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._ttl_s = ttl_s
        self._max_pending = max_pending
        self._now = now
        self._pending: dict[str, tuple[str, float]] = {}

    def _sweep(self) -> None:
        cutoff = self._now() - self._ttl_s
        for run_id, (_, created_at) in list(self._pending.items()):
            if created_at < cutoff:
                del self._pending[run_id]

    def create(self, question: str) -> str:
        """Stash a validated question; returns a one-time run id."""
        self._sweep()
        if len(self._pending) >= self._max_pending:
            # Drop the oldest entry rather than grow unbounded — a local
            # console abandoning streams faster than it starts them is
            # misuse, not a case worth an error path.
            oldest = next(iter(self._pending))
            del self._pending[oldest]
        run_id = str(uuid.uuid4())
        self._pending[run_id] = (question, self._now())
        return run_id

    def consume(self, run_id: str) -> str | None:
        """Consume (pop) a run id — single use, never replayable."""
        self._sweep()
        entry = self._pending.pop(run_id, None)
        return entry[0] if entry is not None else None

    @property
    def size(self) -> int:
        """Current pending count — exposed for tests only."""
        return len(self._pending)
