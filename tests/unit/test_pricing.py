"""Pricing table tests — lookup, env overrides, and the never-guess rule."""

from __future__ import annotations

import json

import pytest

from mcp_toolkit.client.llm.types import TokenUsage
from mcp_toolkit.observability.pricing import (
    PRICE_PER_MTOK,
    cost_usd,
    parse_price_overrides,
    price_per_mtok,
)


def test_known_model_price() -> None:
    assert price_per_mtok("gpt-4o-mini") == PRICE_PER_MTOK["gpt-4o-mini"]


def test_dated_variant_normalizes_to_base() -> None:
    """`gpt-4o-mini-2024-07-18` is priced like `gpt-4o-mini`."""
    assert price_per_mtok("gpt-4o-mini-2024-07-18") == PRICE_PER_MTOK["gpt-4o-mini"]


def test_unknown_model_returns_none() -> None:
    """Never guess a price — an unknown model yields None, not 0."""
    assert price_per_mtok("totally-made-up-model") is None
    assert cost_usd("totally-made-up-model", TokenUsage(1_000_000, 1_000_000)) is None


def test_stub_model_has_no_price() -> None:
    """The deterministic stub is unbillable — its costUsd stays null."""
    assert price_per_mtok("stub") is None


def test_cost_usd_math() -> None:
    usage = TokenUsage(prompt_tokens=1_000_000, completion_tokens=500_000)
    # gpt-4o-mini: $0.15/M in + $0.60/M out → 0.15 + 0.30 = 0.45
    assert cost_usd("gpt-4o-mini", usage) == pytest.approx(0.45)


def test_override_adds_unknown_model() -> None:
    overrides = parse_price_overrides('{"my-model": {"prompt": 1.0, "completion": 2.0}}')
    assert price_per_mtok("my-model", overrides) == (1.0, 2.0)
    assert cost_usd("my-model", TokenUsage(1_000_000, 0), overrides) == pytest.approx(1.0)


def test_override_beats_table() -> None:
    overrides = parse_price_overrides('{"gpt-4o-mini": {"prompt": 9.0, "completion": 9.0}}')
    assert price_per_mtok("gpt-4o-mini", overrides) == (9.0, 9.0)


def test_no_override() -> None:
    assert parse_price_overrides(None) == {}
    assert parse_price_overrides("{}") == {}


def test_malformed_override_fails_loudly() -> None:
    with pytest.raises(ValueError, match="MODEL_PRICING_JSON"):
        parse_price_overrides('{"m": {"prompt": "cheap"}}')
    with pytest.raises(ValueError, match="MODEL_PRICING_JSON"):
        parse_price_overrides("[1,2,3]")
    with pytest.raises(ValueError, match="MODEL_PRICING_JSON"):
        parse_price_overrides('{"m": {"prompt": -1, "completion": 2}}')
    with pytest.raises((ValueError, json.JSONDecodeError)):
        parse_price_overrides("{not json")
