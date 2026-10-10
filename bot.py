import asyncio
import os
import random
import traceback
from collections import deque

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

queues = {}
guild_settings = {}
now_playing = {}
panel_messages = {}
skip_once_flags = {}


def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def get_settings(guild_id):
    if guild_id not in guild_settings:
        guild_settings[guild_id] = {"volume": 0.5, "bass_boost": False, "repeat": False}
    return guild_settings[guild_id]


def clamp(value, minimum=0.0, maximum=2.0):
    return max(minimum, min(maximum, value))


def fmt_volume(value):
    return f"{round(value * 100)}%"


def queue_text(guild_id):
    queue = queues.get(guild_id, deque())
    if not queue:
        return "القائمة فارغة."
    return "\n".join(f"{i}. {item.get('title', 'مقطع صوتي')}" for i, item in enumerate(queue, start=1))


def ffmpeg_options_for(guild_id):
    options = BASE_FFMPEG_OPTIONS["options"]
    if get_settings(guild_id)["bass_boost"]:
        options += " -af bass=g=8:f=110:w=0.25"
    return {"before_options": BASE_FFMPEG_OPTIONS["before_options"], "options": options}


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, guild_id, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.guild_id = guild_id
        self.title = data.get("title", "مقطع صوتي")
        self.webpage_url = data.get("webpage_url")
        self.uploader = data.get("uploader")
        self.view_count = data.get("view_count")
        self.duration = data.get("duration")

    @classmethod
    async def from_webpage(cls, webpage_url, *, guild_id, loop=None, volume=0.5):
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

        return cls(
            discord.FFmpegPCMAudio(audio_url, executable=FFMPEG_EXECUTABLE, **ffmpeg_options_for(guild_id)),
            data=data,
            guild_id=guild_id,
            volume=volume,
        )


def build_track(entry, provider):
    return {
        "title": entry.get("title", "مقطع صوتي"),
        "webpage_url": entry.get("webpage_url") or entry.get("url"),
        "provider": provider,
        "uploader": entry.get("uploader") or entry.get("channel"),
        "view_count": entry.get("view_count") or 0,
        "duration": entry.get("duration"),
    }


async def search_tracks(query, *, provider="youtube", limit=5):
    loop = asyncio.get_running_loop()

    if query.startswith(("http://", "https://")):
        def extract_info():
            return ytdl.extract_info(query, download=False)
        data = await loop.run_in_executor(None, extract_info)
        if not data:
            return []
        if "entries" in data:
            entries = [e for e in data.get("entries", []) if e]
            if not entries:
                return []
            data = entries[0]
        track = build_track(data, provider)
        return [track]

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

    return [build_track(entry, provider) for entry in entries[:limit] if build_track(entry, provider)["webpage_url"]]


def shuffle_queue(guild_id):
    queue = queues.get(guild_id)
    if not queue or len(queue) < 2:
        return False
    items = list(queue)
    random.shuffle(items)
    queue.clear()
    queue.extend(items)
    return True


async def cleanup_bot_messages(channel, limit=30):
    deleted = 0
    async for message in channel.history(limit=limit):
        if message.author == bot.user:
            try:
                await message.delete()
                deleted += 1
            except Exception:
                pass
    return deleted


async def send_now_playing(ctx, track):
    guild_id = ctx.guild.id
    old = panel_messages.get(guild_id)
    if old:
        try:
            await old.delete()
        except Exception:
            pass

    settings = get_settings(guild_id)
    embed = discord.Embed(title="🎶 الآن يتم التشغيل", description=track.get("title", "مقطع صوتي"), color=discord.Color.blurple())
    embed.add_field(name="القناة", value=track.get("uploader") or "غير معروف", inline=True)
    embed.add_field(name="الصوت", value=fmt_volume(settings["volume"]), inline=True)
    embed.add_field(name="الباس", value="مفعّل" if settings["bass_boost"] else "متوقف", inline=True)
    embed.add_field(name="إعادة", value="مفعّلة" if settings["repeat"] else "متوقفة", inline=True)
    if track.get("webpage_url"):
        embed.url = track["webpage_url"]

    message = await ctx.send(embed=embed, view=MusicControlView(guild_id))
    panel_messages[guild_id] = message


