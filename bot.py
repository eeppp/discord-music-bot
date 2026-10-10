from __future__ import annotations

import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

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
from effects import EFFECT_NAMES, VALID_EFFECTS, normalize_effect
from player import player_manager
from permissions import DJ_DENIED_MESSAGE, has_dj_access

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
_database_ready = False
_commands_synced = False


@bot.event
async def on_ready() -> None:
    global _database_ready, _commands_synced
    if not _database_ready:
        await database.initialize()
        player_manager.configure(bot, database)
        await restore_panels(bot)
        _database_ready = True

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


async def _send_status(
    ctx: commands.Context,
    text: str,
    *,
    embed: discord.Embed | None = None,
    delay: int | None = None,
) -> discord.Message:
    message = await ctx.reply(
        content=text or None,
        embed=embed,
        mention_author=False,
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


async def _require_dj(ctx: commands.Context, player) -> bool:
    if has_dj_access(ctx.author, player.dj_role):
        return True
    await _send_status(ctx, DJ_DENIED_MESSAGE)
    return False


@bot.hybrid_command(name="شغل", aliases=["play", "تشغيل"])
@commands.guild_only()
async def play_command(ctx: commands.Context, *, query: str | None = None) -> None:
    await play_query(ctx, query)


@bot.hybrid_command(name="صوت", aliases=["volume", "vol"])
@commands.guild_only()
async def volume_command(ctx: commands.Context, volume: int | None = None) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not await _require_dj(ctx, player):
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
    if not await _require_dj(ctx, player):
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
    if not await _require_dj(ctx, player):
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
    if not await _require_dj(ctx, player):
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
    message = await ctx.reply(embed=queue_embed(player), mention_author=False)
    schedule_cleanup(message)


@bot.hybrid_command(name="إيقاف", aliases=["stop", "اطفاء", "إطفاء"])
@commands.guild_only()
async def stop_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not await _require_dj(ctx, player):
        return
    await player.stop(disconnect=True)
    await _send_status(ctx, "⏹️ توقف البوت ومسح قائمة الانتظار.")


@bot.hybrid_command(name="وقف", aliases=["pause", "مؤقت"])
@commands.guild_only()
async def pause_command(ctx: commands.Context) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not await _require_dj(ctx, player):
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
    if not await _require_dj(ctx, player):
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
    if not await _require_dj(ctx, player):
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
    await ensure_panel(player, ctx.channel)
    await _send_status(ctx, f"🎚️ Bass Boost مضبوط على {player.bass}/10، ويطبّق على الأغنية التالية.")


@bot.hybrid_command(name="تأثير", description="اختيار المؤثر الصوتي")
@commands.guild_only()
async def effect_command(
    ctx: commands.Context,
    effect: str | None = None,
) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not await _require_dj(ctx, player):
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
    await ensure_panel(player, ctx.channel)
    await _send_status(
        ctx,
        f"🎛️ تم اختيار مؤثر {EFFECT_NAMES[normalized]} للأغنية التالية.",
    )


@bot.hybrid_command(name="تلقائي", aliases=["autoplay", "راديو"])
@commands.guild_only()
async def autoplay_command(ctx: commands.Context, mode: str | None = None) -> None:
    player = await _voice_player(ctx)
    if not player:
        return
    if not await _require_dj(ctx, player):
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


@bot.hybrid_command(name="تنظيف", aliases=["clean", "clear"])
@commands.guild_only()
@commands.has_permissions(manage_messages=True)
@commands.bot_has_permissions(manage_messages=True)
async def clean_command(ctx: commands.Context, limit: int = 100) -> None:
    limit = max(1, min(500, limit))
    player = player_manager.players.get(ctx.guild.id)
    panel_id = player.panel_message.id if player and player.panel_message else None
    deleted = await ctx.channel.purge(
        limit=limit,
        check=lambda message: (
            bot.user is not None
            and message.author.id == bot.user.id
            and message.id != panel_id
        ),
        bulk=True,
    )
    status = await ctx.send(f"🧹 حذفت {len(deleted)} رسالة قديمة للبوت.")
    schedule_cleanup(status, 8)


@bot.hybrid_command(name="تعيين-دي-جي", description="تعيين دور DJ أو الرجوع إلى الدور الافتراضي")
@commands.guild_only()
@commands.has_guild_permissions(manage_guild=True)
async def set_dj_role_command(
    ctx: commands.Context,
    role: discord.Role | None = None,
) -> None:
    player = await load_player(ctx.guild.id, ctx.channel)
    player.dj_role = str(role.id) if role else Config.DJ_ROLE
    await player.save_settings()
    if role:
        await _send_status(ctx, f"🎧 تم تعيين {role.mention} كدور DJ.")
    else:
        await _send_status(ctx, f"🎧 عاد دور DJ إلى الإعداد الافتراضي: {Config.DJ_ROLE}.")


@bot.hybrid_command(name="مساعدة", aliases=["help", "أوامر"])
@commands.guild_only()
async def help_command(ctx: commands.Context) -> None:
    prefix = Config.PREFIX
    embed = discord.Embed(
        title="🎵 أوامر Music S3YD",
        description=(
            f"{prefix}شغل اسم الأغنية أو الرابط — بحث وتشغيل YouTube/SoundCloud\n"
            f"{prefix}صوت 0-200 — ضبط الصوت\n"
            f"{prefix}تخطي — تخطي الأغنية الحالية\n"
            f"{prefix}تكرار — تشغيل/إيقاف تكرار الأغنية الحالية\n"
            f"{prefix}خلط — خلط قائمة الانتظار\n"
            f"{prefix}قائمة — عرض قائمة الانتظار\n"
            f"{prefix}وقف — إيقاف مؤقت أو استئناف\n"
            f"{prefix}كمل — استئناف التشغيل\n"
            f"{prefix}باس 0-10 — ضبط تعزيز الجهير\n"
            f"{prefix}تلقائي — تشغيل/إيقاف Autoplay\n"
            f"{prefix}إيقاف — إيقاف البوت ومسح الانتظار\n"
            f"{prefix}تأثير — افتح قائمة المؤثرات من لوحة المشغّل\n"
            f"{prefix}تعيين-دي-جي @دور — تعيين دور DJ (لإدارة الخادم)\n"
            f"{prefix}تنظيف — حذف رسائل البوت القديمة\n"
            "الأوامر تظهر أيضًا كأوامر Slash بعد مزامنتها."
        ),
        color=Config.EMBED_COLOR,
    )
    message = await ctx.reply(embed=embed, mention_author=False)
    schedule_cleanup(message, 60)


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
        await _send_status(ctx, "تحتاج صلاحية إدارة الرسائل لاستخدام هذا الأمر.")
        return
    if isinstance(error, commands.BotMissingPermissions):
        await _send_status(ctx, "يحتاج البوت صلاحية إدارة الرسائل في هذه القناة.")
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.reply("هذا الأمر يعمل داخل خادم Discord فقط.", mention_author=False)
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
