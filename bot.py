import asyncio
import os
import traceback
from collections import deque
from typing import Deque, Dict, List, Optional

import discord
from discord.ext import commands
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

BASE_FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

queues: Dict[int, Deque[dict]] = {}
guild_settings: Dict[int, dict] = {}
now_playing: Dict[int, dict] = {}
panel_messages: Dict[int, discord.Message] = {}


def get_queue(guild_id: int) -> Deque[dict]:
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def get_settings(guild_id: int) -> dict:
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {
            "volume": 0.5,
            "bass_boost": False,
        }
    return guild_settings[guild_id]


def clamp(value: float, minimum: float = 0.0, maximum: float = 2.0) -> float:
    return max(minimum, min(maximum, value))


def fmt_volume(value: float) -> str:
    return f"{round(value * 100)}%"


def queue_text(guild_id: int) -> str:
    queue = queues.get(guild_id, deque())
    if not queue:
        return "القائمة فارغة."

    lines = []
    for i, item in enumerate(queue, start=1):
        title = item.get("title", "مقطع صوتي")
        lines.append(f"{i}. {title}")
    return "\n".join(lines)


def ffmpeg_options_for(guild_id: int) -> dict:
    settings = get_settings(guild_id)
    options = BASE_FFMPEG_OPTIONS["options"]
    if settings["bass_boost"]:
        options += " -af bass=g=8:f=110:w=0.25"
    return {
        "before_options": BASE_FFMPEG_OPTIONS["before_options"],
        "options": options,
    }


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, guild_id: int, volume: float = 0.5):
        super().__init__(source, volume)
        self.data = data
        self.guild_id = guild_id
        self.title = data.get("title", "مقطع صوتي")
        self.webpage_url = data.get("webpage_url")
        self.uploader = data.get("uploader")
        self.view_count = data.get("view_count")
        self.duration = data.get("duration")

    @classmethod
    async def from_webpage(cls, webpage_url: str, *, guild_id: int, loop=None, volume: float = 0.5):
        loop = loop or asyncio.get_running_loop()

        def extract_info():
            return ytdl.extract_info(webpage_url, download=False)

        data = await loop.run_in_executor(None, extract_info)
        if not data:
            raise Exception("لم يتم العثور على بيانات للمقطع.")

        if "entries" in data:
            entries = [e for e in data["entries"] if e]
            if not entries:
                raise Exception("لم يتم العثور على بيانات للمقطع.")
            data = entries[0]

        audio_url = data.get("url")
        if not audio_url:
            for fmt in data.get("formats", []):
                if fmt.get("acodec") != "none" and fmt.get("url"):
                    audio_url = fmt["url"]
                    break

        if not audio_url:
            raise Exception("فشل في الحصول على رابط الصوت.")

        ffmpeg_options = ffmpeg_options_for(guild_id)
        return cls(
            discord.FFmpegPCMAudio(
                audio_url,
                executable=FFMPEG_EXECUTABLE,
                **ffmpeg_options,
            ),
            data=data,
            guild_id=guild_id,
            volume=volume,
        )


def build_track(entry: dict, provider: str) -> dict:
    title = entry.get("title", "مقطع صوتي")
    webpage_url = entry.get("webpage_url") or entry.get("url")
    return {
        "title": title,
        "webpage_url": webpage_url,
        "provider": provider,
        "uploader": entry.get("uploader") or entry.get("channel"),
        "view_count": entry.get("view_count") or 0,
        "duration": entry.get("duration"),
    }


async def search_tracks(query: str, *, provider: str = "youtube", limit: int = 5) -> List[dict]:
    loop = asyncio.get_running_loop()
    prefix = "scsearch" if provider == "soundcloud" else "ytsearch"
    search_target = f"{prefix}{limit}:{query}"

    def extract_info():
        return ytdl.extract_info(search_target, download=False)

    data = await loop.run_in_executor(None, extract_info)
    if not data:
        return []

    entries = [e for e in data.get("entries", []) if e]
    if not entries:
        return []

    if provider == "youtube":
        entries.sort(key=lambda e: e.get("view_count") or 0, reverse=True)

    tracks = []
    for entry in entries[:limit]:
        track = build_track(entry, provider)
        if track["webpage_url"]:
            tracks.append(track)
    return tracks


