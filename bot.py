import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import Config
from database import Database
from player import player_manager

# ==========================
# تحميل متغيرات البيئة
# ==========================

load_dotenv()

# ==========================
# إنشاء المجلدات
# ==========================

os.makedirs("logs", exist_ok=True)
os.makedirs("database", exist_ok=True)

# ==========================
# نظام تسجيل الأخطاء
# ==========================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(Config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("MusicS3YD")

# ==========================
# إعدادات Discord
# ==========================

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=Config.PREFIX,
    intents=intents,
    help_command=None
)

# ==========================
# قاعدة البيانات
# ==========================

database = Database(Config.DATABASE)

# ==========================
# الأحداث
# ==========================

@bot.event
async def on_ready():

    await database.initialize()

    logger.info(f"Logged in as {bot.user}")

    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name="!شغل"
    )

    await bot.change_presence(activity=activity)

    print("=" * 50)
    print("Music S3YD V2")
    print(f"User : {bot.user}")
    print(f"Guilds : {len(bot.guilds)}")
    print("=" * 50)

# ==========================
# أوامر تجريبية
# ==========================

@bot.command(name="ping")
async def ping(ctx):
    await ctx.send(f"🏓 {round(bot.latency*1000)} ms")


@bot.command(name="check", aliases=["فحص"])
async def check(ctx):
    await ctx.send("✅ Music S3YD V2 يعمل بنجاح")


# ==========================
# التشغيل
# ==========================

if __name__ == "__main__":

    if not Config.TOKEN:
        raise RuntimeError("DISCORD_TOKEN غير موجود.")

    bot.run(Config.TOKEN)
