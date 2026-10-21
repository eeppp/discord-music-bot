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
    name = name.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"}))
    return name in {
        "اوامرميوزك",
        "اوامرالميوزك",
        "اوامرمusic",
        "musiccommands",
        "musiccommand",
    } or ("اوامر" in name and "music" in name)


MUSIC_COMMANDS = (
    ("شغل", "تشغيل أغنية بالاسم أو الرابط", ""),
    ("ص", "ضبط الصوت بسرعة من 0 إلى 200", " 0-200"),
    ("صوت", "ضبط مستوى الصوت", " 0-200"),
    ("تخطي", "تخطي الأغنية الحالية", ""),
    ("تكرار", "تشغيل أو إيقاف التكرار", ""),
    ("خلط", "خلط قائمة الانتظار", ""),
    ("قائمة", "عرض قائمة الانتظار", ""),
    ("وقف", "إيقاف مؤقت أو استئناف", ""),
    ("كمل", "استئناف التشغيل", ""),
    ("باس", "ضبط تعزيز الجهير من 0 إلى 10", " 0-10"),
    ("تلقائي", "تشغيل أو إيقاف Autoplay", ""),
    ("إيقاف", "إيقاف البوت ومسح الانتظار", ""),
    ("تأثير", "اختيار مؤثر من لوحة المشغّل", ""),
    ("hours", "عرض ساعاتك الصوتية", ""),
    ("balance", "عرض رصيد العملات", ""),
    ("trivia", "بدء تحدي احزر الأغنية", ""),
    ("playlist", "عرض قوائمك الخاصة", ""),
    ("مساعدة", "عرض قائمة الأوامر", ""),
)


