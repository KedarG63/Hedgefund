"""Config loading. Nothing clever -- YAML in, dataclasses out."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Channel:
    handle: str
    channel_id: str
    name: str
    lens: str = ""

    @property
    def feed_url(self) -> str:
        return f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"


@dataclass
class IngestCfg:
    min_duration_seconds: int = 180
    lookback_days: int = 30
    max_videos_per_run: int = 25
    transcript_languages: list[str] = field(default_factory=lambda: ["en"])
    # Pause between transcript fetches. Fetching a backlog flat out is what gets
    # the IP blocked -- a whole run then fails with IpBlocked and every video is
    # marked error. A second or two per video costs nothing on a daily run and
    # is the difference between a backfill landing and being throttled.
    request_delay_seconds: float = 1.5


@dataclass
class AnalyzeCfg:
    model: str = "claude-sonnet-4-5"
    # "anthropic" or "deepseek" -- deepseek routes through its Anthropic-API-
    # compatible endpoint using the same anthropic SDK client, see Analyzer.
    provider: str = "anthropic"
    chunk_chars: int = 12000
    chunk_overlap_chars: int = 800
    max_output_tokens: int = 8000
    min_confidence: float = 0.45
    signal_types: list[str] = field(
        default_factory=lambda: [
            "investable_idea", "macro_structure", "research_thread", "historical_precedent",
        ]
    )


@dataclass
class DashboardCfg:
    title: str = "Signal Desk"
    subtitle: str = ""
    output_path: str = "data/dashboard.html"
    max_videos: int = 40


@dataclass
class Config:
    channels: list[Channel]
    ingest: IngestCfg
    analyze: AnalyzeCfg
    dashboard: DashboardCfg
    db_path: Path = ROOT / "data" / "signals.db"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        path = Path(path) if path else ROOT / "config.yaml"
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        return cls(
            channels=[Channel(**c) for c in raw.get("channels", [])],
            ingest=IngestCfg(**raw.get("ingest", {})),
            analyze=AnalyzeCfg(**raw.get("analyze", {})),
            dashboard=DashboardCfg(**raw.get("dashboard", {})),
        )

    def channel_by_id(self, channel_id: str) -> Channel | None:
        return next((c for c in self.channels if c.channel_id == channel_id), None)


def load_dotenv(path: str | Path | None = None) -> None:
    """Minimal .env reader so we don't pull in python-dotenv for six lines."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
