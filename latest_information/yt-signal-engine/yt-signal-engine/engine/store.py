"""SQLite knowledge base.

One file, no server. The point of persisting rather than regenerating is that
signals compound: once six months of episodes are in here you can ask "every
time this channel talked about memory pricing, what did they say and when" and
get an answer without re-running a single LLM call.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    video_id        TEXT PRIMARY KEY,
    channel_id      TEXT NOT NULL,
    channel_name    TEXT,
    title           TEXT,
    published       TEXT,
    url             TEXT,
    thumbnail       TEXT,
    views           INTEGER,
    duration_sec    INTEGER,
    description     TEXT,
    chapters_json   TEXT,
    transcript      TEXT,
    status          TEXT DEFAULT 'discovered',  -- discovered|transcribed|analyzed|skipped|error
    error           TEXT,
    discovered_at   TEXT,
    analyzed_at     TEXT
);

CREATE TABLE IF NOT EXISTS summaries (
    video_id        TEXT PRIMARY KEY REFERENCES videos(video_id) ON DELETE CASCADE,
    headline        TEXT,
    summary         TEXT,
    bullets_json    TEXT,
    so_what         TEXT,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id            TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    type                TEXT NOT NULL,    -- investable_idea|macro_structure|research_thread
    headline            TEXT NOT NULL,
    body                TEXT,
    quote               TEXT,
    t_start             INTEGER,
    confidence          REAL,
    stance              TEXT,             -- bullish|bearish|neutral|contested
    entities_json       TEXT,
    research_question   TEXT,
    era                 TEXT,             -- historical_precedent: e.g. "1998" or "2007-2009"
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_items_video ON items(video_id);
CREATE INDEX IF NOT EXISTS idx_items_type  ON items(type);
CREATE INDEX IF NOT EXISTS idx_videos_pub  ON videos(published DESC);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    stats_json  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Additive column migrations for databases created before this column existed."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(items)")}
        if "era" not in cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN era TEXT")

    # -- writes ------------------------------------------------------------

    def upsert_video(self, meta, duration_sec: int | None = None) -> bool:
        """Insert if new. Returns True when this video had not been seen."""
        cur = self.conn.execute("SELECT 1 FROM videos WHERE video_id = ?", (meta.video_id,))
        is_new = cur.fetchone() is None
        self.conn.execute(
            """
            INSERT INTO videos (video_id, channel_id, channel_name, title, published, url,
                                thumbnail, views, duration_sec, description, chapters_json,
                                status, discovered_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'discovered',?)
            ON CONFLICT(video_id) DO UPDATE SET
                views = excluded.views,
                title = excluded.title
            """,
            (
                meta.video_id, meta.channel_id, meta.channel_name, meta.title, meta.published,
                meta.url, meta.thumbnail, meta.views, duration_sec, meta.description,
                json.dumps(meta.chapters), _now(),
            ),
        )
        self.conn.commit()
        return is_new

    def set_transcript(self, video_id: str, text: str, duration_sec: int) -> None:
        self.conn.execute(
            "UPDATE videos SET transcript=?, duration_sec=?, status='transcribed', error=NULL "
            "WHERE video_id=?",
            (text, duration_sec, video_id),
        )
        self.conn.commit()

    def set_status(self, video_id: str, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE videos SET status=?, error=? WHERE video_id=?", (status, error, video_id)
        )
        self.conn.commit()

    def save_analysis(self, video_id: str, summary: dict[str, Any], items: Iterable[dict[str, Any]]) -> int:
        self.conn.execute("DELETE FROM items WHERE video_id=?", (video_id,))
        self.conn.execute(
            """INSERT INTO summaries (video_id, headline, summary, bullets_json, so_what, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET
                 headline=excluded.headline, summary=excluded.summary,
                 bullets_json=excluded.bullets_json, so_what=excluded.so_what,
                 created_at=excluded.created_at""",
            (
                video_id, summary.get("headline"), summary.get("summary"),
                json.dumps(summary.get("bullets", [])), summary.get("so_what"), _now(),
            ),
        )
        n = 0
        for it in items:
            self.conn.execute(
                """INSERT INTO items (video_id, type, headline, body, quote, t_start,
                                      confidence, stance, entities_json, research_question, era, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    video_id, it.get("type"), it.get("headline"), it.get("body"), it.get("quote"),
                    it.get("t_start"), it.get("confidence"), it.get("stance"),
                    json.dumps(it.get("entities", [])), it.get("research_question"), it.get("era"), _now(),
                ),
            )
            n += 1
        self.conn.execute(
            "UPDATE videos SET status='analyzed', analyzed_at=? WHERE video_id=?", (_now(), video_id)
        )
        self.conn.commit()
        return n

    def log_run(self, started_at: str, stats: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO runs (started_at, finished_at, stats_json) VALUES (?,?,?)",
            (started_at, _now(), json.dumps(stats)),
        )
        self.conn.commit()

    # -- reads -------------------------------------------------------------

    def pending(self, status: str) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM videos WHERE status=? ORDER BY published DESC", (status,)))

    def get_video(self, video_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()

    def is_analyzed(self, video_id: str) -> bool:
        row = self.conn.execute("SELECT status FROM videos WHERE video_id=?", (video_id,)).fetchone()
        return bool(row and row["status"] == "analyzed")

    def dashboard_rows(self, limit: int = 40, min_confidence: float = 0.0) -> list[dict[str, Any]]:
        vids = self.conn.execute(
            """SELECT v.*, s.headline AS s_headline, s.summary AS s_summary,
                      s.bullets_json, s.so_what
               FROM videos v LEFT JOIN summaries s ON s.video_id = v.video_id
               WHERE v.status='analyzed'
               ORDER BY v.published DESC LIMIT ?""",
            (limit,),
        ).fetchall()

        out = []
        for v in vids:
            items = self.conn.execute(
                "SELECT * FROM items WHERE video_id=? AND COALESCE(confidence,1) >= ? "
                "ORDER BY confidence DESC, t_start ASC",
                (v["video_id"], min_confidence),
            ).fetchall()
            out.append(
                {
                    **dict(v),
                    "bullets": json.loads(v["bullets_json"] or "[]"),
                    "chapters": json.loads(v["chapters_json"] or "[]"),
                    "items": [
                        {**dict(i), "entities": json.loads(i["entities_json"] or "[]")} for i in items
                    ],
                }
            )
        return out

    def search(self, query: str, limit: int = 50) -> list[sqlite3.Row]:
        like = f"%{query}%"
        return list(
            self.conn.execute(
                """SELECT i.*, v.title, v.channel_name, v.published, v.url
                   FROM items i JOIN videos v ON v.video_id = i.video_id
                   WHERE i.headline LIKE ? OR i.body LIKE ? OR i.entities_json LIKE ?
                   ORDER BY v.published DESC LIMIT ?""",
                (like, like, like, limit),
            )
        )

    def stats(self) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "videos": q("SELECT COUNT(*) FROM videos"),
            "analyzed": q("SELECT COUNT(*) FROM videos WHERE status='analyzed'"),
            "items": q("SELECT COUNT(*) FROM items"),
        }

    def close(self) -> None:
        self.conn.close()
