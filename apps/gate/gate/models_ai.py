"""Model tiers, pricing, and the Azure OpenAI client (DESIGN.md section 3).

Prices in microdollars per token == dollars per 1M tokens, so
cost_microdollars = tokens * price. Cached input assumed at 10% of input.
"""

import math
import os
from typing import Literal, TypedDict

import httpx

Tier = Literal["sol", "terra", "luna"]

TIER_PRICES: dict[Tier, dict[str, float]] = {
    "sol": {"input": 5, "cached_input": 0.5, "output": 30},
    "terra": {"input": 2, "cached_input": 0.2, "output": 12},
    "luna": {"input": 0.2, "cached_input": 0.02, "output": 1.2},
}

# The tier each agent is entitled to (DESIGN.md section 3). An agent may
# request a CHEAPER tier, never a more expensive one.
AGENT_TIER: dict[str, Tier] = {
    "strategist": "sol",
    "web_builder": "sol",
    "marketer": "terra",
    "ops": "luna",
    "evaluator": "luna",
}

_TIER_ORDER: list[Tier] = ["luna", "terra", "sol"]


def resolve_tier(agent_name: str, requested: str | None = None) -> Tier:
    entitled = AGENT_TIER.get(agent_name)
    if entitled is None:
        raise ValueError(f"agent '{agent_name}' has no model tier configured")
    if not requested:
        return entitled
    if requested not in _TIER_ORDER:
        raise ValueError(f"unknown tier '{requested}'")
    if _TIER_ORDER.index(requested) > _TIER_ORDER.index(entitled):
        raise ValueError(f"agent '{agent_name}' is limited to tier '{entitled}', requested '{requested}'")
    return requested  # type: ignore[return-value]


def _deployment(tier: Tier) -> str:
    name = os.environ.get(f"AZURE_OPENAI_DEPLOYMENT_{tier.upper()}")
    if not name:
        raise RuntimeError(f"AZURE_OPENAI_DEPLOYMENT_{tier.upper()} not configured")
    return name


class ChatResult(TypedDict):
    content: str
    model_id: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_microdollars: int


def cost_microdollars(
    tier: Tier, input_tokens: int, cached_input_tokens: int, output_tokens: int
) -> int:
    p = TIER_PRICES[tier]
    fresh_input = max(0, input_tokens - cached_input_tokens)
    return math.ceil(
        fresh_input * p["input"]
        + cached_input_tokens * p["cached_input"]
        + output_tokens * p["output"]
    )


def azure_chat(
    tier: Tier,
    messages: list[dict[str, str]],
    *,
    json_mode: bool = False,
    max_tokens: int | None = None,
) -> ChatResult:
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY")
    if not endpoint or not api_key:
        raise RuntimeError("Azure OpenAI credentials not configured on the gate")

    base = endpoint.rstrip("/")
    if not base.endswith("/openai"):
        base += "/openai"

    body: dict[str, object] = {"model": _deployment(tier), "messages": messages}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if max_tokens:
        body["max_completion_tokens"] = max_tokens

    res = httpx.post(
        f"{base}/v1/chat/completions",
        headers={"api-key": api_key},
        json=body,
        timeout=180,
    )
    if res.status_code != 200:
        raise RuntimeError(f"Azure OpenAI {res.status_code}: {res.text[:400]}")

    data = res.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content")
    if not isinstance(content, str):
        raise RuntimeError("Azure OpenAI returned no message content")

    usage = data.get("usage") or {}
    input_tokens = int(usage.get("prompt_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)

    return {
        "content": content,
        "model_id": data.get("model") or _deployment(tier),
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "cost_microdollars": cost_microdollars(tier, input_tokens, cached, output_tokens),
    }
