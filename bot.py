import asyncio
import os
import traceback
from collections import deque

import discord
from discord.ext import commands
import imageio_ffmpeg
import yt_dlp

FFMPEG_EXECUTABLE = os.getenv("FFMPEG_EXECUTABLE", "ffmpeg")

if not discord.opus.is_loaded():
    try:
        discord.opus.load_opus("libopus.so.0")
    except OSError:
        try:
            discord.opus.load_opus("libopus-0.dll")
        except OSError:
            print("⚠️ لم يتم العثور على مكتبة Opus")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch1",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

# طابور لكل سيرفر
queues = {}


def get_queue(guild_id: int):
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def queue_summary(guild_id: int):
    queue = queues.get(guild_id, deque())
    if not queue:
        return "القائمة فارغة."

    lines = []
    for i, item in enumerate(queue, start=1):
        title = getattr(item, "title", "مقطع صوتي")
        lines.append(f"{i}. {title}")
    return "\n".join(lines)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get("title", "مقطع صوتي")
        self.webpage_url = data.get("webpage_url")

    @classmethod
    async def create_source(cls, search_query: str, *, loop=None):
        loop = loop or asyncio.get_running_loop()

        # رابط مباشر لصوت/بث
        if search_query.startswith(("http://", "https://")) and any(
            x in search_query.lower()
            for x in [".mp3", ".aac", ".ogg", ".wav", ".m4a", ".opus", ".flac", "stream"]
        ):
            return cls(
                discord.FFmpegPCMAudio(
                    search_query,
                    executable=FFMPEG_EXECUTABLE,
                    **FFMPEG_OPTIONS,
                ),
                data={
                    "title": "بث صوتي مباشر",
                    "webpage_url": search_query,
                },
            )

        target = search_query if search_query.startswith(("http://", "https://")) else f"ytsearch1:{search_query}"

        def extract_info():
            return ytdl.extract_info(target, download=False)

        try:
            data = await loop.run_in_executor(None, extract_info)
        except Exception as ex:
            raise Exception(f"خطأ في yt-dlp: {ex}")

        if not data:
            raise Exception("لم يتم العثور على نتائج.")

        if "entries" in data:
            entries = [e for e in data["entries"] if e]
            if not entries:
                raise Exception("لم يتم العثور على نتائج مطابقة.")
            data = entries[0]

        audio_url = data.get("url")

        # fallback لبعض المصادر
        if not audio_url:
            for fmt in data.get("formats", []):
                if fmt.get("acodec") != "none" and fmt.get("url"):
                    audio_url = fmt["url"]
                    break

        if not audio_url:
            raise Exception("فشل في الحصول على رابط الصوت.")

        return cls(
            discord.FFmpegPCMAudio(
                audio_url,
                executable=FFMPEG_EXECUTABLE,
                **FFMPEG_OPTIONS,
            ),
            data=data,
        )


async def play_next(ctx: commands.Context):
    guild_id = ctx.guild.id
    queue = queues.get(guild_id)

    if not queue or len(queue) == 0:
        return

    if not ctx.voice_client or not ctx.voice_client.is_connected():
        return

    next_player = queue.popleft()

    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    try:
        ctx.voice_client.play(next_player, after=after_playing)
        await ctx.send(f"🎵 **جاري تشغيل التالي:** {next_player.title}")
    except Exception as ex:
        await ctx.send(f"⚠️ خطأ أثناء تشغيل التالي: `{ex}`")
        await play_next(ctx)


async def disconnect_if_alone(guild: discord.Guild):
    voice_client = guild.voice_client
    if not voice_client or not voice_client.channel:
        return

    members = [m for m in voice_client.channel.members if not m.bot]
    if len(members) == 0:
        guild_id = guild.id
        if guild_id in queues:
            queues[guild_id].clear()

        await voice_client.disconnect()


@bot.event
async def on_ready():
    print(f"✅ تم تسجيل الدخول بنجاح باسم: {bot.user}")


