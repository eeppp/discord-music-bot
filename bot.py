import asyncio
import os
import discord
from discord.ext import commands
import imageio_ffmpeg
import yt_dlp

FFMPEG_EXECUTABLE = imageio_ffmpeg.get_ffmpeg_exe()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# خيارات yt-dlp متقدمة لتجاوز حظر IPs في Railway
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
    },
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)
queues = {}


def check_queue(ctx, guild_id):
    if guild_id in queues and len(queues[guild_id]) > 0:
        next_player = queues[guild_id].pop(0)
        ctx.voice_client.play(
            next_player, after=lambda e: check_queue(ctx, guild_id)
        )
        asyncio.run_coroutine_threadsafe(
            ctx.send(f"🎵 **جاري تشغيل التالي:** {next_player.title}"), bot.loop
        )


class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "مقطع صوتي")

    @classmethod
    async def from_url(cls, search_query, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()

        # 1. إذا كان الرابط صوتياً مباشراً (MP3 / Stream) يتم تشغيله فوراً عبر FFmpeg
        if search_query.startswith("http") and (
            ".mp3" in search_query
            or ".m3u8" in search_query
            or "stream" in search_query
        ):
            data = {"url": search_query, "title": "بث صوتي مباشر"}
            return cls(
                discord.FFmpegPCMAudio(
                    search_query,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS,
                ),
                data=data,
            )

        # 2. تجهيز هدف البحث للروابط العادية أو الكلمات
        if search_query.startswith("http"):
            target = search_query
        else:
            target = f"scsearch1:{search_query}"

        # 3. محاولة استخراج المقطع بواسطة yt-dlp
        data = await loop.run_in_executor(
            None, lambda: ytdl.extract_info(target, download=False)
        )

        if not data:
            # محاولة احتياطية ثانية مع SoundCloud Search
            if not search_query.startswith("http"):
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(
                        f"scsearch:{search_query}", download=False
                    ),
                )

        if not data:
            raise RuntimeError(
                "تعذر استخراج الصوت من هذا المصدر بسبب حظر IP سيرفر الاستضافة."
            )

        if "entries" in data:
            valid_entries = [e for e in data["entries"] if e and e.get("url")]
            if not valid_entries:
                raise RuntimeError("لم يتم العثور على نتائج لطلب البحث.")
            data = valid_entries[0]

        filename = data.get("url")
        if not filename:
            raise RuntimeError("تعذر الحصول على رابط البث الصوتي.")

        return cls(
            discord.FFmpegPCMAudio(
                filename, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS
            ),
            data=data,
        )


@bot.event
async def on_ready():
    print(f"✅ تم تسجيل الدخول بنجاح باسم: {bot.user.name}")


@bot.command(name="check", aliases=["فحص"])
async def check(ctx):
    await ctx.send("✅ البوت يعمل بنجاح!")


@bot.command(name="play", aliases=["p", "شغل", "تشغيل"])
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        await ctx.send("❌ يجب أن تكون في روم صوتي أولاً!")
        return

    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)

    async with ctx.typing():
        try:
            player = await YTDLSource.from_url(
                search, loop=bot.loop, stream=True
            )
            guild_id = ctx.guild.id

            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                if guild_id not in queues:
                    queues[guild_id] = []
                queues[guild_id].append(player)
                await ctx.send(f"📋 **تمت الإضافة إلى القائمة:** {player.title}")
            else:
                ctx.voice_client.play(
                    player, after=lambda e: check_queue(ctx, guild_id)
                )
                await ctx.send(f"🎵 **جاري التشغيل:** {player.title}")

        except Exception as e:
            await ctx.send(f"⚠️ **تنبيه:** `{str(e)}`")


@bot.command(name="skip", aliases=["تخطي", "سكب"])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ تم تخطي المقطع.")


@bot.command(name="stop", aliases=["ايقاف", "إيقاف", "سكت"])
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues:
        queues[guild_id].clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ تم إيقاف التشغيل ومغادرة الروم.")


TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