async def send_now_playing(ctx: commands.Context, track: dict):
    guild_id = ctx.guild.id
    old_message = panel_messages.get(guild_id)
    if old_message:
        try:
            await old_message.delete()
        except Exception:
            pass

    settings = get_settings(guild_id)
    view = MusicControlView(guild_id)
    embed = discord.Embed(
        title="🎶 الآن يتم التشغيل",
        description=track.get("title", "مقطع صوتي"),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="القناة", value=track.get("uploader") or "غير معروف", inline=True)
    embed.add_field(name="الصوت", value=fmt_volume(settings["volume"]), inline=True)
    embed.add_field(
        name="الباس",
        value="مفعّل" if settings["bass_boost"] else "متوقف",
        inline=True,
    )
    if track.get("webpage_url"):
        embed.url = track["webpage_url"]

    message = await ctx.send(embed=embed, view=view)
    panel_messages[guild_id] = message


async def play_track(ctx: commands.Context, track: dict):
    guild_id = ctx.guild.id
    settings = get_settings(guild_id)
    source = await YTDLSource.from_webpage(
        track["webpage_url"],
        guild_id=guild_id,
        volume=settings["volume"],
    )
    now_playing[guild_id] = track

    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

    if ctx.voice_client:
        ctx.voice_client.play(source, after=after_playing)
        await send_now_playing(ctx, track)


async def play_next(ctx: commands.Context):
    guild_id = ctx.guild.id
    queue = queues.get(guild_id)

    if not queue:
        return

    if not ctx.voice_client or not ctx.voice_client.is_connected():
        return

    if len(queue) == 0:
        return

    next_track = queue.popleft()
    try:
        await play_track(ctx, next_track)
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
        now_playing.pop(guild_id, None)
        try:
            panel = panel_messages.pop(guild_id, None)
            if panel:
                await panel.delete()
        except Exception:
            pass
        await voice_client.disconnect()


async def cleanup_bot_messages(channel: discord.TextChannel, limit: int = 30):
    deleted = 0
    async for message in channel.history(limit=limit):
        if message.author == bot.user:
            try:
                await message.delete()
                deleted += 1
            except Exception:
                pass
    return deleted


