import asyncio
import os
import aiohttp
import discord
from discord.ext import commands
import imageio_ffmpeg
import yt_dlp

FFMPEG_EXECUTABLE = imageio_ffmpeg.get_ffmpeg_exe()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
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

        # 1. إذا كان رابط بث مباشر مباشر (MP3/Stream)
        if search_query.startswith("http") and (
            ".mp3" in search_query or "stream" in search_query
        ):
            return cls(
                discord.FFmpegPCMAudio(
                    search_query,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS,
                ),
                data={"title": "بث صوتي مباشر"},
            )

        # 2. جلب رابط الصوت لتجاوز حظر Railway عبر Piped API
        video_id = None
        if "youtu.be/" in search_query:
            video_id = search_query.split("youtu.be/")[1].split("?")[0]
        elif "watch?v=" in search_query:
            video_id = search_query.split("watch?v=")[1].split("&")[0]

        audio_url = None
        title = "مقطع صوتي"

        if video_id:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"https://pipedapi.kavin.rocks/streams/{video_id}"
                    ) as resp:
                        if resp.status == 200:
                            res_data = await resp.json()
                            title = res_data.get("title", "مقطع صوتي")
                            audio_streams = res_data.get("audioStreams", [])
                            if audio_streams:
                                audio_url = audio_streams[0].get("url")
            except Exception:
                pass

        # 3. إذا لم ينجح عبر API يرجع لـ yt-dlp كخيار ثانٍ
        if not audio_url:
            target = (
                search_query
                if search_query.startswith("http")
                : f"scsearch1:{search_query}"
            )
            data = await loop.run_in_executor(
                None, lambda: ytdl.extract_info(target, download=False)
            )

            if not data:
                raise Exception("تعذر جلب المقطع بسبب حظر السيرفر.")

            if "entries" in data:
                entries = [e for e in data["entries"] if e and e.get("url")]
                if not entries:
                    raise Exception("لم يتم العثور على نتائج.")
                data = entries[0]

            audio_url = data.get("url")
            title = data.get("title", "مقطع صوتي")

        if not audio_url:
            raise Exception("تعذر جلب رابط الصوت.")

        return cls(
            discord.FFmpegPCMAudio(
                audio_url, executable=FFMPEG_EXECUTABLE, **FFMPEG_OPTIONS
            ),
            data={"title": title},
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
            await ctx.send(f"⚠️ **حدث خطأ:** `{str(e)}`")


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
