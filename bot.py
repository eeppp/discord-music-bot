from __future__ import annotations

import logging
import os
import asyncio
import datetime as dt
import random

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv
from rapidfuzz import fuzz

from config import Config
from controls import (
    ensure_panel,
    load_player,
    play_query,
    queue_embed,
    schedule_cleanup,
    restore_panels,
)
from database import Database
from features import FeatureStore
from effects import EFFECT_NAMES, VALID_EFFECTS, normalize_effect
from player import player_manager
from player import Song
from views import MusicCommandsView, is_music_commands_channel, music_commands_embed

load_dotenv()

os.makedirs(os.path.dirname(Config.LOG_FILE) or ".", exist_ok=True)
os.makedirs(os.path.dirname(Config.DATABASE) or ".", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(Config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("MusicS3YD")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True

bot = commands.Bot(
    command_prefix=Config.PREFIX,
    intents=intents,
    help_command=None,
    case_insensitive=True,
)
database = Database(Config.DATABASE)
features = FeatureStore(Config.DATABASE, Config.VOICE_COINS_PER_MINUTE)
_database_ready = False
_commands_synced = False
_commands_panel_published = False
_voice_started: dict[tuple[int, int], dt.datetime] = {}
_trivia_rounds: dict[int, dict[str, object]] = {}
_weekly_report_dates: set[tuple[int, str]] = set()


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _hours_label(seconds: int) -> str:
    hours, remainder = divmod(max(0, seconds), 3600)
    minutes = remainder // 60
    return f"{hours} س و{minutes} د"


@tasks.loop(minutes=30)
async def weekly_reporter() -> None:
    now = _utcnow()
    if now.weekday() != 0 or now.hour < 9 or not Config.WEEKLY_REPORT_CHANNEL_ID:
        return
    week_key = now.strftime("%G-W%V")
    since = now - dt.timedelta(days=7)
    try:
        channel_id = int(Config.WEEKLY_REPORT_CHANNEL_ID)
    except ValueError:
        logger.error("WEEKLY_REPORT_CHANNEL_ID must be a numeric channel ID.")
        return
    for guild in bot.guilds:
        marker = (guild.id, week_key)
        if marker in _weekly_report_dates or await features.has_weekly_report(guild.id, week_key):
            _weekly_report_dates.add(marker)
            continue
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            continue
        try:
            top = await features.weekly_voice(guild.id, since)
            total = await features.weekly_total(guild.id, since)
            songs = await features.popular_songs(guild.id, since)
            members = [f"{index}. <@{user_id}> — {_hours_label(seconds)}"
                       for index, (user_id, seconds) in enumerate(top, 1)] or ["لا توجد بيانات صوتية هذا الأسبوع."]
            popular = [f"{index}. {discord.utils.escape_markdown(title)} — {count} مرة"
                       for index, (title, count) in enumerate(songs, 1)] or ["لا توجد طلبات أغاني مسجلة."]
            embed = discord.Embed(
                title=f"📊 ملخص النشاط الأسبوعي · {guild.name}",
                description=(f"**إجمالي الوقت الصوتي:** {_hours_label(total)}\n\n"
                             "**الأكثر نشاطًا صوتيًا**\n" + "\n".join(members) +
                             "\n\n**الأغاني الأكثر طلبًا**\n" + "\n".join(popular)),
                color=Config.EMBED_COLOR,
            )
            await channel.send(embed=embed)
            await features.mark_weekly_report(guild.id, week_key)
            _weekly_report_dates.add(marker)
        except Exception:
            logger.exception("Could not publish weekly activity summary for guild %s", guild.id)


@weekly_reporter.before_loop
async def before_weekly_reporter() -> None:
    await bot.wait_until_ready()


async def _publish_music_commands_panel() -> None:
    if bot.user is None:
        return

    for guild in bot.guilds:
        channel = None
        if Config.MUSIC_COMMAND_CHANNEL_ID:
            try:
                channel = guild.get_channel(int(Config.MUSIC_COMMAND_CHANNEL_ID))
            except ValueError:
                logger.error("MUSIC_COMMAND_CHANNEL_ID must be a channel ID.")
                return
        else:
            channel = next(
                (item for item in guild.text_channels if is_music_commands_channel(item)),
                None,
            )

        if not isinstance(channel, discord.TextChannel):
            possible_channels = [
                item.name
                for item in guild.text_channels
                if "music" in item.name.casefold() or "اوامر" in item.name
            ]
            logger.info(
                "Music commands channel not found in guild %s. Matching visible channel names: %s",
                guild.id,
                possible_channels,
            )
            continue

        try:
            async for message in channel.history(limit=50):
                if (
                    message.author.id == bot.user.id
                    and any(embed.title == "🎵 أوامر Music S3YD" for embed in message.embeds)
                ):
                    await message.edit(embed=music_commands_embed(), view=MusicCommandsView())
                    logger.info("Updated music commands panel in channel %s.", channel.id)
                    break
            else:
                await channel.send(embed=music_commands_embed(), view=MusicCommandsView())
                logger.info("Published music commands panel in channel %s.", channel.id)
        except discord.Forbidden:
            logger.warning("Cannot publish music commands panel in channel %s.", channel.id)
        except discord.HTTPException:
            logger.exception("Could not publish music commands panel in channel %s.", channel.id)


@bot.event
async def on_ready() -> None:
    global _database_ready, _commands_synced, _commands_panel_published
    if not _commands_panel_published:
        _commands_panel_published = True
        bot.add_view(MusicCommandsView())
        await _publish_music_commands_panel()
    if not _database_ready:
        await database.initialize()
        await features.initialize()
        player_manager.configure(bot, database)
        await restore_panels(bot)
        now = _utcnow()
        for guild in bot.guilds:
            for channel in guild.channels:
                if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                    continue
                for member in channel.members:
                    if not member.bot:
                        _voice_started.setdefault((guild.id, member.id), now)
        _database_ready = True

    if not weekly_reporter.is_running():
        weekly_reporter.start()

    if not _commands_synced:
        try:
            await bot.tree.sync()
            _commands_synced = True
            logger.info("Synced slash commands.")
        except discord.HTTPException:
            logger.exception("Could not sync slash commands.")

    logger.info("Logged in as %s (%s)", bot.user, bot.user.id if bot.user else "unknown")
    activity = discord.Activity(
        type=discord.ActivityType.listening,
        name=f"{Config.PREFIX}شغل",
    )
    await bot.change_presence(activity=activity)
    print("=" * 48)
    print("Music S3YD V2")
    print(f"User: {bot.user}")
    print(f"Guilds: {len(bot.guilds)}")
    print("=" * 48)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
) -> None:
    if member.bot or before.channel == after.channel:
        return
    now = _utcnow()
    key = (member.guild.id, member.id)
    started_at = _voice_started.pop(key, None)
    if started_at is not None:
        seconds = int((now - started_at).total_seconds())
        try:
            earned = await features.record_voice(member.guild.id, member.id, started_at, now, seconds)
            if earned:
                logger.info("Awarded %s voice coins to member %s", earned, member.id)
        except Exception:
            logger.exception("Could not record voice session for member %s", member.id)
    if after.channel is not None:
        _voice_started[key] = now
        player = player_manager.players.get(member.guild.id)
        if (player and player.voice and player.voice.is_connected()
                and player.voice.channel == after.channel
                and not player.voice.is_playing() and not player.voice.is_paused()):
            try:
                source = discord.FFmpegPCMAudio(
                    "sine=frequency=880:duration=0.35",
                    before_options="-f lavfi",
                    options="-vn",
                )
                player.voice.play(source)
            except (discord.ClientException, discord.HTTPException, OSError):
                logger.debug("Could not play the voice join tone.", exc_info=True)


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.guild and not message.author.bot:
        round_data = _trivia_rounds.get(message.guild.id)
        if (round_data and not round_data["solved"]
                and message.channel.id == round_data["channel_id"]):
            answer = str(round_data["answer"])
            if fuzz.WRatio(message.content, answer) >= 88:
                round_data["solved"] = True
                await features.add_coins(message.author.id, Config.TRIVIA_REWARD)
                await message.reply(
                    f"🎉 إجابة صحيحة يا {message.author.mention}! ربحت {Config.TRIVIA_REWARD} عملة.",
                    mention_author=False,
                )
    await bot.process_commands(message)


async def _send_status(
    ctx: commands.Context,
    text: str,
    *,
    embed: discord.Embed | None = None,
    delay: int | None = None,
) -> discord.Message:
    message = await ctx.send(
        content=text or None,
        embed=embed,
    )
    schedule_cleanup(message, delay)
    return message


async def _voice_player(ctx: commands.Context):
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await _send_status(ctx, "🔊 ادخل قناة صوتية أولًا.")
        return None
    player = await load_player(ctx.guild.id, ctx.channel)
    if player.voice and player.voice.is_connected() and player.voice.channel != voice_state.channel:
        await _send_status(ctx, "انضم إلى قناة البوت نفسها للتحكم بالتشغيل.")
        return None
    return player


@bot.hybrid_command(name="شغل", aliases=["play", "تشغيل"])
@commands.guild_only()
async def play_command(ctx: commands.Context, *, query: str | None = None) -> None:
    await play_query(ctx, query)


@bot.hybrid_command(name="صوت", aliases=["ص", "volume", "vol"])
@commands.guild_only()
async def volume_command(ctx: commands.Context, volume: int | None = None) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if volume is None:
        await ensure_panel(player, ctx.channel)
        await _send_status(ctx, "استخدم قائمة الصوت في لوحة المشغّل لاختيار مستوى يناسب الجوال.")
        return
    if not 0 <= volume <= 200:
        await _send_status(ctx, "اختر مستوى صوت بين 0 و200.")
        return
    player.set_volume(volume)
    await player.save_settings()
    await ensure_panel(player, ctx.channel)
    await _send_status(ctx, f"🔊 مستوى الصوت الآن {player.volume}%.")


@bot.hybrid_command(name="تخطي", aliases=["skip", "التالي"])
@commands.guild_only()
async def skip_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not player.current:
        await _send_status(ctx, "لا توجد أغنية لتخطيها.")
        return
    await player.skip()
    await _send_status(ctx, "⏭️ تم تخطي الأغنية.")


@bot.hybrid_command(name="تكرار", aliases=["repeat", "كرر"])
@commands.guild_only()
async def repeat_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    player.repeat = not player.repeat
    await player.save_settings()
    await ensure_panel(player, ctx.channel)
    status = "مفعّل للأغنية الحالية" if player.repeat else "متوقف"
    await _send_status(ctx, f"🔁 التكرار {status}.")


@bot.hybrid_command(name="خلط", aliases=["shuffle"])
@commands.guild_only()
async def shuffle_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if len(player.queue) < 2:
        await _send_status(ctx, "أضف أغنيتين على الأقل قبل خلط الانتظار.")
        return
    player.queue.shuffle()
    player.shuffle = True
    await ensure_panel(player, ctx.channel)
    await _send_status(ctx, "🔀 تم خلط قائمة الانتظار.")


@bot.hybrid_command(name="قائمة", aliases=["queue", "انتظار"])
@commands.guild_only()
async def queue_command(ctx: commands.Context) -> None:
    player = await load_player(ctx.guild.id, ctx.channel)
    message = await ctx.send(embed=queue_embed(player))
    schedule_cleanup(message)


@bot.hybrid_command(name="إيقاف", aliases=["stop", "اطفاء", "إطفاء"])
@commands.guild_only()
async def stop_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    await player.stop(disconnect=True)
    await _send_status(ctx, "⏹️ توقف البوت ومسح قائمة الانتظار.")


@bot.hybrid_command(name="وقف", aliases=["pause", "مؤقت"])
@commands.guild_only()
async def pause_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if await player.pause():
        await _send_status(ctx, "⏸️ تم إيقاف التشغيل مؤقتًا.")
    elif await player.resume():
        await _send_status(ctx, "▶️ استؤنف التشغيل.")
    else:
        await _send_status(ctx, "لا توجد أغنية تعمل الآن.")


@bot.hybrid_command(name="كمل", aliases=["resume", "استئناف"])
@commands.guild_only()
async def resume_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if await player.resume():
        await _send_status(ctx, "▶️ استؤنف التشغيل.")
    else:
        await _send_status(ctx, "التشغيل ليس متوقفًا مؤقتًا.")


@bot.hybrid_command(name="باس", aliases=["bass", "bassboost"])
@commands.guild_only()
async def bass_command(ctx: commands.Context, level: int | None = None) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if level is None:
        await ensure_panel(player, ctx.channel)
        await _send_status(ctx, "استخدم قائمة Bass Boost في لوحة المشغّل، أو اكتب رقمًا من 0 إلى 10.")
        return
    if not 0 <= level <= 10:
        await _send_status(ctx, "اختر Bass Boost من 0 إلى 10.")
        return
    player.set_bass(level)
    await player.save_settings()
    applied = await player.apply_effects_to_current()
    await ensure_panel(player, ctx.channel)
    effect_status = (
        "وطُبّق على الأغنية الحالية من موضعها."
        if applied
        else "سيُطبّق على الأغنية التالية."
    )
    await _send_status(
        ctx,
        f"🎚️ Bass Boost مضبوط على {player.bass}/10، {effect_status}",
    )


@bot.hybrid_command(name="تأثير", description="اختيار المؤثر الصوتي")
@commands.guild_only()
async def effect_command(
    ctx: commands.Context,
    effect: str | None = None,
) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not effect:
        await ensure_panel(player, ctx.channel)
        await _send_status(ctx, "اختر المؤثر من قائمة 🎛️ في لوحة المشغّل.")
        return

    normalized = normalize_effect(effect)
    aliases = {"عادي", "إيقاف", "off", "جهير", "باس", "تريبل", "نايتكور", "٨دي", "8دي"}
    if effect.casefold() not in VALID_EFFECTS and effect.casefold() not in aliases:
        await _send_status(ctx, "المؤثرات: normal, bass, treble, nightcore, 8d.")
        return

    player.set_effect(normalized)
    await player.save_settings()
    applied = await player.apply_effects_to_current()
    await ensure_panel(player, ctx.channel)
    effect_status = (
        "وطُبّق على الأغنية الحالية من موضعها."
        if applied
        else "سيُطبّق على الأغنية التالية."
    )
    await _send_status(
        ctx,
        f"🎛️ تم اختيار مؤثر {EFFECT_NAMES[normalized]}، {effect_status}",
    )


@bot.hybrid_command(name="تلقائي", aliases=["autoplay", "راديو"])
@commands.guild_only()
async def autoplay_command(ctx: commands.Context, mode: str | None = None) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if mode and mode.lower() in {"on", "تشغيل", "تفعيل", "نعم"}:
        player.autoplay = True
    elif mode and mode.lower() in {"off", "إيقاف", "تعطيل", "لا"}:
        player.autoplay = False
    else:
        player.autoplay = not player.autoplay
    await player.save_settings()
    await ensure_panel(player, ctx.channel)
    status = "مفعّل" if player.autoplay else "متوقف"
    await _send_status(ctx, f"📻 التشغيل التلقائي {status}.")


@bot.hybrid_command(name="hours", description="عرض وقتك في القنوات الصوتية")
@commands.guild_only()
async def hours_command(ctx: commands.Context, member: discord.Member | None = None) -> None:
    target = member or ctx.author
    seconds = await features.total_voice_seconds(ctx.guild.id, target.id)
    started = _voice_started.get((ctx.guild.id, target.id))
    if started:
        seconds += max(0, int((_utcnow() - started).total_seconds()))
    await ctx.send(f"🎧 قضى {target.mention} في القنوات الصوتية **{_hours_label(seconds)}**.")


@bot.hybrid_command(name="balance", description="عرض رصيد العملات")
async def balance_command(ctx: commands.Context) -> None:
    amount = await features.balance(ctx.author.id)
    await ctx.send(f"🪙 رصيدك: **{amount:,}** عملة.")


@bot.hybrid_command(name="trivia", description="ابدأ تحدي احزر الأغنية")
@commands.guild_only()
async def trivia_command(ctx: commands.Context) -> None:
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.send("ادخل القناة الصوتية أولًا لتشغيل المقطع.")
        return
    if ctx.guild.id in _trivia_rounds:
        await ctx.send("هناك جولة تخمين شغالة بالفعل في هذا الخادم.")
        return
    data = await features.random_song(ctx.guild.id)
    if not data:
        await ctx.send("لا توجد أغانٍ مكتملة في السجل بعد. شغّل بعض الأغاني ثم جرّب.")
        return
    title, url = data
    player = await load_player(ctx.guild.id, ctx.channel)
    if (player.current or len(player.queue)
            or (player.voice and player.voice.is_connected()
                and player.voice.channel != voice_state.channel)
            or (player.voice and player.voice.is_playing())):
        await ctx.send("أوقف الموسيقى أو انتظر انتهاء قائمة التشغيل قبل بدء الجولة.")
        return
    await ctx.defer()
    from search import music_search
    try:
        song = await music_search.resolve(url)
    except Exception:
        logger.exception("Could not resolve the selected trivia track.")
        await ctx.send("تعذّر تجهيز مقطع المسابقة من المصدر. حاول مرة أخرى لاحقًا.")
        return
    title = song.title or title
    song.requester = ctx.author.id
    clip_start = random.uniform(0, max(0, song.duration - 12)) if song.duration > 12 else 0
    previous_autoplay, previous_repeat = player.autoplay, player.repeat
    player.autoplay = False
    player.repeat = False
    try:
        started = await player.play_clip(song, voice_state.channel, clip_start)
    except Exception:
        player.autoplay, player.repeat = previous_autoplay, previous_repeat
        await ctx.send("تعذّر تشغيل مقطع المسابقة. تحقق من اتصال البوت الصوتي.")
        logger.exception("Could not start music trivia clip.")
        return
    if not started:
        player.autoplay, player.repeat = previous_autoplay, previous_repeat
        await ctx.send("المشغّل لم يعد خاليًا. أوقف الموسيقى ثم أعد المحاولة.")
        return
    _trivia_rounds[ctx.guild.id] = {
        "answer": title,
        "channel_id": ctx.channel.id,
        "requester_id": ctx.author.id,
        "solved": False,
    }
    await ctx.send(f"🎵 بدأ التحدي! اسمعوا المقطع واكتبوا اسم الأغنية هنا. الجائزة: **{Config.TRIVIA_REWARD}** عملة.")

    async def finish_round() -> None:
        solved = False
        try:
            await asyncio.sleep(12)
            if player.voice and player.voice.is_connected() and player.current is song:
                player.voice.stop()
            await asyncio.sleep(13)
            solved = bool(_trivia_rounds.get(ctx.guild.id, {}).get("solved"))
        except Exception:
            logger.debug("Trivia round ended while the guild was unavailable.", exc_info=True)
        finally:
            _trivia_rounds.pop(ctx.guild.id, None)
            player.autoplay, player.repeat = previous_autoplay, previous_repeat
            await player.save_settings()
            if not solved:
                try:
                    await ctx.send(f"انتهت الجولة. كانت الأغنية **{discord.utils.escape_markdown(title)}**.")
                except discord.HTTPException:
                    pass

    asyncio.create_task(finish_round())


@bot.hybrid_group(name="playlist", description="إدارة قوائمك الموسيقية الخاصة", invoke_without_command=True)
@commands.guild_only()
async def playlist_group(ctx: commands.Context) -> None:
    names = await features.playlist_names(ctx.author.id)
    await ctx.send("قوائمك: " + (", ".join(f"`{name}`" for name in names) if names else "لا توجد قوائم محفوظة."))


@playlist_group.command(name="create", description="إنشاء قائمة تشغيل خاصة")
async def playlist_create_command(ctx: commands.Context, name: str) -> None:
    if not 1 <= len(name.strip()) <= 40:
        await ctx.send("اسم القائمة يجب أن يكون من 1 إلى 40 حرفًا.")
        return
    created = await features.playlist_create(ctx.author.id, name.strip())
    await ctx.send("✅ تم إنشاء القائمة." if created else "لديك قائمة بهذا الاسم بالفعل.")


@playlist_group.command(name="add", description="إضافة أغنية إلى قائمتك")
async def playlist_add_command(ctx: commands.Context, name: str, *, query: str) -> None:
    if len(query) > 500:
        await ctx.send("اختصر اسم الأغنية أو أرسل رابطًا أقصر.")
        return
    await ctx.defer()
    from search import music_search
    try:
        results = await music_search.search(query, limit=1)
    except Exception:
        logger.exception("Playlist song search failed.")
        await ctx.send("تعذّر البحث عن الأغنية.")
        return
    if not results:
        await ctx.send("لم أجد أغنية مطابقة.")
        return
    song = results[0]
    if not await features.playlist_add(ctx.author.id, name, song.title, song.link):
        await ctx.send("لم أجد قائمتك. أنشئها أولًا باستخدام `/playlist create`.")
        return
    await ctx.send(f"✅ أضفت **{discord.utils.escape_markdown(song.title)}** إلى `{name}`.")


@playlist_group.command(name="play", description="تشغيل قائمة محفوظة")
async def playlist_play_command(ctx: commands.Context, name: str) -> None:
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.send("ادخل قناة صوتية أولًا.")
        return
    tracks = await features.playlist_get(ctx.author.id, name)
    if tracks is None:
        await ctx.send("لم أجد قائمة بهذا الاسم.")
        return
    if not tracks:
        await ctx.send("هذه القائمة فارغة.")
        return
    player = await load_player(ctx.guild.id, ctx.channel)
    if player.voice and player.voice.is_connected() and player.voice.channel != voice_state.channel:
        await ctx.send("انضم إلى قناة البوت الصوتية نفسها قبل تشغيل قائمتك.")
        return
    if len(player.queue) + len(tracks) > Config.MAX_QUEUE:
        await ctx.send(f"لا يمكن إضافة القائمة؛ الحد الأقصى لقائمة الانتظار {Config.MAX_QUEUE} أغنية.")
        return
    await ctx.defer()
    for index, (title, url) in enumerate(tracks):
        await player.enqueue(
            Song(title=title, url=url, webpage=url, requester=ctx.author.id),
            voice_state.channel if index == 0 else None,
        )
    await ctx.send(f"▶️ أضفت **{len(tracks)}** أغنية من `{name}` إلى قائمة الانتظار.")


@playlist_group.command(name="list", description="عرض قوائمك")
async def playlist_list_command(ctx: commands.Context) -> None:
    names = await features.playlist_names(ctx.author.id)
    await ctx.send("قوائمك: " + (", ".join(f"`{name}`" for name in names) if names else "لا توجد قوائم محفوظة."))


@playlist_group.command(name="delete", description="حذف قائمة خاصة")
async def playlist_delete_command(ctx: commands.Context, name: str) -> None:
    deleted = await features.playlist_delete(ctx.author.id, name)
    await ctx.send("🗑️ حذفت القائمة." if deleted else "لم أجد قائمة بهذا الاسم.")


@bot.hybrid_command(name="تنظيف", aliases=["clean", "clear"])
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def clean_command(ctx: commands.Context, limit: int = 100) -> None:
    limit = max(1, min(500, limit))
    player = player_manager.players.get(ctx.guild.id)
    panel_id = player.panel_message.id if player and player.panel_message else None
    await ctx.defer()
    deleted = await ctx.channel.purge(
        limit=limit,
        check=lambda message: (
            bot.user is not None
            and message.author.id == bot.user.id
            and message.id != panel_id
        ),
        bulk=False,
    )
    status = await ctx.send(f"🧹 حذفت {len(deleted)} رسالة قديمة للبوت.")
    schedule_cleanup(status, 8)


@bot.hybrid_command(name="مساعدة", aliases=["help", "أوامر"])
@commands.guild_only()
async def help_command(ctx: commands.Context) -> None:
    if not is_music_commands_channel(ctx.channel):
        await _send_status(ctx, "استخدم أمر المساعدة داخل روم أوامر الموسيقى فقط.")
        return
    await ctx.send(embed=music_commands_embed(), view=MusicCommandsView())


@bot.hybrid_command(name="فحص", aliases=["check", "ping"])
async def check_command(ctx: commands.Context) -> None:
    await _send_status(ctx, f"✅ Music S3YD يعمل · {round(bot.latency * 1000)} ms")


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
) -> None:
    if isinstance(error, discord.app_commands.CheckFailure):
        message = "لا تملك الصلاحية المطلوبة لاستخدام هذا الأمر."
    else:
        logger.error("Slash command error: %s", error)
        message = "حدث خطأ أثناء تنفيذ الأمر."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.debug("Could not report a slash command error.", exc_info=True)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingPermissions):
        message = "تحتاج صلاحية إدارة الرسائل لاستخدام هذا الأمر."
        await _send_status(ctx, message)
        return
    if isinstance(error, commands.BotMissingPermissions):
        await _send_status(ctx, "يحتاج البوت صلاحية إدارة الرسائل في هذه القناة.")
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("هذا الأمر يعمل داخل خادم Discord فقط.")
        return
    if isinstance(error, commands.BadArgument):
        await _send_status(ctx, "تحقق من الرقم أو طريقة كتابة الأمر.")
        return
    logger.error("Command error: %s", error)
    await _send_status(ctx, "حدث خطأ أثناء تنفيذ الأمر. راجع سجلات Railway للتفاصيل.")


if __name__ == "__main__":
    if not Config.TOKEN:
        raise RuntimeError("DISCORD_TOKEN غير موجود في متغيرات البيئة.")
    bot.run(Config.TOKEN)
