"""Transcript acquisition.

Primary path: youtube-transcript-api (>=1.0). It reads the caption tracks
YouTube already serves to the player -- no download, no audio, no API key.

Two things it needs to work well:

1. A residential IP. YouTube rate-limits datacenter ranges hard, so this runs
   happily on your laptop and badly on a cheap VPS. If you must run it on a
   VPS, set YT_PROXY_HTTP / YT_PROXY_HTTPS in .env.
2. The video to actually have captions. Auto-generated captions count and are
   present on essentially every video from these two channels.

Fallback path: `whisper_fallback()` transcribes the audio locally when there is
no caption track at all. Off by default -- it needs yt-dlp + faster-whisper and
turns a 2-second fetch into a multi-minute one.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


@dataclass
class Segment:
    start: float      # seconds from video start
    duration: float
    text: str

    @property
    def stamp(self) -> str:
        m, s = divmod(int(self.start), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class TranscriptUnavailable(Exception):
    pass


def _proxy_config():
    http = os.environ.get("YT_PROXY_HTTP")
    https = os.environ.get("YT_PROXY_HTTPS")
    if not (http or https):
        return None
    from youtube_transcript_api.proxies import GenericProxyConfig

    return GenericProxyConfig(http_url=http, https_url=https)


def fetch_transcript(
    video_id: str,
    languages: list[str] | None = None,
    retries: int = 3,
    backoff: float = 2.0,
) -> list[Segment]:
    """Return timestamped segments, newest API surface with a legacy fallback."""
    from youtube_transcript_api import YouTubeTranscriptApi

    languages = languages or ["en"]
    last_err: Exception | None = None

    for attempt in range(retries):
        try:
            api = YouTubeTranscriptApi(proxy_config=_proxy_config())
            fetched = api.fetch(video_id, languages=languages)
            return [
                Segment(start=float(s.start), duration=float(s.duration), text=s.text)
                for s in fetched.snippets
                if s.text and s.text.strip()
            ]
        except AttributeError:
            # youtube-transcript-api < 1.0 exposed a classmethod instead.
            raw = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)  # type: ignore[attr-defined]
            return [
                Segment(start=float(r["start"]), duration=float(r.get("duration", 0)), text=r["text"])
                for r in raw
                if r.get("text", "").strip()
            ]
        except Exception as exc:  # noqa: BLE001 - library raises many types
            last_err = exc
            name = type(exc).__name__
            # Hard failures: retrying will not help. IpBlocked belongs here even
            # though it is transient -- retrying while throttled adds requests to
            # the window that caused the throttle. The caller stops the run.
            if name in {"TranscriptsDisabled", "NoTranscriptFound", "VideoUnavailable",
                        "NotTranslatable", "AgeRestricted",
                        "IpBlocked", "RequestBlocked", "TooManyRequests"}:
                break
            time.sleep(backoff * (attempt + 1))

    raise TranscriptUnavailable(f"{video_id}: {type(last_err).__name__}: {last_err}")


def segments_to_text(segments: list[Segment], with_stamps: bool = True) -> str:
    """Flatten to prose, keeping periodic timestamp anchors.

    The anchors are what let the extraction model cite a moment instead of a
    vibe, so every dashboard card can deep-link into the video. One anchor
    every ~30s is enough for the model to locate itself without drowning the
    text in numbers.
    """
    if not with_stamps:
        return " ".join(s.text.replace("\n", " ") for s in segments)

    out: list[str] = []
    next_anchor = 0.0
    for seg in segments:
        if seg.start >= next_anchor:
            out.append(f"\n[{seg.stamp}] ")
            next_anchor = seg.start + 30
        out.append(seg.text.replace("\n", " ") + " ")
    return "".join(out).strip()


def duration_seconds(segments: list[Segment]) -> int:
    if not segments:
        return 0
    last = segments[-1]
    return int(last.start + last.duration)


def whisper_fallback(video_id: str, model_size: str = "base.en") -> list[Segment]:
    """Local transcription for videos with no caption track.

    Needs: pip install yt-dlp faster-whisper
    """
    import tempfile
    from pathlib import Path

    from faster_whisper import WhisperModel  # type: ignore
    from yt_dlp import YoutubeDL  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / "audio.%(ext)s")
        opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": out,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3"}],
        }
        with YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
        audio = next(Path(tmp).glob("audio.*"))
        model = WhisperModel(model_size, compute_type="int8")
        chunks, _ = model.transcribe(str(audio), vad_filter=True)
        return [Segment(start=c.start, duration=c.end - c.start, text=c.text.strip()) for c in chunks]
