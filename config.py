import os

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


class Config:
    TOKEN = os.getenv("DISCORD_TOKEN")
    PREFIX = os.getenv("BOT_PREFIX", "!")
    MUSIC_COMMAND_CHANNEL_ID = os.getenv("MUSIC_COMMAND_CHANNEL_ID", "").strip()
    EMBED_COLOR = int(os.getenv("EMBED_COLOR", "0x7C3AED"), 0)
    DEFAULT_VOLUME = _env_int("DEFAULT_VOLUME", 80, 0, 200)
    MAX_QUEUE = _env_int("MAX_QUEUE", 100, 1, 500)
    SEARCH_RESULTS = 5
    DELETE_DELAY = _env_int("DELETE_DELAY", 20, 5, 300)
    DATABASE = os.getenv("DATABASE_PATH", "database/music.db")
    LOG_FILE = os.getenv("LOG_FILE", "logs/music.log")
    AUTO_PLAY = os.getenv("AUTO_PLAY", "true").lower() in {"1", "true", "yes", "on"}
    YOUTUBE_COOKIES_B64 = os.getenv("YOUTUBE_COOKIES_B64", "").strip()
    VOICE_COINS_PER_MINUTE = _env_int("VOICE_COINS_PER_MINUTE", 1, 0, 1000)
    TRIVIA_REWARD = _env_int("TRIVIA_REWARD", 25, 0, 1000000)
    WEEKLY_REPORT_CHANNEL_ID = os.getenv("WEEKLY_REPORT_CHANNEL_ID", "").strip()
    UPDATE_ANNOUNCEMENT_CHANNEL_ID = os.getenv("UPDATE_ANNOUNCEMENT_CHANNEL_ID", "").strip()
    DJ_ROLE = os.getenv("DJ_ROLE", "DJ")
    DEFAULT_THEME = "Neon"
    VERSION = "2.2.0"
