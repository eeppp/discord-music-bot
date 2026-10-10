from __future__ import annotations

import os
from typing import Any

import aiosqlite

from config import Config


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    async def initialize(self) -> None:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS guild_settings(
                    guild_id INTEGER PRIMARY KEY,
                    volume INTEGER NOT NULL DEFAULT 80,
                    bass INTEGER NOT NULL DEFAULT 0,
                    autoplay INTEGER NOT NULL DEFAULT 1,
                    theme TEXT NOT NULL DEFAULT 'Neon',
                    dj_role TEXT NOT NULL DEFAULT 'DJ',
                    repeat INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            # Upgrade databases created by earlier bot versions.
            try:
                await db.execute(
                    "ALTER TABLE guild_settings ADD COLUMN repeat INTEGER NOT NULL DEFAULT 0"
                )
            except aiosqlite.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS favorites(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS history(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS playlists(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS playlist_songs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playlist_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users(
                    user_id INTEGER PRIMARY KEY,
                    points INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            await db.commit()

    async def get_settings(self, guild_id: int) -> dict[str, Any]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT volume, bass, autoplay, theme, dj_role, repeat "
                "FROM guild_settings WHERE guild_id = ?",
                (guild_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return {
                "volume": Config.DEFAULT_VOLUME,
                "bass": 0,
                "autoplay": 1,
                "theme": "Neon",
                "dj_role": "DJ",
                "repeat": 0,
            }
        return dict(row)

    async def save_settings(self, guild_id: int, **values: Any) -> None:
        allowed = {"volume", "bass", "autoplay", "theme", "dj_role", "repeat"}
        fields = {key: value for key, value in values.items() if key in allowed}
        if not fields:
            return

        columns = list(fields)
        placeholders = ", ".join("?" for _ in columns)
        assignments = ", ".join(f"{column}=excluded.{column}" for column in columns)
        sql = (
            f"INSERT INTO guild_settings (guild_id, {', '.join(columns)}) "
            f"VALUES (?, {placeholders}) "
            f"ON CONFLICT(guild_id) DO UPDATE SET {assignments}"
        )
        params = (guild_id, *(fields[column] for column in columns))
        async with aiosqlite.connect(self.path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def add_history(self, guild_id: int, title: str, url: str) -> None:
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO history (guild_id, title, url) VALUES (?, ?, ?)",
                (guild_id, title, url),
            )
            await db.commit()