@bot.event
async def on_voice_state_update(member, before, after):
    # إذا خرج شخص من الروم وصار البوت لوحده
    if before.channel and before.channel.guild.voice_client:
        voice_client = before.channel.guild.voice_client
        if voice_client.channel == before.channel:
            await disconnect_if_alone(before.channel.guild)


@bot.command(name="check", aliases=["فحص"])
async def check(ctx):
    await ctx.send("✅ البوت يعمل بنجاح!")


@bot.command(name="play", aliases=["p", "شغل", "تشغيل"])
async def play(ctx, *, search: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("❌ يجب أن تكون في روم صوتي أولًا!")
        return

    channel = ctx.author.voice.channel

    if not ctx.voice_client:
        voice_client = await channel.connect()
    else:
        voice_client = ctx.voice_client
        if voice_client.channel != channel:
            await voice_client.move_to(channel)

    async with ctx.typing():
        try:
            player = await YTDLSource.create_source(search, loop=bot.loop)
            guild_id = ctx.guild.id
            queue = get_queue(guild_id)

            if voice_client.is_playing() or voice_client.is_paused():
                queue.append(player)
                await ctx.send(f"📋 **تمت الإضافة إلى القائمة:** {player.title}")
            else:
                def after_playing(error):
                    if error:
                        print(f"Playback error: {error}")
                    asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

                voice_client.play(player, after=after_playing)
                await ctx.send(f"🎵 **جاري التشغيل:** {player.title}")

        except Exception as e:
            traceback.print_exc()
            error_msg = str(e) if str(e) else f"نوع الخطأ: {type(e).__name__}"
            await ctx.send(f"⚠️ **تفاصيل الخطأ:** `{error_msg}`")


@bot.command(name="queue", aliases=["قائمة", "الطابور"])
async def queue_cmd(ctx):
    guild_id = ctx.guild.id
    queue = queues.get(guild_id, deque())

    current = None
    if ctx.voice_client and ctx.voice_client.is_playing():
        current = getattr(ctx.voice_client.source, "title", "مقطع صوتي")

    if current:
        message = f"🎶 **يتم تشغيل الآن:** {current}\n\n"
    else:
        message = "🎶 **لا يوجد شيء يعمل الآن.**\n\n"

    if not queue:
        message += "📭 **قائمة التشغيل فارغة.**"
    else:
        message += "📋 **الطابور الحالي:**\n"
        for i, item in enumerate(queue, start=1):
            message += f"{i}. {item.title}\n"

    await ctx.send(message)


@bot.command(name="clear", aliases=["مسح", "تفريغ"])
async def clear(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues:
        queues[guild_id].clear()
    await ctx.send("🧹 تم تفريغ قائمة التشغيل.")


@bot.command(name="skip", aliases=["تخطي", "سكب"])
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ تم تخطي المقطع.")
    else:
        await ctx.send("❌ لا يوجد مقطع شغال حاليًا.")


@bot.command(name="pause", aliases=["ايقاف_مؤقت", "توقيف"])
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ تم إيقاف التشغيل مؤقتًا.")
    else:
        await ctx.send("❌ لا يوجد شيء قيد التشغيل.")


@bot.command(name="resume", aliases=["استئناف", "تكملة"])
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ تم استئناف التشغيل.")
    else:
        await ctx.send("❌ لا يوجد شيء متوقف مؤقتًا.")


@bot.command(name="stop", aliases=["ايقاف", "إيقاف", "سكت"])
async def stop(ctx):
    guild_id = ctx.guild.id

    if guild_id in queues:
        queues[guild_id].clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ تم إيقاف التشغيل ومغادرة الروم.")
    else:
        await ctx.send("❌ البوت ليس داخل روم صوتي.")


@bot.command(name="leave", aliases=["خروج", "اخرج"])
async def leave(ctx):
    guild_id = ctx.guild.id

    if guild_id in queues:
        queues[guild_id].clear()

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 تم مغادرة الروم.")
    else:
        await ctx.send("❌ البوت ليس داخل روم صوتي.")


TOKEN = os.getenv("DISCORD_TOKEN")
if TOKEN:
    bot.run(TOKEN)
else:
    print("❌ لم يتم العثور على DISCORD_TOKEN في متغيرات البيئة.")