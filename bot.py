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

# خيارات yt-dlp محدثة بالكامل لتتجاوز حظر خوادم Railway
YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": True,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
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

        # التعامل مع البحث والروبط
        if search_query.startswith("http://") or search_query.startswith(
            "https://"
        ):
            target = search_query
        else:
            target = f"scsearch1:{search_query}"

        # استخراج البيانات مع مهلة زمنية ومحاولات متعددة
        try:
            data = await loop.run_in_executor(
                None, lambda: ytdl.extract_info(target, download=False)
            )
        except Exception as e:
            data = None

        # إذا أخفق SoundCloud، يجرب محرك جلب ثاني تلقائياً
        if not data and not search_query.startswith("http"):
            try:
                data = await loop.run_in_executor(
                    None,
                    lambda: ytdl.extract_info(
                        f"ytsearch1:{search_query}", download=False
                    ),
                )
            except Exception:
                data = None

        if not data:
            raise RuntimeError(
                "تعذر الاتصال بـ SoundCloud/YouTube من سيرفر الاستضافة. جرب استخدام رابط مقطع مباشر."
            )

        if "entries" in data:
            entries = [e for e in data["entries"] if e and e.get("url")]
            if not entries:
                raise RuntimeError("لم يتم العثور على نتائج صالحة لهذا البحث.")
            data = entries[0]

        filename = data.get("url")
        if not filename:
            raise RuntimeError("لم يتم العثور على رابط بث مباشر للمقطع.")

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
