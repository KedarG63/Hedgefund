"""Extraction: transcript -> structured signals.

Two stages.

MAP  — the transcript is cut into overlapping slices and each is read on its
       own. Slicing matters: a 30-minute episode read in one pass produces
       generic summary mush, because the model compresses. Read in slices, it
       stays specific and keeps its timestamps straight.

REDUCE — every slice's output is put back in front of the model together, which
       merges duplicates, drops the sponsor reads that slipped through, ranks
       what is left, and writes the briefing at the top of the card.

Structured output is enforced with tool schemas rather than "please return
JSON", so parsing never becomes the flaky part of the pipeline.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"

SIGNAL_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["investable_idea", "macro_structure", "research_thread", "historical_precedent"],
        },
        "headline": {"type": "string", "description": "One line, under 100 chars, specific."},
        "body": {"type": "string", "description": "2-4 sentences. Attribute to the speakers."},
        "quote": {"type": "string", "description": "10-40 words copied verbatim from the transcript."},
        "t_start": {"type": "integer", "description": "Seconds into the video of the nearest preceding timestamp."},
        "confidence": {"type": "number", "description": "0-1, how well supported within the transcript."},
        "stance": {"type": "string", "enum": ["bullish", "bearish", "neutral", "contested"]},
        "entities": {"type": "array", "items": {"type": "string"}},
        "research_question": {
            "type": "string",
            "description": "Required for research_thread: the specific question to go answer.",
        },
        "era": {
            "type": "string",
            "description": (
                "Required for historical_precedent: when this happened, e.g. '1998' or "
                "'2007-2009'. body should state the outcome/resolution explicitly."
            ),
        },
    },
    "required": ["type", "headline", "body", "quote", "t_start", "confidence", "stance"],
}

EMIT_SIGNALS_TOOL = {
    "name": "emit_signals",
    "description": "Return the signals extracted from this transcript slice.",
    "input_schema": {
        "type": "object",
        "properties": {"items": {"type": "array", "items": SIGNAL_ITEM_SCHEMA}},
        "required": ["items"],
    },
}

EMIT_BRIEFING_TOOL = {
    "name": "emit_briefing",
    "description": "Return the episode briefing plus the deduplicated, ranked signals.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string"},
            "summary": {"type": "string"},
            "bullets": {"type": "array", "items": {"type": "string"}},
            "so_what": {"type": "string"},
            "items": {"type": "array", "items": SIGNAL_ITEM_SCHEMA},
        },
        "required": ["headline", "summary", "bullets", "so_what", "items"],
    },
}


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """Slice on paragraph/sentence boundaries so a claim is not cut in half."""
    if len(text) <= size:
        return [text]
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            window = text[end - 400 : end]
            for pattern in ("\n[", ". ", " "):
                idx = window.rfind(pattern)
                if idx != -1:
                    end = end - 400 + idx + len(pattern)
                    break
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def _fmt_chapters(chapters: list) -> str:
    if not chapters:
        return "The creator published no chapter list for this video."
    lines = []
    for sec, label in chapters:
        m, s = divmod(int(sec), 60)
        h, m = divmod(m, 60)
        stamp = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        lines.append(f"- [{stamp}] {label}")
    return "The creator's own chapter list (use it to place what you read):\n" + "\n".join(lines)



# DeepSeek exposes an Anthropic-API-compatible endpoint: same anthropic SDK,
# same tool-calling schema, just a different base_url and key. It maps Claude
# model names onto its own tiers, but sending its native model name directly
# is clearer to read here than a Claude name that would actually hit DeepSeek.
PROVIDERS = {
    "anthropic": {"env": "ANTHROPIC_API_KEY", "base_url": None},
    "deepseek": {"env": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com/anthropic"},
}


class Analyzer:
    def __init__(
        self,
        model: str,
        max_tokens: int = 8000,
        api_key: str | None = None,
        provider: str = "anthropic",
    ):
        from anthropic import Anthropic

        if provider not in PROVIDERS:
            raise RuntimeError(f"unknown analyze.provider {provider!r}; use one of {sorted(PROVIDERS)}")
        env_var, base_url = PROVIDERS[provider]["env"], PROVIDERS[provider]["base_url"]

        key = api_key or os.environ.get(env_var)
        if not key:
            raise RuntimeError(f"{env_var} is not set. Copy .env.example to .env.")
        self.client = Anthropic(api_key=key, base_url=base_url) if base_url else Anthropic(api_key=key)
        self.model = model
        self.max_tokens = max_tokens
        # DeepSeek's Anthropic-compatible endpoint runs thinking mode by
        # default, which rejects forced tool_choice -- disable it via the
        # standard Anthropic `thinking` field (their docs confirm it's
        # supported, just ignoring budget_tokens).
        self._thinking = {"type": "disabled"} if provider == "deepseek" else None
        self.extract_tmpl = (PROMPTS / "extract.md").read_text()
        self.synth_tmpl = (PROMPTS / "synthesize.md").read_text()

    # -- plumbing ----------------------------------------------------------

    def _call(self, prompt: str, tool: dict, retries: int = 3) -> dict[str, Any]:
        last: Exception | None = None
        for attempt in range(retries):
            try:
                kwargs = {}
                if self._thinking is not None:
                    kwargs["thinking"] = self._thinking
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    tools=[tool],
                    tool_choice={"type": "tool", "name": tool["name"]},
                    messages=[{"role": "user", "content": prompt}],
                    **kwargs,
                )
                for block in resp.content:
                    if block.type == "tool_use":
                        return block.input  # type: ignore[return-value]
                raise ValueError("model returned no tool_use block")
            except Exception as exc:  # noqa: BLE001
                last = exc
                if "overloaded" in str(exc).lower() or "rate" in str(exc).lower():
                    time.sleep(5 * (attempt + 1))
                else:
                    time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"extraction call failed after {retries} tries: {last}")

    # -- stages ------------------------------------------------------------

    def extract_chunk(self, chunk: str, meta: dict, index: int, total: int) -> list[dict]:
        prompt = self.extract_tmpl.format(
            channel_name=meta["channel_name"],
            lens=meta.get("lens", "(no channel notes provided)"),
            title=meta["title"],
            published=meta["published"][:10],
            chunk_index=index + 1,
            chunk_total=total,
            chapter_block=_fmt_chapters(meta.get("chapters", [])),
        )
        prompt += f"\n\n---\nTRANSCRIPT SLICE\n---\n{chunk}\n"
        out = self._call(prompt, EMIT_SIGNALS_TOOL)
        return out.get("items", []) or []

    def synthesize(self, items: list[dict], meta: dict) -> dict[str, Any]:
        if not items:
            signal_block = "(no signals were extracted from this episode)"
        else:
            signal_block = "\n".join(
                f"- [{it.get('type')}|{it.get('stance')}|conf {it.get('confidence')}] "
                f"{it.get('headline')}\n  {it.get('body')}\n  quote: \"{it.get('quote')}\" "
                f"(t={it.get('t_start')}s)"
                for it in items
            )
        prompt = self.synth_tmpl.format(
            channel_name=meta["channel_name"],
            title=meta["title"],
            published=meta["published"][:10],
            signal_block=signal_block,
            chapter_block=_fmt_chapters(meta.get("chapters", [])),
        )
        return self._call(prompt, EMIT_BRIEFING_TOOL)

    # -- public ------------------------------------------------------------

    def analyze(
        self,
        transcript: str,
        meta: dict,
        chunk_chars: int = 12000,
        overlap: int = 800,
        allowed_types: list[str] | None = None,
        min_confidence: float = 0.0,
        verbose: bool = True,
    ) -> tuple[dict[str, Any], list[dict]]:
        chunks = chunk_text(transcript, chunk_chars, overlap)
        raw: list[dict] = []
        for i, chunk in enumerate(chunks):
            if verbose:
                print(f"    map {i + 1}/{len(chunks)} ({len(chunk):,} chars)", flush=True)
            try:
                raw.extend(self.extract_chunk(chunk, meta, i, len(chunks)))
            except Exception as exc:  # noqa: BLE001
                print(f"    ! slice {i + 1} failed: {exc}")

        if verbose:
            print(f"    reduce: {len(raw)} raw signals", flush=True)
        briefing = self.synthesize(raw, meta)

        items = briefing.get("items", []) or []
        if allowed_types:
            items = [i for i in items if i.get("type") in allowed_types]
        items = [i for i in items if float(i.get("confidence") or 0) >= min_confidence]
        # research_thread without a question is not usable -- demote it.
        for it in items:
            if it.get("type") == "research_thread" and not it.get("research_question"):
                it["research_question"] = f"Verify: {it.get('headline')}"
            if it.get("type") == "historical_precedent" and not it.get("era"):
                it["era"] = "date unclear"

        summary = {
            "headline": briefing.get("headline"),
            "summary": briefing.get("summary"),
            "bullets": briefing.get("bullets", []),
            "so_what": briefing.get("so_what"),
        }
        return summary, items


def strip_sponsor_blocks(text: str) -> str:
    """Cheap pre-filter: drop obvious ad reads before they cost you tokens.

    Deliberately conservative -- the prompt is the real defence. This only
    catches the boilerplate URL walls in descriptions and read-out promo codes.
    """
    patterns = [
        r"(?i)(use|with) (my |the )?(promo |discount )?code [A-Z0-9']{3,15}[^.]{0,80}\.",
        r"(?i)link in the (description|bio)[^.]{0,60}\.",
        r"(?i)thank you (so much )?to [A-Z][\w .]{2,30} for sponsoring[^.]{0,60}\.",
    ]
    for p in patterns:
        text = re.sub(p, " ", text)
    return re.sub(r"\s{3,}", "  ", text)
