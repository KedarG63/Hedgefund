"""
DeepSeek client via its Anthropic-API-compatible endpoint. Ported (not
copied wholesale) from
latest_information/yt-signal-engine/yt-signal-engine/engine/analyze.py's
Analyzer class.

This is the only place an LLM is called anywhere in the analytics engine.
Forced tool_choice is what makes it non-conversational: the model can only
return the tool's JSON args, never free text. Every actual number (beta,
momentum, Greeks, correlation) is deterministic Python/scipy/statsmodels
math computed before anything in analytics/digest.py ever calls this.
"""
from __future__ import annotations

import time
from typing import Any

from core.config import require

DEEPSEEK_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-chat"


def deepseek_client():
    from anthropic import Anthropic
    return Anthropic(api_key=require("DEEPSEEK_API_KEY"), base_url=DEEPSEEK_BASE_URL)


def call_tool(client, tool: dict, prompt: str, model: str = DEFAULT_MODEL,
             max_tokens: int = 1024, retries: int = 3) -> dict[str, Any]:
    """
    One forced tool-use call. DeepSeek's Anthropic-compatible endpoint runs
    thinking mode by default, which rejects a forced tool_choice -- disabled
    here exactly as in the ported pattern (`analyze.py`'s `_thinking`).
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                tools=[tool],
                tool_choice={"type": "tool", "name": tool["name"]},
                thinking={"type": "disabled"},
                messages=[{"role": "user", "content": prompt}],
            )
            for block in resp.content:
                if block.type == "tool_use":
                    return block.input
            raise ValueError("model returned no tool_use block")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "overloaded" in str(exc).lower() or "rate" in str(exc).lower():
                time.sleep(5 * (attempt + 1))
            else:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"DeepSeek tool call failed after {retries} tries: {last}")
