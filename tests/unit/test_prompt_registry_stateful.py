"""Property-based state machine for the registry invariants.

Hypothesis drives random sequences of ``create_candidate`` / ``promote``
/ ``promote_override`` / ``rollback`` / ``transition`` / ``reload`` —
including calls that must fail — over a throwaway registry. After every
step the same invariants must hold: exactly one promoted per name,
versions contiguous from 1, a verifiable audit chain, and an on-disk
state that a fresh ``PromptRegistry`` can load.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from mcp_toolkit.prompts.errors import PromptRegistryError
from mcp_toolkit.prompts.registry import ALLOWED_TRANSITIONS, PromptRegistry
from mcp_toolkit.prompts.schema import PROMPT_STATUSES

NAMES = ("agent_system", "judge_rubric")


def _promoted_artifact(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "status": "promoted",
        "author": "stateful-test",
        "created_at": "2026-01-01T00:00:00+00:00",
        "description": f"{name} v1",
        "template": f"You are {name} v1.",
    }


class RegistryMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.root = Path(tempfile.mkdtemp(prefix="registry-sm-"))
        for name in NAMES:
            d = self.root / name
            d.mkdir(parents=True)
            (d / "v1.yaml").write_text(yaml.safe_dump(_promoted_artifact(name)), encoding="utf-8")
        self.registry = PromptRegistry(self.root, audit_path=self.root / "audit.jsonl")

    def teardown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _version(self, data: st.DataObject, name: str) -> int:
        """Draw a version around the real range so both hits and misses occur."""
        hi = max((a.version for a in self.registry.list(name)), default=1) + 2
        return data.draw(st.integers(min_value=1, max_value=hi), label="version")

    @rule(name=st.sampled_from(NAMES), data=st.data())
    def candidate(self, name: str, data: st.DataObject) -> None:
        template = data.draw(st.text(alphabet="xy ", min_size=1, max_size=200), label="template")
        # Rejected is fine — the invariant is what matters.
        with contextlib.suppress(PromptRegistryError, ValueError):
            self.registry.create_candidate(name, template=template, description="sm", author="sm")

    @rule(data=st.data())
    def unknown_candidate(self, data: st.DataObject) -> None:
        name = data.draw(
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz_",
                min_size=1,
                max_size=12,
            ).filter(lambda s: s not in NAMES and s[:1].isalpha()),
            label="unknown name",
        )
        with contextlib.suppress(PromptRegistryError):
            self.registry.create_candidate(name, template="x", description="sm", author="sm")

    @rule(name=st.sampled_from(NAMES), data=st.data())
    def promote(self, name: str, data: st.DataObject) -> None:
        version = self._version(data, name)
        with contextlib.suppress(PromptRegistryError, ValueError):
            self.registry.promote(name, version, reason="sm")

    @rule(name=st.sampled_from(NAMES), data=st.data())
    def promote_override(self, name: str, data: st.DataObject) -> None:
        version = self._version(data, name)
        with contextlib.suppress(PromptRegistryError, ValueError):
            self.registry.promote_override(name, version, reason="sm")

    @rule(name=st.sampled_from(NAMES), data=st.data())
    def rollback(self, name: str, data: st.DataObject) -> None:
        version = self._version(data, name)
        with contextlib.suppress(PromptRegistryError, ValueError):
            self.registry.rollback(name, version, reason="sm")

    @rule(name=st.sampled_from(NAMES), to=st.sampled_from(PROMPT_STATUSES), data=st.data())
    def transition(self, name: str, to: Any, data: st.DataObject) -> None:
        version = self._version(data, name)
        # When the rule happens to land on a legal move the call succeeds;
        # every other combination must be a clean, typed rejection.
        with contextlib.suppress(PromptRegistryError, ValueError):
            self.registry.transition(name, version, to, reason="sm")

    @rule(name=st.sampled_from(NAMES))
    def reload(self, name: str) -> None:
        self.registry.reload()

    @invariant()
    def invariants_hold(self) -> None:
        for name in NAMES:
            arts = self.registry.list(name)
            promoted = [a for a in arts if a.status == "promoted"]
            assert len(promoted) == 1, f"{name}: {len(promoted)} promoted"
            versions = sorted(a.version for a in arts)
            assert versions == list(range(1, len(versions) + 1)), versions
            # The state machine never produces a transition the table
            # does not allow — sanity check our own bookkeeping.
            for a in arts:
                assert a.status in ALLOWED_TRANSITIONS
        assert self.registry.audit_log.verify_chain() is None
        # The strongest check: the on-disk state reloads cleanly.
        fresh = PromptRegistry(self.root, audit_path=self.root / "audit.jsonl")
        assert fresh.audit_log.verify_chain() is None


TestRegistryMachine = RegistryMachine.TestCase
TestRegistryMachine.settings = hyp_settings(
    max_examples=60,
    stateful_step_count=25,
    deadline=None,  # YAML writes under coverage can exceed the default
)
