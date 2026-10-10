from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from config import Config
from player import MusicPlayer, Song, player_manager
from search import music_search
from views import PlayerControls, SearchResultsView

logger = logging.getLogger("MusicS3YD.controls")


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "مباشر"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


def panel_view(player: MusicPlayer) -> PlayerControls:
    if player.control_view is None or player.control_view.is_finished():
        player.control_view = PlayerControls(player)
    return player.control_view


def player_embed(player: MusicPlayer) -> discord.Embed:
    embed = discord.Embed(title="🎵 مشغّل Music S3YD", color=Config.EMBED_COLOR)
    if player.current:
        title = discord.utils.escape_markdown(player.current.title)
        embed.description = f"**يعمل الآن**\n[{title}]({player.current.link})"
        embed.add_field(
            name="المدة",
            value=format_duration(player.current.duration),
            inline=True,
        )
        if player.current.thumbnail:
            embed.set_thumbnail(url=player.current.thumbnail)
    else:
        embed.description = "لا توجد أغنية تعمل الآن. اكتب أمر شغل للبحث والتشغيل."
    if player.voice and player.voice.is_paused():
        state = "⏸️ متوقف مؤقتًا"
    elif player.current:
        state = "▶️ يعمل"
    else:
        state = "⏹️ متوقف"
    embed.add_field(name="الحالة", value=state, inline=True)
    embed.add_field(name="الصوت", value=f"{player.volume}%", inline=True)
    embed.add_field(name="Bass Boost", value=f"{player.bass}/10", inline=True)
    embed.add_field(name="الانتظار", value=f"{len(player.queue)} أغنية", inline=True)
    embed.add_field(name="التكرار", value="مفعّل" if player.repeat else "متوقف", inline=True)
    embed.add_field(name="Autoplay", value="مفعّل" if player.autoplay else "متوقف", inline=True)
    embed.set_footer(text="استخدم الأزرار والقوائم لضبط المشغّل من الجوال.")
    return embed


def queue_embed(player: MusicPlayer) -> discord.Embed:
    embed = discord.Embed(title="📜 قائمة الانتظار", color=Config.EMBED_COLOR)
    lines = []
    if player.current:
        lines.append(
            f"**الآن:** [{discord.utils.escape_markdown(player.current.title)}]({player.current.link})"
        )
    queued = player.queue.snapshot(10)
    for index, song in enumerate(queued, start=1):
        title = discord.utils.escape_markdown(song.title)
        lines.append(f"{index}. [{title}]({song.link}) · {format_duration(song.duration)}")
    if len(player.queue) > len(queued):
        lines.append(f"… و{len(player.queue) - len(queued)} أغنية أخرى.")
    embed.description = "\n".join(lines) if lines else "قائمة الانتظار فارغة."
    return embed


async def update_panel(player: MusicPlayer) -> None:
    if not player.panel_message:
        return
    try:
        await player.panel_message.edit(
            embed=player_embed(player),
            view=panel_view(player),
        )
    except discord.NotFound:
        player.panel_message = None
        try:
            await player.database.delete_panel(player.guild_id)
        except Exception:
            logger.debug("Could not remove a deleted panel record.", exc_info=True)
    except discord.HTTPException:
        logger.exception("Could not update the player panel.")


async def ensure_panel(player: MusicPlayer, channel: discord.abc.Messageable) -> None:
    player.text_channel = channel
    if player.panel_message:
        await update_panel(player)
        if player.panel_message:
            return
    player.control_view = panel_view(player)
    player.panel_message = await channel.send(
        embed=player_embed(player),
        view=player.control_view,
    )
    if player.panel_message.guild:
        await player.database.save_panel(
            player.guild_id, player.panel_message.channel.id, player.panel_message.id
        )


async def load_player(guild_id: int, channel: discord.abc.Messageable) -> MusicPlayer:
    player = player_manager.get(guild_id)
    if not player._settings_loaded:
        await player.load_settings()
    player.text_channel = channel
    return player


async def _delete_after(message: discord.Message, delay: int) -> None:
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


def schedule_cleanup(message: discord.Message, delay: int | None = None) -> None:
    asyncio.create_task(_delete_after(message, delay or Config.DELETE_DELAY))


