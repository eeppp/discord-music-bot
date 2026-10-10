import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    TOKEN = os.getenv("DISCORD_TOKEN")

    PREFIX = "!"

    EMBED_COLOR = 0x7C3AED

    DEFAULT_VOLUME = 0.80

    MAX_QUEUE = 100

    SEARCH_RESULTS = 5

    DELETE_DELAY = 20

    DATABASE = "database/music.db"

    LOG_FILE = "logs/music.log"

    AUTO_PLAY = True

    DJ_ROLE = "DJ"

    DEFAULT_THEME = "Neon"

    VERSION = "2.0.0"