async def play_track(ctx, track):
    guild_id = ctx.guild.id
    source = await YTDLSource.from_webpage(track["webpage_url"], guild_id=guild_id, volume=get_settings(guild_id)["volume"])
    now_playing[guild_id] = track

    def after_playing(error):
        if error:
            print(f"Playback error: {error}")
        asyncio.run_coroutine_threadsafe(handle_track_end(ctx), bot.loop)

    if ctx.voice_client:
        ctx.voice_client.play(source, after=after_playing)
        await send_now_playing(ctx, track)


async def play_next(ctx):
    guild_id = ctx.guild.id
    queue = queues.get(guild_id)
    if not queue or not ctx.voice_client or not ctx.voice_client.is_connected() or len(queue) == 0:
        return
    next_track = queue.popleft()
    try:
        await play_track(ctx, next_track)
    except Exception as ex:
        await ctx.send(f"⚠️ خطأ أثناء تشغيل التالي: `{ex}`")
        await play_next(ctx)


async def handle_track_end(ctx):
    guild_id = ctx.guild.id
    settings = get_settings(guild_id)

    if skip_once_flags.pop(guild_id, False):
        await play_next(ctx)
        return

    if settings.get("repeat") and now_playing.get(guild_id):
        try:
            await play_track(ctx, now_playing[guild_id])
            return
        except Exception as ex:
            print(f"Repeat playback error: {ex}")

    await play_next(ctx)


async def disconnect_if_alone(guild):
    vc = guild.voice_client
    if not vc or not vc.channel:
        return

    if len([m for m in vc.channel.members if not m.bot]) == 0:
        gid = guild.id
        queues.get(gid, deque()).clear()
        now_playing.pop(gid, None)
        skip_once_flags.pop(gid, None)
        panel = panel_messages.pop(gid, None)
        if panel:
            try:
                await panel.delete()
            except Exception:
                pass
        await vc.disconnect()


