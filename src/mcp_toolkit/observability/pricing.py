"""Model pricing — USD per 1M tokens, prompt and completion separately.

Only a pure lookup over a committed table plus an optional
``MODEL_PRICING_JSON`` env override. An unknown model yields ``None`` —
cost is **never guessed**, because a fabricated price is worse than no
price (it silently defeats the ``RUN_BUDGET_USD`` gate and falsifies the
scorecard).
"""

from __future__ import annotations

import json
import re
from typing import Any

from mcp_toolkit.client.llm.types import TokenUsage

# (prompt, completion) USD per 1M tokens — public OpenAI list prices.
PRICE_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "o4-mini": (1.10, 4.40),
}

_DATED_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def parse_price_overrides(raw: str | None) -> dict[str, tuple[float, float]]:
    """Parse ``MODEL_PRICING_JSON`` — a JSON object mapping model name to
    ``{"prompt": <usd>, "completion": <usd>}`` per 1M tokens.

    A malformed payload raises ``ValueError`` so a typo fails loudly at
    startup instead of silently disabling the override.
    """
    if raw is None:
        return {}
    parsed: Any = json.loads(raw)  # raises — loud config error
    if not isinstance(parsed, dict):
        raise ValueError("MODEL_PRICING_JSON must be a JSON object")
    out: dict[str, tuple[float, float]] = {}
    for model, entry in parsed.items():
        if not isinstance(model, str) or not isinstance(entry, dict):
            raise ValueError(f"MODEL_PRICING_JSON: bad entry for {model!r}")
        prompt = entry.get("prompt")
        completion = entry.get("completion")
        if (
            not isinstance(prompt, int | float)
            or not isinstance(completion, int | float)
            or prompt < 0
            or completion < 0
        ):
            raise ValueError(
                f"MODEL_PRICING_JSON[{model!r}] needs numeric 'prompt' and 'completion'"
            )
        out[model] = (float(prompt), float(completion))
    return out


def price_per_mtok(
    model: str, overrides: dict[str, tuple[float, float]] | None = None
) -> tuple[float, float] | None:
    """(prompt, completion) USD per 1M tokens for ``model``.

    Lookup order: env override → table → dated-variant normalization
    (``gpt-4o-mini-2024-07-18`` is the same price as ``gpt-4o-mini``).
    Unknown ⇒ ``None``.
    """
    if overrides and model in overrides:
        return overrides[model]
    base = _DATED_SUFFIX.sub("", model)
    if overrides and base in overrides:
        return overrides[base]
    if model in PRICE_PER_MTOK:
        return PRICE_PER_MTOK[model]
    return PRICE_PER_MTOK.get(base)


def cost_usd(
    model: str,
    usage: TokenUsage,
    overrides: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """USD cost of ``usage`` under ``model`` — ``None`` when the model's
    price is unknown (never estimated)."""
    price = price_per_mtok(model, overrides)
    if price is None:
        return None
    prompt_price, completion_price = price
    return (usage.prompt_tokens * prompt_price + usage.completion_tokens * completion_price) / 1e6
