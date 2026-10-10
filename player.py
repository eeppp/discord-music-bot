from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import discord
import yt_dlp

from config import Config
from effects import clamp_bass, ffmpeg_options
from modules.queue import SongQueue

logger = logging.getLogger("MusicS3YD.player")


@dataclass(slots=True)
class Song:
    title: str
    url: str
    webpage: str | None = None
    duration: int = 0
    requester: int | None = None
    thumbnail: str | None = None
    source: str = ""

    @property
    def link(self) -> str:
        return self.webpage or self.url


class MusicPlayer:
    def __init__(self, guild_id: int, bot: Any, database: Any) -> None:
        self.guild_id = guild_id
        self.bot = bot
        self.database = database
        self.queue = SongQueue()
        self.current: Song | None = None
        self.volume = Config.DEFAULT_VOLUME
        self.bass = 0
        self.repeat = False
        self.shuffle = False
        self.autoplay = Config.AUTO_PLAY
        self.voice: discord.VoiceClient | None = None
        self.panel_message: discord.Message | None = None
        self.control_view: discord.ui.View | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self._advance_lock = asyncio.Lock()
        self._skip_repeat = False
        self._settings_loaded = False

    async def load_settings(self) -> None:
        settings = await self.database.get_settings(self.guild_id)
        self.volume = max(0, min(200, int(settings.get("volume", Config.DEFAULT_VOLUME))))
        self.bass = clamp_bass(settings.get("bass", 0))
        self.autoplay = bool(settings.get("autoplay", int(Config.AUTO_PLAY)))
        self.repeat = bool(settings.get("repeat", 0))
        self._settings_loaded = True

    async def save_settings(self) -> None:
        await self.database.save_settings(
            self.guild_id,
            volume=self.volume,
            bass=self.bass,
            autoplay=int(self.autoplay),
            repeat=int(self.repeat),
        )

    async def connect(self, channel: discord.abc.GuildChannel) -> discord.VoiceClient:
        guild = channel.guild
        voice = guild.voice_client
        if voice and voice.is_connected():
            if voice.channel != channel:
                await voice.move_to(channel)
        else:
            voice = await channel.connect()
        self.voice = voice
        return voice

    async def enqueue(self, song: Song, channel: discord.abc.GuildChannel | None = None) -> None:
        if channel is not None:
            await self.connect(channel)
        self.queue.add(song)
        if not self.current and self.voice and not self.voice.is_playing() and not self.voice.is_paused():
            await self.advance()

    async def _stream_url(self, song: Song) -> str:
        def extract(url: str, use_fallback: bool = False) -> str:
            options: dict[str, Any] = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
            }
            if not use_fallback:
                options["format"] = "bestaudio/best"
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if isinstance(info, dict) and info.get("entries"):
                info = next((item for item in info["entries"] if item), None)
            if not isinstance(info, dict) or not info.get("url"):
                raise ValueError("لم أجد رابط بث صوتي صالحًا.")
            return str(info["url"])

        try:
            return await asyncio.to_thread(extract, song.link)
        except Exception as first_error:
            try:
                return await asyncio.to_thread(extract, song.link, True)
            except Exception:
                raise first_error

    async def _start_song(self, song: Song) -> None:
        if not self.voice or not self.voice.is_connected():
            logger.warning("Cannot start playback without a connected voice client.")
            return
        try:
            stream = await self._stream_url(song)
            source = discord.FFmpegPCMAudio(stream, **ffmpeg_options(self.bass))
            source = discord.PCMVolumeTransformer(source, volume=self.volume / 100)
            loop = asyncio.get_running_loop()

            def after(error: Exception | None) -> None:
                if error:
                    logger.error("Audio playback failed in guild %s: %s", self.guild_id, error)
                loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._after_track())
                )

            self.voice.play(source, after=after)
            await self._refresh_panel()
        except Exception as exc:
            logger.exception("Could not play %s: %s", song.title, exc)
            if self.text_channel:
                try:
                    await self.text_channel.send(
                        f"⚠️ تعذّر تشغيل **{discord.utils.escape_markdown(song.title)}**. "
                        "سأتجاوزها وأحاول تشغيل التالية."
                    )
                except discord.HTTPException:
                    pass
            await self.advance(force=True)

    async def _after_track(self) -> None:
        if self.current:
            try:
                await self.database.add_history(
                    self.guild_id, self.current.title, self.current.link
                )
            except Exception:
                logger.exception("Could not store playback history.")
        await self.advance()

    async def advance(self, force: bool = False) -> None:
        async with self._advance_lock:
            if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
                return
            if self._skip_repeat:
                force = True
                self._skip_repeat = False

            if self.current and self.repeat and not force:
                next_song = self.current
            else:
                next_song = self.queue.pop()

            if next_song is None and self.current and self.autoplay:
                try:
                    from search import music_search

                    candidates = await music_search.search(
                        f"{self.current.title} similar songs", limit=5
                    )
                    next_song = next(
                        (candidate for candidate in candidates
                         if candidate.link != self.current.link),
                        None,
                    )
                except Exception:
                    logger.exception("Autoplay search failed.")

            if next_song is None:
                self.current = None
                await self._refresh_panel()
                return

            self.current = next_song

        await self._start_song(next_song)

    async def skip(self) -> None:
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self._skip_repeat = True
            self.voice.stop()
        else:
            await self.advance(force=True)

    async def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self.voice.pause()
            await self._refresh_panel()
            return True
        return False

    async def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            await self._refresh_panel()
            return True
        return False

    async def stop(self, disconnect: bool = False) -> None:
        self.queue.clear()
        self.current = None
        self._skip_repeat = True
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self.voice.stop()
        if disconnect and self.voice and self.voice.is_connected():
            await self.voice.disconnect()
            self.voice = None
        await self._refresh_panel()

    def set_volume(self, value: int) -> int:
        self.volume = max(0, min(200, int(value)))
        if self.voice and isinstance(self.voice.source, discord.PCMVolumeTransformer):
            self.voice.source.volume = self.volume / 100
        return self.volume

    def set_bass(self, value: int) -> int:
        self.bass = clamp_bass(value)
        return self.bass

    async def _refresh_panel(self) -> None:
        try:
            from controls import update_panel

            await update_panel(self)
        except Exception:
            logger.debug("Panel refresh skipped.", exc_info=True)


class PlayerManager:
    def __init__(self) -> None:
        self.players: dict[int, MusicPlayer] = {}
        self.bot: Any = None
        self.database: Any = None

    def configure(self, bot: Any, database: Any) -> None:
        self.bot = bot
        self.database = database

    def get(self, guild_id: int) -> MusicPlayer:
        player = self.players.get(guild_id)
        if player is None:
            if self.bot is None or self.database is None:
                raise RuntimeError("PlayerManager must be configured before use.")
            player = MusicPlayer(guild_id, self.bot, self.database)
            self.players[guild_id] = player
        return player

    def remove(self, guild_id: int) -> None:
        self.players.pop(guild_id, None)


player_manager = PlayerManager()
