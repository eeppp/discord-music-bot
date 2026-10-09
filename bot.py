import asyncio
import os
import discord
from discord.ext import commands
import yt_dlp

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# إعدادات yt-dlp محدثة لتفادي انقطاع البث واستخراج العنوان الصحيح
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    },
}

# خيارات FFmpeg لضمان استقرار البث وعدم إغلاقه فوراً
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn -loglevel error",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "مقطع صوتي")
        self.url = data.get("url")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        # استخدام process=True لضمان جلب العنوان المباشر للمقطع
        data = await loop.run_in_executor(
            None,
            lambda: ytdl.extract_info(url, download=not stream, process=True),
        )

        if "entries" in data:
            data = data["entries"][0]

        filename = data["url"] if stream else ytdl.prepare_filename(data)
        return cls(
            discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data
        )


@bot.event
async def on_ready():
    print(f"تم تسجيل الدخول بنجاح باسم: {bot.user.name}")


@bot.command(name="play", aliases=["p", "تشغيل", "شغل"])
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        await ctx.send("❌ يجب أن تكون في روم صوتي أولاً!")
        return

    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()

    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(
                search, loop=bot.loop, stream=True
            )

            def after_playing(error):
                if error:
                    print(f"خطأ في التشغيل: {error}")

            ctx.voice_client.play(player, after=after_playing)
            await ctx.send(f"🎵 **شغال الحين:** {player.title}")

        except Exception as e:
            await ctx.send(
                f"⚠️ **حدث خطأ أثناء البحث أو التشغيل:**\n`{str(e)}`"
            )


@bot.command(name="stop", aliases=["ايقاف", "إيقاف", "سكت"])
async def stop(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ تم الإيقاف ومغادرة الروم الصوتي.")
    else:
        await ctx.send("❌ البوت ليس متصلاً بأي روم صوتي.")


TOKEN = os.getenv("MTU1Nzg0MDQzNTEzOTk3MzI0MQ.GOccIt.-UTJi04rG6UYZ1M8S2-56-PFmNxVlQiwcMzKkk")
if TOKEN:
    bot.run(TOKEN)