async def play_query(ctx: commands.Context, query: str | None) -> None:
    voice_state = getattr(ctx.author, "voice", None)
    if not voice_state or not voice_state.channel:
        await ctx.reply("🔊 ادخل قناة صوتية أولًا، ثم أرسل أمر التشغيل.", mention_author=False)
        return

    player = await load_player(ctx.guild.id, ctx.channel)
    if len(player.queue) >= Config.MAX_QUEUE:
        await ctx.reply("قائمة الانتظار ممتلئة. ابدأ تشغيلها أو أفرغ جزءًا منها أولًا.", mention_author=False)
        return
    if not query:
        if player.current:
            await ensure_panel(player, ctx.channel)
        else:
            await ctx.reply("اكتب اسم أغنية أو رابط YouTube/SoundCloud بعد أمر شغل.", mention_author=False)
        return

    try:
        songs = await music_search.search(query, limit=Config.SEARCH_RESULTS)
    except Exception as exc:
        logger.exception("Search failed.")
        await ctx.reply(f"تعذّر البحث عن الأغنية: {exc}", mention_author=False)
        return

    if not songs:
        await ctx.reply("ما لقيت نتائج. جرّب اسمًا آخر أو رابطًا مباشرًا.", mention_author=False)
        return

    requester_id = ctx.author.id
    for song in songs:
        song.requester = requester_id

    if music_search._is_url(query):
        song = songs[0]
        await ensure_panel(player, ctx.channel)
        await player.enqueue(song, voice_state.channel)
        notice = await ctx.reply(
            f"➕ أُضيفت **{discord.utils.escape_markdown(song.title)}** إلى المشغّل.",
            mention_author=False,
        )
        schedule_cleanup(notice)
        return

    embed = discord.Embed(
        title="🔎 أفضل 5 نتائج",
        description=(
            f"اختر الأغنية التي تريدها من القائمة أدناه:\n"
            f"**{discord.utils.escape_markdown(query)[:180]}**"
        ),
        color=Config.EMBED_COLOR,
    )
    for index, song in enumerate(songs[:5], start=1):
        embed.add_field(
            name=f"{index}. {discord.utils.escape_markdown(song.title)[:240]}",
            value=f"{song.source or 'نتيجة'} · {format_duration(song.duration)}",
            inline=False,
        )
    result_message = await ctx.reply(
        embed=embed,
        view=SearchResultsView(songs, requester_id),
        mention_author=False,
    )
    schedule_cleanup(result_message, 120)


async def add_selected_song(
    interaction: discord.Interaction, song: Song
) -> None:
    guild = interaction.guild
    voice_state = getattr(interaction.user, "voice", None)
    if not guild or not voice_state or not voice_state.channel:
        await interaction.followup.send(
            "ادخل قناة صوتية أولًا ثم اختر الأغنية مجددًا.",
            ephemeral=True,
        )
        return

    player = await load_player(guild.id, interaction.channel)
    if len(player.queue) >= Config.MAX_QUEUE:
        await interaction.followup.send("قائمة الانتظار ممتلئة حاليًا.", ephemeral=True)
        return
    song.requester = interaction.user.id
    await ensure_panel(player, interaction.channel)
    await player.enqueue(song, voice_state.channel)
    await interaction.followup.send(
        f"أُضيفت **{discord.utils.escape_markdown(song.title)}** إلى قائمة التشغيل.",
        ephemeral=True,
    )


async def restore_panels(bot: commands.Bot) -> None:
    """Restore persistent controls for previously-created player panels."""
    for guild_id, channel_id, message_id in await player_manager.database.get_panels():
        try:
            channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
            message = await channel.fetch_message(message_id)
            player = await load_player(guild_id, channel)
            player.panel_message = message
            player.control_view = panel_view(player)
            bot.add_view(player.control_view, message_id=message_id)
            await update_panel(player)
        except discord.NotFound:
            await player_manager.database.delete_panel(guild_id)
            logger.info("Removed missing player panel %s.", message_id)
        except (discord.Forbidden, discord.HTTPException, AttributeError):
            logger.warning("Could not restore player panel %s.", message_id, exc_info=True)

