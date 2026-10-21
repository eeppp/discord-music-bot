from __future__ import annotations

import datetime as dt
import os

import aiosqlite


class FeatureStore:
    """SQLite storage for voice time, coins, weekly reports, and private playlists."""

    def __init__(self, path: str, coins_per_minute: int = 1) -> None:
        self.path = path
        self.coins_per_minute = max(0, coins_per_minute)

    async def initialize(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_sessions(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT NOT NULL,
                    seconds INTEGER NOT NULL CHECK(seconds >= 0)
                );
                CREATE INDEX IF NOT EXISTS idx_voice_week ON voice_sessions(guild_id, ended_at);
                CREATE INDEX IF NOT EXISTS idx_playlist_song_order ON playlist_songs(playlist_id, id);
                CREATE TABLE IF NOT EXISTS weekly_reports(
                    guild_id INTEGER NOT NULL,
                    week_key TEXT NOT NULL,
                    sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(guild_id, week_key)
                );
                """
            )
            try:
                await db.execute("ALTER TABLE users ADD COLUMN voice_seconds INTEGER NOT NULL DEFAULT 0")
            except aiosqlite.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            await db.commit()

    async def record_voice(self, guild_id: int, user_id: int, started_at: dt.datetime,
                           ended_at: dt.datetime, seconds: int) -> int:
        seconds = max(0, int(seconds))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO voice_sessions(guild_id,user_id,started_at,ended_at,seconds) VALUES(?,?,?,?,?)",
                (guild_id, user_id, started_at.astimezone(dt.timezone.utc).isoformat(),
                 ended_at.astimezone(dt.timezone.utc).isoformat(), seconds),
            )
            await db.execute("INSERT OR IGNORE INTO users(user_id,points,voice_seconds) VALUES(?,0,0)", (user_id,))
            cur = await db.execute("SELECT voice_seconds FROM users WHERE user_id=?", (user_id,))
            old_seconds = int((await cur.fetchone())[0])
            earned = ((old_seconds + seconds) // 60 - old_seconds // 60) * self.coins_per_minute
            await db.execute("UPDATE users SET points=points+?,voice_seconds=voice_seconds+? WHERE user_id=?", (earned, seconds, user_id))
            await db.commit()
        return earned

    async def total_voice_seconds(self, guild_id: int, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COALESCE(SUM(seconds),0) FROM voice_sessions WHERE guild_id=? AND user_id=?",
                (guild_id, user_id),
            )
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def balance(self, user_id: int) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT points FROM users WHERE user_id=?", (user_id,))
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_coins(self, user_id: int, amount: int) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO users(user_id,points) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET points=points+excluded.points",
                (user_id, amount),
            )
            await db.commit()

    async def weekly_voice(self, guild_id: int, since: dt.datetime) -> list[tuple[int, int]]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT user_id,SUM(seconds) FROM voice_sessions WHERE guild_id=? AND ended_at>=? GROUP BY user_id ORDER BY SUM(seconds) DESC LIMIT 10",
                (guild_id, since.astimezone(dt.timezone.utc).isoformat()),
            )
            return [(int(uid), int(seconds)) for uid, seconds in await cur.fetchall()]

    async def weekly_total(self, guild_id: int, since: dt.datetime) -> int:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT COALESCE(SUM(seconds),0) FROM voice_sessions WHERE guild_id=? AND ended_at>=?",
                (guild_id, since.astimezone(dt.timezone.utc).isoformat()),
            )
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    async def popular_songs(self, guild_id: int, since: dt.datetime) -> list[tuple[str, int]]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT title,COUNT(*) AS plays FROM history WHERE guild_id=? AND played_at>=? GROUP BY title ORDER BY plays DESC LIMIT 5",
                (guild_id, since.astimezone(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
            )
            return [(str(title), int(plays)) for title, plays in await cur.fetchall()]

    async def has_weekly_report(self, guild_id: int, week_key: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT 1 FROM weekly_reports WHERE guild_id=? AND week_key=?", (guild_id, week_key))
            return await cur.fetchone() is not None

    async def mark_weekly_report(self, guild_id: int, week_key: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute("INSERT OR IGNORE INTO weekly_reports(guild_id,week_key) VALUES(?,?)", (guild_id, week_key))
            await db.commit()

    async def random_song(self, guild_id: int) -> tuple[str, str] | None:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT title,url FROM history WHERE guild_id=? ORDER BY RANDOM() LIMIT 1", (guild_id,))
            row = await cur.fetchone()
        return (str(row[0]), str(row[1])) if row else None

    async def playlist_create(self, owner_id: int, name: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT 1 FROM playlists WHERE owner_id=? AND name=? COLLATE NOCASE", (owner_id, name.strip()))
            if await cur.fetchone():
                return False
            await db.execute("INSERT INTO playlists(owner_id,name) VALUES(?,?)", (owner_id, name.strip()))
            await db.commit()
        return True

    async def playlist_add(self, owner_id: int, name: str, title: str, url: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT id FROM playlists WHERE owner_id=? AND name=? COLLATE NOCASE", (owner_id, name))
            row = await cur.fetchone()
            if not row:
                return False
            await db.execute("INSERT INTO playlist_songs(playlist_id,title,url) VALUES(?,?,?)", (row[0], title, url))
            await db.commit()
        return True

    async def playlist_get(self, owner_id: int, name: str) -> list[tuple[str, str]] | None:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute(
                "SELECT s.title,s.url FROM playlists p JOIN playlist_songs s ON s.playlist_id=p.id WHERE p.owner_id=? AND p.name=? COLLATE NOCASE ORDER BY s.id",
                (owner_id, name),
            )
            rows = await cur.fetchall()
            if not rows:
                exists = await db.execute("SELECT 1 FROM playlists WHERE owner_id=? AND name=? COLLATE NOCASE", (owner_id, name))
                if not await exists.fetchone():
                    return None
        return [(str(title), str(url)) for title, url in rows]

    async def playlist_names(self, owner_id: int) -> list[str]:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT name FROM playlists WHERE owner_id=? ORDER BY name COLLATE NOCASE", (owner_id,))
            return [str(row[0]) for row in await cur.fetchall()]

    async def playlist_delete(self, owner_id: int, name: str) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cur = await db.execute("SELECT id FROM playlists WHERE owner_id=? AND name=? COLLATE NOCASE", (owner_id, name))
            row = await cur.fetchone()
            if not row:
                return False
            await db.execute("DELETE FROM playlist_songs WHERE playlist_id=?", (row[0],))
            await db.execute("DELETE FROM playlists WHERE id=?", (row[0],))
            await db.commit()
        return True
