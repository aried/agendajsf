"""SQLite storage. One file keeps every snapshot so trends can be compared over time."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .textutil import norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
    command TEXT NOT NULL, summary TEXT);
CREATE TABLE IF NOT EXISTS source_log(
    run_id INTEGER NOT NULL, at TEXT NOT NULL, source TEXT NOT NULL, market TEXT,
    ok INTEGER NOT NULL, items INTEGER NOT NULL, error TEXT);
CREATE TABLE IF NOT EXISTS signals(
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, collected_at TEXT NOT NULL,
    source TEXT NOT NULL, market TEXT, term TEXT NOT NULL, term_norm TEXT NOT NULL,
    rank INTEGER, value REAL, label TEXT, url TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS signals_term ON signals(term_norm, collected_at);
CREATE INDEX IF NOT EXISTS signals_run ON signals(run_id, source);
CREATE TABLE IF NOT EXISTS checks(
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, collected_at TEXT NOT NULL,
    market TEXT NOT NULL, term TEXT NOT NULL, term_norm TEXT NOT NULL,
    origin TEXT, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS checks_run ON checks(run_id);
CREATE TABLE IF NOT EXISTS yt_measures(
    id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, collected_at TEXT NOT NULL,
    market TEXT NOT NULL, kind TEXT NOT NULL, query TEXT, metrics TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS yt_measures_run ON yt_measures(run_id);
CREATE TABLE IF NOT EXISTS yt_videos(
    video_id TEXT NOT NULL, collected_at TEXT NOT NULL, measure_id INTEGER,
    title TEXT, channel_id TEXT, published_at TEXT, duration_s INTEGER,
    views INTEGER, likes INTEGER, comments INTEGER,
    PRIMARY KEY(video_id, collected_at));
CREATE INDEX IF NOT EXISTS yt_videos_measure ON yt_videos(measure_id);
CREATE TABLE IF NOT EXISTS yt_channels(
    channel_id TEXT NOT NULL, collected_at TEXT NOT NULL, title TEXT,
    created_at TEXT, subs INTEGER, views INTEGER, videos INTEGER, country TEXT,
    PRIMARY KEY(channel_id, collected_at));
CREATE TABLE IF NOT EXISTS quota(day TEXT PRIMARY KEY, units INTEGER NOT NULL);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.commit()
        self.db.close()

    # runs -----------------------------------------------------------------
    def start_run(self, command: str) -> int:
        cursor = self.db.execute(
            "INSERT INTO runs(started_at, command) VALUES(?, ?)", (utcnow(), command)
        )
        self.db.commit()
        return cursor.lastrowid

    def finish_run(self, run_id: int, summary: dict) -> None:
        self.db.execute(
            "UPDATE runs SET finished_at = ?, summary = ? WHERE id = ?",
            (utcnow(), json.dumps(summary, ensure_ascii=False), run_id),
        )
        self.db.commit()

    def latest_run(self) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def log_source(self, run_id, source, market, ok, items, error=None) -> None:
        self.db.execute(
            "INSERT INTO source_log VALUES(?, ?, ?, ?, ?, ?, ?)",
            (run_id, utcnow(), source, market, int(ok), items, error),
        )
        self.db.commit()

    # signals and checks -----------------------------------------------------
    def add_signals(self, run_id: int, signals: list) -> None:
        now = utcnow()
        self.db.executemany(
            "INSERT INTO signals(run_id, collected_at, source, market, term, term_norm,"
            " rank, value, label, url, extra) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (run_id, now, s.source, s.market, s.term, norm(s.term), s.rank, s.value,
                 s.label, s.url, json.dumps(s.extra, ensure_ascii=False))
                for s in signals
            ],
        )
        self.db.commit()

    def days_seen(self, term: str, source: str | None = None) -> int:
        """Distinct days on which a term appeared in the stored signals."""
        sql = "SELECT COUNT(DISTINCT substr(collected_at, 1, 10)) FROM signals WHERE term_norm = ?"
        args: list = [norm(term)]
        if source:
            sql += " AND source = ?"
            args.append(source)
        return self.db.execute(sql, args).fetchone()[0]

    def add_check(self, run_id, market, term, origin, data: dict) -> None:
        self.db.execute(
            "INSERT INTO checks(run_id, collected_at, market, term, term_norm, origin, data)"
            " VALUES(?, ?, ?, ?, ?, ?, ?)",
            (run_id, utcnow(), market, term, norm(term), origin,
             json.dumps(data, ensure_ascii=False)),
        )
        self.db.commit()

    # YouTube -----------------------------------------------------------------
    def add_measure(self, run_id, market, kind, query, metrics, videos, channels) -> int:
        now = utcnow()
        cursor = self.db.execute(
            "INSERT INTO yt_measures(run_id, collected_at, market, kind, query, metrics)"
            " VALUES(?, ?, ?, ?, ?, ?)",
            (run_id, now, market, kind, query, json.dumps(metrics, ensure_ascii=False)),
        )
        measure_id = cursor.lastrowid
        self.db.executemany(
            "INSERT OR REPLACE INTO yt_videos VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (v["video_id"], now, measure_id, v["title"], v["channel_id"],
                 v["published_at"], v["duration_s"], v["views"], v["likes"], v["comments"])
                for v in videos
            ],
        )
        self.db.executemany(
            "INSERT OR REPLACE INTO yt_channels VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (c["channel_id"], now, c["title"], c["created_at"], c["subs"], c["views"],
                 c["videos"], c["country"])
                for c in channels
            ],
        )
        self.db.commit()
        return measure_id

    def quota_used(self, day: str) -> int:
        row = self.db.execute("SELECT units FROM quota WHERE day = ?", (day,)).fetchone()
        return row[0] if row else 0

    def add_quota(self, day: str, units: int) -> None:
        self.db.execute(
            "INSERT INTO quota(day, units) VALUES(?, ?)"
            " ON CONFLICT(day) DO UPDATE SET units = units + excluded.units",
            (day, units),
        )
        self.db.commit()

    def purge_youtube(self, retention_days: int) -> int:
        """Delete YouTube API data older than the retention window.

        The YouTube API Services policies ask that stored API data be refreshed
        or deleted after 30 days.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        removed = 0
        for table in ("yt_videos", "yt_channels", "yt_measures"):
            removed += self.db.execute(
                f"DELETE FROM {table} WHERE collected_at < ?", (cutoff,)
            ).rowcount
        self.db.commit()
        return removed
