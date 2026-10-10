from __future__ import annotations

import asyncio
import atexit
import base64
import binascii
import logging
import os
import shlex
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import discord
import yt_dlp

from config import Config
from effects import clamp_bass, ffmpeg_options, normalize_effect
from modules.queue import SongQueue

logger = logging.getLogger("MusicS3YD.player")
_youtube_cookie_lock = threading.Lock()
_youtube_cookie_path: str | None = None


def _cleanup_youtube_cookie_file() -> None:
    if _youtube_cookie_path:
        try:
            os.remove(_youtube_cookie_path)
        except FileNotFoundError:
            pass


atexit.register(_cleanup_youtube_cookie_file)


def _get_youtube_cookie_file() -> str | None:
    """Materialize an optional base64 Netscape cookie secret to a private temp file."""
    global _youtube_cookie_path
    encoded = Config.YOUTUBE_COOKIES_B64
    if not encoded:
        return None

    with _youtube_cookie_lock:
        if _youtube_cookie_path and os.path.isfile(_youtube_cookie_path):
            return _youtube_cookie_path
        try:
            cookie_data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("YOUTUBE_COOKIES_B64 is not valid base64.") from exc
        if not cookie_data.startswith(
            (b"# Netscape HTTP Cookie File", b"# HTTP Cookie File")
        ):
            raise ValueError("YouTube cookies must use Netscape cookies.txt format.")

        file_descriptor, path = tempfile.mkstemp(
            prefix="music-s3yd-youtube-", suffix="-cookies.txt"
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(file_descriptor, 0o600)
            with os.fdopen(file_descriptor, "wb") as cookie_file:
                cookie_file.write(cookie_data)
        except Exception:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            try:
                os.remove(path)
            except OSError:
                pass
            raise
        _youtube_cookie_path = path
        return path


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
        self.effect = "normal"
        self.dj_role = Config.DJ_ROLE
        self.repeat = False
        self.shuffle = False
        self.autoplay = Config.AUTO_PLAY
        self.voice: discord.VoiceClient | None = None
        self.panel_message: discord.Message | None = None
        self.control_view: discord.ui.View | None = None
        self.text_channel: discord.abc.Messageable | None = None
        self._advance_lock = asyncio.Lock()
        self._effect_update_lock = asyncio.Lock()
        self._playback_generation = 0
        self._track_offset = 0.0
        self._track_started_at: float | None = None
        self._track_rate = 1.0
        self._skip_repeat = False
        self._settings_loaded = False
        self._autoplay_seed: str | None = None
        self._consecutive_failures = 0
        self._youtube_download_dir: str | None = None
        self._youtube_download_link: str | None = None
        self._youtube_download_path: str | None = None

    async def load_settings(self) -> None:
        settings = await self.database.get_settings(self.guild_id)
        self.volume = max(0, min(200, int(settings.get("volume", Config.DEFAULT_VOLUME))))
        self.bass = clamp_bass(settings.get("bass", 0))
        self.effect = normalize_effect(str(settings.get("effect", "normal")))
        saved_dj_role = str(settings.get("dj_role") or "")
        if saved_dj_role == "DJ" and Config.DJ_ROLE != "DJ":
            saved_dj_role = Config.DJ_ROLE
        self.dj_role = saved_dj_role or Config.DJ_ROLE
        self.autoplay = bool(settings.get("autoplay", int(Config.AUTO_PLAY)))
        self.repeat = bool(settings.get("repeat", 0))
        self._settings_loaded = True

    async def save_settings(self) -> None:
        await self.database.save_settings(
            self.guild_id,
            volume=self.volume,
            bass=self.bass,
            effect=self.effect,
            dj_role=self.dj_role,
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
        # A new user request starts a fresh playback attempt window.
        self._consecutive_failures = 0
        self.queue.add(song)
        if not self.current and self.voice and not self.voice.is_playing() and not self.voice.is_paused():
            await self.advance()

    async def _stream_url(self, song: Song) -> tuple[str, dict[str, str]]:
        def extract(
            url: str, use_fallback: bool = False
        ) -> tuple[str, dict[str, str]]:
            options: dict[str, Any] = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
            }
            if not use_fallback:
                options["format"] = "bestaudio/best"
            host = (urlparse(url).hostname or "").lower()
            if host == "youtu.be" or host.endswith("youtube.com"):
                cookie_file = _get_youtube_cookie_file()
                if cookie_file:
                    options["cookiefile"] = cookie_file
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
            if isinstance(info, dict) and info.get("entries"):
                info = next((item for item in info["entries"] if item), None)
            if not isinstance(info, dict) or not info.get("url"):
                raise ValueError("لم أجد رابط بث صوتي صالحًا.")
            headers = info.get("http_headers")
            return str(info["url"]), (
                {str(key): str(value) for key, value in headers.items()}
                if isinstance(headers, dict)
                else {}
            )

        try:
            return await asyncio.to_thread(extract, song.link)
        except Exception as first_error:
            try:
                return await asyncio.to_thread(extract, song.link, True)
            except Exception as fallback_error:
                raise fallback_error from first_error

    @staticmethod
    def _is_youtube_url(url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return host == "youtu.be" or host.endswith("youtube.com")

    def _cleanup_youtube_download(self) -> None:
        directory = self._youtube_download_dir
        self._youtube_download_dir = None
        self._youtube_download_link = None
        self._youtube_download_path = None
        if directory:
            shutil.rmtree(directory, ignore_errors=True)

    def _download_youtube_audio(self, url: str) -> tuple[str, str]:
        """Download YouTube audio with yt-dlp so its cookies and headers are used."""
        directory = tempfile.mkdtemp(prefix="music-s3yd-track-")
        options: dict[str, Any] = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": os.path.join(directory, "%(id)s.%(ext)s"),
        }
        cookie_file = _get_youtube_cookie_file()
        if cookie_file:
            options["cookiefile"] = cookie_file

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
                if isinstance(info, dict) and info.get("entries"):
                    info = next((item for item in info["entries"] if item), None)
                if not isinstance(info, dict):
                    raise ValueError("yt-dlp did not return downloaded audio metadata.")

                requested = info.get("requested_downloads") or []
                path = next(
                    (
                        str(item.get("filepath"))
                        for item in requested
                        if isinstance(item, dict)
                        and item.get("filepath")
                        and os.path.isfile(str(item["filepath"]))
                    ),
                    None,
                )
                if not path:
                    candidate = ydl.prepare_filename(info)
                    if os.path.isfile(candidate):
                        path = candidate
                if not path:
                    path = next(
                        (
                            os.path.join(directory, name)
                            for name in os.listdir(directory)
                            if not name.endswith((".part", ".ytdl"))
                            and os.path.isfile(os.path.join(directory, name))
                        ),
                        None,
                    )
                if not path:
                    raise FileNotFoundError("yt-dlp finished without an audio file.")
                return directory, path
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    async def _playback_input(
        self, song: Song, start_at: float = 0
    ) -> discord.AudioSource:
        if self._is_youtube_url(song.link):
            if (
                self._youtube_download_link != song.link
                or not self._youtube_download_path
                or not os.path.isfile(self._youtube_download_path)
            ):
                self._cleanup_youtube_download()
                directory, path = await asyncio.to_thread(
                    self._download_youtube_audio, song.link
                )
                self._youtube_download_dir = directory
                self._youtube_download_link = song.link
                self._youtube_download_path = path
            return self._create_source(self._youtube_download_path, start_at=start_at)

        stream, headers = await self._stream_url(song)
        return self._create_source(stream, headers=headers, start_at=start_at)

    def _create_source(
        self,
        stream_url: str,
        headers: dict[str, str] | None = None,
        start_at: float = 0,
    ) -> discord.AudioSource:
        options = ffmpeg_options(self.bass, self.effect, start_at=start_at)
        if os.path.isfile(stream_url):
            options["before_options"] = (
                f"-ss {max(0.0, start_at):.3f}" if start_at > 0 else ""
            )
        if headers:
            header_block = "".join(
                f"{name}: {value}\r\n"
                for name, value in headers.items()
                if name.lower() not in {"host", "content-length"}
            )
            if header_block:
                options["before_options"] += f" -headers {shlex.quote(header_block)}"
        source = discord.FFmpegPCMAudio(
            stream_url,
            **options,
        )
        return discord.PCMVolumeTransformer(source, volume=self.volume / 100)

    def _play_source(
        self,
        source: discord.AudioSource,
        start_at: float = 0,
        start_paused: bool = False,
    ) -> None:
        if not self.voice or not self.voice.is_connected():
            raise RuntimeError("Voice client disconnected before playback started.")

        self._playback_generation += 1
        generation = self._playback_generation
        loop = asyncio.get_running_loop()

        def after(error: Exception | None) -> None:
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self._after_track(generation, error))
            )

        self.voice.play(source, after=after)
        self._track_offset = max(0.0, start_at)
        self._track_rate = 1.12 if self.effect == "nightcore" else 1.0
        if start_paused:
            self.voice.pause()
            self._track_started_at = None
        else:
            self._track_started_at = time.monotonic()

    def _current_position(self) -> float:
        position = self._track_offset
        if self._track_started_at is not None:
            position += max(0.0, time.monotonic() - self._track_started_at) * self._track_rate
        if self.current and self.current.duration > 0:
            position = min(position, float(self.current.duration))
        return position

    async def _start_song(
        self,
        song: Song,
        start_at: float = 0,
        start_paused: bool = False,
    ) -> None:
        if not self.voice or not self.voice.is_connected():
            logger.warning("Cannot start playback without a connected voice client.")
            return
        try:
            self._play_source(
                await self._playback_input(song, start_at=start_at),
                start_at=start_at,
                start_paused=start_paused,
            )
        except Exception as exc:
            logger.exception("Could not play %s: %s", song.title, exc)
            await self._handle_playback_failure(song)
            return
        await self._refresh_panel()

    async def _after_track(
        self, generation: int, error: Exception | None = None
    ) -> None:
        if generation != self._playback_generation:
            return
        self._track_started_at = None
        if error is not None:
            logger.error(
                "Audio playback failed in guild %s: %s",
                self.guild_id,
                error,
                exc_info=(type(error), error, error.__traceback__),
            )
            if self.current:
                await self._handle_playback_failure(self.current)
            return

        self._consecutive_failures = 0
        if self.current:
            try:
                await self.database.add_history(
                    self.guild_id, self.current.title, self.current.link
                )
            except Exception:
                logger.exception("Could not store playback history.")
        await self.advance()

    async def _handle_playback_failure(self, song: Song) -> None:
        self._consecutive_failures += 1
        stopped = self._consecutive_failures >= 3
        message = (
            f"⚠️ تعذّر تشغيل **{discord.utils.escape_markdown(song.title)}**. "
            "سأتجاوزها وأحاول تشغيل التالية من قائمة الانتظار."
        )
        if stopped:
            message += " أوقفت المحاولات بعد عدة أخطاء، وبقية القائمة محفوظة."
        if self.text_channel:
            try:
                await self.text_channel.send(message)
            except discord.HTTPException:
                pass

        if stopped:
            self._cleanup_youtube_download()
            self.current = None
            self._track_offset = 0.0
            self._track_started_at = None
            await self._refresh_panel()
            return

        # Playback failures must not start another Autoplay search.
        await self.advance(force=True, allow_autoplay=False)

    async def advance(
        self, force: bool = False, allow_autoplay: bool = True
    ) -> None:
        async with self._advance_lock:
            if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
                return
            if self._skip_repeat:
                force = True
                self._skip_repeat = False

            previous_song = self.current

            if self.current and self.repeat and not force:
                next_song = self.current
            else:
                next_song = self.queue.pop()

            if next_song is None and self.current and self.autoplay and allow_autoplay:
                try:
                    from search import music_search

                    seed = self._autoplay_seed or self.current.title
                    candidates = await music_search.search(
                        f"{seed} similar songs", limit=5
                    )
                    next_song = next(
                        (candidate for candidate in candidates
                         if candidate.link != self.current.link),
                        None,
                    )
                except Exception:
                    logger.exception("Autoplay search failed.")

            if next_song is None:
                self._cleanup_youtube_download()
                self.current = None
                self._track_offset = 0.0
                self._track_started_at = None
                await self._refresh_panel()
                return

            if next_song is not previous_song:
                self._cleanup_youtube_download()
            self.current = next_song
            if next_song.requester is not None:
                self._autoplay_seed = next_song.title

        await self._start_song(next_song)

    async def skip(self) -> None:
        if self.voice and (self.voice.is_playing() or self.voice.is_paused()):
            self._skip_repeat = True
            self.voice.stop()
        else:
            await self.advance(force=True)

    async def pause(self) -> bool:
        if self.voice and self.voice.is_playing():
            self._track_offset = self._current_position()
            self._track_started_at = None
            self.voice.pause()
            await self._refresh_panel()
            return True
        return False

    async def resume(self) -> bool:
        if self.voice and self.voice.is_paused():
            self.voice.resume()
            self._track_started_at = time.monotonic()
            await self._refresh_panel()
            return True
        return False

    async def stop(self, disconnect: bool = False) -> None:
        self.queue.clear()
        self._cleanup_youtube_download()
        self.current = None
        self._track_offset = 0.0
        self._track_started_at = None
        self._playback_generation += 1
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

    def set_effect(self, value: str) -> str:
        self.effect = normalize_effect(value)
        return self.effect

    async def apply_effects_to_current(self) -> bool:
        """Restart the current stream with updated filters at its current position."""
        async with self._effect_update_lock:
            voice = self.voice
            song = self.current
            if (
                not voice
                or not voice.is_connected()
                or not song
                or not (voice.is_playing() or voice.is_paused())
            ):
                return False

            if (
                self.current is not song
                or self.voice is not voice
                or not voice.is_connected()
                or not (voice.is_playing() or voice.is_paused())
            ):
                return False

            position = self._current_position()
            was_paused = voice.is_paused()
            try:
                source = await self._playback_input(song, start_at=position)
            except Exception:
                logger.exception("Could not prepare the current song with updated effects.")
                return False

            if not (voice.is_playing() or voice.is_paused()):
                source.cleanup()
                return False

            # No await between stopping the old source and starting the replacement.
            voice.stop()
            try:
                self._play_source(
                    source,
                    start_at=position,
                    start_paused=was_paused,
                )
            except Exception:
                source.cleanup()
                logger.exception("Could not restart the current song with updated effects.")
                await self._start_song(
                    song,
                    start_at=position,
                    start_paused=was_paused,
                )
                return bool(
                    self.current is song
                    and voice.is_connected()
                    and (voice.is_playing() or voice.is_paused())
                )

            await self._refresh_panel()
            return True

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