class MusicControlView(discord.ui.View):
    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("هذا التحكم ليس لهذا السيرفر.", ephemeral=True)
            return False
        return True

    async def _voice_client(self) -> Optional[discord.VoiceClient]:
        guild = bot.get_guild(self.guild_id)
        if not guild:
            return None
        return guild.voice_client

    @discord.ui.button(label="إيقاف/تشغيل", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._voice_client()
        if not vc:
            await interaction.response.send_message("البوت ليس في روم صوتي.", ephemeral=True)
            return

        if vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ تم إيقاف التشغيل مؤقتًا.", ephemeral=True)
        elif vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ تم استئناف التشغيل.", ephemeral=True)
        else:
            await interaction.response.send_message("لا يوجد تشغيل حاليًا.", ephemeral=True)

    @discord.ui.button(label="تخطي", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._voice_client()
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("تم تخطي المقطع.", ephemeral=True)
        else:
            await interaction.response.send_message("لا يوجد مقطع شغال.", ephemeral=True)

    @discord.ui.button(label="القائمة", style=discord.ButtonStyle.secondary, emoji="📜")
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = now_playing.get(self.guild_id)
        text = queue_text(self.guild_id)
        embed = discord.Embed(title="📜 قائمة الانتظار", color=discord.Color.dark_teal())
        embed.add_field(name="الآن", value=current["title"] if current else "لا يوجد", inline=False)
        embed.add_field(name="الطابور", value=text[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="صوت -", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._voice_client()
        if not vc or not vc.source or not hasattr(vc.source, "volume"):
            await interaction.response.send_message("لا يوجد مصدر صوتي.", ephemeral=True)
            return

        settings = get_settings(self.guild_id)
        settings["volume"] = clamp(settings["volume"] - 0.1)
        vc.source.volume = settings["volume"]
        await interaction.response.send_message(f"تم خفض الصوت إلى {fmt_volume(settings['volume'])}.", ephemeral=True)

    @discord.ui.button(label="صوت +", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = await self._voice_client()
        if not vc or not vc.source or not hasattr(vc.source, "volume"):
            await interaction.response.send_message("لا يوجد مصدر صوتي.", ephemeral=True)
            return

        settings = get_settings(self.guild_id)
        settings["volume"] = clamp(settings["volume"] + 0.1)
        vc.source.volume = settings["volume"]
        await interaction.response.send_message(f"تم رفع الصوت إلى {fmt_volume(settings['volume'])}.", ephemeral=True)

    @discord.ui.button(label="باس", style=discord.ButtonStyle.success, emoji="🎚️")
    async def bass_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        settings = get_settings(self.guild_id)
        settings["bass_boost"] = not settings["bass_boost"]
        status = "مفعّل" if settings["bass_boost"] else "متوقف"
        await interaction.response.send_message(f"تم {status} الباس للأغاني القادمة.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"✅ تم تسجيل الدخول بنجاح باسم: {bot.user}")


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.guild.voice_client:
        voice_client = before.channel.guild.voice_client
        if voice_client.channel == before.channel:
            await disconnect_if_alone(before.channel.guild)


@bot.command(name="check", aliases=["فحص"])
async def check(ctx):
    await ctx.send("✅ البوت يعمل بنجاح!")


@bot.command(name="شغل", aliases=["play", "p", "تشغيل"])
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

    provider = "youtube"
    query = search.strip()

    if query.startswith("س "):
        provider = "soundcloud"
        query = query[2:].strip()
    elif query == "س":
        await ctx.send("❌ اكتب اسم الأغنية بعد حرف س.")
        return

    if not query:
        await ctx.send("❌ اكتب اسم الأغنية.")
        return

    async with ctx.typing():
        try:
            tracks = await search_tracks(query, provider=provider, limit=5)
            if not tracks:
                await ctx.send("❌ لم أجد نتائج مناسبة.")
                return

            guild_id = ctx.guild.id
            queue = get_queue(guild_id)

            start_now = not voice_client.is_playing() and not voice_client.is_paused() and len(queue) == 0

            if start_now:
                first = tracks[0]
                queue.extend(tracks[1:])
                await play_track(ctx, first)
                await ctx.send(
                    "🎵 **تم التشغيل:**\n" +
                    "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(tracks[:5])])
                )
            else:
                queue.extend(tracks)
                await ctx.send(
                    "📋 **تمت الإضافة إلى قائمة الانتظار:**\n" +
                    "\n".join([f"{i+1}. {t['title']}" for i, t in enumerate(tracks[:5])])
                )

        except Exception as e:
            traceback.print_exc()
            error_msg = str(e) if str(e) else f"نوع الخطأ: {type(e).__name__}"
            await ctx.send(f"⚠️ **تفاصيل الخطأ:** `{error_msg}`")


@bot.command(name="queue", aliases=["قائمة", "الطابور"])
async def queue_cmd(ctx):
    guild_id = ctx.guild.id
    queue = queues.get(guild_id, deque())

    current = now_playing.get(guild_id)
    message = f"🎶 **يتم تشغيل الآن:** {current['title'] if current else 'لا يوجد شيء الآن'}\n\n"

    if not queue:
        message += "📭 **قائمة التشغيل فارغة.**"
    else:
        message += "📋 **الطابور الحالي:**\n"
        for i, item in enumerate(queue, start=1):
            message += f"{i}. {item['title']}\n"

    await ctx.send(message)


@bot.command(name="صوت", aliases=["volume", "فوليوم"])
async def set_volume(ctx, value: int):
    if value < 0 or value > 200:
        await ctx.send("❌ اكتب قيمة بين 0 و 200.")
        return

    guild_id = ctx.guild.id
    settings = get_settings(guild_id)
    settings["volume"] = value / 100

    if ctx.voice_client and ctx.voice_client.source and hasattr(ctx.voice_client.source, "volume"):
        ctx.voice_client.source.volume = settings["volume"]

    await ctx.send(f"🔊 تم ضبط الصوت إلى {value}%.")


@bot.command(name="باس", aliases=["bass"])
async def bass(ctx):
    guild_id = ctx.guild.id
    settings = get_settings(guild_id)
    settings["bass_boost"] = not settings["bass_boost"]
    await ctx.send(f"🎚️ الباس أصبح: {'مفعّل' if settings['bass_boost'] else 'متوقف'}")


@bot.command(name="تنظيف", aliases=["تنضيف", "مسح"])
async def clean(ctx, limit: int = 30):
    if limit < 1 or limit > 100:
        await ctx.send("❌ الحد بين 1 و 100.")
        return

    deleted = await cleanup_bot_messages(ctx.channel, limit=limit)
    await ctx.send(f"🧹 تم تنظيف {deleted} رسالة من رسائل البوت.", delete_after=5)


@bot.command(name="clear", aliases=["تفريغ"])
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


@bot.command(name="stop", aliases=["إيقاف", "ايقاف", "سكت"])
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