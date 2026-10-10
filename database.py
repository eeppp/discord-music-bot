import os
import aiosqlite


class Database:

    def __init__(self, path: str):
        self.path = path

    async def initialize(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

        async with aiosqlite.connect(self.path) as db:

            await db.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings(
                guild_id INTEGER PRIMARY KEY,
                volume INTEGER DEFAULT 80,
                bass INTEGER DEFAULT 0,
                autoplay INTEGER DEFAULT 1,
                theme TEXT DEFAULT 'Neon',
                dj_role TEXT DEFAULT 'DJ'
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                title TEXT,
                url TEXT
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                title TEXT,
                url TEXT,
                played_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS playlists(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER,
                name TEXT
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS playlist_songs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER,
                title TEXT,
                url TEXT
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                points INTEGER DEFAULT 0
            )
            """)

            await db.execute("""
            CREATE TABLE IF NOT EXISTS logs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)

            await db.commit()

        print("✅ Database initialized.")
