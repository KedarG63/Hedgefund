"""Command line entry point.

    python run.py pull                 # discover + transcribe new videos
    python run.py analyze              # extract signals from anything pending
    python run.py build                # render the dashboard
    python run.py run                  # pull + analyze + build (the cron target)
    python run.py backfill --per 5     # seed history, N videos per channel
    python run.py search "memory"      # query the knowledge base
    python run.py status               # what's in the box
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .analyze import Analyzer, strip_sponsor_blocks
from .config import ROOT, Config, load_dotenv
from .dashboard import render, render_json
from .feeds import fetch_channel_feed, filter_recent
from .store import Store
from .transcripts import (
    TranscriptUnavailable,
    duration_seconds,
    fetch_transcript,
    segments_to_text,
)


def _is_ip_block(exc: Exception) -> bool:
    """An IP block says nothing about the video -- only about this machine."""
    return any(k in str(exc) for k in ("IpBlocked", "RequestBlocked", "TooManyRequests"))


def cmd_pull(cfg: Config, store: Store, args) -> dict:
    stats = {"discovered": 0, "transcribed": 0, "skipped": 0, "failed": 0, "blocked": 0}
    limit = args.per or cfg.ingest.max_videos_per_run
    lookback = 0 if args.all else cfg.ingest.lookback_days

    for ch in cfg.channels:
        print(f"\n[{ch.name}]")
        try:
            videos = fetch_channel_feed(ch.feed_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! feed failed: {exc}")
            continue

        videos = filter_recent(videos, lookback)[:limit]
        print(f"  {len(videos)} videos in window")

        for v in videos:
            if store.is_analyzed(v.video_id) and not args.force:
                continue
            row = store.get_video(v.video_id)
            store.upsert_video(v)
            if row and row["transcript"] and not args.force:
                continue
            stats["discovered"] += 1

            time.sleep(cfg.ingest.request_delay_seconds)
            try:
                segs = fetch_transcript(v.video_id, cfg.ingest.transcript_languages)
            except TranscriptUnavailable as exc:
                # An IP block is about this machine, not this video. Marking the
                # rest of the backlog 'error' would be a lie that hides them from
                # every future run, so stop here and leave them 'discovered' to
                # be picked up once the block clears.
                if _is_ip_block(exc):
                    print(f"  ! IP blocked by YouTube -- stopping pull, {v.title[:40]} and the")
                    print("    rest stay queued as 'discovered'. Wait for the block to clear,")
                    print("    raise ingest.request_delay_seconds, or set YT_PROXY_HTTP.")
                    stats["blocked"] += 1
                    return stats
                print(f"  - {v.title[:58]:58s} no transcript")
                store.set_status(v.video_id, "error", str(exc))
                stats["failed"] += 1
                continue

            dur = duration_seconds(segs)
            if dur < cfg.ingest.min_duration_seconds:
                store.set_status(v.video_id, "skipped", f"too short ({dur}s)")
                stats["skipped"] += 1
                continue

            text = strip_sponsor_blocks(segments_to_text(segs))
            store.set_transcript(v.video_id, text, dur)
            stats["transcribed"] += 1
            print(f"  + {v.title[:58]:58s} {dur // 60:>3d}m  {len(text):>7,d} chars")

    return stats


def cmd_analyze(cfg: Config, store: Store, args) -> dict:
    analyzer = Analyzer(cfg.analyze.model, cfg.analyze.max_output_tokens, provider=cfg.analyze.provider)
    pending = store.pending("transcribed")
    if args.limit:
        pending = pending[: args.limit]
    stats = {"analyzed": 0, "items": 0, "failed": 0}

    for row in pending:
        ch = cfg.channel_by_id(row["channel_id"])
        print(f"\n> {row['title'][:70]}")
        meta = {
            "channel_name": row["channel_name"],
            "lens": ch.lens if ch else "",
            "title": row["title"],
            "published": row["published"] or "",
            "chapters": json.loads(row["chapters_json"] or "[]"),
        }
        try:
            summary, items = analyzer.analyze(
                row["transcript"],
                meta,
                chunk_chars=cfg.analyze.chunk_chars,
                overlap=cfg.analyze.chunk_overlap_chars,
                allowed_types=cfg.analyze.signal_types,
                min_confidence=cfg.analyze.min_confidence,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  ! failed: {exc}")
            store.set_status(row["video_id"], "error", str(exc))
            stats["failed"] += 1
            continue

        n = store.save_analysis(row["video_id"], summary, items)
        stats["analyzed"] += 1
        stats["items"] += n
        print(f"  = {summary['headline']}")
        print(f"    {n} signals kept")

    return stats


def cmd_build(cfg: Config, store: Store, args) -> dict:
    rows = store.dashboard_rows(cfg.dashboard.max_videos, cfg.analyze.min_confidence)
    out = args.out or cfg.dashboard.output_path

    path = Path(out)
    if not path.is_absolute():
        path = ROOT / out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows, cfg.dashboard, fragment=args.fragment, sources=cfg.channels), encoding="utf-8")
    print(f"dashboard -> {path}")

    if args.json:
        jpath = path.with_suffix(".json")
        jpath.write_text(render_json(rows), encoding="utf-8")
        print(f"json      -> {jpath}")
    return {"videos": len(rows), "path": str(path)}


def cmd_search(cfg: Config, store: Store, args) -> dict:
    hits = store.search(args.query, args.limit)
    if not hits:
        print("no matches")
        return {"hits": 0}
    for h in hits:
        stamp = f"{int(h['t_start'] or 0) // 60}:{int(h['t_start'] or 0) % 60:02d}"
        print(f"\n[{h['type']}] {h['headline']}")
        print(f"  {h['channel_name']} · {(h['published'] or '')[:10]} · {h['url']}&t={int(h['t_start'] or 0)}s ({stamp})")
        print(f"  {(h['body'] or '')[:200]}")
    return {"hits": len(hits)}


def cmd_status(cfg: Config, store: Store, args) -> dict:
    s = store.stats()
    print(f"videos    {s['videos']}")
    print(f"analyzed  {s['analyzed']}")
    print(f"signals   {s['items']}")
    for status in ("discovered", "transcribed", "error", "skipped"):
        rows = store.pending(status)
        if rows:
            print(f"\n{status} ({len(rows)}):")
            for r in rows[:10]:
                print(f"  {r['video_id']}  {r['title'][:60]}  {r['error'] or ''}")
    return s


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="yt-signal-engine")
    p.add_argument("--config", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("pull", help="discover new videos and fetch transcripts")
    sp.add_argument("--per", type=int, default=None, help="max videos per channel")
    sp.add_argument("--all", action="store_true", help="ignore the lookback window")
    sp.add_argument("--force", action="store_true", help="re-fetch transcripts already stored")

    sp = sub.add_parser("analyze", help="extract signals from transcribed videos")
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("build", help="render the dashboard")
    sp.add_argument("--out", default=None)
    sp.add_argument("--fragment", action="store_true", help="body-only HTML for publishing")
    sp.add_argument("--json", action="store_true", help="also write the raw JSON")

    sp = sub.add_parser("run", help="pull + analyze + build")
    sp.add_argument("--per", type=int, default=None)
    sp.add_argument("--all", action="store_true")
    sp.add_argument("--force", action="store_true")
    sp.add_argument("--limit", type=int, default=None)
    sp.add_argument("--out", default=None)
    sp.add_argument("--fragment", action="store_true")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("backfill", help="seed history from the feed")
    sp.add_argument("--per", type=int, default=10)
    sp.add_argument("--out", default=None)
    sp.add_argument("--fragment", action="store_true")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--limit", type=int, default=None)

    sp = sub.add_parser("search", help="query the knowledge base")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=25)

    sub.add_parser("status", help="show pipeline state")

    args = p.parse_args(argv)
    cfg = Config.load(args.config)
    store = Store(cfg.db_path)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    stats: dict = {}

    try:
        if args.cmd == "pull":
            stats = cmd_pull(cfg, store, args)
        elif args.cmd == "analyze":
            stats = cmd_analyze(cfg, store, args)
        elif args.cmd == "build":
            stats = cmd_build(cfg, store, args)
        elif args.cmd == "search":
            stats = cmd_search(cfg, store, args)
        elif args.cmd == "status":
            stats = cmd_status(cfg, store, args)
        elif args.cmd in ("run", "backfill"):
            if args.cmd == "backfill":
                args.all, args.force = True, False
            stats = {}
            stats.update(cmd_pull(cfg, store, args))
            stats.update(cmd_analyze(cfg, store, args))
            stats.update(cmd_build(cfg, store, args))
        store.log_run(started, stats)
        print(f"\ndone: {stats}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