class MusicControlView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    async def interaction_check(self, interaction):
        if not interaction.guild or interaction.guild.id != self.guild_id:
            await interaction.response.send_message("هذا التحكم ليس لهذا السيرفر.", ephemeral=True)
            return False
        return True

    async def _vc(self):
        guild = bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None

    @discord.ui.button(label="إيقاف/تشغيل", style=discord.ButtonStyle.primary, emoji="⏯️")
    async def pause_resume(self, interaction, button):
        vc = await self._vc()
        if not vc:
            return await interaction.response.send_message("البوت ليس في روم صوتي.", ephemeral=True)
        if vc.is_playing():
            vc.pause()
            return await interaction.response.send_message("⏸️ تم إيقاف التشغيل مؤقتًا.", ephemeral=True)
        if vc.is_paused():
            vc.resume()
            return await interaction.response.send_message("▶️ تم استئناف التشغيل.", ephemeral=True)
        await interaction.response.send_message("لا يوجد تشغيل حاليًا.", ephemeral=True)

    @discord.ui.button(label="تخطي", style=discord.ButtonStyle.secondary, emoji="⏭️")
    async def skip(self, interaction, button):
        vc = await self._vc()
        if vc and vc.is_playing():
            skip_once_flags[self.guild_id] = True
            vc.stop()
            return await interaction.response.send_message("تم تخطي المقطع.", ephemeral=True)
        await interaction.response.send_message("لا يوجد مقطع شغال.", ephemeral=True)

    @discord.ui.button(label="القائمة", style=discord.ButtonStyle.secondary, emoji="📜")
    async def queue_button(self, interaction, button):
        current = now_playing.get(self.guild_id)
        embed = discord.Embed(title="📜 قائمة الانتظار", color=discord.Color.dark_teal())
        embed.add_field(name="الآن", value=current["title"] if current else "لا يوجد", inline=False)
        embed.add_field(name="الطابور", value=queue_text(self.guild_id)[:1024], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="خلط", style=discord.ButtonStyle.secondary, emoji="🔀")
    async def shuffle_button(self, interaction, button):
        if shuffle_queue(self.guild_id):
            return await interaction.response.send_message("🔀 تم خلط قائمة الانتظار.", ephemeral=True)
        await interaction.response.send_message("لا توجد قائمة كافية للخلط.", ephemeral=True)

    @discord.ui.button(label="تكرار", style=discord.ButtonStyle.success, emoji="🔁")
    async def repeat_button(self, interaction, button):
        settings = get_settings(self.guild_id)
        settings["repeat"] = not settings["repeat"]
        await interaction.response.send_message(f"🔁 التكرار أصبح: {'مفعّل' if settings['repeat'] else 'متوقف'}", ephemeral=True)

    @discord.ui.button(label="صوت -", style=discord.ButtonStyle.secondary, emoji="🔉")
    async def volume_down(self, interaction, button):
        vc = await self._vc()
        if not vc or not vc.source or not hasattr(vc.source, "volume"):
            return await interaction.response.send_message("لا يوجد مصدر صوتي.", ephemeral=True)
        settings = get_settings(self.guild_id)
        settings["volume"] = clamp(settings["volume"] - 0.1)
        vc.source.volume = settings["volume"]
        await interaction.response.send_message(f"تم خفض الصوت إلى {fmt_volume(settings['volume'])}.", ephemeral=True)

    @discord.ui.button(label="صوت +", style=discord.ButtonStyle.secondary, emoji="🔊")
    async def volume_up(self, interaction, button):
        vc = await self._vc()
        if not vc or not vc.source or not hasattr(vc.source, "volume"):
            return await interaction.response.send_message("لا يوجد مصدر صوتي.", ephemeral=True)
        settings = get_settings(self.guild_id)
        settings["volume"] = clamp(settings["volume"] + 0.1)
        vc.source.volume = settings["volume"]
        await interaction.response.send_message(f"تم رفع الصوت إلى {fmt_volume(settings['volume'])}.", ephemeral=True)

    @discord.ui.button(label="باس", style=discord.ButtonStyle.success, emoji="🎚️")
    async def bass_toggle(self, interaction, button):
        settings = get_settings(self.guild_id)
        settings["bass_boost"] = not settings["bass_boost"]
        await interaction.response.send_message(f"تم {'تفعيل' if settings['bass_boost'] else 'إيقاف'} الباس للأغاني القادمة.", ephemeral=True)


@bot.event
async def on_ready():
    print(f"✅ تم تسجيل الدخول بنجاح باسم: {bot.user}")


@bot.event
async def on_voice_state_update(member, before, after):
    if before.channel and before.channel.guild.voice_client:
        vc = before.channel.guild.voice_client
        if vc.channel == before.channel:
            await disconnect_if_alone(before.channel.guild)


@bot.command(name="check", aliases=["فحص"])
async def check(ctx):
    await ctx.send("✅ البوت يعمل بنجاح!")


