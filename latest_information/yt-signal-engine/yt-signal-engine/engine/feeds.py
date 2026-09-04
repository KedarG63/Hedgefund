"""Channel ingest.

YouTube publishes a public Atom feed per channel at
    https://www.youtube.com/feeds/videos.xml?channel_id=UC...
No API key, no quota, no OAuth. It carries the last ~15 uploads with title,
publish time, description (which for these channels includes the chapter list)
and view/rating counts. That is enough to drive the whole pipeline.

Limitation: 15 videos. For a deeper backfill you need either the YouTube Data
API (quota'd, needs a key) or yt-dlp. See `list_channel_videos_deep`.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# "0:00 Intro" / "10:19 Cancer Breakthrough" / "1:02:30 Closing"
CHAPTER_RE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.{2,90})$", re.MULTILINE)


@dataclass
class VideoMeta:
    video_id: str
    channel_id: str
    channel_name: str
    title: str
    published: str          # ISO 8601
    description: str
    thumbnail: str
    views: int | None
    chapters: list[tuple[int, str]]   # (seconds, label)

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _hms_to_seconds(stamp: str) -> int:
    parts = [int(p) for p in stamp.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts[-3:]
    return h * 3600 + m * 60 + s


def parse_chapters(description: str) -> list[tuple[int, str]]:
    """Pull the creator's own chapter list out of the description.

    This is free structure. Both tracked channels publish chapters, and they
    are a far better segmentation of the episode than anything we could infer
    from the transcript -- the creator is telling us where the topics are.
    """
    out: list[tuple[int, str]] = []
    for stamp, label in CHAPTER_RE.findall(description or ""):
        label = label.strip().strip("-–—").strip()
        if not label or label.lower().startswith(("http", "▶")):
            continue
        out.append((_hms_to_seconds(stamp), label))
    # Chapter lists are monotonic; anything else is a false positive (e.g. a
    # price like "5:41" inside prose). Keep the longest increasing run.
    cleaned: list[tuple[int, str]] = []
    for sec, label in out:
        if not cleaned or sec > cleaned[-1][0]:
            cleaned.append((sec, label))
    return cleaned if len(cleaned) >= 2 else []


def fetch_channel_feed(feed_url: str, timeout: int = 20) -> list[VideoMeta]:
    resp = requests.get(feed_url, headers={"User-Agent": UA}, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    channel_name = (root.findtext("atom:title", default="", namespaces=NS) or "").strip()
    videos: list[VideoMeta] = []

    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", default="", namespaces=NS)
        if not vid:
            continue
        group = entry.find("media:group", NS)
        desc = group.findtext("media:description", default="", namespaces=NS) if group is not None else ""
        thumb_el = group.find("media:thumbnail", NS) if group is not None else None
        stats_el = group.find("media:community/media:statistics", NS) if group is not None else None

        videos.append(
            VideoMeta(
                video_id=vid,
                channel_id=entry.findtext("yt:channelId", default="", namespaces=NS),
                channel_name=channel_name,
                title=(entry.findtext("atom:title", default="", namespaces=NS) or "").strip(),
                published=entry.findtext("atom:published", default="", namespaces=NS),
                description=desc,
                thumbnail=thumb_el.get("url") if thumb_el is not None else
                          f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                views=int(stats_el.get("views")) if stats_el is not None and stats_el.get("views") else None,
                chapters=parse_chapters(desc),
            )
        )
    return videos


def filter_recent(videos: list[VideoMeta], lookback_days: int) -> list[VideoMeta]:
    if lookback_days <= 0:
        return videos
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    keep = []
    for v in videos:
        try:
            ts = datetime.fromisoformat(v.published.replace("Z", "+00:00"))
        except ValueError:
            keep.append(v)
            continue
        if ts >= cutoff:
            keep.append(v)
    return keep


def list_channel_videos_deep(channel_id: str, limit: int = 200) -> list[str]:
    """Full-history video ids via yt-dlp, for backfills beyond the 15-item feed.

    Optional dependency -- install yt-dlp only if you want deep history:
        pip install yt-dlp
    """
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Deep backfill needs yt-dlp. Install it with: pip install yt-dlp"
        ) from exc

    url = f"https://www.youtube.com/channel/{channel_id}/videos"
    opts = {"quiet": True, "extract_flat": "in_playlist", "playlistend": limit}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e["id"] for e in (info or {}).get("entries", []) if e.get("id")]