class CommandCopyButton(discord.ui.Button):
    def __init__(self, command_name: str, usage: str, index: int) -> None:
        self.command = f"{Config.PREFIX}{command_name}{usage}"
        super().__init__(
            label=f"{Config.PREFIX}{command_name}",
            style=discord.ButtonStyle.secondary,
            custom_id=f"music:copy_command:{index}",
            row=index // 5,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not is_music_commands_channel(interaction.channel):
            await interaction.response.send_message(
                "هذا الزر يعمل فقط داخل روم أوامر الموسيقى.", ephemeral=False
            )
            return
        await interaction.response.send_message(
            f"`{self.command}`", ephemeral=False
        )


class MusicCommandsView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        for index, (command_name, _, usage) in enumerate(MUSIC_COMMANDS):
            self.add_item(CommandCopyButton(command_name, usage, index))


def music_commands_embed() -> discord.Embed:
    prefix = Config.PREFIX
    commands_by_name = {
        command_name: (description, usage)
        for command_name, description, usage in MUSIC_COMMANDS
    }
    sections = (
        ("🎵 تشغيل وتحكم", ("شغل", "تخطي", "قائمة", "وقف", "كمل", "تكرار", "خلط", "إيقاف")),
        ("🔊 الصوت والتأثيرات", ("ص", "صوت", "باس", "تأثير", "تلقائي")),
        ("👥 التواجد والعملات", ("hours", "balance", "trivia")),
        ("📚 قوائم التشغيل", ("playlist",)),
        ("🛠️ المساعدة", ("مساعدة",)),
    )

    embed = discord.Embed(
        title="🎶 لوحة Music S3YD",
        description=(
            "أوامر الموسيقى والتواجد في مكان واحد.\n"
            "الأوامر تعمل بصيغة `/` أو بالبادئة `" + prefix + "`. اضغط زرًا لنسخ الأمر."
        ),
        color=Config.EMBED_COLOR,
    )
    for title, command_names in sections:
        lines = []
        for command_name in command_names:
            description, usage = commands_by_name[command_name]
            example = usage or (" <اسم أو رابط>" if command_name == "شغل" else "")
            lines.append(f"\u200f• {description} — `{prefix}{command_name}{example}`")
        if title == "📚 قوائم التشغيل":
            lines.extend(
                (
                    f"\u200f• إنشاء — `{prefix}playlist create <اسم>`",
                    f"\u200f• إضافة — `{prefix}playlist add <القائمة> <أغنية>`",
                    f"\u200f• تشغيل — `{prefix}playlist play <اسم>`",
                    f"\u200f• عرض أو حذف — `{prefix}playlist list` / `{prefix}playlist delete <اسم>`",
                )
            )
        embed.add_field(name=title, value="\n".join(lines), inline=False)

    embed.set_footer(text="Music S3YD · يتم تحديث هذه اللوحة تلقائيًا مع كل إصدار")
    return embed


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
                ephemeral=False,
            )
            return False
        return True

    @discord.ui.button(label="إيقاف مؤقت", emoji="⏯️", style=discord.ButtonStyle.primary, custom_id="music:pause", row=0)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        if self.player.voice and self.player.voice.is_paused():
            changed = await self.player.resume()
            message = "▶️ استؤنف التشغيل." if changed else "لا يوجد تشغيل متوقف مؤقتًا."
        else:
            changed = await self.player.pause()
            message = "⏸️ تم إيقاف التشغيل مؤقتًا." if changed else "لا توجد أغنية تعمل الآن."
        await self.player._refresh_panel()
        await interaction.followup.send(message, ephemeral=False)

    @discord.ui.button(label="تخطي", emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="music:skip", row=0)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        await self.player.skip()
        await interaction.followup.send("تم تخطي الأغنية.", ephemeral=False)

    @discord.ui.button(label="إيقاف", emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="music:stop", row=0)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        await self.player.stop()
        await interaction.followup.send("توقف التشغيل وتم مسح قائمة الانتظار.", ephemeral=False)

    @discord.ui.button(label="خلط", emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="music:shuffle", row=0)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        self.player.queue.shuffle()
        self.player.shuffle = True
        await self.player._refresh_panel()
        await interaction.followup.send("تم خلط قائمة الانتظار.", ephemeral=False)

    @discord.ui.button(label="تكرار", emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="music:repeat", row=0)
    async def repeat_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        self.player.repeat = not self.player.repeat
        await self.player.save_settings()
        await self.player._refresh_panel()
        status = "مفعّل" if self.player.repeat else "متوقف"
        await interaction.followup.send(f"التكرار: {status}.", ephemeral=False)

    @discord.ui.button(label="التشغيل التلقائي", emoji="📻", style=discord.ButtonStyle.secondary, custom_id="music:autoplay", row=1)
    async def autoplay_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=False)
        self.player.autoplay = not self.player.autoplay
        await self.player.save_settings()
        await self.player._refresh_panel()
        status = "مفعّل" if self.player.autoplay else "متوقف"
        await interaction.followup.send(f"التشغيل التلقائي: {status}.", ephemeral=False)

    @discord.ui.button(label="قائمة الانتظار", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="music:queue", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from controls import queue_embed

        await interaction.response.send_message(
            embed=queue_embed(self.player), ephemeral=False
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
        await interaction.response.defer(ephemeral=False)
        self.player.set_volume(int(self.values[0]))
        await self.player.save_settings()
        await self.player._refresh_panel()
        await interaction.followup.send(
            f"تم ضبط الصوت على {self.player.volume}%.", ephemeral=False
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
        await interaction.response.defer(ephemeral=False)
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
            ephemeral=False,
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
        await interaction.response.defer(ephemeral=False)
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
            ephemeral=False,
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
                "هذه النتائج تخص الشخص الذي بدأ البحث.", ephemeral=False
            )
            return
        song = self.songs[int(self.values[0])]
        await interaction.response.defer(ephemeral=False)
        from controls import add_selected_song

        await add_selected_song(interaction, song)


class SearchResultsView(discord.ui.View):
    def __init__(self, songs: list[Song], requester_id: int) -> None:
        super().__init__(timeout=120)
        self.requester_id = requester_id
        self.add_item(SongSelect(songs))