@bot.command(name="شغل", aliases=["play", "p", "تشغيل"])
async def play(ctx, *, search: str):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("❌ يجب أن تكون في روم صوتي أولًا!")

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
        return await ctx.send("❌ اكتب اسم الأغنية بعد حرف س.")
    if not query:
        return await ctx.send("❌ اكتب اسم الأغنية.")

    async with ctx.typing():
        try:
            tracks = await search_tracks(query, provider=provider, limit=5)
            if not tracks:
                return await ctx.send("❌ لم أجد نتائج مناسبة.")

            queue = get_queue(ctx.guild.id)
            queue.extend(tracks)

            if not voice_client.is_playing() and not voice_client.is_paused():
                await play_next(ctx)
                await ctx.send("🎵 **تم بدء التشغيل:**\n" + "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(tracks[:5])))
            else:
                await ctx.send("📋 **تمت الإضافة إلى قائمة الانتظار:**\n" + "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(tracks[:5])))

        except Exception as e:
            traceback.print_exc()
            await ctx.send(f"⚠️ **تفاصيل الخطأ:** `{str(e) or type(e).__name__}`")


@bot.command(name="queue", aliases=["قائمة", "الطابور"])
async def queue_cmd(ctx):
    queue = queues.get(ctx.guild.id, deque())
    current = now_playing.get(ctx.guild.id)
    message = f"🎶 **يتم تشغيل الآن:** {current['title'] if current else 'لا يوجد شيء الآن'}\n\n"
    if not queue:
        message += "📭 **قائمة التشغيل فارغة.**"
    else:
        message += "📋 **الطابور الحالي:**\n" + "\n".join(f"{i}. {item['title']}" for i, item in enumerate(queue, start=1))
    await ctx.send(message)


@bot.command(name="صوت", aliases=["volume", "فوليوم"])
async def set_volume(ctx, value: int):
    if value < 0 or value > 200:
        return await ctx.send("❌ اكتب قيمة بين 0 و 200.")
    settings = get_settings(ctx.guild.id)
    settings["volume"] = value / 100
    if ctx.voice_client and ctx.voice_client.source and hasattr(ctx.voice_client.source, "volume"):
        ctx.voice_client.source.volume = settings["volume"]
    await ctx.send(f"🔊 تم ضبط الصوت إلى {value}%.")


@bot.command(name="باس", aliases=["bass"])
async def bass(ctx):
    settings = get_settings(ctx.guild.id)
    settings["bass_boost"] = not settings["bass_boost"]
    await ctx.send(f"🎚️ الباس أصبح: {'مفعّل' if settings['bass_boost'] else 'متوقف'}")


@bot.command(name="خلط", aliases=["shuffle", "mix"])
async def shuffle_cmd(ctx):
    if shuffle_queue(ctx.guild.id):
        await ctx.send("🔀 تم خلط قائمة الانتظار.")
    else:
        await ctx.send("❌ لا توجد قائمة كافية للخلط.")


@bot.command(name="تكرار", aliases=["loop", "repeat"])
async def repeat_cmd(ctx):
    settings = get_settings(ctx.guild.id)
    settings["repeat"] = not settings["repeat"]
    await ctx.send(f"🔁 التكرار أصبح: {'مفعّل' if settings['repeat'] else 'متوقف'}")


@bot.command(name="تخطي", aliases=["skip", "سكب"])
async def skip(ctx, count: int = 1):
    if count < 1:
        return await ctx.send("❌ اكتب رقم صحيح.")

    queue = get_queue(ctx.guild.id)
    removed = 0

    if ctx.voice_client and (ctx.voice_client.is_playing() or ctx.voice_client.is_paused()):
        skip_once_flags[ctx.guild.id] = True
        while removed < count - 1 and queue:
            queue.popleft()
            removed += 1
        ctx.voice_client.stop()
        return await ctx.send(f"⏭️ تم تخطي {count} مقطع/مقاطع.")

    while removed < count and queue:
        queue.popleft()
        removed += 1
    await ctx.send(f"⏭️ تم حذف {removed} مقطع من الطابور.")

    if queue and ctx.voice_client and ctx.voice_client.is_connected():
        await play_next(ctx)


@bot.command(name="تنظيف", aliases=["تنضيف", "مسح"])
async def clean(ctx, limit: int = 30):
    if limit < 1 or limit > 100:
        return await ctx.send("❌ الحد بين 1 و 100.")
    deleted = await cleanup_bot_messages(ctx.channel, limit=limit)
    await ctx.send(f"🧹 تم تنظيف {deleted} رسالة من رسائل البوت.", delete_after=5)


@bot.command(name="clear", aliases=["تفريغ"])
async def clear(ctx):
    queues.get(ctx.guild.id, deque()).clear()
    await ctx.send("🧹 تم تفريغ قائمة التشغيل.")


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
    queues.get(ctx.guild.id, deque()).clear()
    now_playing.pop(ctx.guild.id, None)
    skip_once_flags.pop(ctx.guild.id, None)
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ تم إيقاف التشغيل ومغادرة الروم.")
    else:
        await ctx.send("❌ البوت ليس داخل روم صوتي.")


@bot.command(name="leave", aliases=["خروج", "اخرج"])
async def leave(ctx):
    queues.get(ctx.guild.id, deque()).clear()
    now_playing.pop(ctx.guild.id, None)
    skip_once_flags.pop(ctx.guild.id, None)
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