import asyncio
import os
import discord
from discord.ext import commands
import yt_dlp

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# استخدام البحث عبر SoundCloud للتغلب على حظر خوادم Railway وضمان الاستقرار
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "scsearch",
    "source_address": "0.0.0.0",
}

# خيارات FFmpeg المحدثة لضمان استمرار البث ومنع الانقطاع
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "مقطع صوتي")

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()

        # استخراج معلومات الصوت بدون تحميل
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(url, download=not stream)
        )

        if "entries" in data and len(data["entries"]) > 0:
            data = data["entries"][0]

        filename = data.get("url")
        if not filename:
            raise Exception("لم يتم العثور على رابط مباشر للبث الصوتي.")

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

    # الاتصال بالروم الصوتي إذا لم يكن البوت متصلاً
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)

    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(
                search, loop=bot.loop, stream=True
            )

            if ctx.voice_client.is_playing():
                ctx.voice_client.stop()

            def after_playing(error):
                if error:
                    print(f"خطأ أثناء تشغيل الصوت: {error}")

            ctx.voice_client.play(player, after=after_playing)
            await ctx.send(f"🎵 **جاري التشغيل:** {player.title}")

        except Exception as e:
            print(f"Error: {e}")
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


TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
