from __future__ import annotations

import re

import discord

from config import Config
from player import MusicPlayer, Song


def is_music_commands_channel(channel: object) -> bool:
    """Limit the help panel to the configured channel, or its known names."""
    configured_id = Config.MUSIC_COMMAND_CHANNEL_ID
    if configured_id:
        return str(getattr(channel, "id", "")) == configured_id
    name = re.sub(r"[\W_]+", "", str(getattr(channel, "name", "")).casefold())
    return name in {"اوامرميوزك", "اوامرالميوزك", "musiccommands", "musiccommand"}


COMMANDS_COPY_TEXT = """{prefix}شغل اسم الأغنية أو الرابط — بحث وتشغيل
{prefix}ص 0-200 — ضبط الصوت بسرعة
{prefix}صوت 0-200 — ضبط الصوت
{prefix}تخطي — تخطي الأغنية الحالية
{prefix}تكرار — تشغيل أو إيقاف التكرار
{prefix}خلط — خلط قائمة الانتظار
{prefix}قائمة — عرض قائمة الانتظار
{prefix}وقف — إيقاف مؤقت أو استئناف
{prefix}كمل — استئناف التشغيل
{prefix}باس 0-10 — ضبط تعزيز الجهير
{prefix}تلقائي — تشغيل أو إيقاف التشغيل التلقائي
{prefix}إيقاف — إيقاف البوت ومسح الانتظار
{prefix}تأثير — اختيار مؤثر من لوحة المشغّل
{prefix}مساعدة — عرض هذه الرسالة"""


class MusicCommandsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=60)

    @discord.ui.button(label="نسخ الأوامر", emoji="📋", style=discord.ButtonStyle.primary)
    async def copy_commands(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not is_music_commands_channel(interaction.channel):
            await interaction.response.send_message(
                "هذا الزر يعمل فقط داخل روم أوامر الموسيقى.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"```text\n{COMMANDS_COPY_TEXT.format(prefix=Config.PREFIX)}\n```",
            ephemeral=True,
        )


def _duration(seconds: int) -> str:
    if seconds <= 0:
        return "مباشر"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


class PlayerControls(discord.ui.View):
    def __init__(self, player: MusicPlayer) -> None:
        super().__init__(timeout=None)
        self.player = player
        self.add_item(VolumeSelect(player))
        self.add_item(BassSelect(player))
        self.add_item(EffectSelect(player))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        voice = getattr(interaction.user, "voice", None)
        if not self.player.voice or not voice or voice.channel != self.player.voice.channel:
            await interaction.response.send_message(
                "انضم إلى القناة الصوتية نفسها التي يوجد فيها البوت للتحكم بالمشغّل.",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="إيقاف مؤقت", emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="music:pause", row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        if self.player.voice and self.player.voice.is_paused():
            changed = await self.player.resume()
            message = "▶️ استؤنف التشغيل." if changed else "لا يوجد تشغيل متوقف مؤقتًا."
        else:
            changed = await self.player.pause()
            message = "⏸️ تم إيقاف التشغيل مؤقتًا." if changed else "لا توجد أغنية تعمل الآن."
        await self.player._refresh_panel()
        await interaction.followup.send(message, ephemeral=True)

    @discord.ui.button(label="تخطي", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip", row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.player.skip()
        await interaction.followup.send("تم تخطي الأغنية.", ephemeral=True)

    @discord.ui.button(label="إيقاف", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.player.stop()
        await interaction.followup.send("توقف التشغيل وتم مسح قائمة الانتظار.", ephemeral=True)

    @discord.ui.button(label="خلط", emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music:shuffle", row=0)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        self.player.queue.shuffle()
        self.player.shuffle = True
        await self.player._refresh_panel()
        await interaction.followup.send("تم خلط قائمة الانتظار.", ephemeral=True)

    @discord.ui.button(label="تكرار", emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music:repeat", row=0)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        self.player.repeat = not self.player.repeat
        await self.player.save_settings()
        await self.player._refresh_panel()
        status = "مفعّل" if self.player.repeat else "متوقف"
        await interaction.followup.send(f"التكرار: {status}.", ephemeral=True)

    @discord.ui.button(label="التشغيل التلقائي", emoji="📻", style=discord.ButtonStyle.secondary, custom_id="music:autoplay", row=1)
    async def autoplay_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        self.player.autoplay = not self.player.autoplay
        await self.player.save_settings()
        await self.player._refresh_panel()
        status = "مفعّل" if self.player.autoplay else "متوقف"
        await interaction.followup.send(f"التشغيل التلقائي: {status}.", ephemeral=True)

    @discord.ui.button(label="قائمة الانتظار", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="music:queue", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from controls import queue_embed

        await interaction.response.send_message(
            embed=queue_embed(self.player), ephemeral=True
        )


class VolumeSelect(discord.ui.Select):
    def __init__(self, player: MusicPlayer) -> None:
        self.player = player
        options = [
            discord.SelectOption(label=f"{value}%", value=str(value),
                                 description="مستوى الصوت")
            for value in (0, 40, 60, 80, 100, 120, 150, 200)
        ]
        super().__init__(
            placeholder="🔊 مستوى الصوت (0–200%)",
            custom_id="music:volume",
            min_values=1,
            max_values=1,
            options=options,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.view or not await self.view.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        self.player.set_volume(int(self.values[0]))
        await self.player.save_settings()
        await self.player._refresh_panel()
        await interaction.followup.send(
            f"تم ضبط الصوت على {self.player.volume}%.", ephemeral=True
        )


class BassSelect(discord.ui.Select):
    def __init__(self, player: MusicPlayer) -> None:
        self.player = player
        options = [
            discord.SelectOption(label=f"Bass {value}", value=str(value))
            for value in (0, 2, 4, 6, 8, 10)
        ]
        super().__init__(
            placeholder="🎚️ تعزيز الجهير",
            custom_id="music:bass",
            min_values=1,
            max_values=1,
            options=options,
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.view or not await self.view.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        self.player.set_bass(int(self.values[0]))
        await self.player.save_settings()
        applied = await self.player.apply_effects_to_current()
        await self.player._refresh_panel()
        effect_status = (
            "وطُبّق على الأغنية الحالية من موضعها."
            if applied
            else "سيُطبّق على الأغنية التالية."
        )
        await interaction.followup.send(
            f"تم ضبط Bass Boost على {self.player.bass}/10، {effect_status}",
            ephemeral=True,
        )


class EffectSelect(discord.ui.Select):
    def __init__(self, player: MusicPlayer) -> None:
        self.player = player
        options = [
            discord.SelectOption(label="عادي", value="normal"),
            discord.SelectOption(label="Bass Boost", value="bass"),
            discord.SelectOption(label="Treble", value="treble"),
            discord.SelectOption(label="Nightcore", value="nightcore"),
            discord.SelectOption(label="8D", value="8d"),
        ]
        super().__init__(
            placeholder="🎛️ اختر المؤثر الصوتي",
            custom_id="music:effect",
            min_values=1,
            max_values=1,
            options=options,
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.view or not await self.view.interaction_check(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        self.player.set_effect(self.values[0])
        await self.player.save_settings()
        applied = await self.player.apply_effects_to_current()
        await self.player._refresh_panel()
        effect_status = (
            "وطُبّق على الأغنية الحالية من موضعها."
            if applied
            else "سيُطبّق على الأغنية التالية."
        )
        await interaction.followup.send(
            f"تم اختيار المؤثر {effect_status}",
            ephemeral=True,
        )


class SongSelect(discord.ui.Select):
    def __init__(self, songs: list[Song]) -> None:
        self.songs = songs
        options = []
        for index, song in enumerate(songs[:5]):
            source = song.source or "نتيجة بحث"
            description = f"{source} • {_duration(song.duration)}"
            options.append(
                discord.SelectOption(
                    label=song.title[:100],
                    value=str(index),
                    description=description[:100],
                )
            )
        super().__init__(
            placeholder="اختر أغنية من أفضل 5 نتائج",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SearchResultsView):
            return
        if interaction.user.id != view.requester_id:
            await interaction.response.send_message(
                "هذه النتائج تخص الشخص الذي بدأ البحث.", ephemeral=True
            )
            return
        song = self.songs[int(self.values[0])]
        await interaction.response.defer(ephemeral=True)
        from controls import add_selected_song

        await add_selected_song(interaction, song)


class SearchResultsView(discord.ui.View):
    def __init__(self, songs: list[Song], requester_id: int) -> None:
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(SongSelect(songs))
