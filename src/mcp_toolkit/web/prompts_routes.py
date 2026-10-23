"""Prompt-registry API — read surfaces for everyone, mutations gated
behind ``ENABLE_PROMPT_ADMIN=1`` (the console is unauthenticated, so
writes must be opted into explicitly).

Routes:

- ``GET  /api/prompts``                    all artifacts, grouped
- ``GET  /api/prompts/audit``              audit chain + verification
- ``GET  /api/prompts/ab/{runId}``         A/B run status/result
- ``GET  /api/prompts/ab/{runId}/stream``  A/B progress over SSE
- ``GET  /api/prompts/{name}``             one name's versions
- ``GET  /api/prompts/{name}/{version}``   one artifact
- ``GET  /api/prompts/{name}/diff``        ``?from=&to=`` unified diff
- ``POST /api/prompts/{name}/candidates``  create candidate (admin)
- ``POST /api/prompts/{name}/ab-test``     start A/B run (admin)
- ``POST /api/prompts/{name}/promote``     gated promotion (admin)
- ``POST /api/prompts/{name}/rollback``    restore a version (admin)

Templates are *data*: validated, size-capped and rejected when they
contain key-looking strings — never executed, never interpolated.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from mcp_toolkit.prompts.ab_test import ABResult
from mcp_toolkit.prompts.audit import looks_key_like
from mcp_toolkit.prompts.errors import (
    InvalidTransition,
    PromptRegistryError,
    RegistryValidationError,
    UnknownPromptVersion,
    UnsafeAuditReason,
)
from mcp_toolkit.prompts.loader import LoadedPrompt
from mcp_toolkit.prompts.registry import PromptRegistry
from mcp_toolkit.prompts.schema import PromptArtifact
from mcp_toolkit.web.ab_runs import (
    AB_RESULTS_DIR,
    AbRun,
    AbRunManager,
    latest_ab_result,
)

MAX_TEMPLATE_BYTES = 32_768
# Candidate submissions are tighter than the artifact schema limit —
# 8 000 chars per the A/B workflow spec.
MAX_CANDIDATE_TEMPLATE_CHARS = 8_000
MAX_DESCRIPTION_CHARS = 2_000


def _err(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"detail": message, "code": code, **extra},
    )


class CandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Length is validated manually so the API returns 400 (not 422) —
    # the spec's status-code matrix pins 400 for oversize templates.
    template: str = Field(min_length=1)
    description: str = Field(min_length=1)
    author: str = Field(default="api", min_length=1, max_length=128)
    model_hint: str | None = Field(default=None, max_length=128)


class AbTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_version: int = Field(ge=1)
    baseline_version: int | None = Field(default=None, ge=1)
    # Explicit smoke-test flag: runs the mechanics on StubLLM and never
    # produces promotable evidence.
    offline: bool = False


class PromoteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    ab_id: str | None = None
    override_reason: str | None = Field(default=None, max_length=2000)


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class PromptAPIDeps:
    """Everything the registry API needs — injected by the app factory."""

    def __init__(
        self,
        *,
        registry: PromptRegistry,
        admin_enabled: bool,
        ab_manager: AbRunManager,
        make_ab_runner: Callable[
            [str, LoadedPrompt, LoadedPrompt, bool, Callable[[str], None]],
            Awaitable[ABResult],
        ]
        | None,
        ab_results_dir: Path | str | None = None,
    ) -> None:
        self.registry = registry
        self.admin_enabled = admin_enabled
        self.ab_manager = ab_manager
        # (name, baseline, candidate, offline, progress) → awaitable ABResult
        self.make_ab_runner = make_ab_runner
        self.ab_results_dir = Path(ab_results_dir) if ab_results_dir is not None else AB_RESULTS_DIR


def _artifact_json(a: PromptArtifact) -> dict[str, Any]:
    return {
        "name": a.name,
        "version": a.version,
        "status": a.status,
        "author": a.author,
        "createdAt": a.created_at.isoformat(),
        "description": a.description,
        "modelHint": a.model_hint,
        "sha256": a.sha256,
        "template": a.template,
    }


def create_prompt_router(
    deps_provider: Callable[[], PromptAPIDeps | None],
) -> APIRouter:
    router = APIRouter(prefix="/api/prompts")

    def _deps() -> PromptAPIDeps:
        deps = deps_provider()
        if deps is None:
            raise HTTPException(404, detail="prompt registry is not configured")
        return deps

    def _require_admin() -> JSONResponse | None:
        if not _deps().admin_enabled:
            return _err(
                403,
                "admin_disabled",
                "prompt mutations are disabled — set ENABLE_PROMPT_ADMIN=1 to enable",
            )
        return None

    def _registry_or_500() -> PromptRegistry | JSONResponse:
        try:
            _deps().registry.reload()
        except RegistryValidationError as exc:
            return _err(500, "registry_invalid", str(exc))
        return _deps().registry

    # ── literal routes first (before /{name} catches them) ──────────

    @router.get("")
    async def list_all() -> Any:
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        out: dict[str, list[dict[str, Any]]] = {}
        for a in registry.list():
            out.setdefault(a.name, []).append(_artifact_json(a))
        # The console needs this to render its read-only state —
        # enforcement stays server-side on every mutation route.
        return {"prompts": out, "adminEnabled": _deps().admin_enabled}

    @router.get("/audit")
    async def audit() -> Any:
        deps = _deps()
        verified = deps.registry.audit_log.verify_chain()
        return {
            "events": deps.registry.audit_log.read(),
            "verified": verified is None,
            "firstTamperedLine": verified,
        }

    @router.get("/ab/{run_id}")
    async def ab_status(run_id: str) -> Any:
        run = _deps().ab_manager.get(run_id)
        if run is None:
            return _err(404, "not_found", f"unknown A/B run {run_id!r}")
        return {
            "runId": run.id,
            "name": run.name,
            "status": run.status,
            "progress": run.progress,
            "result": run.result,
            "error": run.error,
        }

    @router.get("/ab/{run_id}/stream")
    async def ab_stream(run_id: str) -> StreamingResponse:
        run = _deps().ab_manager.get(run_id)
        if run is None:
            return StreamingResponse(
                _sse_error(f"unknown A/B run {run_id!r}"), media_type="text/event-stream"
            )

        async def frames() -> AsyncIterator[bytes]:
            for msg in run.progress:
                yield _sse("progress", {"message": msg})
            while True:
                try:
                    item = await asyncio.wait_for(run.queue.get(), timeout=15.0)
                except TimeoutError:
                    yield b": heartbeat\n\n"
                    continue
                if item is None:
                    break
                kind, payload = item
                yield _sse(kind, payload if isinstance(payload, dict) else {"message": payload})

        return StreamingResponse(frames(), media_type="text/event-stream")

    # ── per-name reads ──────────────────────────────────────────────

    @router.get("/{name}")
    async def show_name(name: str) -> Any:
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        versions = registry.list(name)
        if not versions:
            return _err(404, "not_found", f"unknown prompt {name!r}")
        try:
            promoted = registry.get_promoted(name).version
        except PromptRegistryError:
            promoted = None
        return {
            "name": name,
            "promotedVersion": promoted,
            "versions": [_artifact_json(a) for a in versions],
        }

    @router.get("/{name}/diff")
    async def diff(
        name: str,
        from_: int = Query(default=0, alias="from"),
        to: int = 0,
    ) -> Any:
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        if from_ < 1 or to < 1:
            return _err(400, "invalid_input", "diff requires ?from=<v>&to=<v>")
        try:
            text = registry.diff(name, from_, to)
        except UnknownPromptVersion as exc:
            return _err(404, "not_found", str(exc))
        return {"name": name, "from": from_, "to": to, "diff": text}

    @router.get("/{name}/ab-result")
    async def ab_result(name: str, candidate: int = Query(default=0)) -> Any:
        """Newest persisted A/B result for ``name@v{candidate}`` — what a
        promote call would use as evidence."""
        if candidate < 1:
            return _err(400, "invalid_input", "ab-result requires ?candidate=<v>")
        evidence = latest_ab_result(name, candidate, _deps().ab_results_dir)
        if evidence is None:
            return _err(404, "not_found", f"no A/B result for {name}@v{candidate}")
        return evidence

    @router.get("/{name}/{version}")
    async def show_version(name: str, version: int) -> Any:
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        try:
            return _artifact_json(registry.get(name, version))
        except UnknownPromptVersion as exc:
            return _err(404, "not_found", str(exc))

    # ── mutations (admin-gated) ─────────────────────────────────────

    @router.post("/{name}/candidates", status_code=201)
    async def create_candidate(name: str, body: CandidateBody, request: Request) -> Any:
        if (blocked := _require_admin()) is not None:
            return blocked
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > MAX_TEMPLATE_BYTES:
            return _err(400, "invalid_input", "request body exceeds 32 KiB")
        if len(body.template) > MAX_CANDIDATE_TEMPLATE_CHARS:
            return _err(
                400,
                "invalid_input",
                f"template exceeds {MAX_CANDIDATE_TEMPLATE_CHARS} characters",
            )
        if len(body.description) > MAX_DESCRIPTION_CHARS:
            return _err(400, "invalid_input", "description exceeds 2 000 characters")
        if looks_key_like(body.template):
            return _err(400, "invalid_input", "template contains a key-looking string")
        if looks_key_like(body.description):
            return _err(400, "invalid_input", "description contains a key-looking string")
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        if not registry.list(name):
            # A candidate-only lineage would violate the one-promoted
            # invariant on the next reload — mutations need an existing
            # prompt name.
            return _err(404, "not_found", f"unknown prompt {name!r}")
        try:
            artifact = registry.create_candidate(
                name,
                template=body.template,
                description=body.description,
                author=body.author,
                model_hint=body.model_hint,
            )
        except (ValueError, InvalidTransition, UnsafeAuditReason) as exc:
            return _err(400, "invalid_input", str(exc))
        return _artifact_json(artifact)

    @router.post("/{name}/ab-test", status_code=202)
    async def start_ab_test(name: str, body: AbTestBody) -> Any:
        if (blocked := _require_admin()) is not None:
            return blocked
        deps = _deps()
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        try:
            candidate_art = registry.get(name, body.candidate_version)
            baseline_art = (
                registry.get(name, body.baseline_version)
                if body.baseline_version is not None
                else registry.get_promoted(name)
            )
        except UnknownPromptVersion as exc:
            return _err(404, "not_found", str(exc))
        runner = deps.make_ab_runner
        if runner is None:
            return _err(
                503,
                "live_unavailable",
                "A/B evaluation is not configured on this server",
            )
        baseline = LoadedPrompt(
            name=baseline_art.name,
            version=baseline_art.version,
            sha256=baseline_art.sha256,
            template=baseline_art.template,
        )
        candidate = LoadedPrompt(
            name=candidate_art.name,
            version=candidate_art.version,
            sha256=candidate_art.sha256,
            template=candidate_art.template,
        )
        run = deps.ab_manager.start(
            name,
            lambda progress: runner(name, baseline, candidate, body.offline, progress),
            on_done=_persist(deps),
        )
        if run is None:
            return _err(409, "ab_running", "an A/B run is already in progress")
        return {"runId": run.id, "name": name, "status": "running"}

    @router.post("/{name}/promote")
    async def promote(name: str, body: PromoteBody) -> Any:
        if (blocked := _require_admin()) is not None:
            return blocked
        deps = _deps()
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        try:
            artifact = registry.get(name, body.version)
        except UnknownPromptVersion as exc:
            return _err(404, "not_found", str(exc))
        if artifact.status != "candidate":
            return _err(
                400,
                "invalid_input",
                f"{name}@v{body.version} is {artifact.status!r} — only candidates promote",
            )
        if body.override_reason:
            try:
                updated = registry.promote_override(name, body.version, reason=body.override_reason)
            except (InvalidTransition, UnsafeAuditReason, ValueError) as exc:
                return _err(400, "invalid_input", str(exc))
            return _artifact_json(updated)

        evidence = _find_evidence(deps, name, body.version, body.ab_id)
        if evidence is None:
            return _err(
                409,
                "no_ab_evidence",
                f"no completed A/B run for {name}@v{body.version} — run /ab-test first "
                "or pass overrideReason",
            )
        if not evidence.get("promotionAllowed"):
            return _err(
                409,
                "gate_failed",
                "A/B gate failed — promotion blocked",
                regressions=evidence.get("regressions", []),
                mode=evidence.get("mode"),
            )
        try:
            updated = registry.promote(
                name, body.version, reason=f"A/B gate passed ({body.ab_id or 'latest'})"
            )
        except (InvalidTransition, UnsafeAuditReason, ValueError) as exc:
            return _err(400, "invalid_input", str(exc))
        return _artifact_json(updated)

    @router.post("/{name}/rollback")
    async def rollback(name: str, body: RollbackBody) -> Any:
        if (blocked := _require_admin()) is not None:
            return blocked
        registry = _registry_or_500()
        if isinstance(registry, JSONResponse):
            return registry
        try:
            restored = registry.rollback(name, body.to_version, reason=body.reason)
        except UnknownPromptVersion as exc:
            return _err(404, "not_found", str(exc))
        except (InvalidTransition, UnsafeAuditReason, ValueError) as exc:
            return _err(400, "invalid_input", str(exc))
        return _artifact_json(restored)

    def _persist(deps: PromptAPIDeps) -> Callable[[AbRun], None]:
        def _save(run: AbRun) -> None:
            if run.status == "completed" and run.result is not None:
                # Persisted evidence for a later promote call + audit.
                registry = deps.registry
                wire = run.result
                registry.audit_log.append(
                    {
                        "type": "ab_test",
                        "name": wire["name"],
                        "baseline_version": wire["baselineVersion"],
                        "candidate_version": wire["candidateVersion"],
                        "mode": wire["mode"],
                        "gate_passed": wire["gatePassed"],
                        "deltas": wire["deltas"],
                        "regressions": wire["regressions"],
                        "run_id": run.id,
                    }
                )
                _persist_result_file(wire, deps.ab_results_dir)

        return _save

    return router


def _persist_result_file(wire: dict[str, Any], out_dir: Path) -> None:
    """Write the run's A/B result to the evidence dir — promote-time
    evidence independent of process memory."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / (
        f"{wire['name']}-b{wire['baselineVersion']}-c{wire['candidateVersion']}-"
        f"{wire['candidateSha256'][:8]}-{stamp}.json"
    )
    path.write_text(json.dumps(wire, indent=2), encoding="utf-8")


def _find_evidence(
    deps: PromptAPIDeps, name: str, version: int, ab_id: str | None
) -> dict[str, Any] | None:
    """Completed A/B evidence for ``name@v{version}`` — the in-memory run
    (when ``ab_id`` given) or the newest persisted result."""
    if ab_id:
        run = deps.ab_manager.get(ab_id)
        if run is None or run.status != "completed" or run.result is None:
            return None
        if run.result.get("candidateVersion") != version or run.result.get("name") != name:
            return None
        return run.result
    return latest_ab_result(name, version, deps.ab_results_dir)


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _sse_error(message: str) -> Iterator[bytes]:
    yield _sse("error", {"message": message})
